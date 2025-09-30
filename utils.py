import numpy as np
import numba
import xarray as xr
import yaml
from time import perf_counter

class StepTimer:
    """Lightweight step timer for coarse profiling.

    Example:
        timer = StepTimer()
        ... code ...
        timer.mark("after load")
    """
    __slots__ = ("_last", "enabled")
    def __init__(self, enabled: bool = True):
        self._last = perf_counter()
        self.enabled = enabled
    def mark(self, label: str):
        if not self.enabled:
            return 0.0
        now = perf_counter()
        dt = now - self._last
        print(f"[TIMER] {label}: {dt:8.3f}s")
        self._last = now
        return dt

class time_block:
    """Context manager to time a code block.

    Usage:
        with time_block("compute vort"):
            ...
    """
    def __init__(self, label: str, enabled: bool = True):
        self.label = label
        self.enabled = enabled
    def __enter__(self):
        if self.enabled:
            self._start = perf_counter()
        return self
    def __exit__(self, exc_type, exc, tb):
        if self.enabled:
            dt = perf_counter() - self._start
            print(f"[TIMER] {self.label}: {dt:8.3f}s")
        return False

def time_func(func):
    """Decorator to time a function call (simple wall clock)."""
    def wrapper(*args, **kwargs):
        start = perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            dt = perf_counter() - start
            print(f"[TIMER] {func.__name__}: {dt:8.3f}s")
    return wrapper

@numba.njit
def haversine(lato, lono, lat, lon):
    distance = np.empty((len(lat), len(lon)))
    At = 6371.22
    Theta1 = np.deg2rad(lono)
    Phi1 = np.deg2rad(90. - lato)
    for j in range(len(lat)):
        for i in range(len(lon)):
            Theta2 = np.deg2rad(lon[i])
            Phi2 = np.deg2rad(90. - lat[j])
            temp = np.cos(Phi1) * np.cos(Phi2) + np.cos(Theta1 - Theta2) * np.sin(Phi1) * np.sin(Phi2)
            temp = np.minimum(temp, 1.)
            temp = np.maximum(temp, -1.)
            Alpha = np.arccos(temp)
            distance[j, i] = Alpha * At
    return distance

@numba.njit(parallel=True)
def _vintp_multi(var, pres, plevs):
    """Original multi-level interpolation using np.interp inside loops.

    Parameters
    ----------
    var : 4D array (time, lev, lat, lon)
    pres : 4D array (time, lev, lat, lon) ascending in lev
    plevs : 1D array of target pressures (ascending)
    """
    s = var.shape  # time, lev, lat, lon
    out = np.empty((s[0], len(plevs), s[2], s[3]), dtype=var.dtype)
    for t in numba.prange(s[0]):
        for j in range(s[2]):
            for i in range(s[3]):
                out[t, :, j, i] = np.interp(plevs, pres[t, :, j, i], var[t, :, j, i])
    return out

@numba.njit(parallel=True)
def _vintp_single(var, pres, plev):
    """Specialized fast path for a single pressure level.

    Performs manual linear interpolation to avoid np.interp overhead per column.

    Returns
    -------
    out : 3D array (time, lat, lon)
    """
    s = var.shape  # time, lev, lat, lon
    out = np.empty((s[0], s[2], s[3]), dtype=var.dtype)
    for t in numba.prange(s[0]):
        for j in range(s[2]):
            for i in range(s[3]):
                pcol = pres[t, :, j, i]
                vcol = var[t, :, j, i]
                # Bounds check
                if plev <= pcol[0]:
                    out[t, j, i] = vcol[0]
                    continue
                if plev >= pcol[-1]:
                    out[t, j, i] = vcol[-1]
                    continue
                # Binary search index (manual searchsorted)
                lo = 0
                hi = pcol.shape[0] - 1
                while hi - lo > 1:
                    mid = (hi + lo) // 2
                    if pcol[mid] <= plev:
                        lo = mid
                    else:
                        hi = mid
                p1 = pcol[lo]
                p2 = pcol[hi]
                v1 = vcol[lo]
                v2 = vcol[hi]
                w = (plev - p1) / (p2 - p1)
                out[t, j, i] = v1 + w * (v2 - v1)
    return out

def vintp(var, pres, plevs):
    """Adaptive interface for vertical interpolation.

    If a single target level is requested, use a specialized fast path.
    Otherwise, fall back to the original multi-level interpolation.

    Parameters
    ----------
    var : ndarray (time, lev, lat, lon)
    pres : ndarray (time, lev, lat, lon)
    plevs : 1D ndarray of target pressures (ascending)
    """
    if isinstance(plevs, (list, tuple)):
        plevs = np.asarray(plevs, dtype=pres.dtype)
    if plevs.ndim != 1:
        raise ValueError("plevs must be 1D")
    if plevs.size == 1:
        single = _vintp_single(var, pres, plevs[0])
        return single[:, np.newaxis, :, :]
    return _vintp_multi(var, pres, plevs)

def _ensure_odd(n: int) -> int:
    if n % 2 == 0:
        return n + 1
    return n

