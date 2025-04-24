import numpy as np
import xarray as xr
import numba
import csv
from itertools import groupby, chain
from time import ctime

def get_sections(fil):
    with open(fil) as f:
        grps = groupby(f, key=lambda x: x.lstrip().startswith('*'))
        for k, v in grps:
            if k:
                yield chain([next(v)], (next(grps)[1]))

def extract(row):
    num = int(row[1])
    init = float(row[3])
    intv = float(row[4])
    return num, init, intv

def pre_SST(ds):
    return ds.SST[:,45:147,:]

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

def main(case):
    path = '/data/W.eddie/tracking/OUTPUTtracking/'+case+'/'
    sstfils = '/data/W.eddie/SPCAM/'+case+'/atm/hist/'+case+'.cam.h0.*.nc'
    nTC = 0
    life_all = 0
    ctl = path+'irt_tracks_mask.ctl'
    with open(ctl, 'r') as f:
        reader = csv.reader(f, delimiter=' ')
        for row in reader:
            head = row[0]
            match head:
                case 'XDEF':
                    nx, x0, xint = extract(row)
                case 'YDEF':
                    ny, y0, yint = extract(row)
    maskfile = path+'irt_tracks_mask.dat'
    mask = np.fromfile(maskfile, dtype=np.float32)
    TCnew = np.zeros(mask.shape, dtype=np.float32)
    print(ctime(), 'open SST files...')
#    sst = xr.open_mfdataset(sstfils, preprocess=pre_SST, decode_cf=False).SST
#    sst = xr.open_mfdataset(case+'.SST.nc', decode_cf=False, chunks={}).SST
    sstnc = xr.open_mfdataset(sstfils, decode_cf=False, chunks={})
    sst = sstnc.TS.where(sstnc.OCNFRAC==1.)
    print(ctime(), 'read tracking txt file...')
    txtfile = path+'irt_tracks_output.txt'
    txt = get_sections(txtfile)
    for sec in txt:
        TCid, life = lifetime(list(sec), sst.data)
        if TCid > 0:
            print(ctime(), TCid)
            nTC += 1
            life_all += life
            TCnew = write_tc(TCid, TCnew, mask)
    print(ctime(), 'output TCnew data...')
    TCnew = TCnew.reshape((-1,ny,nx))
    TCnew.tofile('/data/W.eddie/SPCAM/'+case+'/atm/hist/TCnew_py.dat')
    with open(case+'.TC.txt', 'w') as f:
        f.write('Total TCs = '+str(nTC)+'\n')
        f.write('Life-Time average = '+str(life_all/nTC))
    return

if __name__ == "__main__":
    cases = ['FSPS20170907', 'FSPS20170907_norelax', 'Talim_CESM1']
    for case in cases:
        print(ctime(), case)
        main(case)
