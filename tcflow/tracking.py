"""Run sequential IRT stages in a private workspace."""
from pathlib import Path
import shlex
import shutil
from .build import ready, shell


def run(cfg):
    out = Path(cfg['output_path']) / cfg['case']
    metadata = out / '.irt_build_dir'
    if not metadata.exists():
        raise RuntimeError('Tracking requires a completed build stage')
    binary_dir = Path(metadata.read_text().strip())
    if not ready(binary_dir):
        raise RuntimeError(f'Invalid IRT build: {binary_dir}')
    workspace = Path(cfg.get('_work_dir', out / 'work')) / 'tracking'
    if workspace.exists():
        # Failed stages are retried in a new directory, preserving diagnostics.
        import uuid
        workspace.rename(workspace.with_name('tracking.failed-' + uuid.uuid4().hex[:8]))
    workspace.mkdir(parents=True)
    (workspace / 'TC.nc').symlink_to((out / (cfg['case'] + '.TC.nc')).resolve())
    shutil.copy2(binary_dir / 'irt_parameters.f90', workspace)
    # IRT's trackmask reader expects at least one track record. Represent an
    # empty detection explicitly instead of invoking that reader on an empty file.
    import numpy as np
    import xarray as xr
    with xr.open_dataset(workspace / 'TC.nc') as detection:
        empty = not bool((detection.TC > 0).any().values)
        shape = tuple(detection.sizes[d] for d in ('time', 'lat', 'lon'))
    if empty:
        for name in ('irt_objects_mask.dat', 'irt_tracks_mask.dat'):
            np.zeros(shape, dtype=np.float32).tofile(out / name)
        for name in ('irt_objects_output.txt', 'irt_tracks_output.txt', 'irt_tracklinks_output.txt'):
            (out / name).write_text('')
        print('No detected objects: wrote zero masks and empty track tables')
        return
    commands = [shlex.join([str(binary_dir / 'irt_objects_release.x'), '1']),
                shlex.quote(str(binary_dir / 'irt_tracks_release.x')),
                'LC_ALL=C sort -n -k2 irt_tracks_nohead_output.txt > irt_tracks_sorted.txt',
                shlex.quote(str(binary_dir / 'irt_trackmask_release.x')),
                shlex.quote(str(binary_dir / 'irt_tracklinks_release.x'))]
    output = shell(cfg['runtime'], '\n'.join(commands), workspace)
    print(output)
    for name in ('irt_objects_mask.dat', 'irt_objects_output.txt', 'irt_tracks_mask.dat',
                 'irt_tracks_output.txt', 'irt_tracklinks_output.txt'):
        shutil.copy2(workspace / name, out / name)
