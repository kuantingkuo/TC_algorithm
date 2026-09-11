# Portability validation — 2026-09-07

## F1 bootstrap follow-up — 2026-09-11

F1 exposed Python 3.10 packages from its Spack stack to the Conda Python 3.13 through
an inherited `PYTHONPATH`. Launchers now isolate `PYTHONPATH`, unset `PYTHONHOME`, and
disable the user site directory. A second F1 check then showed that optional Zarr was
not installed in `forge`. The F1/T3 profiles now explicitly select NetCDF temporary
storage for initial deployment, while an explicit Zarr selection receives an
actionable dependency error instead of a traceback.

The focused portability suite passed 28 tests after these changes. The two optional
real-compiler integration tests were not repeated in this follow-up because the
changes affect only bootstrap dependency checks and site-profile preprocessing
selection; their earlier successful result remains documented below.

## Scope and environment

Validation ran locally in the existing Conda `forge` environment. No Python packages
were installed, upgraded or replaced. The user's pre-existing `config.yaml` changes
were preserved byte-for-byte, and the modified working-tree science code (rather
than only Git HEAD) was saved before implementation as the regression baseline.

Actual installed versions reported by package metadata:

| Component | Version |
| --- | --- |
| Python | 3.13.15 |
| NumPy | 2.5.2 |
| xarray | 2026.7.0 |
| Dask | 2026.8.0 |
| Numba | 0.67.0 |
| netCDF4 Python | 1.7.4 |
| h5netcdf | 1.8.1 |
| Zarr | 3.3.0 |
| PyYAML | 6.0.3 |
| windspharm | 0.0.0 as reported by this installation's metadata |
| Fortran | Intel ifort 2021.5.0, build 20211109 |
| NetCDF Fortran | 4.5.3 |
| NetCDF C | 4.7.4 |

The local windspharm installation contains the spherical-harmonic implementation;
its metadata version is recorded as reported, not inferred to be upstream 2.0.0.
The portable `envs/environment.yaml` preserves the formerly documented scientific
version targets and adds I/O dependencies. It is a deployment specification, not
an export of this newer `forge` installation, and its solve was not tested here.

Loaded modules for the tested Fortran stack:
`Intel-oneAPI-2022.1`, `szip/2.1.1`, `hdf5/1.12.0`, `netcdf/4.7.4`.
Existing debug/bounds-check compiler settings were retained for the comparison;
NetCDF include/link flags came from `nf-config` in the new build.

## Automated checks

**26 tests passed**, including actual Fortran builds and workflow subprocesses:

```bash
TC_FORTRAN_TEST_CONFIG=/path/to/validated-local.yaml \
  conda run -n forge python -m pytest -q tests
```

Without `TC_FORTRAN_TEST_CONFIG`, the two real-compiler tests are skipped and the
24 configuration/input/lifetime tests run normally.

Coverage includes:

- Renamed coordinates/variables and fields split into separate files produce the
  same xarray dataset as the original layout.
- Reject non-hourly/gapped timestamps, inconsistent units, reversed or regional
  grids, missing variables, and SST time mismatch.
- Configuration precedence, explicit account clearing, valid integer CPU counts,
  single-case overrides and Slurm array script syntax.
- Frozen configuration/input/environment checks, run-ID collisions and Slurm CPU
  allocation restrictions.
- Exact 36-step and 299.15 K lifetime/SST threshold behavior.
- No-object handling produces zero masks and `Total TCs = 0`.
- A synthetic object moving west one grid cell/hour crosses longitude zero with
  diagnosed velocity consistently -1, and passes the lifetime/SST filter.
- Two cases run concurrently, sharing the same verified build cache, with NetCDF
  and Zarr preprocessing respectively.
- Completed runs resume without rewriting outputs; damage to final `TC.nc` reruns
  lifetime while preserving completed detection output.

`doctor` also successfully compiled, linked and ran a NetCDF Fortran probe, and
verified that direct-access Fortran output reads back as the expected NumPy float32
sequence. Shell syntax checks and `git diff --check` passed.

