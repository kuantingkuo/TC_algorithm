import xarray as xr
import numpy as np
from utils import (load_config, StepTimer, vertical_slice_bounds, vintp, latpad, uvlatpad)
from tc_detection import TC_detect
import os
import math
from concurrent.futures import ProcessPoolExecutor
from windspharm.standard import VectorWind

def irt_params(h0):
    """
    Extract parameters from the dataset and return them as a dictionary.
    """
    nx = len(h0.lon)
    ny = len(h0.lat)
    nt = len(h0.time)
    lat_first = h0.lat[0].values
    lat_last = h0.lat[-1].values
    lat_inc = np.abs(h0.lat[1].values - lat_first)
    lon_inc = np.abs(h0.lon[1].values - h0.lon[0].values)
    lpole = ((lat_first-lat_inc < -90.) or (lat_first+lat_inc > 90.)) and ((lat_last-lat_inc < -90.) or (lat_last+lat_inc > 90.))

    return {
        "domainsize_x": nx,
        "domainsize_y": ny,
        "time_steps": nt,
        "lat_first": lat_first,
        "lat_inc": lat_inc,
        "lon_inc": lon_inc,
        "lpole": lpole
    }

def update_irt_parameters(fortran_file, params):
    """
    Update the Fortran file with the new parameter values.

    Parameters:
        fortran_file (str): Path to the Fortran file.
        params (dict): Dictionary containing parameter values.
    """
    with open(fortran_file, "r") as file:
        lines = file.readlines()

    # Replace parameter values in the Fortran file
    updated_lines = []
    for line in lines:
        if "INTEGER, PARAMETER    :: domainsize_x" in line:
            updated_lines.append(f"INTEGER, PARAMETER    :: domainsize_x = {params['domainsize_x']}\n")
        elif "INTEGER, PARAMETER    :: domainsize_y" in line:
            updated_lines.append(f"INTEGER, PARAMETER    :: domainsize_y = {params['domainsize_y']}\n")
        elif "INTEGER, PARAMETER    :: time_steps" in line:
            updated_lines.append(f"INTEGER, PARAMETER    :: time_steps = {params['time_steps']}\n")
        elif "REAL, PARAMETER       :: lat_first" in line:
            updated_lines.append(f"REAL, PARAMETER       :: lat_first = {params['lat_first']:.7f}\n")
        elif "REAL, PARAMETER       :: lat_inc" in line:
            updated_lines.append(f"REAL, PARAMETER       :: lat_inc = {params['lat_inc']:.9f}\n")
        elif "REAL, PARAMETER       :: lon_inc" in line:
            updated_lines.append(f"REAL, PARAMETER       :: lon_inc = {params['lon_inc']:.7f}\n")
        elif "LOGICAL, PARAMETER    :: lpole" in line:
            updated_lines.append(f"LOGICAL, PARAMETER    :: lpole = {'.TRUE.' if params['lpole'] else '.FALSE.'}\n")
        else:
            updated_lines.append(line)

    # Write the updated lines back to the Fortran file
    with open(fortran_file, "w") as file:
        file.writelines(updated_lines)

def update_compile_command(config, compile_script_path="tracking/tracking2/compile.sh"):
    """Rewrite COMPILE_COMMAND line in compile.sh based on runtime.compiler and runtime.compiler_flags.

    Falls back silently if script missing or keys absent.
    """
    runtime = config.get('runtime') or {}
    compiler = runtime.get('compiler')
    flags = runtime.get('compiler_flags')
    if not compiler or not flags:
        return
    if not os.path.isfile(compile_script_path):
        return
    try:
        with open(compile_script_path, 'r') as f:
            lines = f.readlines()
        new_lines = []
        replaced = False
        for line in lines:
            if line.startswith('COMPILE_COMMAND=') or line.startswith('COMPILE_COMMAND="'):
                new_lines.append(f'COMPILE_COMMAND="{compiler} {" ".join(str(flags).split())}"\n')
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            # Prepend if not found
            new_lines.insert(0, f'COMPILE_COMMAND="{compiler} {" ".join(str(flags).split())}"\n')
        with open(compile_script_path, 'w') as f:
            f.writelines(new_lines)
    except Exception:
        pass

