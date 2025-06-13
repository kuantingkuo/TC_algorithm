import numpy as np
import xarray as xr
import numba
from itertools import groupby, chain
from time import ctime
from utils import load_config

def get_sections(fil):
    with open(fil) as f:
        grps = groupby(f, key=lambda x: x.lstrip().startswith('*'))
        for k, v in grps:
            if k:
                yield chain([next(v)], (next(grps)[1]))

def lifetime(sec, sst):
    line = []
    line.extend(i for i in sec[1].split())
    life = int(line[2])
    if life < 36:
        return 0,0
    TCid = int(line[0])
    num = len(sec)
    for n in range(2, num):
        line = []
        line.extend(i for i in sec[n].split())
        t = int(line[1])
        x = np.round(float(line[14])).astype(int) - 1
        if x >= 288:
            x -= 288
        y = np.round(float(line[15])).astype(int) - 1
        if (sst[t,y,x] >= 299.15).compute():
            return TCid, life
    return 0,0

@numba.njit(parallel=True)
def write_tc(TCid, TCnew, mask):
    TCnew[mask==TCid] = TCid
    return TCnew

def main(case, sstfils, path):
    nTC = 0
    life_all = 0
    print(ctime(), 'open SST files:', sstfils)
    sstnc = xr.open_mfdataset(sstfils)
    if 'SST' in sstnc.data_vars:
        sst = sstnc.SST.persist()
    elif 'TS' in sstnc.data_vars:
        sst = sstnc.TS.persist()
    else:
        raise ValueError('No SST or TS variable found in the dataset.')
    nx = len(sst.lon)
    ny = len(sst.lat)
    maskfile = f'{path}/irt_tracks_mask.dat'
    mask = np.fromfile(maskfile, dtype=np.float32)
    TCnew = np.zeros(mask.shape, dtype=np.float32)
    print(ctime(), 'read tracking txt file...')
    txtfile = f'{path}/irt_tracks_output.txt'
    txt = get_sections(txtfile)
    for sec in txt:
        TCid, life = lifetime(list(sec), sst.data)
        if TCid > 0:
            print(ctime(), 'TC ID:',TCid)
            nTC += 1
            life_all += life
            TCnew = write_tc(TCid, TCnew, mask)
    print(ctime(), 'output TCnew data...')
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
    )
    TC.to_netcdf(f'{path}/TC.nc')
    with open(f'{path}/TC.txt', 'w') as f:
        f.write('Total TCs = '+str(nTC)+'\n')
        f.write('Life-Time average = '+str(life_all/nTC)+'\n')

    ctl_path = f'{path}/irt_tracks_mask.ctl'
    print(TC)
    t_start = TC.time[0].dt.strftime('%H:%MZ%d%b%Y').values
    ctl = f"""DSET ^irt_tracks_mask.dat
OPTIONS 365_day_calendar
UNDEF -9.99e8
XDEF {nx} LINEAR {TC.lon[0].values} {(TC.lon[1]-TC.lon[0]).values}
YDEF {ny} LINEAR {TC.lat[0].values} {(TC.lat[1]-TC.lat[0]).values}
ZDEF 1 levels {sstnc.lev[-1].values}
TDEF {tsize} LINEAR {t_start} 1hr
VARS 1
obj 0 t,y,x TC-like object ID
ENDVARS
"""
    with open(ctl_path, "w") as f:
        f.write(ctl)
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
