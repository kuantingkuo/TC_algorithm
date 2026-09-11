"""One configuration parser for all workflow stages."""
from copy import deepcopy
import os
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    'runtime': {'cpus': 1, 'preproc_format': 'netcdf',
                'env_manager': 'conda', 'env_name': 'forge',
                'load_modules': [], 'compiler': 'gfortran',
                'compiler_flags': None, 'module_purge': False,
                'detection_parallel_point_budget': 25_000_000},
    'scheduler': {'backend': 'local', 'time': '08:00:00',
                  'memory': '16G', 'array_limit': 1},
    'dataset': {'adapter': 'cesm_hybrid', 'rename': {}},
    'initializations': [],
    'invert_vorticity_SH': True,
}


def merge(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def read(path):
    with open(path) as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise ValueError(f'{path}: configuration must be a mapping')
    return value


def safe_name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', str(value)):
        raise ValueError(f'Unsafe case/run name: {value!r}')
    return str(value)


def resolve(paths, overrides=None):
    cfg = deepcopy(DEFAULTS)
    for layer in [*(read(path) for path in paths), overrides or {}]:
        layer = deepcopy(layer)
        if 'case' in layer and 'cases' not in layer:
            layer['cases'] = [layer['case']]
        # Migrate legacy scheduler keys at their own precedence level.
        for key in ('account', 'partition'):
            if key in (layer.get('runtime') or {}) and key not in (layer.get('scheduler') or {}):
                layer.setdefault('scheduler', {})[key] = layer['runtime'][key]
        cfg = merge(cfg, layer)
    for section in ('runtime', 'scheduler', 'dataset'):
        if not isinstance(cfg.get(section), dict):
            raise ValueError(f'{section} must be a mapping')
    cfg['cases'] = cfg.get('cases') or ([cfg['case']] if cfg.get('case') else [])
    if not isinstance(cfg['cases'], list):
        raise ValueError('cases must be a list')
    cfg['cases'] = list(dict.fromkeys(safe_name(c) for c in cfg['cases']))
    if not cfg['cases']:
        raise ValueError('Set case or cases')
    initializations = cfg.get('initializations') or []
    if not isinstance(initializations, list):
        raise ValueError('initializations must be a list')
    cfg['initializations'] = list(dict.fromkeys(
        safe_name(value) for value in initializations
    ))
    rt = cfg['runtime']
    if not re.fullmatch(r'[1-9][0-9]*', str(rt['cpus'])):
        raise ValueError('runtime.cpus must be a positive integer')
    rt['cpus'] = int(rt['cpus'])
    detection_workers = rt.get('detection_workers')
    if detection_workers is not None:
        if detection_workers != 'auto':
            if (isinstance(detection_workers, bool) or
                    not re.fullmatch(r'[1-9][0-9]*', str(detection_workers))):
                raise ValueError(
                    "runtime.detection_workers must be 'auto' or a positive integer"
                )
            rt['detection_workers'] = int(detection_workers)
            if rt['detection_workers'] > rt['cpus']:
                raise ValueError('runtime.detection_workers cannot exceed runtime.cpus')
    point_budget = rt.get('detection_parallel_point_budget')
    if (isinstance(point_budget, bool) or
            not re.fullmatch(r'[1-9][0-9]*', str(point_budget))):
        raise ValueError(
            'runtime.detection_parallel_point_budget must be a positive integer'
        )
    rt['detection_parallel_point_budget'] = int(point_budget)
    for field in ('load_modules', 'python_modules'):
        if field in rt and (not isinstance(rt[field], list) or not all(isinstance(v, str) for v in rt[field])):
            raise ValueError(f'runtime.{field} must be a list of module names')
    if not isinstance(cfg.get('invert_vorticity_SH'), bool):
        raise ValueError('invert_vorticity_SH must be a boolean')
    window = cfg.get('time_range')
    if window is not None and (not isinstance(window, list) or len(window) != 2 or not all(isinstance(v, str) for v in window)):
        raise ValueError('time_range must be two quoted date/time strings')
    if not isinstance(cfg['dataset'].get('rename'), dict):
        raise ValueError('dataset.rename must be a mapping')
    first_files = cfg['dataset'].get('first_files')
    if first_files is not None:
        if isinstance(first_files, bool) or not re.fullmatch(r'[1-9][0-9]*', str(first_files)):
            raise ValueError('dataset.first_files must be a positive integer')
        cfg['dataset']['first_files'] = int(first_files)
    if rt.get('env_manager') in ('conda', 'micromamba') and not rt.get('env_name'):
        raise ValueError('runtime.env_name is required')
    if cfg['scheduler']['backend'] not in ('local', 'slurm'):
        raise ValueError('scheduler.backend must be local or slurm')
    if cfg['dataset']['adapter'] != 'cesm_hybrid':
        raise ValueError('Only cesm_hybrid is validated; other vertical coordinates need an adapter')
    if rt['preproc_format'] not in ('netcdf', 'zarr'):
        raise ValueError('runtime.preproc_format must be netcdf or zarr')
    if not cfg.get('output_path'):
        raise ValueError('Set output_path')
    # Resolve paths at preparation time, never in the batch job working directory.
    for key in ('case_path', 'output_path'):
        if cfg.get(key):
            cfg[key] = str(Path(os.path.expandvars(cfg[key])).expanduser().resolve())
    for key in ('scratch_root', 'irt_build_cache_root', 'module_use'):
        if rt.get(key):
            rt[key] = str(Path(os.path.expandvars(rt[key])).expanduser().resolve())
    if not rt.get('irt_build_cache_root'):
        rt['irt_build_cache_root'] = str(Path(cfg['output_path']) / '.build_cache')
    return cfg


def load(path=None):
    return read(path or os.environ.get('TC_CONFIG') or os.environ.get('CONFIG_FILE') or 'config.yaml')


def write(path, cfg):
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=False))
