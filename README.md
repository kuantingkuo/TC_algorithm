# TC algorithm

This repository contains scripts and utilities for tropical cyclone (TC) detection and tracking. The detection process of the first version of this algorithm is described in Section 2.3 of [Kuo et al. (2023)](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2022EA002681). The tracking tool is adapted from the [Iterative Rain Cell Tracking (IRT)](https://github.com/christophermoseley/iterative_raincell_tracking) software.

## Getting Started

The main workflow is managed by the `run.sh` shell script.

### Prerequisites

This code has been tested with the following packages:

- Python==3.10
- xarray==2025.6.1
- dask==2025.5.1
- netCDF4==1.7.2
- numpy==1.26.4
- numba==0.61.2
- windspharm==2.0.0
- PyYAML==6.0.2

Fortran compiler (for the tracking code) with NetCDF library support

### Input Files

The input NetCDF files are primarily designed for CESM output.

Required by `tc_algorithm.py`:

- `U`, `V`, `PS`, `T`, `Q`, `Z3`, `hyam`, `hybm`, `P0`

Optional in `tc_algorithm.py` (computed if missing):

- `U850`, `V850` (derived from `U`/`V`)
- `PSL` (derived from low-level thermodynamic fields)

Required by `TC_lifetime.py`:

- `SST` or `TS`

### Configuration

Edit `config.yaml` to specify your case, input/output paths, file patterns, and options such as vorticity sign inversion for the Southern Hemisphere.
Currently, only a single case is supported through the singular `case` field.

Optional runtime settings (under `runtime:`):

- `env_manager`: Python environment manager to use in generated `submit.sh`. Supported values are `conda`, `micromamba`, and `none`.
- `env_name`: Environment name to activate when `env_manager` is `conda` or `micromamba`. Use `base` if that machine keeps packages in the base environment.
- `cpus`: Logical CPU count used for time-parallel detection.
- `module_use`: A modulefiles directory path to prepend via `module use <path>` before any `module load` statements. If empty or omitted, no `module use` line is emitted.
- `account`: Default Slurm account to use if `--account` not passed to `run.sh`.
- `partition`: Default Slurm partition to use if `--partition` not passed.
- `load_modules`: List of modules to load (after optional `module use`).
- `compiler` / `compiler_flags`: Used to rewrite `tracking/tracking2/compile.sh` `COMPILE_COMMAND`.

Example:

```yaml
case: F2000
case_path: /data/User/archive/
output_path: /data/User/track_output/
file_pattern: cam.h0.*.nc
invert_vorticity_SH: true
runtime:
  env_manager: conda
  env_name: myenv
  cpus: 32
  module_use: /home/user/custom/modulefiles
  account: proj1234
  load_modules:
    - netcdf/4.7.4
    - hdf5/1.12.0
  compiler: ifort
  compiler_flags: >-
    -O2 -traceback
```

### Workflow Overview

Before beginning, ensure your environment and required modules are set up correctly. Pay particular attention to these two files:

- `run.sh`
- `tracking/tracking2/compile.sh`

Note on environment setup: `run.sh` reads environment and module settings from `config.yaml` under `runtime:` and emits them into `submit.sh`. In most cases, update `config.yaml` rather than editing `run.sh` directly. If your system does not use Environment Modules, keep `runtime.load_modules` empty. If your Python environment is already active before submission, use `env_manager: none`.

`tracking/tracking2/compile.sh` assumes Intel ifort and a specific NetCDF installation (include/lib paths embedded in `COMPILE_COMMAND`). Edit `COMPILE_COMMAND` to match your compiler (e.g., ifort, gfortran) and your NetCDF Fortran include and library paths on your system.
  
### Run the tracking algorithm

Run the workflow in two steps:

1. Generate `submit.sh` from `config.yaml` (and optional CLI overrides).

```bash
bash run.sh
```

2. Execute the generated script via Slurm, or run it directly in shell.

```bash
sbatch submit.sh
# or
bash submit.sh
```

The generated `submit.sh` script runs three main steps:

1. `tc_algorithm.py`: Identifies all potential TC grid points for each time snapshot.
2. `tracking.sh`: Links detected objects and tracks them over time.
3. `TC_lifetime.py`: Filters TC-like objects based on their lifetime and sea surface temperature (SST).

Parallel behavior summary:

- `tc_algorithm.py` uses time-parallel workers (`ProcessPoolExecutor`) controlled by `runtime.cpus`.
- `utils.py` and `TC_lifetime.py` include Numba parallel kernels for selected loops.
- `tracking/tracking2/iterate.sh` executes the Fortran tracking stages sequentially.

### Output

The following files are generated in the `output_path` directory (within the case subfolder):

- `irt_objects_mask.dat`
- `irt_objects_output.txt`
- `irt_tracklinks_output.txt`
- `irt_tracks_mask.ctl`
- `irt_tracks_mask.dat`
- `irt_tracks_output.txt`
- `CASE.TC.nc` (intermediate detection features & mask)
- **`TC.nc`** (final lifetime-filtered storm IDs)
- `TC.txt` (summary counts and mean lifetime)

Here, `CASE` refers to the case name specified in `config.yaml`.

`TC.nc` is the main output file containing TC IDs that correspond to `irt_tracks_output.txt`. `CASE.TC.nc` is an intermediate file holding detection-time features and masks (useful for debugging).

## References

1. **Kuo, K.**, C. Wu, and W. Chen, 2023: Effects of the Horizontal Scales of the Cloud‐Resolving Model on Tropical Cyclones in the Superparameterized Community Atmosphere Model. *Earth Sp. Sci.*, 10, [https://doi.org/10.1029/2022EA002681](https://doi.org/10.1029/2022EA002681).

2. Moseley, C., O. Henneberg, and J. O. Haerter, 2019: A Statistical Model for Isolated Convective Precipitation Events. *J. Adv. Model. Earth Syst.*, 11, 360–375, [https://doi.org/10.1029/2018MS001383](https://doi.org/10.1029/2018MS001383).

3. Oouchi, K., J. Yoshimura, H. Yoshimura, R. Mizuta, S. Kusunoki, and A. Noda, 2006: Tropical Cyclone Climatology in a Global-Warming Climate as Simulated in a 20 km-Mesh Global Atmospheric Model: Frequency and Wind Intensity Analyses. *J. Meteorol. Soc. Japan. Ser. II*, 84, 259–276, [https://doi.org/10.2151/jmsj.84.259](https://doi.org/10.2151/jmsj.84.259).
