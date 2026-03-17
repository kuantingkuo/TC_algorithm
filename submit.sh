#!/bin/bash
#SBATCH --job-name=TC.Talim0903_gen_vary8
#SBATCH --nodes=1
#SBATCH --cpus-per-task=10
#SBATCH --output=/data/W.eddie/track_output//Talim0903_gen_vary8/TC.Talim0903_gen_vary8.out
#SBATCH --error=/data/W.eddie/track_output//Talim0903_gen_vary8/TC.Talim0903_gen_vary8.err
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "./run.sh")" && pwd)"
CONFIG_FILE="/data/W.eddie/TC_algorithm/config.yaml"
RUNTIME_ENV_MANAGER="micromamba"
RUNTIME_ENV_NAME="base"
RUNTIME_MODULES="Intel-oneAPI-2022.1 szip/2.1.1 hdf5/1.12.0 netcdf/4.7.4 mpi/2021.5.0 pnetcdf/1.12.2"
RUNTIME_MODULE_USE=""
RUNTIME_ACCOUNT=""
RUNTIME_PARTITION=""
module_available() {
  type -t module >/dev/null 2>&1
}
activate_python_env() {
  local env_manager="$1"
  local env_name="$2"
  case "$env_manager" in
    ""|none|current|system)
      echo "[submit] Using current shell Python environment"
      return 0
      ;;
    conda)
      if ! command -v conda >/dev/null 2>&1; then
        echo "[submit][WARN] conda not available; skipping activation for ${env_name:-<none>}" >&2
        return 0
      fi
      [ -n "$env_name" ] || { echo "[submit][WARN] env_name is empty for conda; skipping activation" >&2; return 0; }
      __conda_setup="$(conda shell.bash hook 2>/dev/null)" || true
      if [ -z "${__conda_setup}" ]; then
        base_dir="$(conda info --base 2>/dev/null || true)"
        [ -n "$base_dir" ] && [ -f "$base_dir/etc/profile.d/conda.sh" ] && . "$base_dir/etc/profile.d/conda.sh"
      else
        eval "${__conda_setup}"
      fi
      if ! conda activate "$env_name" >/dev/null 2>&1; then
        if ! conda activate "$env_name"; then
          echo "[submit][WARN] Failed to activate conda env $env_name" >&2
          return 1
        fi
      fi
      echo "[submit] Activated conda env: $env_name"
      return 0
      ;;
    micromamba)
      if ! command -v micromamba >/dev/null 2>&1; then
        echo "[submit][WARN] micromamba not available; skipping activation for ${env_name:-<none>}" >&2
        return 0
      fi
      [ -n "$env_name" ] || { echo "[submit][WARN] env_name is empty for micromamba; skipping activation" >&2; return 0; }
      __micromamba_setup="$(micromamba shell hook --shell bash 2>/dev/null)" || true
      [ -n "${__micromamba_setup}" ] && eval "${__micromamba_setup}"
      if ! micromamba activate "$env_name" >/dev/null 2>&1; then
        if ! micromamba activate "$env_name"; then
          echo "[submit][WARN] Failed to activate micromamba env $env_name" >&2
          return 1
        fi
      fi
      echo "[submit] Activated micromamba env: $env_name"
      return 0
      ;;
    *)
      echo "[submit][WARN] Unknown env manager: $env_manager; using current shell environment" >&2
      return 0
      ;;
  esac
}
load_modules() {
  local modules_line="$1"
  [ -z "$modules_line" ] && return 0
  if ! module_available; then
    echo "[submit][WARN] module command not available; skipping module load" >&2
    return 0
  fi
  module purge || true
  for m in $modules_line; do module load "$m"; done
}
echo "[submit] Starting job on $(date)"
if module_available; then module purge || true; fi
if [ -n "${RUNTIME_MODULE_USE}" ]; then
  if module_available; then
    echo "[submit] module use ${RUNTIME_MODULE_USE}"
    module use "${RUNTIME_MODULE_USE}"
  else
    echo "[submit][WARN] module use requested but module command is unavailable" >&2
  fi
fi
set +u  # allow environment hooks to reference unset vars
activate_python_env "${RUNTIME_ENV_MANAGER}" "${RUNTIME_ENV_NAME}"
set -u
python "${SCRIPT_DIR}/tc_algorithm.py"
load_modules "${RUNTIME_MODULES}"
echo "[submit] Loaded modules: ${RUNTIME_MODULES}"
( cd "${SCRIPT_DIR}" && ./tracking.sh )
if module_available; then module purge || true; fi
set +u
activate_python_env "${RUNTIME_ENV_MANAGER}" "${RUNTIME_ENV_NAME}"
set -u
python "${SCRIPT_DIR}/TC_lifetime.py"
echo "[submit] Finished at $(date)"