def rolling_stat(arr: xr.DataArray,
                 dlat: float,
                 dlon: float,
                 win_lat_deg: float = 3.0,
                 win_lon_deg: float = 3.0,
                 stat: str = 'min',
                 lon_cyclic: bool = True,
                 pad_mode_lat: str = 'edge') -> xr.DataArray:
    """Generic degree-aware rolling statistic with padding.

    Parameters
    ----------
    arr : xr.DataArray (dims must include 'lat','lon')
    dlat, dlon : grid spacing in degrees
    win_lat_deg, win_lon_deg : target window size in degrees (approximate)
    stat : one of {'min','max','mean'} (nan-aware)
    lon_cyclic : if True, wrap lon when padding
    pad_mode_lat : padding mode for latitude (passed to xarray.DataArray.pad)

    Returns
    -------
    xr.DataArray with same shape as input (time, lat, lon) after trimming pad.
    """
    if 'lat' not in arr.dims or 'lon' not in arr.dims:
        raise ValueError("Input array must have 'lat' and 'lon' dimensions")
    # Derive window sizes in grid points (ensure odd)
    nlat = _ensure_odd(max(1, int(np.round(win_lat_deg / dlat))))
    nlon = _ensure_odd(max(1, int(np.round(win_lon_deg / dlon))))
    plat = nlat // 2
    plon = nlon // 2
    lon_pad = (plon, plon)
    lat_pad = (plat, plat)
    pad_kwargs = {}
    pad_kwargs['lon'] = lon_pad
    pad_kwargs['lat'] = lat_pad
    arrpad = arr.pad(**pad_kwargs, mode='wrap' if lon_cyclic else 'edge')
    # Latitude padding uses separate mode if specified and differs from lon
    if pad_mode_lat != 'edge':
        # Re-pad latitude only if a different mode requested
        arrpad = xr.concat([
            arrpad.isel(lat=slice(plat, None)),  # placeholder to keep dims if needed
        ], dim='lat')  # minimal; currently pad_mode_lat variations not implemented deeply
    roll = arrpad.rolling(lat=nlat, lon=nlon, center=True)
    stat_map = {
        'min': np.nanmin,
        'max': np.nanmax,
        'mean': np.nanmean
    }
    if stat not in stat_map:
        raise ValueError(f"Unsupported stat '{stat}'")
    out = roll.reduce(stat_map[stat])
    # Trim back to original size
    out = out.isel(lat=slice(plat, -plat if plat > 0 else None), lon=slice(plon, -plon if plon > 0 else None))
    # Align coordinates with original
    out = out.assign_coords(lat=arr.lat, lon=arr.lon)
    return out

def latpad(arr, dlat, pad_deg: float = 3.0):
    """Add polar mirror padding for scalar fields.

    Parameters
    ----------
    arr : xr.DataArray with dims (..., lat, lon) where leading dim often 'time'
    dlat : latitude increment (deg)
    pad_deg : degrees of latitude to mirror (default 3)
    """
    deg_rows = int(np.round(pad_deg / dlat))
    arrpad = arr.pad(lat=(deg_rows, deg_rows))
    shift = int(len(arr.lon) / 2)
    Spad = arr.isel(lat=list(range(deg_rows, 0, -1))).roll(lon=shift)
    Npad = arr.isel(lat=list(range(-2, -deg_rows-2, -1))).roll(lon=shift)
    arrpad.data[:, :deg_rows, :] = Spad
    arrpad.data[:, -deg_rows:, :] = Npad
    return arrpad

def uvlatpad(arr, dlat, pad_deg: float = 3.0):
    """Add polar mirror padding for vector wind components (sign inversion at poles).

    Parameters
    ----------
    arr : xr.DataArray (u or v) with dims (..., lat, lon)
    dlat : latitude increment (deg)
    pad_deg : degrees of latitude to mirror (default 3)
    """
    deg_rows = int(np.round(pad_deg / dlat))
    arrpad = arr.pad(lat=(deg_rows, deg_rows))
    shift = int(len(arr.lon) / 2)
    Spad = arr.isel(lat=list(range(deg_rows, 0, -1))).roll(lon=shift)
    Npad = arr.isel(lat=list(range(-2, -deg_rows-2, -1))).roll(lon=shift)
    arrpad.data[:, :deg_rows, :] = -Spad
    arrpad.data[:, -deg_rows:, :] = -Npad
    return arrpad

def first_nonzero(arr, axis, invalid_val=-999):  # numpy form
    mask = arr != 0
    return np.where(mask.any(axis=axis), mask.argmax(axis=axis), invalid_val)

def vertical_slice_bounds(pres: np.ndarray,
                          target_pressure: float,
                          lev_len: int,
                          axis: int = 1,
                          pad: int = 1,
                          invalid_val: int = -999):
    """Return inclusive (k1, k2) vertical index bounds bracketing target pressure.

    Finds first indices where pres >= target_pressure along given axis, expands
    by pad levels on each side, clips to [0, lev_len-1]. If no crossings, returns
    full range (0, lev_len-1).
    """
    k = first_nonzero(pres >= target_pressure, axis=axis, invalid_val=invalid_val)
    valid = k != invalid_val
    if not np.any(valid):  # Fallback: keep all levels
        return 0, lev_len - 1
    k_min = int(k[valid].min()) - pad
    k_max = int(k[valid].max()) + pad
    if k_min < 0:
        k_min = 0
    if k_max > lev_len - 1:
        k_max = lev_len - 1
    return k_min, k_max

def load_config(config_path="config.yaml"):
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)
