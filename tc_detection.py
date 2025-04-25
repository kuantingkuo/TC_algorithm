import numpy as np
import numba
import xarray as xr
from utils import pad_rolling_max, pad_rolling_min, pad_rolling_mean, pad_rolling_min2, mag, latpad, uvlatpad

@numba.njit(parallel=True)
def TC_np(maxvort, maxV850, maxV300, slplow, ps, minps, minvort):
    s = maxvort.shape
    TC = np.full(s, False)
    for t in numba.prange(s[0]):
        for j in range(s[1]):
            for i in range(s[2]):
                TC[t, j, i] = np.all(
                    np.asarray([
                        maxvort[t, j, i] > 3.5e-5,
                        maxV850[t, j, i] > 15.,
                        maxV850[t, j, i] > maxV300[t, j, i],
                        slplow[t, j, i] < -200.,
                        ps[t, j, i] < 100800.,
                        minps[t, j, i] > 88000.,
                        minvort[t, j, i] > 0.
                    ])
                )
    return TC

def TC_detect(vort, u850, u300, v850, v300, slp, ps):
    maxvort = pad_rolling_max(vort).sel(lat=slice(-90, 90))
    maxV850 = pad_rolling_max(mag(u850, v850)).sel(lat=slice(-90, 90))
    maxV300 = mag(u300, v300).sel(lat=slice(-90, 90))
    minps = pad_rolling_min2(ps).sel(lat=slice(-90, 90))
    slplow = slp.sel(lat=slice(-90, 90)) - pad_rolling_mean(slp).sel(lat=slice(-90, 90))
    ps = ps.sel(lat=slice(-90, 90))
    minvort = pad_rolling_min(vort).sel(lat=slice(-90, 90))

    TC = TC_np(
        maxvort.values, maxV850.values, maxV300.values, slplow.values,
        ps.values, minps.values, minvort.values
    ).astype(int)

    ds = xr.Dataset(
        data_vars=dict(
            vort=maxvort,
            V850=maxV850,
            V300=maxV300,
            TC=(maxvort.dims, TC),
            slplow=slplow,
            PS=ps,
            minps=minps
        ),
        coords=dict(
            time=maxvort.time,
            lat=maxvort.lat,
            lon=maxvort.lon
        )
    )
    ds = ds.reset_coords(drop=True)
    ds.load()

    return ds