# Portable execution and deployment

The supported scientific input contract remains CESM hybrid levels on an ascending,
regular global latitude/longitude grid, with continuous hourly sampling. Other
vertical coordinates, regional/curvilinear grids and non-hourly tracking require a
separate scientific extension. The workflow does not resample, fill missing data,
convert units or regrid automatically.

## Configuration

The merge order is built-in defaults, `--config` (legacy `config.yaml` by default),
`--site` (`local` by default), `--dataset`, `--experiment`, `--user-config`, then CLI
overrides. Nested mappings merge; lists replace. A `null` explicitly clears a value.
Relative paths resolve from the preparation command's working directory. Use absolute
paths in shared deployment configurations. `{root}`, `{case}` and `{initialization}`
are supported in input patterns; `{root}` is `case_path`. Environment variables are
expanded in input and runtime directory paths at preparation time.

The local site keeps compiler/modules from your base configuration and selects
Conda `forge`. The bundled F1 profile contains this project's current case, paths,
compiler modules, account and partition. Update it when those F1 values change. The
Taiwania 3 profile remains a template that requires target-specific values. Both HPC
profiles select NetCDF preprocessing for initial deployment, so Zarr remains optional
unless a later user configuration selects it.

```yaml
# Example user file: replace paths and module names, do not use these literally.
case_path: /absolute/input/root
output_path: /absolute/output/root
runtime:
  env_manager: conda
  env_name: forge
  # env_executable: /absolute/path/to/conda
  cpus: 8
  compiler: ifx
  compiler_flags: null  # compiler-specific conservative defaults
  nf_config: nf-config
  nc_config: nc-config
  module_purge: true
  load_modules:
    - YOUR_COMPILER_MODULE
    - YOUR_MATCHING_NETCDF_FORTRAN_MODULE
  # module_init: /path/to/module/initialization.sh
  # module_use: /path/to/modulefiles
  # python_modules: [YOUR_CONDA_MODULE]
  scratch_root: /absolute/scratch
  irt_build_cache_root: /absolute/shared/cache
scheduler:
  account: YOUR_PROJECT
  partition: YOUR_PARTITION
  time: '08:00:00'
  memory: 32G
  array_limit: 2
```

`runtime.python_modules`, when set, initializes the Python command environment in
the generated script. Fortran module setup occurs in separate subprocess shells
for building and tracking. Both compiler and NetCDF Fortran must be compatible;
a GNU compiler cannot use an Intel-built `netcdf.mod`. `doctor` verifies an actual
NetCDF Fortran compile/link/run and a direct-access binary roundtrip. No environment
activation or module-load failure silently falls back to another installation.
The build combines `nf-config --flibs` with `nc-config --libs`, placing both library
search paths before the link libraries. This supports module stacks where
`nf-config` omits the NetCDF-C `-L` path.

The launcher discards inherited `PYTHONPATH` and `PYTHONHOME`, and disables the user
site directory before entering `forge`. This prevents F1/Spack Python 3.10 packages
from being imported by the Conda Python. The generated Slurm script repeats this
isolation after loading any configured Python modules. Python packages needed by the
workflow must be installed in the selected Conda environment instead of being exposed
through a site-wide `PYTHONPATH`.

All Python development/test commands use Conda `forge`. The job environment is
explicitly configurable for deployment. `env_manager: none` requires an explicit
`python_executable`; it does not guess a system Python.

## Input layouts

Without dataset patterns, the legacy layout is retained:
`{case_path}/{case}/atm/hist/{case}.{file_pattern}`.

For a collaborator's CESM-compatible data:

```yaml
dataset:
  adapter: cesm_hybrid
  atmosphere:
    - '{root}/{case}/atmosphere/*/*.nc'
  sst:
    - '{root}/{case}/surface/*/*.nc'
  rename:
    longitude: lon
    latitude: lat
    eastward_wind: U
```

