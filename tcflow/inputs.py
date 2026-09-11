"""Explicit CESM input contract; no regridding, filling or unit conversion."""
import glob
import os
from pathlib import Path
import numpy as np
import xarray as xr


def discover(cfg, case, kind='atmosphere'):
    dataset = cfg.get('dataset', {})
    spec = dataset.get(kind)
    if kind == 'sst' and spec is None:
        spec = dataset.get('atmosphere')
    if spec is None:
        spec = '{root}/{case}/atm/hist/{case}.' + cfg['file_pattern']
    if isinstance(spec, str):
        spec = [spec]
    if not isinstance(spec, list) or not spec:
        raise ValueError(f'dataset.{kind} must be a pattern or nonempty list')
    files = []
    initialization = cfg.get('initialization', '')
    for pattern in spec:
        expanded = os.path.expandvars(pattern.format(
            root=cfg.get('case_path', ''), case=case,
            initialization=initialization,
        ))
        matches = sorted(glob.glob(os.path.expanduser(expanded)))
        if not matches:
            raise FileNotFoundError(f'No {kind} files match {expanded}')
        files.extend(str(Path(p).resolve()) for p in matches)
    return sorted(set(files))


def file_manifest(cfg, case):
    manifest = {}
    for kind in ('atmosphere', 'sst'):
        manifest[kind] = [dict(path=p, size=Path(p).stat().st_size,
                               mtime_ns=Path(p).stat().st_mtime_ns)
                          for p in discover(cfg, case, kind)]
    return manifest


def open_input(cfg, case, kind='atmosphere', decode_cf=False):
    frozen = cfg.get('_input_manifest', {}).get(kind)
    files = [item['path'] for item in frozen] if frozen else discover(cfg, case, kind)
    rename = cfg.get('dataset', {}).get('rename', {})
    def preprocess(ds):
        return ds.rename({old: new for old, new in rename.items() if old in ds.variables or old in ds.dims})
    ds = xr.open_mfdataset(files, preprocess=preprocess, decode_cf=decode_cf,
                           data_vars='all', join='exact', compat='no_conflicts')
    window = cfg.get('time_range')
    if window:
        # Select against decoded timestamps, retaining original raw representation for detection.
        decoded = ds if decode_cf else xr.decode_cf(ds[['time']])
        selection = decoded.sel(time=slice(window[0], window[1])).time
        keep = decoded.indexes['time'].get_indexer(selection.values)
        ds = ds.isel(time=keep)
    return ds


def validate(cfg, case):
    with open_input(cfg, case, decode_cf=True) as atmosphere, open_input(cfg, case, 'sst', True) as sst:
        for name in ('time', 'lat', 'lon', 'lev'):
            if name not in atmosphere.coords or atmosphere[name].ndim != 1:
                raise ValueError(f'Required 1D coordinate: {name}')
        required = ['U', 'V', 'PS', 'T', 'Q', 'Z3', 'hyam', 'hybm', 'P0']
        missing = [v for v in required if v not in atmosphere]
        if missing:
            raise ValueError(f'Missing CESM fields: {missing}')
        for name in ('U', 'V', 'T', 'Q', 'Z3'):
            if set(atmosphere[name].dims) != {'time', 'lev', 'lat', 'lon'}:
                raise ValueError(f'{name}: expected time/lev/lat/lon dimensions')
        for name in ('PS', 'PSL', 'U850', 'V850'):
            if name in atmosphere and set(atmosphere[name].dims) != {'time', 'lat', 'lon'}:
                raise ValueError(f'{name}: expected time/lat/lon dimensions')
        if ('U850' in atmosphere) != ('V850' in atmosphere):
            raise ValueError('Provide U850 and V850 together, or neither')
        if not np.all(np.diff(atmosphere.lev.values) > 0):
            raise ValueError('CESM lev must increase toward the surface')
        if atmosphere.sel(lev=slice(200, None)).sizes['lev'] < 2:
            raise ValueError('Legacy lev >= 200 selection leaves fewer than two levels')
        units = {'U': {'m/s', 'm s-1', 'm s**-1'}, 'V': {'m/s', 'm s-1', 'm s**-1'},
                 'PS': {'Pa'}, 'P0': {'Pa'}, 'T': {'K'}, 'Q': {'kg/kg', 'kg kg-1', '1'},
                 'Z3': {'m'}, 'PSL': {'Pa'}, 'U850': {'m/s', 'm s-1', 'm s**-1'},
                 'V850': {'m/s', 'm s-1', 'm s**-1'}}
        for name, allowed in units.items():
            if name in atmosphere and atmosphere[name].attrs.get('units') not in allowed:
                raise ValueError(f'{name}: units must be one of {sorted(allowed)}; explicit conversion needed')
        for name in ('lat', 'lon'):
            values = atmosphere[name].values
            delta = np.diff(values)
            if len(delta) == 0 or not np.all(delta > 0) or not np.allclose(delta, delta[0], rtol=1e-5, atol=1e-7):
                raise ValueError(f'{name}: current tracking requires an ascending regular grid')
            if delta[0] >= 3:
                raise ValueError(f'{name}: legacy resolution limit is < 3 degrees')
        lon, lat = atmosphere.lon.values, atmosphere.lat.values
        if not np.isclose(lon[-1] - lon[0] + lon[1] - lon[0], 360, atol=1e-4):
            raise ValueError('Current longitude-periodic algorithm requires global coverage without duplicate endpoint')
        if lat[0] < -90 or lat[-1] > 90 or lat[0] - (lat[1]-lat[0]) > -90 or lat[-1] + (lat[1]-lat[0]) < 90:
            raise ValueError('Current polar treatment requires a global latitude grid')
        times = atmosphere.time.values
        if len(times) < 2:
            raise ValueError('At least two timestamps required to verify hourly sampling')
        delta = np.diff(times)
        hours = np.array([d.total_seconds()/3600 if hasattr(d, 'total_seconds') else d/np.timedelta64(1, 'h') for d in delta])
        if not np.allclose(hours, 1, rtol=0, atol=1e-8):
            raise ValueError('Legacy lifetime=36 counts timesteps: only continuous hourly input is supported')
        for name in ('time', 'lat', 'lon'):
            if name not in sst.coords or not atmosphere[name].equals(sst[name]):
                raise ValueError(f'SST {name} does not exactly match atmosphere; no positional alignment allowed')
        field = 'SST' if 'SST' in sst else 'TS' if 'TS' in sst else None
        if field is None or sst[field].attrs.get('units') != 'K':
            raise ValueError('SST or TS in Kelvin is required')
        if tuple(sst[field].dims) != ('time', 'lat', 'lon'):
            raise ValueError('SST/TS dimensions must be (time, lat, lon)')
        calendar = atmosphere.time.encoding.get('calendar', 'standard')
        if calendar not in ('noleap', '365_day', 'standard', 'gregorian', 'proleptic_gregorian'):
            raise ValueError(f'Calendar {calendar} needs explicit CTL support')
        return {'case': case, 'sizes': dict(atmosphere.sizes), 'calendar': calendar,
                'start': str(times[0]), 'end': str(times[-1]), 'interval_hours': 1,
                'sst_field': field, 'variables': sorted(atmosphere.data_vars),
                **({'initialization': cfg['initialization']}
                   if cfg.get('initialization') else {})}
