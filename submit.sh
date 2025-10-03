#!/bin/bash
#SBATCH --job-name=TC.test
#SBATCH --nodes=1
#SBATCH --cpus-per-task=32
#SBATCH --output=/data/W.eddie/track_output//test/TC.test.out
#SBATCH --error=/data/W.eddie/track_output//test/TC.test.err
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "./run.sh")" && pwd)"
CONFIG_FILE="/data/W.eddie/TC_algorithm/config.yaml"
RUNTIME_CONDA_ENV="forge"
RUNTIME_MODULES="Intel-oneAPI-2022.1 szip/2.1.1 hdf5/1.12.0 netcdf/4.7.4 mpi/2021.5.0 pnetcdf/1.12.2"
RUNTIME_MODULE_USE=""
ensure_conda_env() { local target_env="$1"; if [ -z "$target_env" ]; then return 0; fi; local current_env; current_env=$(conda info --json 2>/dev/null | python -c "import sys,json; print(json.load(sys.stdin).get(\"active_prefix_name\",\"\"))" || echo ""); if [ -z "$current_env" ] || [ "$current_env" != "$target_env" ]; then __conda_setup="$(conda shell.bash hook 2>/dev/null)" || true; [ -n "${__conda_setup}" ] && eval "${__conda_setup}" && conda activate "$target_env"; fi; }
load_modules() { local modules_line="$1"; [ -z "$modules_line" ] && return 0; module purge || true; for m in $modules_line; do module load "$m"; done; }
echo "[submit] Starting job on $(date)"
echo "[submit] Conda env: ${RUNTIME_CONDA_ENV}"
echo "[submit] Modules: ${RUNTIME_MODULES}"
[ -n "${RUNTIME_MODULE_USE}" ] && echo "[submit] module use ${RUNTIME_MODULE_USE}" && module use "${RUNTIME_MODULE_USE}"
ensure_conda_env "${RUNTIME_CONDA_ENV}"
python "${SCRIPT_DIR}/tc_algorithm.py"
load_modules "${RUNTIME_MODULES}"
( cd "${SCRIPT_DIR}" && ./tracking.sh )
ensure_conda_env "${RUNTIME_CONDA_ENV}"
python "${SCRIPT_DIR}/TC_lifetime.py"
echo "[submit] Finished at $(date)"
