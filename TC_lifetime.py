import numpy as np
import xarray as xr
import numba
from time import ctime
from utils import load_config, StepTimer

SST_THRESHOLD_K = 299.15  # SST warm threshold (K)
LAT_CHUNK = 24
LON_CHUNK = 24

def get_sections(fil):
    """Yield track sections separated by lines starting with '*'."""
    star_line = None
    block = []
    with open(fil) as f:
        for line in f:
            if line.lstrip().startswith('*'):
                # Emit previous section (if it has content beyond the star)
                if star_line is not None and block:
                    yield [star_line] + block
                star_line = line
                block = []
            else:
                if star_line is not None:  # Only collect after first '*'
                    block.append(line)
        # Final section
        if star_line is not None and block:
            yield [star_line] + block

def lifetime(sec, sst):
    """Return (TCid, lifetime) if storm section has life>=36 and any point over warm SST; else (0,0)."""
    header = sec[1].split()
    life = int(header[2])
    if life < 36:
        return 0, 0
    TCid = int(header[0])
    if hasattr(sst, 'shape') and len(sst.shape) >= 3:
        nx = sst.shape[-1]
        ny = sst.shape[-2]
    else:
        raise ValueError("SST input must have at least 3 dims")
    for line in sec[2:]:
        parts = line.split()
        if len(parts) < 16:
            continue
        t = int(parts[1])
        x = int(np.round(float(parts[14]))) - 1
        y = int(np.round(float(parts[15]))) - 1
        x %= nx
        if t < 0 or t >= sst.shape[0] or y < 0 or y >= ny:
            raise ValueError("Track line index out of bounds")
        val = sst[t, y, x]
        if hasattr(val, 'compute'):
            val = val.compute()
        if val >= SST_THRESHOLD_K:
            return TCid, life
    return 0, 0

@numba.njit(parallel=True)
def write_tc(TCid, TCnew, mask):
    TCnew[mask==TCid] = TCid
    return TCnew

def main(case, sstfils, path):
    timer = StepTimer(enabled=True)
    nTC = 0
    life_all = 0
    print(ctime(), 'open SST files:', sstfils)
    sstnc = xr.open_mfdataset(sstfils); timer.mark('open_sst_mfdataset')
    if 'SST' in sstnc.data_vars:
        sst = sstnc.SST
        var_timer_tag = 'select_SST'
    elif 'TS' in sstnc.data_vars:
        sst = sstnc.TS
        var_timer_tag = 'select_TS'
    else:
        raise ValueError('No SST or TS variable found in the dataset.')
    timer.mark(var_timer_tag)
    # Rechunk then persist for faster repeated scalar access
    sst = sst.chunk({'time': 24, 'lat': LAT_CHUNK, 'lon': LON_CHUNK})
    timer.mark('rechunk_SST')
    sst = sst.persist(); timer.mark('persist_SST')
    nx = len(sst.lon)
    ny = len(sst.lat)
    maskfile = f'{path}/irt_tracks_mask.dat'
    mask = np.fromfile(maskfile, dtype=np.float32); timer.mark('read_mask_binary')
    TCnew = np.zeros(mask.shape, dtype=np.float32)
    print(ctime(), 'read tracking txt file...')
    txtfile = f'{path}/irt_tracks_output.txt'
    txt = list(get_sections(txtfile)); timer.mark('parse_tracking_sections')

    for sec in txt:
        TCid, life = lifetime(list(sec), sst.data)
        if TCid > 0:
            nTC += 1
            life_all += life
            TCnew = write_tc(TCid, TCnew, mask)
    timer.mark('iterate_sections')
    TCnew = TCnew.reshape((-1,ny,nx))
    tsize = TCnew.shape[0]
    TC = xr.DataArray(
        data=TCnew,
        dims=['time', 'lat', 'lon'],
        coords=dict(
            time=sstnc.time[:tsize],
            lat=sstnc.lat,
            lon=sstnc.lon
        ),
        name='TC',
        attrs=dict(
            units='#',
            long_name='TC ID'
        )
    ); timer.mark('assemble_TC_da')
    TC.to_netcdf(f'{path}/TC.nc'); timer.mark('write_TC_nc')
    with open(f'{path}/TC.txt', 'w') as f:
        f.write('Total TCs = '+str(nTC)+'\n')
        if nTC > 0:
            f.write('Life-Time average = '+str(life_all/nTC)+'\n')
        else:
            f.write('Life-Time average = 0\n')
    timer.mark('write_summary_txt')

    ctl_path = f'{path}/irt_tracks_mask.ctl'
    t_start = TC.time[0].dt.strftime('%H:%MZ%d%b%Y').values
    ctl = f"""DSET ^irt_tracks_mask.dat
OPTIONS 365_day_calendar
UNDEF -9.99e8
XDEF {nx} LINEAR {TC.lon[0].values} {(TC.lon[1]-TC.lon[0]).values}
YDEF {ny} LINEAR {TC.lat[0].values} {(TC.lat[1]-TC.lat[0]).values}
ZDEF 1 levels {getattr(sstnc, 'lev', xr.DataArray([0])).values[-1]}
TDEF {tsize} LINEAR {t_start} 1hr
VARS 1
obj 0 t,y,x TC-like object ID
ENDVARS
"""
    with open(ctl_path, "w") as f:
        f.write(ctl); timer.mark('write_ctl')
    return

if __name__ == "__main__":
    config = load_config()
    cases = config['cases']
    case_path = config['case_path']
    output_path = config['output_path']
    file_pattern = config['file_pattern']
    for case in cases:
        print(ctime(), case)
        main(case, f'{case_path}/{case}/atm/hist/{case}.{file_pattern}', f'{output_path}/{case}')