Zarr's smallest standalone local-file operation hung inside this tool session's
restricted sandbox, before detection workers started. The identical probe succeeded
outside the sandbox. The complete 26-test suite therefore ran outside the sandbox,
with all generated fixtures/builds/results under `/tmp`. No Zarr fallback or change
to the requested preprocessing format was introduced. Workers use `spawn` so they
do not inherit numerical-library threads or asynchronous I/O state.

## Real-data comparison

Input: `Talim0903_gen_CAM6`, 2017-09-03 00:00 through 2017-09-04 23:00, 48 hourly
snapshots, 192 latitudes × 288 longitudes. The validation interval was explicitly
selected in a temporary validation configuration; the user's experiment settings
and source files were not changed.

Both the saved baseline and final implementation processed the same two source days
with two detection workers. The baseline Fortran was rebuilt under the same loaded
modules as the new implementation. Attempting to use the inherited compiler/runtime
environment without those modules produced an Intel processor-feature error; the
comparison uses the successful consistently configured builds.

| Output | Comparison |
| --- | --- |
| All fields and coordinates in `<case>.TC.nc` | `xarray.testing.assert_identical` passed |
| Final `TC.nc` | `xarray.testing.assert_identical` passed |
| `irt_objects_mask.dat` | Byte-identical |
| `irt_tracks_mask.dat` | Byte-identical |
| `irt_tracks_mask.ctl` | Byte-identical for this noleap input |
| `TC.txt` | Byte-identical: 0 TCs, mean lifetime 0 |
| Object/track/link text tables | Identical except the single corrected periodic x-velocity propagated into each table |

Object and track membership, IDs, links and lifetime were unchanged in this sample.
It contains detected objects/tracks but no final accepted TCs; a separate synthetic
positive-TC integration test covers successful lifetime filtering. This local
regression does not establish the full climatology or target-cluster equivalence.

The detector feature function, interpolation helpers and lifetime-selection function
were also compared structurally with the saved baseline and are unchanged.

Temporary evidence from this session:

- Final real-data run: `/tmp/tc-portability-validation/Talim0903_gen_CAM6/spawn-48h/`
- Saved baseline/results: `/tmp/tc_portability_baseline/`
- Detailed comparison: `/tmp/tc-portability-validation/comparison.json`

These temporary artifacts may be cleaned by the host. This document and
`docs/validation-comparison.json` preserve the comparison outcome in the repository.

## Explicit Fortran correction

The previous forward-link displacement code used the uninitialized local variable
`domsize_x` when wrapping longitude, although the grid width is `domainsize_x`.
The unused local declaration was removed and both wrap branches now use the declared
grid width. This matches the existing periodic-displacement convention elsewhere
in the same file. No thresholds or grid/physical constants changed.

In the real-data comparison, one westward crossing was formerly reported with
x-velocity 271 (another pre-fix build reported 283). Its corrected value is -1 grid
cell/timestep. The only numerical text differences are:

- `irt_objects_output.txt`: line 15, column 16.
- `irt_tracks_output.txt`: line 74, column 17.
- `irt_tracklinks_output.txt`: line 74, column 17.

All other columns/rows were checked exactly. The dedicated synthetic crossing test
checks the corrected signed velocity rather than accepting arbitrary differences.

The existing IRT end-of-series behavior is retained: a continuously present object
in 48 input snapshots produces 47 track records and a reported lifetime of 47.
No endpoint or timestep-index correction was folded into this portability change.

## Remaining deployment work

Forerunner 1 and Taiwania 3 have configuration templates, not verified live
deployments. No cluster login, submission or allocation was performed. Completion
on each target requires the account's actual compiler/NetCDF modules, project,
partition, paths and a repeat of the same-data regression there.

The collaborator's actual data layout/header is also still needed to finalize its
input profile. Pressure-level or other hybrid-coordinate adapters, non-hourly
lifetime definitions, regional/irregular grids, single-case multi-node execution,
and a solved environment lock for each target remain separate work.