Patterns or explicit file lists are supported. Fields split across files are merged
by coordinates with exact alignment. `sst` defaults to the atmosphere files.
Renaming changes identifiers only; it does not change dimension order, units or
coordinate values. Atmosphere and SST must have identical time/lat/lon coordinates.

`inspect-input` checks required fields, recognized units, grid assumptions,
continuous hourly timestamps, supported calendar, and exact SST alignment. The
legacy `lev >= 200` selection and `hyam * P0 + hybm * PS` calculation are retained.
Pressure fields are in Pa, temperature in K, wind in m/s and Z3 is height in m.
Missing-value behavior and all detection thresholds remain in the original science
functions. New input encodings need scientific review, not a silent conversion.

Independent hindcast initializations must be listed explicitly instead of matching
all initialization directories with one wildcard. The workflow forms the Cartesian
product of `cases` and `initializations`; each combination receives a separate input
validation, run directory, detection, tracking and lifetime calculation. For example:

```yaml
cases: [experiment-control, experiment-up]
initializations: [hist.0907, hist.0908]
dataset:
  atmosphere: '{root}/{case}/atm/{initialization}/{case}.cam.h1.*.nc'
```

This produces four independent runs under
`output/<case>/<initialization>/<run_id>/`. It does not concatenate timestamps from
different initializations. Within one initialization, all files matching its pattern
still form that hindcast's continuous time series.

Optional `time_range: [start, end]` is an explicit, inclusive decoded-time selection
used by all stages. It never appears automatically to shorten an experiment.
At least two timestamps are needed to check sampling; meaningful lifetime validation
needs a longer interval. Lifetime still counts track timesteps and uses the existing
36-step threshold. Supported calendars are noleap/365_day and Gregorian variants;
the CTL noleap option is emitted only for noleap data.

If `runtime.preproc_format: zarr` is selected, both `zarr` and `numcodecs` must be
installed in the selected Conda environment. To validate F1 initially without that
optional dependency, select the F1 site profile or set the following explicitly:

```yaml
runtime:
  preproc_format: netcdf
```

## Preparing, running and resuming

```bash
# Local preflight, then an explicitly selected configuration.
bash run.sh doctor --user-config /path/to/local.yaml
bash run.sh inspect-input --user-config /path/to/local.yaml
bash run.sh run --user-config /path/to/local.yaml

# Generate scripts on the target host, inspect them, then submit when ready.
bash run.sh prepare --site forerunner1
# The command prints the absolute job.sh path.
sbatch /printed/path/to/job.sh

# Immediate submission is explicit.
bash run.sh prepare --site taiwania3 --user-config /path/to/t3.yaml --submit

# Multiple cases/initializations use a job array under Slurm; local execution is sequential.
bash run.sh prepare --site forerunner1 --user-config /path/to/f1.yaml \
  --cases case_a,case_b --cpus 8

# Resume the same frozen run (inside its allocation when backend is Slurm).
bash run.sh execute --run /output/case/run_id
```

`bash run.sh` without a command means `prepare`. It no longer overwrites a tracked
`submit.sh` or creates `submit_<case>.sh` in the repository. Use the printed generated
script path. Old `submit.sh` files are not the new entry point. CLI `--case`,
`--cases`, `--initializations`, `--cpus`, `--account`, `--partition` and `--time` are retained. Old
`--python-cmd`, `--output-prefix` and `--job-name` flags have been replaced by explicit
runtime settings and `scheduler.job_name`.

Each run has:

```text
output/<case>/[<initialization>/]<run_id>/
  resolved_config.yaml
  input_manifest.json
  input_report.json
  run_metadata.json
  logs/{build,detect,track,lifetime}.log
  *.done.json
  results/<case>/
    <case>.TC.nc
    irt_objects_mask.dat
    irt_objects_output.txt
    irt_tracks_mask.dat
    irt_tracks_output.txt
    irt_tracklinks_output.txt
    irt_tracks_mask.ctl
    TC.nc
    TC.txt
  work/                       # unless scratch_root is configured
```