def pre(ds):
    return ds.sel(lev=slice(200,None))

## Sequential rechunking removed (always parallel path)

# -------------------------------
# Parallel per-time feature + detection helpers
# -------------------------------
def _compute_features_time_batch_from_dataset(ds_batch, invert_vorticity_SH):
    """Process a pre-sliced dataset (contiguous batch of time steps).

    ds_batch : xr.Dataset with time subset.
    Returns xr.Dataset concatenated over its time dimension (preserved).
    """
    # Debug print: summarize batch time coverage
    try:
        n_times = ds_batch.sizes.get('time', 'NA')
        times = ds_batch.time.values
        if len(times) > 0:
            print(f"[parallel-batch] processing {n_times} time steps: {times[0]} -> {times[-1]}")
        else:
            print("[parallel-batch] received empty time batch")
    except Exception as e:
        print(f"[parallel-batch] debug print failed: {e}")
    out_list = []
    # Determine latitude orientation once (True if need to reverse to descending before windspharm)
    reverse_lat = False
    try:
        if ds_batch.sizes.get('lat', 0) > 1:
            reverse_lat = bool(ds_batch.lat[-1].values > ds_batch.lat[0].values)
    except Exception:
        reverse_lat = False
    # Detect precomputed 850 hPa winds (expected dims: time, lat, lon)
    has_pre850 = ('U850' in ds_batch.data_vars) and ('V850' in ds_batch.data_vars)
    if has_pre850:
        pre_U850 = ds_batch['U850'].transpose('time','lat','lon')
        pre_V850 = ds_batch['V850'].transpose('time','lat','lon')
    # Detect precomputed sea-level pressure PSL
    has_psl = 'PSL' in ds_batch.data_vars
    # Iterate each time step inside the batch to reuse existing per-time logic
    for local_t in range(ds_batch.sizes['time']):
        dsi = ds_batch.isel(time=local_t).expand_dims('time')
        dlat = float(abs(dsi.lat[1] - dsi.lat[0]))
        dlon = float(abs(dsi.lon[1] - dsi.lon[0]))
        pres_t = (dsi.hyam * dsi.P0 + dsi.hybm * dsi.PS).transpose('time', 'lev', 'lat', 'lon')
        pres_np = pres_t.values
        # Derive vertical slice bounds for 300 hPa (needed in both branches)
        k1_300, k2_300 = vertical_slice_bounds(pres_np, 30000., lev_len=len(dsi.lev), axis=1)
        U_300_slice = dsi.U.isel(lev=slice(k1_300, k2_300))
        V_300_slice = dsi.V.isel(lev=slice(k1_300, k2_300))
        U_300_np = U_300_slice.transpose('time','lev','lat','lon').values
        V_300_np = V_300_slice.transpose('time','lev','lat','lon').values
        u300 = vintp(U_300_np, pres_np[:, k1_300:k2_300, :, :], [30000.]); u300 = np.squeeze(u300, axis=1)
        v300 = vintp(V_300_np, pres_np[:, k1_300:k2_300, :, :], [30000.]); v300 = np.squeeze(v300, axis=1)
        if has_pre850:
            # Use provided 850 hPa winds directly (no vertical interpolation)
            u850_level = pre_U850.isel(time=local_t).values  # (lat, lon)
            v850_level = pre_V850.isel(time=local_t).values
            u850 = u850_level[None, ...]
            v850 = v850_level[None, ...]
            if reverse_lat:
                u850_level = u850_level[::-1, :]
                v850_level = v850_level[::-1, :]
            w = VectorWind(u850_level, v850_level)
            vort850 = w.vorticity().astype(np.float32)[None, ...]
            if reverse_lat:
                vort850 = vort850[:, ::-1, :]
        else:
            # Derive vertical slice bounds for 850 hPa only (300 already done)
            k1_850, k2_850 = vertical_slice_bounds(pres_np, 85000., lev_len=len(dsi.lev), axis=1)
            U_850_slice = dsi.U.isel(lev=slice(k1_850, k2_850))
            V_850_slice = dsi.V.isel(lev=slice(k1_850, k2_850))
            # Retain original dtypes; only vort and slp will be explicitly cast to float32
            U_850_np = U_850_slice.transpose('time','lev','lat','lon').values
            V_850_np = V_850_slice.transpose('time','lev','lat','lon').values
            # Prepare (lat, lon, lev) ordering for winds to feed windspharm (level axis last originally)
            U_for_vort = U_850_np[0].transpose(1,2,0)
            V_for_vort = V_850_np[0].transpose(1,2,0)
            if reverse_lat:
                U_for_vort = U_for_vort[::-1, :, :]
                V_for_vort = V_for_vort[::-1, :, :]
            w = VectorWind(U_for_vort, V_for_vort)
            vort = w.vorticity().astype(np.float32)  # (lat, lon, lev)
            if reverse_lat:
                vort = vort[::-1, :, :]
            vort = vort.transpose(2,0,1)[np.newaxis, ...]  # (time=1, lev, lat, lon)
            vort850 = vintp(vort, pres_np[:, k1_850:k2_850, :, :], [85000.]); vort850 = np.squeeze(vort850, axis=1)
            u850 = vintp(U_850_np, pres_np[:, k1_850:k2_850, :, :], [85000.]); u850 = np.squeeze(u850, axis=1)
            v850 = vintp(V_850_np, pres_np[:, k1_850:k2_850, :, :], [85000.]); v850 = np.squeeze(v850, axis=1)
        if has_psl:
            slp = dsi.PSL
        else:
            base = dsi.isel(lev=-1)
            T = base.T; Q = base.Q; Z = base.Z3
            T_v = T * (1 + 0.608 * Q)
            g = 9.80616; Cp = 1004.64; Rd = 287.04
            P1 = pres_t.isel(lev=-1)
            slp = ((g * Z) / (Cp * T_v) + 1.0) ** (Cp / Rd) * P1
            slp = slp.astype(np.float32).rename('PSL')  # keep float32 cast for SLP
        ps = dsi.PS
        coords = dict(time=dsi.time, lat=dsi.lat, lon=dsi.lon)
        vort_da = xr.DataArray(vort850, coords=coords, dims=('time','lat','lon'), name='vorticity')
        if invert_vorticity_SH:
            vort_da = xr.where(vort_da.lat < 0, -vort_da, vort_da)
        u850_da = xr.DataArray(u850, coords=coords, dims=('time','lat','lon'))
        v850_da = xr.DataArray(v850, coords=coords, dims=('time','lat','lon'))
        u300_da = xr.DataArray(u300, coords=coords, dims=('time','lat','lon'))
        v300_da = xr.DataArray(v300, coords=coords, dims=('time','lat','lon'))
        slp_da = slp.transpose('time','lat','lon')
        ps_da = ps.transpose('time','lat','lon')
        ds_out = TC_detect(
            latpad(vort_da, dlat),
            uvlatpad(u850_da, dlat), uvlatpad(u300_da, dlat), uvlatpad(v850_da, dlat), uvlatpad(v300_da, dlat),
            latpad(slp_da, dlat), latpad(ps_da, dlat), dlat, dlon
        )
        out_list.append(ds_out)
    return xr.concat(out_list, dim='time')

