"""Frozen runs, Slurm submission, stage receipts and safe retries."""
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid
from .config import ROOT, safe_name, write, load
from .inputs import file_manifest, validate
from .build import module_setup


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def source_signature():
    files = sorted(list(ROOT.glob('*.py')) + list((ROOT / 'tcflow').glob('*.py')) +
                   list((ROOT / 'tracking' / 'tracking2').glob('*.f90')))
    return {str(p.relative_to(ROOT)): digest(p) for p in files}


def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True))
    temporary.replace(path)


def package_versions():
    versions = {}
    for package in ('numpy', 'xarray', 'numba', 'windspharm', 'netCDF4', 'h5netcdf', 'zarr', 'dask', 'PyYAML'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def prepare(cfg, case, run_id=None):
    from copy import deepcopy
    cfg = deepcopy(cfg)
    run_id = safe_name(run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    run_dir = Path(cfg['output_path']) / case / run_id
    if run_dir.exists():
        raise FileExistsError(f'Run already exists: {run_dir}; use execute --run to resume')
    manifest = file_manifest(cfg, case)
    # Freeze absolute discovery patterns as well as their resolved file list.
    for kind in ('atmosphere', 'sst'):
        cfg['dataset'][kind] = [entry['path'] for entry in manifest[kind]]
    report = validate(cfg, case)
    cfg['case'], cfg['cases'] = case, [case]
    cfg['_input_manifest'] = manifest
    cfg['_run_dir'] = str(run_dir)
    cfg['output_path'] = str(run_dir / 'results')
    scratch = Path(cfg['runtime'].get('scratch_root') or run_dir / 'work')
    if cfg['runtime'].get('scratch_root'):
        scratch = scratch / ('tc-' + case + '-' + run_id)
    cfg['_work_dir'] = str(scratch)
    cfg['runtime']['preproc_tmp_dir'] = str(scratch / 'preproc')
    (run_dir / 'logs').mkdir(parents=True)
    (run_dir / 'results' / case).mkdir(parents=True)
    write(run_dir / 'resolved_config.yaml', cfg)
    save_json(run_dir / 'input_manifest.json', manifest)
    save_json(run_dir / 'input_report.json', report)
    versions = package_versions()
    save_json(run_dir / 'run_metadata.json', {
        'config_sha256': digest(run_dir / 'resolved_config.yaml'),
        'sources': source_signature(), 'prepared_python': sys.executable,
        'prepared_packages': versions, 'prepared_python_version': sys.version, 'created_utc': datetime.now(timezone.utc).isoformat(),
    })
    return run_dir


def python_command(runtime):
    manager = runtime.get('env_manager', 'conda')
    if manager == 'conda':
        return [runtime.get('env_executable', 'conda'), 'run', '--no-capture-output',
                '-n', runtime['env_name'], 'python']
    if manager == 'micromamba':
        return [runtime.get('env_executable', 'micromamba'), 'run', '-n', runtime['env_name'], 'python']
    if manager == 'none' and runtime.get('python_executable'):
        return [runtime['python_executable']]
    raise ValueError('Set env_manager=conda/micromamba with env_name, or none with python_executable')


def generate_job(cfg, runs):
    scheduler = cfg['scheduler']
    if scheduler['backend'] == 'slurm':
        for key in ('account', 'partition'):
            if not scheduler.get(key):
                raise ValueError(f'Set scheduler.{key} for Slurm; no example values are assumed')
    root = Path(cfg['output_path']) / 'submissions' / uuid.uuid4().hex
    root.mkdir(parents=True)
    save_json(root / 'runs.json', [str(r) for r in runs])
    lines = ['#!/bin/bash']
    def directive(key, value):
        value = str(value)
        if any(c.isspace() for c in value) or any(c in value for c in '\r\n'):
            raise ValueError(f'Slurm {key} cannot contain whitespace')
        lines.append(f'#SBATCH --{key}={value}')
    if scheduler['backend'] == 'slurm':
        for key, value in [('job-name', scheduler.get('job_name', 'TC.pipeline')),
                           ('nodes', 1), ('ntasks', 1), ('cpus-per-task', cfg['runtime']['cpus']),
                           ('account', scheduler['account']), ('partition', scheduler['partition']),
                           ('time', scheduler['time']), ('mem', scheduler['memory']),
                           ('output', root / '%A_%a.out'), ('error', root / '%A_%a.err')]:
            directive(key, value)
        if scheduler.get('qos'):
            directive('qos', scheduler['qos'])
        if len(runs) > 1:
            limit = int(scheduler['array_limit'])
            if limit < 1:
                raise ValueError('scheduler.array_limit must be positive')
            directive('array', f'0-{len(runs)-1}%{limit}')
    lines += ['set -euo pipefail', 'cd ' + shlex.quote(str(ROOT))]
    runtime = cfg['runtime']
    if runtime.get('python_modules'):
        lines.append(module_setup(dict(runtime, load_modules=runtime['python_modules'])))
    # A compiler or application module may expose packages built for another
    # Python ABI. Apply this after module loading so the selected environment
    # sees only the repository plus its own site-packages.
    lines += ['unset PYTHONHOME', 'export PYTHONNOUSERSITE=1',
              'export PYTHONPATH=' + shlex.quote(str(ROOT))]
    cmd = shlex.join(python_command(runtime) + ['-m', 'tcflow', 'execute', '--batch', str(root / 'runs.json')])
    if scheduler['backend'] == 'slurm':
        lines.append('exec ' + cmd + ' --index "${SLURM_ARRAY_TASK_ID:-0}"')
    else:
        for index in range(len(runs)):
            lines.append(cmd + ' --index ' + str(index))
    script = root / 'job.sh'
    script.write_text('\n'.join(lines) + '\n')
    script.chmod(0o755)
    return script


def execute(run_dir):
    run_dir = Path(run_dir).resolve()
    cfg_path = run_dir / 'resolved_config.yaml'
    cfg = load(cfg_path)
    metadata = json.loads((run_dir / 'run_metadata.json').read_text())
    if metadata['config_sha256'] != digest(cfg_path) or metadata['sources'] != source_signature():
        raise RuntimeError('Configuration or source changed since preparation; prepare a new run')
    if metadata['prepared_packages'] != package_versions() or metadata['prepared_python_version'] != sys.version:
        raise RuntimeError('Python environment changed since preparation; prepare using the deployment environment')
    if file_manifest(cfg, cfg['case']) != cfg['_input_manifest']:
        raise RuntimeError('Input size/mtime changed since preparation; prepare a new run')
    cpus = cfg['runtime']['cpus']
    if os.environ.get('SLURM_JOB_ID'):
        allocated = os.environ.get('SLURM_CPUS_PER_TASK')
        if not allocated:
            raise RuntimeError('SLURM_CPUS_PER_TASK is missing; request --cpus-per-task')
        if cpus > int(allocated):
            raise RuntimeError(f'Requested {cpus} workers exceeds Slurm allocation {allocated}')
    elif cfg['scheduler']['backend'] == 'slurm':
        raise RuntimeError('This run requires a Slurm allocation; submit its generated job.sh')
    with (run_dir / 'run.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f'Run is already executing: {run_dir}') from None
        metadata.update(executed_python=sys.executable, slurm_job_id=os.environ.get('SLURM_JOB_ID'))
        save_json(run_dir / 'run_metadata.json', metadata)
        out = Path(cfg['output_path']) / cfg['case']
        expected = {
            'build': ['.irt_build_dir', '.irt_grid_signature'],
            'detect': [cfg['case'] + '.TC.nc'],
            'track': ['irt_objects_mask.dat', 'irt_objects_output.txt', 'irt_tracks_mask.dat',
                      'irt_tracks_output.txt', 'irt_tracklinks_output.txt'],
            'lifetime': ['TC.nc', 'TC.txt', 'irt_tracks_mask.ctl'],
        }
        redo = False
        for stage, names in expected.items():
            if metadata['sources'] != source_signature() or metadata['config_sha256'] != digest(cfg_path):
                raise RuntimeError('Source or configuration changed during execution; prepare a new run')
            receipt = run_dir / (stage + '.done.json')
            valid = False
            if receipt.exists() and not redo:
                recorded = json.loads(receipt.read_text())
                valid = all((out / n).is_file() and digest(out / n) == recorded.get(n) for n in names)
                if valid and stage == 'build':
                    from .build import ready
                    valid = ready((out / '.irt_build_dir').read_text().strip())
            if valid:
                print(f'[{stage}] verified completed output; skipping', flush=True)
                continue
            redo = True
            # Invalidate downstream receipts before rerunning an earlier stage.
            for downstream in list(expected)[list(expected).index(stage):]:
                (run_dir / (downstream + '.done.json')).unlink(missing_ok=True)
            env = os.environ.copy()
            env.update(TC_CONFIG=str(cfg_path), TC_CPUS=str(cpus),
                       OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                       NUMBA_NUM_THREADS=str(cpus if stage == 'lifetime' else 1))
            env.pop('PYTHONHOME', None)
            env['PYTHONNOUSERSITE'] = '1'
            env['PYTHONPATH'] = str(ROOT)
            print(f'[{stage}] running; log: {run_dir / "logs" / (stage + ".log")}', flush=True)
            with (run_dir / 'logs' / (stage + '.log')).open('a') as log:
                result = subprocess.run([sys.executable, '-m', 'tcflow.stages', stage, '--config', str(cfg_path)],
                                        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError(f'{stage} failed (exit {result.returncode}); see {log.name}')
            if file_manifest(cfg, cfg['case']) != cfg['_input_manifest']:
                raise RuntimeError('Input changed during execution; prepare a new run')
            save_json(receipt, {n: digest(out / n) for n in names})
        print(f'Completed: {out}', flush=True)
