import os
import xarray as xr
import numpy as np
from .utils import vintp, first_nonzero
from windspharm.xarray import VectorWind

def process_vorticity(u850, v850, pres, path, casename):
    vortfile = path + casename + '.vort850.nc'
    if os.path.isfile(vortfile):
        print('open vorticity file')
        vortnc = xr.open_dataset(vortfile, chunks={})
        vort = vortnc.vorticity
        vortnc.close()
    else:
        print('calculate vorticity...')
        k = first_nonzero(pres >= 85000., axis=1)
        k1 = k.min() - 1
        k2 = k.max() + 1
        w = VectorWind(u850, v850)
        vort = w.vorticity()
        if vort.lat[0].values > vort.lat[1].values:
            vort = vort[..., -1::-1, :]
        vort.to_netcdf(vortfile)
    return vort

def process_uv300(h0, pres, path, casename):
    uv300file = path+casename+'.UV300.nc'
    if os.path.isfile(uv300file):
        print('open UV300 file')
        uv300 = xr.open_dataset(uv300file, chunks={})
    else:
        print('calculate U300, V300...')
        u300 = vintp(h0.U.values, pres.values, np.array([30000.])).squeeze()
        v300 = vintp(h0.V.values, pres.values, np.array([30000.])).squeeze()
        uv300 = xr.Dataset(
                data_vars=dict(
                    U300=(['time','lat','lon'], u300),
                    V300=(['time','lat','lon'], v300)
                ),
                coords=dict(
                    time=h0.time,
                    lat=h0.lat,
                    lon=h0.lon
                )
            )
        uv300['U300'] = uv300.U300.assign_attrs(h0.U.attrs)
        uv300['V300'] = uv300.V300.assign_attrs(h0.V.attrs)
        uv300.to_netcdf(uv300file)
    u300 = uv300.U300
    v300 = uv300.V300
    uv300.close()
    return u300, v300

def process_uv850(h0, pres, path, casename):
    uv850file = path+casename+'.UV850.nc'
    if 'U850' in h0.data_vars:
        u850 = h0.U850
        v850 = h0.V850
    elif os.path.isfile(uv850file):
        print('open UV850 file')
        uv850 = xr.open_dataset(uv850file, chunks={})
        u850 = uv850.U850
        v850 = uv850.V850
        uv850.close()
    else:
        print('calculate U850, V850...')
        u850 = vintp(h0.U.values, pres.values, np.array([85000.])).squeeze()
        v850 = vintp(h0.V.values, pres.values, np.array([85000.])).squeeze()
        uv850 = xr.Dataset(
                data_vars=dict(
                    U850=(['time','lat','lon'], u850),
                    V850=(['time','lat','lon'], v850)
                ),
                coords=dict(
                    time=h0.time,
                    lat=h0.lat,
                    lon=h0.lon
                )
            )
        uv850['U850'] = uv850.U850.assign_attrs(h0.U.attrs)
        uv850['V850'] = uv850.V850.assign_attrs(h0.V.attrs)
        uv850.to_netcdf(uv850file)
        u850 = uv850.U850
        v850 = uv850.V850
        uv850.close()
    return u850, v850

def process_slp(h0, pres, path, casename):
    slpfile = path+casename+'.slp.nc'
    if 'PSL' in h0.data_vars:
        slp = h0.PSL
    elif os.path.isfile(slpfile):
        print('open SLP file')
        slp = xr.open_dataset(slpfile, chunks={})
    else:
        print('calculate SLP...')
        temp_ds = h0.isel(lev=-1).squeeze()
        T_v = temp_ds.T.values * (1 + 0.608 * temp_ds.Q.values)
        Z1 = temp_ds.Z3.values
        g = 9.80616
        Cp = 1004.64
        Rd = 287.04
        P1 = pres.isel(lev=-1).squeeze()
        slp = np.power(g*Z1/Cp/T_v+1., Cp/Rd) * P1
        slp = xr.DataArray(
            slp,
            dims=['time', 'lat', 'lon'],
            coords=dict(
                time=h0.time,
                lat=h0.lat,
                lon=h0.lon
            ),
            attrs=dict(
                units='Pa',
                long_name='Sea-level Pressure'
            )
        )
        slp = slp.rename('PSL')
        slp.to_netcdf(slpfile)
    return slp
    