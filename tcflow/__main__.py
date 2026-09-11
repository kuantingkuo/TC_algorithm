import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from .config import ROOT, resolve


def check_python_dependencies(cfg):
    modules = ('yaml', 'numpy', 'xarray', 'numba', 'windspharm.standard',
               'netCDF4', 'h5netcdf', 'dask')
    if cfg['runtime']['preproc_format'] == 'zarr':
        modules += ('zarr',)
    for module in modules:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as error:
            missing = error.name or module
            environment = cfg['runtime'].get('env_name', 'forge')
            if module == 'zarr' or missing in ('zarr', 'numcodecs'):
                raise RuntimeError(
                    f"Python module {missing!r} is required because "
                    "runtime.preproc_format is 'zarr', but it is unavailable in "
                    f"{sys.executable}. Install zarr and numcodecs in Conda "
                    f"environment {environment!r}, or explicitly set "
                    "runtime.preproc_format: netcdf."
                ) from None
            raise RuntimeError(
                f"Python module {missing!r}, required by {module!r}, is unavailable "
                f"in {sys.executable}. Install the project environment from "
                "envs/environment.yaml and rerun doctor."
            ) from None


def configuration(args):
    paths = [args.config]
    if args.site:
        site = Path(args.site)
        paths.append(site if site.is_file() else ROOT / 'configs' / 'sites' / (args.site + '.yaml'))
    paths.extend(p for p in (args.dataset, args.experiment, args.user_config) if p)
    overrides = {'runtime': {}, 'scheduler': {}}
    cases = args.case or []
    if args.cases:
        cases.extend(c.strip() for c in args.cases.split(',') if c.strip())
    if cases:
        overrides['cases'] = cases
    if args.cpus is not None:
        overrides['runtime']['cpus'] = args.cpus
    for key in ('account', 'partition', 'time', 'memory', 'backend'):
        value = getattr(args, key, None)
        if value is not None:
            overrides['scheduler'][key] = value
    if args.output_path:
        overrides['output_path'] = args.output_path
    return resolve(paths, overrides)


def main():
    parser = argparse.ArgumentParser(description='Portable TC workflow (unchanged CESM science)')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'inspect-input', 'prepare', 'run'):
        p = commands.add_parser(name)
        p.add_argument('--config', default=os.environ.get('CONFIG_FILE', str(ROOT / 'config.yaml')))
        p.add_argument('--site', default='local')
        p.add_argument('--dataset')
        p.add_argument('--experiment')
        p.add_argument('--user-config')
        p.add_argument('--case', action='append')
        p.add_argument('--cases')
        p.add_argument('--cpus', type=int)
        p.add_argument('--account')
        p.add_argument('--partition')
        p.add_argument('--time')
        p.add_argument('--memory')
        p.add_argument('--backend', choices=['local', 'slurm'])
        p.add_argument('--output-path')
        if name in ('prepare', 'run'):
            p.add_argument('--run-id')
            p.add_argument('--submit', action='store_true')
    p = commands.add_parser('execute', help='Run/resume a frozen prepared run')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--run')
    group.add_argument('--batch')
    p.add_argument('--index', type=int, default=0)
    args = parser.parse_args()
    if args.command == 'execute':
        from .workflow import execute
        run = args.run
        if args.batch:
            runs = json.loads(Path(args.batch).read_text())
            if args.index < 0 or args.index >= len(runs):
                raise ValueError('Batch index out of range')
            run = runs[args.index]
        execute(run)
        return
    cfg = configuration(args)
    if args.command == 'inspect-input':
        from .inputs import validate
        print(json.dumps([validate(cfg, c) for c in cfg['cases']], indent=2))
    elif args.command == 'doctor':
        from .build import toolchain, binary_roundtrip, shell
        import shlex
        check_python_dependencies(cfg)
        tc = toolchain(cfg['runtime'])
        with tempfile.TemporaryDirectory(prefix='tc-doctor-') as directory:
            binary_roundtrip(directory, cfg['runtime'], tc)
            source = Path(directory) / 'nc_probe.f90'
            source.write_text('program probe\nuse netcdf\nimplicit none\nprint *, nf90_inq_libvers()\nend program\n')
            print(shell(cfg['runtime'], shlex.join([tc['compiler'], *tc['flags'], *tc['includes'],
                       '-o', 'nc_probe.x', 'nc_probe.f90', *tc['libraries']]) + '\n./nc_probe.x', directory))
        print(json.dumps(tc, indent=2))
        print(f'Python: {sys.executable}; imports, NetCDF link and binary roundtrip passed')
        for key in ('output_path',):
            path = Path(cfg[key])
            parent = path
            while not parent.exists():
                parent = parent.parent
            if not os.access(parent, os.W_OK):
                raise ValueError(f'Not writable: {path}')
        if cfg['scheduler']['backend'] == 'slurm':
            for key in ('account', 'partition'):
                if not cfg['scheduler'].get(key):
                    raise ValueError(f'Set scheduler.{key}; target availability still needs checking on the cluster')
    else:
        from .workflow import prepare, generate_job
        if cfg['scheduler']['backend'] == 'slurm':
            for key in ('account', 'partition'):
                if not cfg['scheduler'].get(key):
                    raise ValueError(f'Set scheduler.{key} before preparing Slurm jobs')
        if args.submit and cfg['scheduler']['backend'] != 'slurm':
            raise ValueError('--submit requires --backend slurm')
        if args.command == 'run' and cfg['scheduler']['backend'] == 'slurm' and not args.submit:
            raise ValueError('Use prepare for Slurm script generation, or run --submit')
        runs = [prepare(cfg, case, args.run_id) for case in cfg['cases']]
        script = generate_job(cfg, runs)
        print(f'Job script: {script}', flush=True)
        for run in runs:
            print(f'Run: {run}', flush=True)
        if args.submit:
            subprocess.run(['sbatch', str(script)], check=True)
        elif args.command == 'run':
            # Honor the configured Python environment in local and batch execution alike.
            subprocess.run(['bash', str(script)], check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError, subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
