# TC algorithm

Detect tropical cyclone candidates, construct IRT objects and tracks, and filter
tracks to produce TC counts and lifetime statistics. The detection method originates
from Section 2.3 of [Kuo et al. (2023)](https://doi.org/10.1029/2022EA002681).
Tracking is adapted from [Iterative Rain Cell Tracking](https://github.com/christophermoseley/iterative_raincell_tracking).

The portable workflow runs the same scientific functions locally or in Slurm jobs.
Machine configuration, input layout and experiment settings are separate. Existing
CESM hybrid-coordinate, global regular-grid, hourly data is the supported input
contract. F1 and Taiwania 3 site templates require settings from your actual account
and software environment; they are not prevalidated deployments.

## Quick start

Use the Conda environment `forge` for Python. See `envs/environment.yaml` for the
package specification and the deployment guide for compiler/NetCDF compatibility.
Your existing local compiler/module settings in `config.yaml` are retained by the
local site profile.

```bash
# Validate the actual environment and the configured input data.
bash run.sh doctor
bash run.sh inspect-input

# Generate a job script without running the experiment.
bash run.sh prepare

# Execute locally in an independent run directory.
bash run.sh run

# Configure a target machine and generate a Slurm script.
bash run.sh prepare --site forerunner1 --user-config /path/to/my-f1.yaml
# Submit the job.sh path printed by the command.
```

The launcher isolates `forge` from `PYTHONPATH` and `PYTHONHOME` inherited from HPC
module stacks. This is required when a site module exposes packages compiled for a
different Python version, as can happen with the F1 Spack stack.

The F1 and Taiwania 3 profiles use NetCDF temporary storage initially. Zarr is an
optional preprocessing format and requires `zarr` plus `numcodecs` in `forge`.

The pipeline performs input validation, an isolated Fortran build, time-parallel
detection, sequential object/track construction, then lifetime/SST filtering.
Each run records its resolved configuration, input manifest, source fingerprint,
logs and output checksums. Resume with `bash run.sh execute --run /path/to/run`.
A Slurm run must be resumed inside its allocation or by resubmitting its generated
job script.

Results are under `output_path/<case>/<run_id>/results/<case>/`:

- `<case>.TC.nc`: intermediate detection features and candidate mask.
- `irt_objects_*`, `irt_tracks_*`, `irt_tracklinks_output.txt`: IRT objects and tracks.
- `TC.nc`: final lifetime-filtered TC IDs.
- `TC.txt`: total TC count and mean lifetime (legacy hourly timestep convention).

[Deployment, input configuration, Slurm arrays and resume behavior](docs/PORTABILITY.md)
includes configuration precedence, examples and migration from the former generated
`submit.sh` interface. `run.sh` no longer overwrites scripts in the source directory.

## Validation

```bash
conda run -n forge python -m pytest -q tests
```

Tests cover input layout/variable mapping, scientific-contract rejection, immutable
runs, CPU allocation, and Slurm script generation. Target-machine scientific
regression remains required before production use; consult the validation report in
`docs/VALIDATION.md` for checks actually performed in this workspace.

## References

- Kuo, K., C. Wu, and W. Chen (2023), Effects of the Horizontal Scales of the
  Cloud-Resolving Model on Tropical Cyclones in the Superparameterized Community
  Atmosphere Model. *Earth and Space Science*, 10,
  [doi:10.1029/2022EA002681](https://doi.org/10.1029/2022EA002681).
- Moseley, C., O. Henneberg, and J. O. Haerter (2019), A Statistical Model for
  Isolated Convective Precipitation Events. *JAMES*, 11, 360–375,
  [doi:10.1029/2018MS001383](https://doi.org/10.1029/2018MS001383).
- Oouchi, K., et al. (2006), Tropical Cyclone Climatology in a Global-Warming Climate
  as Simulated in a 20 km-Mesh Global Atmospheric Model. *JMSJ*, 84, 259–276,
  [doi:10.2151/jmsj.84.259](https://doi.org/10.2151/jmsj.84.259).

The regression also corrected an uninitialized Fortran variable used for longitude
wrapping. Its precise velocity-output impact and the preserved end-of-series
lifetime behavior are documented in [the validation report](docs/VALIDATION.md).
