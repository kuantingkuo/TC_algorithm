from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import numpy as np
import pytest
import xarray as xr
from tcflow.config import resolve, write
from tcflow.inputs import open_input, validate, file_manifest
from tcflow.workflow import prepare, generate_job, execute


@pytest.fixture
def dataset(tmp_path):
    time = np.array(['2001-01-01T00', '2001-01-01T01'], dtype='datetime64[h]')
    coords = dict(time=time, lev=[300., 850.], lat=np.linspace(-90, 90, 91), lon=np.arange(0, 360, 2.))
    ds = xr.Dataset(coords=coords)
    for name, value, units in [('U', 1., 'm/s'), ('V', 2., 'm/s'), ('T', 280., 'K'),
                                ('Q', .01, 'kg/kg'), ('Z3', 100., 'm')]:
        ds[name] = xr.DataArray(np.full((2, 2, 91, 180), value, dtype='float32'),
                               dims=('time','lev','lat','lon'), attrs={'units': units})
    for name, value, units in [('PS', 100000., 'Pa'), ('TS', 300., 'K')]:
        ds[name] = xr.DataArray(np.full((2,91,180), value, dtype='float32'),
                               dims=('time','lat','lon'), attrs={'units': units})
    ds['P0'] = xr.DataArray(100000., attrs={'units': 'Pa'})
    ds['hyam'] = xr.DataArray([.3, .85], dims='lev')
    ds['hybm'] = xr.DataArray([0., 0.], dims='lev')
    source = tmp_path / 'original.nc'
    ds.to_netcdf(source)
    path = tmp_path / 'config.yaml'
    write(path, dict(case='sample', output_path=str(tmp_path / 'out'),
                     dataset={'atmosphere': str(source)}, runtime={'cpus': 2}))
    return ds, resolve([path])


def test_renamed_split_input_matches_original(dataset, tmp_path):
    ds, cfg = dataset
    reference = validate(cfg, 'sample')
    renamed = ds.rename(lat='latitude', lon='longitude', U='eastward_wind')
    files = []
    for name in renamed.data_vars:
        path = tmp_path / (name + '.nc')
        renamed[[name]].to_netcdf(path)
        files.append(str(path))
    other = deepcopy(cfg)
    other['dataset'].update(atmosphere=files, rename={'latitude':'lat', 'longitude':'lon', 'eastward_wind':'U'})
    assert validate(other, 'sample') == reference
    with open_input(cfg, 'sample', decode_cf=True) as a, open_input(other, 'sample', decode_cf=True) as b:
        xr.testing.assert_equal(a, b)


@pytest.mark.parametrize('mutation, match', [
    ('hourly', 'hourly'), ('units', 'units'), ('latitude', 'ascending'),
    ('longitude', 'global coverage'), ('level', 'toward the surface'), ('missing', 'Missing CESM')])
def test_reject_incompatible_science_input(dataset, tmp_path, mutation, match):
    ds, cfg = dataset
    if mutation == 'hourly':
        ds = ds.assign_coords(time=ds.time.values + np.array([0, 2], dtype='timedelta64[h]'))
    elif mutation == 'units':
        ds.PS.attrs['units'] = 'hPa'
    elif mutation == 'latitude':
        ds = ds.isel(lat=slice(None, None, -1))
    elif mutation == 'longitude':
        ds = ds.isel(lon=slice(0, 80))
    elif mutation == 'level':
        ds = ds.isel(lev=slice(None, None, -1))
    else:
        ds = ds.drop_vars('U')
    path = tmp_path / 'changed.nc'
    ds.to_netcdf(path)
    cfg['dataset']['atmosphere'] = str(path)
    with pytest.raises(ValueError, match=match):
        validate(cfg, 'sample')


def test_separate_sst_requires_exact_time(dataset, tmp_path):
    ds, cfg = dataset
    sst = ds[['TS']].assign_coords(time=ds.time.values + np.timedelta64(1, 'h'))
    path = tmp_path / 'sst.nc'
    sst.to_netcdf(path)
    cfg['dataset']['sst'] = str(path)
    with pytest.raises(ValueError, match='SST time'):
        validate(cfg, 'sample')


def test_cli_overrides_all_stages(tmp_path):
    path = tmp_path / 'config.yaml'
    write(path, dict(cases=['a'], output_path=str(tmp_path), runtime={'cpus': 10}))
    cfg = resolve([path], {'runtime': {'cpus': 3}, 'scheduler': {'account': 'mine'}})
    assert cfg['runtime']['cpus'] == 3
    assert cfg['scheduler']['account'] == 'mine'


def test_run_collision_and_input_mutation(dataset):
    ds, cfg = dataset
    run = prepare(cfg, 'sample', 'frozen')
    with pytest.raises(FileExistsError):
        prepare(cfg, 'sample', 'frozen')
    source = Path(cfg['dataset']['atmosphere'])
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    with pytest.raises(RuntimeError, match='Input size/mtime'):
        execute(run)


