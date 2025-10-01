import os
import xarray as xr
import numpy as np
from utils import rolling_stat, StepTimer

def TC_detect(vort, u850, u300, v850, v300, slp, ps, dlat, dlon):
    fine = os.environ.get('PROFILE_FINE', '0') == '1'
    timer = StepTimer(enabled=fine)

    # Rolling statistics using unified degree-aware function
    maxvort = rolling_stat(vort, dlat, dlon, win_lat_deg=3, win_lon_deg=3, stat='max').sel(lat=slice(-90, 90)); timer.mark('roll_max_vort')

    speed850 = np.hypot(u850, v850); timer.mark('compute_speed850')
    maxV850 = rolling_stat(speed850, dlat, dlon, win_lat_deg=3, win_lon_deg=3, stat='max').sel(lat=slice(-90, 90)); timer.mark('roll_max_V850')

    # 300 hPa wind speed (no rolling)
    maxV300 = np.hypot(u300, v300).sel(lat=slice(-90, 90)); timer.mark('compute_speed300')

    # Surface pressure minima (~5° window)
    minps = rolling_stat(ps, dlat, dlon, win_lat_deg=5, win_lon_deg=5, stat='min').sel(lat=slice(-90, 90)); timer.mark('roll_min_ps')

    # Sea-level pressure anomaly (~7° mean window)
    slp_mean = rolling_stat(slp, dlat, dlon, win_lat_deg=7, win_lon_deg=7, stat='mean'); timer.mark('roll_mean_slp')
    slplow = slp.sel(lat=slice(-90, 90)) - slp_mean.sel(lat=slice(-90, 90)); timer.mark('compute_slplow')

    ps = ps.sel(lat=slice(-90, 90))

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
            vort=maxvort,
            V850=maxV850,
            V300=maxV300,
            TC=TC,
            slplow=slplow,
            PS=ps,
            minps=minps
        )
    ).reset_coords(drop=True)
    return ds