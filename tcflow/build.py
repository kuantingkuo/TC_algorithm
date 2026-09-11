"""Compiler-aware, atomic IRT builds outside the source tree."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import tempfile
from .config import ROOT

BINARIES = ['irt_objects_release.x', 'irt_advection_field_release.x',
            'irt_tracks_release.x', 'irt_trackmask_release.x', 'irt_tracklinks_release.x']


def module_setup(runtime):
    lines = ['set -euo pipefail']
    if runtime.get('load_modules') or runtime.get('module_use') or runtime.get('module_purge'):
        init = runtime.get('module_init')
        if init:
            lines.append('source ' + shlex.quote(init))
        lines += ['if ! type module >/dev/null 2>&1; then',
                  '  if [ -f /etc/profile.d/modules.sh ]; then source /etc/profile.d/modules.sh; fi',
                  'fi', 'type module >/dev/null 2>&1 || { echo "module command unavailable" >&2; exit 1; }']
        if runtime.get('module_purge'):
            lines.append('module purge')
        if runtime.get('module_use'):
            lines.append('module use ' + shlex.quote(runtime['module_use']))
        for module in runtime.get('load_modules', []):
            lines.append('module load ' + shlex.quote(module))
    return '\n'.join(lines) + '\n'


def shell(runtime, command, cwd=None):
    result = subprocess.run(['bash', '-c', module_setup(runtime) + command],
                            cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'Fortran environment/command failed:\n{command}\n{result.stdout}\n{result.stderr}')
    return result.stdout.strip()


def ordered_link_flags(*groups):
    """Place all library search paths before libraries, preserving link order."""
    search_paths = []
    libraries = []
    for group in groups:
        tokens = list(group)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == '-L' and index + 1 < len(tokens):
                token = '-L' + tokens[index + 1]
                index += 1
            if token.startswith('-L'):
                if token not in search_paths:
                    search_paths.append(token)
            else:
                libraries.append(token)
            index += 1
    return search_paths + libraries


def toolchain(runtime):
    fc = runtime.get('compiler')
    if not fc:
        raise ValueError('Set runtime.compiler after checking the target machine')
    nf = runtime.get('nf_config', 'nf-config')
    compiler_path = shell(runtime, 'command -v ' + shlex.quote(fc))
    compiler_version = shell(runtime, shlex.join([fc, '--version']))
    nf_info = shell(runtime, shlex.join([nf, '--all']))
    include = shlex.split(shell(runtime, shlex.join([nf, '--fflags'])))
    nf_libraries = shlex.split(shell(runtime, shlex.join([nf, '--flibs'])))

    # Some packaged nf-config files include -lnetcdf but omit NetCDF-C's -L
    # directory. Add nc-config's link flags and move all search paths before
    # the libraries so the linker can resolve both netcdff and netcdf.
    nc = runtime.get('nc_config', 'nc-config')
    try:
        nc_path = shell(runtime, 'command -v ' + shlex.quote(nc))
    except RuntimeError:
        nc_path = None
        nc_info = None
        nc_libraries = []
    else:
        nc_info = shell(runtime, shlex.join([nc, '--all']))
        nc_libraries = shlex.split(shell(runtime, shlex.join([nc, '--libs'])))
    libraries = ordered_link_flags(nf_libraries, nc_libraries)
    flags = runtime.get('compiler_flags')
    if flags is None:
        name = Path(fc).name
        if name == 'gfortran':
            flags = ['-O2', '-ffree-line-length-none', '-mcmodel=large']
        elif name in ('ifort', 'ifx'):
            flags = ['-O2', '-free', '-mcmodel=large', '-heap-arrays', '10']
        else:
            raise ValueError(f'Set compiler_flags explicitly for {fc}')
    if isinstance(flags, str):
        flags = shlex.split(flags)
    return dict(compiler=fc, compiler_path=compiler_path, compiler_version=compiler_version,
                flags=flags, includes=include, libraries=libraries,
                netcdf_fortran=nf_info, netcdf_c=nc_info, nc_config_path=nc_path,
                platform=platform.platform(), machine=platform.machine(),
                modules=runtime.get('load_modules', []))


def binary_roundtrip(directory, runtime, tc):
    """Check the existing direct-access representation against NumPy float32."""
    import numpy as np
    directory = Path(directory)
    (directory / 'io_probe.f90').write_text('''program probe
implicit none
real :: a(3,2)
integer :: n
 a=reshape([1.,2.,3.,4.,5.,6.],shape(a))
 inquire(iolength=n) a
 open(10,file='io_probe.dat',form='unformatted',access='direct',recl=n,status='replace')
 write(10,rec=1) a
 close(10)
end program
''')
    command = [tc['compiler'], *tc['flags'], '-o', 'io_probe.x', 'io_probe.f90']
    shell(runtime, shlex.join(command) + '\n./io_probe.x', directory)
    values = np.fromfile(directory / 'io_probe.dat', dtype=np.float32)
    if not np.array_equal(values, np.arange(1, 7, dtype=np.float32)):
        raise RuntimeError('Fortran direct-access output is not compatible with NumPy float32')


def prepare(params, cfg, case_out_dir):
    from tc_algorithm import update_irt_parameters
    runtime = cfg.get('runtime', {})
    tc = toolchain(runtime)
    source_dir = ROOT / 'tracking' / 'tracking2'
    sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source_dir.glob('*.f90'))}
    # Normalize numpy scalars before serializing.
    parameters = {k: v.item() if hasattr(v, 'item') else v for k, v in params.items()}
    metadata = dict(schema=1, toolchain=tc, parameters=parameters, sources=sources,
                    builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    signature = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    root = Path(runtime.get('irt_build_cache_root', ROOT / 'tracking' / 'build_cache')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / signature
    with (root / (signature + '.lock')).open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not ready(target):
            if target.exists():
                raise RuntimeError(f'Incomplete or corrupted build cache: {target}; inspect/remove it explicitly')
            temporary = Path(tempfile.mkdtemp(prefix=signature + '.tmp-', dir=root))
            try:
                for name in sources:
                    shutil.copy2(source_dir / name, temporary / name)
                update_irt_parameters(temporary / 'irt_parameters.f90', parameters)
                binary_roundtrip(temporary, runtime, tc)
                programs = {
                    BINARIES[0]: ['irt_parameters.f90', 'irt_tools.f90', 'irt_objects_release.f90'],
                    BINARIES[1]: ['irt_parameters.f90', 'irt_advection_field_release.f90'],
                    BINARIES[2]: ['irt_parameters.f90', 'irt_tracks_release.f90'],
                    BINARIES[3]: ['irt_parameters.f90', 'irt_trackmask_release.f90'],
                    BINARIES[4]: ['irt_parameters.f90', 'irt_tracklinks_release_shao.f90'],
                }
                logs = []
                for binary, files in programs.items():
                    command = shlex.join([tc['compiler'], *tc['flags'], *tc['includes'], '-o', binary, *files, *tc['libraries']])
                    logs.append(command + '\n' + shell(runtime, command, temporary))
                (temporary / 'build.log').write_text('\n'.join(logs))
                metadata['binary_sha256'] = {b: hashlib.sha256((temporary / b).read_bytes()).hexdigest() for b in BINARIES}
                (temporary / 'build.json').write_text(json.dumps(metadata, indent=2, sort_keys=True))
                temporary.rename(target)
            except BaseException:
                shutil.rmtree(temporary)
                raise
    out = Path(case_out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / '.irt_build_dir').write_text(str(target) + '\n')
    (out / '.irt_grid_signature').write_text(signature + '\n')
    return str(target)


def ready(path):
    path = Path(path)
    if not (path / 'build.json').is_file():
        return False
    metadata = json.loads((path / 'build.json').read_text())
    return all((path / b).is_file() and os.access(path / b, os.X_OK) and
               hashlib.sha256((path / b).read_bytes()).hexdigest() == metadata.get('binary_sha256', {}).get(b)
               for b in BINARIES)
