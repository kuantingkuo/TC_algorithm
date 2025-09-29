import xarray as xr
import numpy as np
import dask
from utils import (load_config, latpad, uvlatpad, StepTimer)
from tc_detection import TC_detect
from file_handling import process_vorticity, process_uv300, process_uv850, process_slp
import os

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

def pre(ds):
    return ds.sel(lev=slice(200,None))

def rechunk_h0(h0):
    # Get dimension sizes
    time_size = len(h0['time'])
    lev_size = len(h0['lev'])
    lat_size = len(h0['lat'])
    lon_size = len(h0['lon'])

    # Calculate the max chunk size for time*lev so that time*lev*lat*lon*4 ≈ 1e8
    # (assuming float32, 4 bytes per element)
    max_elements = int(1e8 // 4)
    latlon_size = lat_size * lon_size
    max_timelev = max_elements // latlon_size

    # Split time first
    if lev_size <= max_timelev:
        lev_chunk = lev_size
        # Prevent division producing zero; ensure denominator >=1
        denom = max(1, (max_timelev // max(1, lev_chunk)))
        # available_time approximates how many time steps fit; fallback to 1
        available_time = max(1, time_size // denom)
        time_chunk = min(time_size, available_time)
    else:
        lev_chunk = max_timelev
        time_chunk = 1

    print(f"Rechunking: time={time_chunk}, lev={lev_chunk}, lat={lat_size}, lon={lon_size}")
    return h0.chunk({'time': time_chunk, 'lev': lev_chunk, 'lat': lat_size, 'lon': lon_size})

def main(casename, inpath, outpath, file_pattern, invert_vorticity_SH):
    timer = StepTimer()
    path = f'{inpath}/{casename}/atm/hist/'
    outfile = f'{outpath}/{casename}.TC.nc'
    print(f'output filename: {outfile}')
    print(f'open files: {path}/{casename}.{file_pattern}')
    h0 = xr.open_mfdataset(f'{path}/{casename}.{file_pattern}', preprocess=pre, decode_cf=False)
    timer.mark('open_mfdataset')
    h0 = rechunk_h0(h0)
    timer.mark('rechunk')
    print('Update IRT parameters...')
    params = irt_params(h0)
    timer.mark('extract_params')
    update_irt_parameters('tracking/tracking2/irt_parameters.f90', params)
    timer.mark('update_fortran_params')

    # Resolution validation (latitude increment) with configurable threshold via env or fallback
    dlat = float(params['lat_inc'])
    dlon = float(params['lon_inc'])
    if dlat >= 3. or dlon >= 3.:
        raise ValueError(
            f"Resolution too coarse: lat_inc={dlat}°, lon_inc={dlon}° (threshold=3°)."
        )
    pres = (h0.hyam * h0.P0 + h0.hybm * h0.PS).transpose('time', 'lev', 'lat', 'lon')
    print('Preparing data...')
    pres, h0['U'], h0['V'] = dask.persist(pres, h0.U, h0.V)
    timer.mark('persist_pres_U_V')
    u300, v300 = process_uv300(h0, pres, outpath, casename)
    timer.mark('uv300')
    u850, v850 = process_uv850(h0, pres, outpath, casename)
    timer.mark('uv850')
    vort = process_vorticity(h0, pres, outpath, casename)
    timer.mark('vorticity')
    slp = process_slp(h0, pres, outpath, casename)
    timer.mark('slp')

    if invert_vorticity_SH:
        print('Inverting vorticity sign for the Southern Hemisphere.')
        vort = xr.where(vort.lat < 0, -vort, vort).transpose('time', ...)

    ps = h0.PS.compute()
    timer.mark('ps_compute')

    print("Detecting TC-like objects...")
    ds = TC_detect(latpad(vort, dlat),
                   uvlatpad(u850, dlat), uvlatpad(u300, dlat), uvlatpad(v850, dlat), uvlatpad(v300, dlat),
                   latpad(slp, dlat), latpad(ps, dlat), dlat, dlon)
    timer.mark('TC_detect')
    print(ds)
    return ds

if __name__ == "__main__":
    config = load_config() # Default: config.yaml
    cases = config['cases']
    case_path = config['case_path']
    output_path = config['output_path']
    file_pattern = config['file_pattern']
    invert_vorticity_SH = config.get('invert_vorticity_SH', True)  # Default to True

    for case in cases:
        os.makedirs(f"{output_path}/{case}", exist_ok=True)
        ds = main(case, case_path, f'{output_path}/{case}', file_pattern, invert_vorticity_SH)
        print("Output...")
        ds.to_netcdf(f"{output_path}/{case}/{case}.TC.nc")
        ds.close()