def test_config_mutation_rejected(dataset):
    _, cfg = dataset
    run = prepare(cfg, 'sample')
    with (run / 'resolved_config.yaml').open('a') as stream:
        stream.write('\n# modified\n')
    with pytest.raises(RuntimeError, match='Configuration or source changed'):
        execute(run)


def test_slurm_script_array_and_quoting(dataset, tmp_path):
    _, cfg = dataset
    cfg['scheduler'].update(backend='slurm', account='project123', partition='cpu', array_limit=2)
    script = generate_job(cfg, [tmp_path / 'run one', tmp_path / 'run two'])
    text = script.read_text()
    subprocess.run(['bash', '-n', str(script)], check=True)
    assert '#SBATCH --array=0-1%2' in text
    assert '#SBATCH --ntasks=1' in text
    assert '#SBATCH --cpus-per-task=2' in text
    assert 'conda run --no-capture-output -n forge python' in text
    assert 'SLURM_ARRAY_TASK_ID' in text
    assert json.loads((script.parent / 'runs.json').read_text())[0].endswith('run one')


def test_slurm_requires_real_allocation_settings(dataset):
    _, cfg = dataset
    cfg['scheduler']['backend'] = 'slurm'
    with pytest.raises(ValueError, match='account'):
        generate_job(cfg, [])


def test_slurm_cannot_run_on_login_node(dataset, monkeypatch):
    _, cfg = dataset
    cfg['scheduler'].update(backend='slurm', account='project', partition='cpu')
    run = prepare(cfg, 'sample')
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    with pytest.raises(RuntimeError, match='requires a Slurm allocation'):
        execute(run)


def test_slurm_workers_cannot_exceed_allocation(dataset, monkeypatch):
    _, cfg = dataset
    run = prepare(cfg, 'sample')
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '1')
    with pytest.raises(RuntimeError, match='exceeds Slurm allocation'):
        execute(run)


@pytest.mark.parametrize('value', [0, -1, True, 1.5, None])
def test_cpu_count_is_not_silently_coerced(dataset, tmp_path, value):
    _, cfg = dataset
    path = tmp_path / 'bad.yaml'
    cfg['runtime']['cpus'] = value
    write(path, cfg)
    with pytest.raises(ValueError, match='positive integer'):
        resolve([path])


def test_site_clears_legacy_account_and_singular_case_overrides(dataset, tmp_path):
    _, cfg = dataset
    cfg['runtime']['account'] = 'local-project'
    base = tmp_path / 'base.yaml'
    site = tmp_path / 'site.yaml'
    write(base, cfg)
    write(site, {'case': 'new-case', 'scheduler': {'account': None}})
    result = resolve([base, site])
    assert result['cases'] == ['new-case']
    assert result['scheduler']['account'] is None


def test_environment_change_blocks_resume(dataset, monkeypatch):
    _, cfg = dataset
    run = prepare(cfg, 'sample')
    monkeypatch.setattr('tcflow.workflow.package_versions', lambda: {'numpy': 'different'})
    with pytest.raises(RuntimeError, match='Python environment changed'):
        execute(run)


def test_lifetime_preserves_36_step_and_sst_threshold():
    from TC_lifetime import lifetime
    sst = np.full((48, 2, 2), 299.15)
    record = ['0'] * 16
    record[1], record[14], record[15] = '0', '1', '1'
    section = ['*', '7 0 36 0 0', ' '.join(record)]
    assert lifetime(section, sst) == (7, 36)
    assert lifetime(['*', '7 0 35 0 0', ' '.join(record)], sst) == (0, 0)
    assert lifetime(section, sst - .01) == (0, 0)
    assert lifetime(section, sst, np.zeros(sst.shape, dtype=bool)) == (0, 0)


def test_empty_detection_writes_zero_tc_count(dataset, tmp_path, monkeypatch):
    from tcflow.tracking import run
    from TC_lifetime import main
    ds, cfg = dataset
    cfg['case'] = 'sample'
    out = Path(cfg['output_path']) / cfg['case']
    out.mkdir(parents=True)
    build = tmp_path / 'build'
    build.mkdir()
    (build / 'irt_parameters.f90').write_text('! test fixture')
    (out / '.irt_build_dir').write_text(str(build))
    ds[['TS']].rename(TS='TC').assign(TC=xr.zeros_like(ds.TS)).to_netcdf(out / 'sample.TC.nc')
    monkeypatch.setattr('tcflow.tracking.ready', lambda path: True)
    run(cfg)
    main('sample', None, str(out), config=cfg)
    assert (out / 'TC.txt').read_text() == 'Total TCs = 0\nLife-Time average = 0\n'
    with xr.open_dataset(out / 'TC.nc') as result:
        assert not result.TC.values.any()
    assert '365_day_calendar' not in (out / 'irt_tracks_mask.ctl').read_text()