# Top-level worker wrapper for multiprocessing: reopen dataset inside worker to avoid
# sharing netCDF4 handles across processes (prevents 'Resource temporarily unavailable').
def _worker_time_batch(args):
    time_indices, casename, path, file_pattern, invert_flag, var_needed = args
    # Reopen dataset fresh in each process
    ds = xr.open_mfdataset(f'{path}/{casename}.{file_pattern}', preprocess=pre, decode_cf=False)
    try:
        # Subset to required time indices and variables
        sub = ds.isel(time=time_indices)
        available = [v for v in var_needed if v in sub.data_vars]
        sub = sub[available]
        result = _compute_features_time_batch_from_dataset(sub, invert_flag)
    finally:
        # Ensure file handles closed in worker
        ds.close()
    return result

def run_parallel_detection(meta_ds, casename, path, file_pattern, invert_vorticity_SH, cpus):
    """Batched detection with per-process dataset reopen to avoid netCDF handle contention.

    Each worker re-opens the dataset and processes a list of time indices. This prevents sharing
    file handles across processes (reducing RuntimeError: Resource temporarily unavailable) at the
    cost of additional metadata reads. Suitable when CPU parallelism outweighs open overhead.
    """
    time_len = meta_ds.sizes['time']
    if cpus is None or cpus < 1:
        cpus = os.cpu_count() or 1
    workers = min(cpus, time_len)
    print(f"Parallel detection (reopen per worker): {time_len} time steps across {workers} workers")
    indices = list(range(time_len))
    batches = [b for b in np.array_split(indices, workers) if len(b) > 0]
    # Variables required for feature computation
    var_needed = ['U','V','hyam','hybm','P0','PS','Z3','T','Q','U850','V850','PSL']
    # Close the shared dataset before forking to minimize inherited open handles
    try:
        meta_ds.close()
    except Exception:
        pass
    work_items = [(batch.tolist(), casename, path, file_pattern, invert_vorticity_SH, var_needed) for batch in batches]
    ds_list = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for ds_part in ex.map(_worker_time_batch, work_items):
            ds_list.append(ds_part)
    combined = xr.concat(ds_list, dim='time').sortby('time')
    return combined

