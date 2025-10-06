#!/bin/bash
#SBATCH --job-name=TC.f02.F2000.Tai-Talim.su11
#SBATCH --nodes=1
#SBATCH --cpus-per-task=16
#SBATCH --output=/data/W.eddie/track_output//f02.F2000.Tai-Talim.su11/TC.f02.F2000.Tai-Talim.su11.out
#SBATCH --error=/data/W.eddie/track_output//f02.F2000.Tai-Talim.su11/TC.f02.F2000.Tai-Talim.su11.err
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "./run.sh")" && pwd)"
CONFIG_FILE="/data/W.eddie/TC_algorithm/config.yaml"
RUNTIME_CONDA_ENV="forge"
RUNTIME_MODULES="Intel-oneAPI-2022.1 szip/2.1.1 hdf5/1.12.0 netcdf/4.7.4 mpi/2021.5.0 pnetcdf/1.12.2"
RUNTIME_MODULE_USE=""
RUNTIME_ACCOUNT=""
RUNTIME_PARTITION=""
ensure_conda_env() {
  local target_env="$1"
  [ -z "$target_env" ] && return 0
  if ! type -t conda >/dev/null 2>&1; then return 0; fi
  __conda_setup="$(conda shell.bash hook 2>/dev/null)" || true
  [ -n "${__conda_setup}" ] && eval "${__conda_setup}"
  if ! conda activate "$target_env" >/dev/null 2>&1; then
    # Retry noisy (do not fail job immediately; caller can decide)
    if ! conda activate "$target_env"; then
      echo "[submit][WARN] Failed to activate conda env $target_env" >&2
      return 1
    fi
  fi
  return 0
}
load_modules() { local modules_line="$1"; [ -z "$modules_line" ] && return 0; module purge || true; for m in $modules_line; do module load "$m"; done; }
echo "[submit] Starting job on $(date)"
module purge || true
[ -n "${RUNTIME_MODULE_USE}" ] && echo "[submit] module use ${RUNTIME_MODULE_USE}" && module use "${RUNTIME_MODULE_USE}"
set +u  # allow conda hook to reference unset vars
ensure_conda_env "${RUNTIME_CONDA_ENV}"
set -u
echo "[submit] Activated conda env: ${RUNTIME_CONDA_ENV}"
python "${SCRIPT_DIR}/tc_algorithm.py"
load_modules "${RUNTIME_MODULES}"
echo "[submit] Loaded modules: ${RUNTIME_MODULES}"
( cd "${SCRIPT_DIR}" && ./tracking.sh )
module purge || true
set +u
ensure_conda_env "${RUNTIME_CONDA_ENV}"
set -u
echo "[submit] Re-activated conda env: ${RUNTIME_CONDA_ENV}"
python "${SCRIPT_DIR}/TC_lifetime.py"
echo "[submit] Finished at $(date)"
