import os
import xarray as xr
import numpy as np
from utils import rolling_stat, StepTimer


def _clip_lat(arr: xr.DataArray, lower: float = -90.0, upper: float = 90.0) -> xr.DataArray:
    """Latitude clip robust to non-monotonic coordinates.

    Using boolean masking avoids pandas slice-index monotonicity requirements.
    """
    return arr.where((arr.lat >= lower) & (arr.lat <= upper), drop=True)

def TC_detect(vort, u850, u300, v850, v300, slp, ps, dlat, dlon):
    fine = os.environ.get('PROFILE_FINE', '0') == '1'
    timer = StepTimer(enabled=fine)

    # Rolling statistics using unified degree-aware function
    maxvort = _clip_lat(rolling_stat(vort, dlat, dlon, win_lat_deg=3, win_lon_deg=3, stat='max')); timer.mark('roll_max_vort')

    speed850 = np.hypot(u850, v850); timer.mark('compute_speed850')
    maxV850 = _clip_lat(rolling_stat(speed850, dlat, dlon, win_lat_deg=3, win_lon_deg=3, stat='max')); timer.mark('roll_max_V850')

    # 300 hPa wind speed (no rolling)
    maxV300 = _clip_lat(np.hypot(u300, v300)); timer.mark('compute_speed300')

    # Surface pressure minima (~5° window)
    minps = _clip_lat(rolling_stat(ps, dlat, dlon, win_lat_deg=5, win_lon_deg=5, stat='min')); timer.mark('roll_min_ps')

    # Sea-level pressure anomaly (~7° mean window)
    slp_mean = rolling_stat(slp, dlat, dlon, win_lat_deg=7, win_lon_deg=7, stat='mean'); timer.mark('roll_mean_slp')
    slplow = _clip_lat(slp) - _clip_lat(slp_mean); timer.mark('compute_slplow')

    ps = _clip_lat(ps)

    # Align all feature fields to guard against subtle time/lat mismatches
    ps, maxvort, maxV850, maxV300, slplow, minps = xr.align(
        ps, maxvort, maxV850, maxV300, slplow, minps, join='override'
    )

    # Inline boolean mask (xarray-aware, dask friendly)
    TC = (
        (maxvort > 3.5e-5) &
        (maxV850 > 15.0) &
        (maxV850 > maxV300) &
        (slplow < -200.0) &
        (ps < 100800.0) &
        (minps > 86000.0)
    ).astype('uint8').rename('TC'); timer.mark('TC_mask')

    ds = xr.Dataset(
        data_vars=dict(
            maxvort=maxvort,
            V850=maxV850,
            V300=maxV300,
            TC=TC,
            slplow=slplow,
            PS=ps,
            minps=minps
        )
    ).reset_coords(drop=True)
    return ds