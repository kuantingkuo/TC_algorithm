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

The input NetCDF files are primarily designed for CESM output and should include the following variables: **`U`**, **`V`**, **`PS`**, **`SST`**, `U850`, `V850`, `PSL`, `T`, `Q`, and `Z3`.

### Main Workflow

Before beginning, ensure that your environment and required modules are set up correctly. Pay particular attention to these two files:

- `run.sh`
- `tracking/tracking2/compile.sh`
  
#### Confuguration

Edit `config.yaml` to specify your cases, input/output paths, file patterns, and options such as vorticity sign inversion for the Southern Hemisphere.
Currently, the `cases` field supports only a single case at a time.

Example:

```yaml
cases:
  - F2000
case_path: /data/User/archive/
output_path: /data/User/track_output/
file_pattern: cam.h0.*.nc
invert_vorticity_SH: true
```

#### Run through the tracking algorithm

To execute the tracking algorithm, run the `run.sh` script.

```bash
bash run.sh
```

The `run.sh` script consists of three main steps:

1. `tc_algorithm.py`: Identifies all potential TC grid points for each time snapshot.
2. `tracking.sh`: Links detected objects and tracks them over time.
3. `TC_lifetime.py`: Filters TC-like objects based on their lifetime and sea surface temperature (SST).

### Output

The following files are generated in the `output_path` directory:

- `irt_objects_mask.dat`
- `irt_objects_output.txt`
- `irt_tracklinks_output.txt`
- `irt_tracks_mask.ctl`
- `irt_tracks_mask.dat`
- `irt_tracks_output.txt`
- `CASE.slp.nc` (if not present in input)
- `CASE.TC.nc`
- `CASE.UV300.nc`
- `CASE.UV850.nc` (if not present in input)
- `CASE.vort850.nc`
- **`TC.nc`**
- `TC.txt`

Here, `CASE` refers to the case name specified in `config.yaml`.

`TC.nc` is the main output file containing TC IDs that correspond to `irt_tracks_output.txt`. `CASE.TC.nc` is a temporary file, but it can be useful for debugging.

## References

1. **Kuo, K.**, C. Wu, and W. Chen, 2023: Effects of the Horizontal Scales of the Cloud‐Resolving Model on Tropical Cyclones in the Superparameterized Community Atmosphere Model. *Earth Sp. Sci.*, 10, [https://doi.org/10.1029/2022EA002681](https://doi.org/10.1029/2022EA002681).

2. Moseley, C., O. Henneberg, and J. O. Haerter, 2019: A Statistical Model for Isolated Convective Precipitation Events. *J. Adv. Model. Earth Syst.*, 11, 360–375, [https://doi.org/10.1029/2018MS001383](https://doi.org/10.1029/2018MS001383).

3. Oouchi, K., J. Yoshimura, H. Yoshimura, R. Mizuta, S. Kusunoki, and A. Noda, 2006: Tropical Cyclone Climatology in a Global-Warming Climate as Simulated in a 20 km-Mesh Global Atmospheric Model: Frequency and Wind Intensity Analyses. *J. Meteorol. Soc. Japan. Ser. II*, 84, 259–276, [https://doi.org/10.2151/jmsj.84.259](https://doi.org/10.2151/jmsj.84.259).
