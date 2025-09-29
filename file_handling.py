import os
import numpy as np
import xarray as xr
from utils import vintp, StepTimer, vertical_slice_bounds
from windspharm.standard import VectorWind
from concurrent.futures import ProcessPoolExecutor

def _calc_vort_time(args):
    t, U_slice, V_slice, reverse_lat = args
    U_slice = U_slice.transpose(1, 2, 0)  # shape (lat, lon, lev)
    V_slice = V_slice.transpose(1, 2, 0)  # shape (lat, lon, lev)
    if reverse_lat:
        U_slice = U_slice[::-1, :, :]
        V_slice = V_slice[::-1, :, :]
    w = VectorWind(U_slice, V_slice)
    vort = w.vorticity()
    vort = vort.transpose(2, 0, 1)  # shape (lev, lat, lon)
    if reverse_lat:
        vort = vort[:, ::-1, :]
    return t, vort

def process_vorticity(h0, pres, path, casename):
    vortfile = f'{path}/{casename}.vort850.nc'
    fine = os.environ.get('PROFILE_FINE', '0') == '1'
    timer = StepTimer(enabled=fine)
    if os.path.isfile(vortfile):
        print('open vorticity file')
        vortnc = xr.open_dataset(vortfile, chunks={}); timer.mark('open_vort_file')
        vort = vortnc.vorticity
        vortnc.close(); timer.mark('close_vort_file')
    else:
        print('calculate vorticity...')
        k1, k2 = vertical_slice_bounds(pres, 85000., lev_len=len(h0.lev), axis=1)
        U_slice = h0.U.isel(lev=slice(k1, k2)).load(); timer.mark('load_U_slice')
        V_slice = h0.V.isel(lev=slice(k1, k2)).load(); timer.mark('load_V_slice')
        reverse_lat = h0.lat[-1].values > h0.lat[0].values
        shape = h0.U[:, k1:k2, :, :].shape
        vort = np.zeros(shape)
        args_list = []
        for t in range(shape[0]):
            utemp = U_slice.isel(time=t).values
            vtemp = V_slice.isel(time=t).values
            args_list.append((t, utemp, vtemp, reverse_lat))
        num_workers = np.minimum(shape[0], int(os.cpu_count() / 4))
        print(f'Using {num_workers} workers for vorticity calculation')
        timer.mark('prep_args')
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            results = list(executor.map(_calc_vort_time, args_list))
        timer.mark('pool_compute')
        for t, vorttemp in results:
            vort[t, :, :, :] = vorttemp
        vort850 = vintp(vort, pres[:, k1:k2, :, :].values, [85000.]); timer.mark('interp_850')
        vort850 = np.squeeze(vort850, axis=1) if vort850.shape[1] == 1 else vort850
        vort = xr.DataArray(
            data=vort850,
            dims=['time', 'lat', 'lon'],
            coords=dict(
                time=h0.time,
                lat=h0.lat,
                lon=h0.lon
            ),
            name='vorticity',
            attrs=dict(
                units='s-1',
                long_name='Vorticity at 850 hPa'
            )
        )
        vort.to_netcdf(vortfile); timer.mark('write_vort_file')
    return vort

def process_uv300(h0, pres, path, casename):
    uv300file = f'{path}/{casename}.UV300.nc'
    fine = os.environ.get('PROFILE_FINE', '0') == '1'
    timer = StepTimer(enabled=fine)
    if os.path.isfile(uv300file):
        print('open UV300 file')
        uv300 = xr.open_dataset(uv300file, chunks={}); timer.mark('open_uv300_file')
    else:
        print('calculate U300, V300...')
        # Determine minimal vertical slice covering 300 hPa (add one level padding on each side)
        k1, k2 = vertical_slice_bounds(pres, 30000., lev_len=len(h0.lev), axis=1); timer.mark('k_index_300')
        # Slice only needed vertical band to reduce memory / compute
        U_slice = h0.U.isel(lev=slice(k1, k2)).load(); timer.mark('load_U300_slice')
        V_slice = h0.V.isel(lev=slice(k1, k2)).load(); timer.mark('load_V300_slice')
        pres_slice = pres[:, k1:k2, :, :].values
        u300 = vintp(U_slice.values, pres_slice, [30000.]); timer.mark('interp_u300')
        v300 = vintp(V_slice.values, pres_slice, [30000.]); timer.mark('interp_v300')
        u300 = np.squeeze(u300, axis=1) if u300.shape[1] == 1 else u300
        v300 = np.squeeze(v300, axis=1) if v300.shape[1] == 1 else v300
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
        uv300.to_netcdf(uv300file); timer.mark('write_uv300_file')
    u300 = uv300.U300
    v300 = uv300.V300
    uv300.close(); timer.mark('close_uv300_file')
    return u300, v300

def process_uv850(h0, pres, path, casename):
    uv850file = f'{path}/{casename}.UV850.nc'
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
        # Determine minimal vertical slice covering 850 hPa (add one level padding on each side)
        k1, k2 = vertical_slice_bounds(pres, 85000., lev_len=len(h0.lev), axis=1)
        U_slice = h0.U.isel(lev=slice(k1, k2)).load()
        V_slice = h0.V.isel(lev=slice(k1, k2)).load()
        pres_slice = pres[:, k1:k2, :, :].values
        u850 = vintp(U_slice.values, pres_slice, np.array([85000.]))
        v850 = vintp(V_slice.values, pres_slice, np.array([85000.]))
        u850 = np.squeeze(u850, axis=1) if u850.shape[1] == 1 else u850
        v850 = np.squeeze(v850, axis=1) if v850.shape[1] == 1 else v850
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
    slpfile = f'{path}/{casename}.slp.nc'
    if 'PSL' in h0.data_vars:
        slp = h0.PSL
    elif os.path.isfile(slpfile):
        print('open SLP file')
        slp = xr.open_dataset(slpfile, chunks={}).PSL
    else:
        print('calculate SLP...')
        fine = os.environ.get('PROFILE_FINE', '0') == '1'
        timer = StepTimer(enabled=fine)
        # Lowest model level variables (keep lazy dask arrays, no .values)
        base = h0.isel(lev=-1)
        T = base.T; Q = base.Q; Z = base.Z3; timer.mark('select_lowest_level')
        # Virtual temperature (lazy)
        T_v = T * (1 + 0.608 * Q); timer.mark('compute_Tv')
        # Constants (kept same for reproducibility with original formula)
        g = 9.80616
        Cp = 1004.64
        Rd = 287.04
        P1 = pres.isel(lev=-1)  # pressure at lowest model level (lazy)
        # Original formula preserved; stays in graph until compute
        slp = ((g * Z) / (Cp * T_v) + 1.0) ** (Cp / Rd) * P1; timer.mark('form_slp_expr')
        slp = slp.rename('PSL').assign_attrs(units='Pa', long_name='Sea-level Pressure (diagnostic)')
        # Use to_netcdf to trigger a single fused compute
        slp.to_netcdf(slpfile); timer.mark('write_slp_file')
    return slp
