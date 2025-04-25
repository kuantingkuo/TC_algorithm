import xarray as xr
import numpy as np
from utils import (load_config, latpad, uvlatpad)
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
    lat_inc = h0.lat[1].values - lat_first
    lon_inc = h0.lon[1].values - h0.lon[0].values

    return {
        "domainsize_x": nx,
        "domainsize_y": ny,
        "time_steps": nt,
        "lat_first": lat_first,
        "lat_inc": lat_inc,
        "lon_inc": lon_inc
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
        else:
            updated_lines.append(line)

    # Write the updated lines back to the Fortran file
    with open(fortran_file, "w") as file:
        file.writelines(updated_lines)

def pre(ds):
    return ds.sel(lev=slice(200,None))

def main(casename, inpath, outpath, file_pattern):
    path = f'{inpath}/{casename}/atm/hist/'
    outfile = f'{outpath}/{casename}.TC.nc'
    print(f'output filename: {outfile}')
    #if os.path.isfile(outfile):
    #    continue
    print(f'open files: {path}/{casename}.{file_pattern}')
    h0 = xr.open_mfdataset(f'{path}/{casename}.{file_pattern}', preprocess=pre, decode_cf=False)
    print('Update IRT parameters...')
    params = irt_params(h0)
    update_irt_parameters('irt_parameters.f90', params)
    pres = h0.hyam * h0.P0 + h0.hybm * h0.PS

    u300, v300 = process_uv300(h0, pres, outpath, casename)
    u850, v850 = process_uv850(h0, pres, outpath, casename)
    vort = process_vorticity(h0, pres, outpath, casename)
    slp = process_slp(h0, pres, outpath, casename)

    print("load data...")
    if True:  # Toggle to adjust vorticity sign for the Southern Hemisphere
        print('Adjusting vorticity sign for the Southern Hemisphere.')
        vort.load()
        vort = xr.where(vort.lat < 0, -vort, vort).transpose('time', ...)
        ps = h0.PS.load()

    print("Detecting TC-like objects...")
    ds = TC_detect(latpad(vort),
                   uvlatpad(u850), uvlatpad(u300), uvlatpad(v850), uvlatpad(v300),
                   latpad(slp), latpad(ps))
    print(ds)
    return ds

if __name__ == "__main__":
    config = load_config()
    cases = config['cases']
    case_path = config['case_path']
    output_path = config['output_path']
    file_pattern = config['file_pattern']

    for case in cases:
        os.makedirs(f"{output_path}/{case}", exist_ok=True)
        ds = main(case, case_path, f'{output_path}/{case}', file_pattern)
        print("Output...")
        ds.to_netcdf(f"{output_path}/{case}/{case}.TC.nc")
        ds.close()