Runs never overwrite another run with the same ID. Configuration, source hashes and
input path/size/mtime metadata are frozen before queueing. Input metadata fingerprints
are not full content checksums: upstream changes that preserve both size and mtime
cannot be detected. Treat input files as immutable after preparation.

A shared-filesystem run lock prevents concurrent execution of the same run. Stage
receipts store output hashes. A missing/corrupt output reruns that stage and all later
stages. A changed configuration, source or input fingerprint requires a new run.
Failed tracking workspaces are retained with a unique suffix for inspection. Temporary
preprocessing is published only after successful writing. Scratch should persist for
the allocation; completed final outputs are stored in the configured output directory.
No automatic scratch deletion is performed.

Fortran builds are isolated, contain only copied `.f90` sources, and include source,
compiler, flags, NetCDF information, platform, grid/time parameters and builder hashes
in their cache signature. Executable hashes are checked before reuse. A lock adjacent
to the build cache protects atomic publication across nodes using the shared filesystem's
POSIX locking. Choose a filesystem with working advisory locks. Never use node-local
`/tmp` to lock a shared cache. The cache lock does not cover tracking execution.

## Parallel execution

Each case/initialization combination uses one Slurm task on one node with
`cpus-per-task` worker capacity.
Detection uses processes across time, with inner Numba/BLAS/OpenMP threading capped
at one. Lifetime can use the allocated Numba threads. The configured worker count
must fit `SLURM_CPUS_PER_TASK`. Arrays distribute independent cases across nodes;
tracking stays sequential over each complete time series. A Slurm-configured run
refuses execution without an allocation.

This release does not distribute a single case across nodes, independently track
calendar chunks, or automatically select memory/chunking. Existing preprocessing
and SST persistence can use substantial memory on long experiments. Benchmark a
representative explicitly selected period before requesting full-run resources;
never change scientific thresholds to fit memory.

## Environment reproduction

`envs/environment.yaml` records the documented scientific stack plus missing I/O
dependencies. It is a portable solve specification, not a verified lock for every
cluster. Test the solve on the target OS and export the exact installed environment:

```bash
conda env create -f envs/environment.yaml
# If forge already exists, inspect/compare it rather than replacing it blindly.
conda list -n forge --explicit > forge-linux-64.explicit.txt
conda env export -n forge > forge-resolved.yaml
conda run -n forge python -m pytest -q tests
```

Store exports alongside the deployment record. No solver or package upgrade is run
as part of a job. `doctor` imports the actual installed dependencies and reports the
Python executable and compiler/NetCDF details.

## Cluster completion checklist

The repository provides F1/T3 configuration templates; their deployment is not
verified until run on the actual systems. For each target:

1. Inspect available modules, compiler and matching NetCDF Fortran (`module avail`,
   `module show`, `nf-config --all`). Set the profile/user configuration accordingly.
2. Confirm account, permitted partition, memory and time limits, scratch/cache paths.
3. Run `doctor` and `inspect-input` there.
4. Run the same representative data on local, F1 and T3. Compare feature fields,
   detection masks, object membership, track relationships, TC counts and lifetime.
5. Exercise two array cases, a same-case independent run, and restart after failure.

Floating differences need a documented tolerance; a changed threshold decision or
track must be investigated. IDs may be compared by membership/link equivalence if
numbering differs. Keep any physical-definition change in a separate reviewed change.

Official references:
- [Forerunner 1 manual](https://man.twcc.ai/@f1-manual/manual)
- [Taiwania 3 manual](https://man.twcc.ai/@twnia3/rJM5qk3Aw)

## Scientific regression note

The portability regression exposed and corrected an uninitialized Fortran grid-width
variable in periodic longitude displacement. Masks and track links stayed unchanged
in the validated sample; one velocity value changed to the correct signed displacement.
The original IRT final-timestep treatment is retained. See [the validation report](VALIDATION.md)
for the exact changes and evidence. Python version and package metadata are also checked
when resuming a prepared run; prepare it in the same environment used by its job.
