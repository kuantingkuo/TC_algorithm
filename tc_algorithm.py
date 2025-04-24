import xarray as xr
import numpy as np
import numba
import yaml
from utils import (latpad, uvlatpad)
from tc_detection import TC_detect
from file_handling import process_vorticity, process_uv300, process_uv850, process_slp

@numba.njit(parallel=True)
def vintp(var, pres, plevs):
    s = var.shape # time, lev, lat, lon
    out = np.zeros((s[0],len(plevs),s[2],s[3]))
    for t in numba.prange(s[0]):
        for j in range(s[2]):
            for i in range(s[3]):
                out[t,:,j,i] = np.interp(plevs, pres[t,:,j,i], var[t,:,j,i])
                out[t,:,j,i] = np.where(plevs > pres[t,-1,j,i], np.nan, out[t,:,j,i])
    return out

def pre(ds):
    return ds.sel(lev=slice(200,None))

def main(case):
    opath='/data/W.eddie/SPCAM/'
    casename = case
    path = opath+casename+'/atm/hist/'
    outpath = path+casename+'.TC.nc'
    print(outpath)
    #if os.path.isfile(outpath):
    #    continue
    print("open files...")
    with xr.open_mfdataset(path+casename+'.cam.h0.*.nc',
            preprocess=pre, decode_cf=False) as h0:
        pres = h0.hyam*h0.P0 + h0.hybm*h0.PS

    u300, v300 = process_uv300(h0, pres, path, casename)
    u850, v850 = process_uv850(h0, pres, path, casename)
    vort = process_vorticity(u850, v850, pres, path, casename)
    slp = process_slp(h0, pres, path, casename)

    print("load data...")
    if True: ############# switch of south hemisphere ##########
        print('Defines negative vorticity in the Southern Hemisphere.')
        vort.load()
        vort = xr.where(vort.lat < 0, -vort, vort).transpose('time',...)
        ps = h0.PS.load()

    print("Detecting TC-like objects...")
    ds = TC_detect(latpad(vort),
            uvlatpad(u850), uvlatpad(u300), uvlatpad(v850), uvlatpad(v300),
            latpad(slp), latpad(ps)
        )
    print(ds)
    return ds

def load_config(config_path="config.yaml"):
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

if __name__ == "__main__":
    config = load_config()
    cases = config['cases']
    output_path = config['output_path']
    for case in cases:
        ds = main(case)
        print("Output...")
        ds.to_netcdf(f"{output_path}{case}/atm/hist/{case}.TC.nc")
        ds.close()
