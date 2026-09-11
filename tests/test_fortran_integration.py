"""Optional real-compiler tests; set TC_FORTRAN_TEST_CONFIG to a local site config.

Run with: conda run -n forge python -m pytest -q tests/test_fortran_integration.py
All datasets created here are synthetic fixtures, not altered experimental data.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest
import xarray as xr
from tcflow.config import resolve, ROOT
from tcflow.build import prepare as build
from tcflow.tracking import run as track
from tcflow.workflow import prepare, execute
from tc_algorithm import irt_params
from TC_lifetime import main as lifetime

CONFIG = os.environ.get('TC_FORTRAN_TEST_CONFIG')
pytestmark = pytest.mark.skipif(not CONFIG, reason='TC_FORTRAN_TEST_CONFIG required for real compiler tests')


def make_data(nt):
    coords = dict(time=np.datetime64('2001-01-01') + np.arange(nt).astype('timedelta64[h]'),
                  lev=[300., 850.], lat=np.linspace(-90,90,91), lon=np.arange(0,360,2.))
    ds = xr.Dataset(coords=coords)
    for name, value, units in [('U',1.,'m/s'),('V',2.,'m/s'),('T',280.,'K'),('Q',.01,'kg/kg'),('Z3',100.,'m')]:
        ds[name] = xr.DataArray(np.full((nt,2,91,180),value,dtype='float32'),
                                dims=('time','lev','lat','lon'), attrs={'units':units})
    for name, value, units in [('PS',100000.,'Pa'),('PSL',100000.,'Pa'),('TS',300.,'K')]:
        ds[name] = xr.DataArray(np.full((nt,91,180),value,dtype='float32'),
                                dims=('time','lat','lon'),attrs={'units':units})
    ds['P0'] = xr.DataArray(100000.,attrs={'units':'Pa'})
    ds['hyam'] = xr.DataArray([.3,.85],dims='lev')
    ds['hybm'] = xr.DataArray([0.,0.],dims='lev')
    return ds


def configuration(tmp_path):
    cfg = resolve([ROOT / 'config.yaml', ROOT / 'configs/sites/local.yaml', CONFIG])
    cfg.pop('time_range', None)
    cfg.update(output_path=str(tmp_path / 'output'))
    cfg['runtime'].update(cpus=1, irt_build_cache_root=str(tmp_path / 'build-cache'))
    cfg['scheduler']['backend'] = 'local'
    return cfg


def test_periodic_velocity_and_positive_lifetime(tmp_path):
    cfg = configuration(tmp_path)
    ds = make_data(48)
    source = tmp_path / 'atmosphere.nc'
    ds.to_netcdf(source)
    cfg.update(case='crossing', dataset={'adapter':'cesm_hybrid','rename':{},'atmosphere':str(source)})
    out = Path(cfg['output_path']) / cfg['case']
    out.mkdir(parents=True)
    mask = np.zeros((48,91,180),dtype='uint8')
    for t in range(48):
        # Constant westward displacement of one cell/hour across longitude zero.
        mask[t,50,[(1-t+i)%180 for i in (-1,0,1)]] = 1
    xr.Dataset({'TC': (('time','lat','lon'),mask)},
               coords={n:ds[n] for n in ('time','lat','lon')}).to_netcdf(out / 'crossing.TC.nc')
    directory = build(irt_params(ds),cfg,out)
    track(cfg)
    objects = np.loadtxt(out / 'irt_objects_output.txt')
    velocities = objects[:,15]
    np.testing.assert_allclose(velocities[velocities > -1e8], -1, atol=1e-5, rtol=0)
    lifetime('crossing',None,str(out),config=cfg)
    # Existing IRT writes the previous timestep, retaining 47 records from 48
    # input snapshots. Preserve this end-of-series behavior in the port.
    assert (out/'TC.txt').read_text() == 'Total TCs = 1\nLife-Time average = 47.0\n'
    assert build(irt_params(ds),cfg,out) == directory


def test_concurrent_cases_netcdf_zarr_and_resume(tmp_path):
    cfg = configuration(tmp_path)
    ds = make_data(2)
    source = tmp_path / 'atmosphere.nc'
    ds.to_netcdf(source)
    cfg['dataset'] = {'adapter':'cesm_hybrid','rename':{},'atmosphere':str(source)}
    runs = []
    for name, fmt in [('first','netcdf'),('second','zarr')]:
        case_cfg = deepcopy(cfg)
        case_cfg['runtime']['preproc_format'] = fmt
        runs.append(prepare(case_cfg,name,'concurrent'))
    def launch(run):
        result = subprocess.run([sys.executable,'-m','tcflow','execute','--run',str(run)],
                                cwd=ROOT,text=True,capture_output=True,timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(launch,runs))
    caches = []
    for name, run in zip(('first','second'),runs):
        out = run/'results'/name
        assert (out/'TC.txt').read_text() == 'Total TCs = 0\nLife-Time average = 0\n'
        caches.append((out/'.irt_build_dir').read_text())
        mtime = (out/'TC.nc').stat().st_mtime_ns
        execute(run)
        assert (out/'TC.nc').stat().st_mtime_ns == mtime
    assert caches[0] == caches[1]
    # A damaged final output reruns lifetime only; earlier results stay intact.
    out = runs[0]/'results'/'first'
    detection_mtime = (out/'first.TC.nc').stat().st_mtime_ns
    (out/'TC.nc').write_bytes(b'incomplete')
    execute(runs[0])
    with xr.open_dataset(out/'TC.nc') as result:
        assert not result.TC.values.any()
    assert (out/'first.TC.nc').stat().st_mtime_ns == detection_mtime