def main(casename, inpath, outpath, file_pattern, invert_vorticity_SH, config=None):
    timer = StepTimer()
    path = f'{inpath}/{casename}/atm/hist/'
    outfile = f'{outpath}/{casename}.TC.nc'
    print(f'output filename: {outfile}')
    print(f'open files: {path}/{casename}.{file_pattern}')
    runtime_cfg = (config or {}).get('runtime') or {}
    cpu_cap = None
    try:
        cpu_cap = int(runtime_cfg.get('cpus')) if runtime_cfg.get('cpus') is not None else None
    except ValueError:
        cpu_cap = None
    # Always compute IRT parameters & compile settings once (not part of parallel work)
    meta = xr.open_mfdataset(f'{path}/{casename}.{file_pattern}', preprocess=pre, decode_cf=False)
    params = irt_params(meta); timer.mark('extract_params')
    update_irt_parameters('tracking/tracking2/irt_parameters.f90', params)
    update_compile_command(config or {})
    timer.mark('update_fortran_params')
    dlat = float(params['lat_inc']); dlon = float(params['lon_inc'])
    if dlat >= 3. or dlon >= 3.:
        raise ValueError(f"Resolution too coarse: lat_inc={dlat}°, lon_inc={dlon}° (threshold=3°).")

    print('Running time-parallel detection path (only mode)...')
    ds = run_parallel_detection(meta, casename, path, file_pattern, invert_vorticity_SH, cpu_cap)
    meta.close()
    timer.mark('TC_detect_parallel')
    print(ds)
    return ds

if __name__ == "__main__":
    config = load_config() # Default: config.yaml
    case = config.get('case')
    if case is None:
        raise ValueError("Missing required 'case' key in config.yaml")
    case_path = config['case_path']
    output_path = config['output_path']
    file_pattern = config['file_pattern']
    invert_vorticity_SH = config.get('invert_vorticity_SH', True)  # Default to True

    os.makedirs(f"{output_path}/{case}", exist_ok=True)
    ds = main(case, case_path, f'{output_path}/{case}', file_pattern, invert_vorticity_SH, config=config)
    print("Output...")
    ds.to_netcdf(f"{output_path}/{case}/{case}.TC.nc")
    ds.close()
