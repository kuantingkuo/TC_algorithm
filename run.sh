#!/bin/bash
set -euo pipefail

# Root directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${SCRIPT_DIR}/config.yaml}"

echo "[run.sh] Using config: ${CONFIG_FILE}" >&2

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "[run.sh] FATAL: config.yaml not found at ${CONFIG_FILE}" >&2
    exit 1
fi

# Initialize defaults (avoid unbound vars later)
RUNTIME_CONDA_ENV=""
RUNTIME_MODULES=""
RUNTIME_COMPILER=""
RUNTIME_COMPILER_FLAGS=""
CONFIG_CASE=""
CONFIG_OUTPUT_PATH=""

# Extract runtime settings from YAML (requires PyYAML); pass CONFIG_FILE as argv[1]
if [ -r "${CONFIG_FILE}" ]; then
    eval "$(python - "$CONFIG_FILE" <<'PY'
import yaml, sys, shlex, os
cfg_path = sys.argv[1]
try:
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f) or {}
except Exception as e:
    print(f'echo "[run.sh] WARNING: Failed to read {cfg_path}: {e}" >&2')
    cfg = {}
rt = (cfg.get('runtime') or {})
env = rt.get('conda_env') or ''
mods = rt.get('load_modules') or []
compiler = rt.get('compiler') or ''
flags = rt.get('compiler_flags') or ''
flags_one_line = " ".join(str(flags).split())
cpus_cfg = rt.get('cpus') or ''
case_val = cfg.get('case') or ''
out_path = cfg.get('output_path') or ''
print(f'RUNTIME_CONDA_ENV={shlex.quote(env)}')
print(f'RUNTIME_MODULES={shlex.quote(" ".join(mods))}')
print(f'RUNTIME_COMPILER={shlex.quote(compiler)}')
print(f'RUNTIME_COMPILER_FLAGS={shlex.quote(flags_one_line)}')
print(f'RUNTIME_CPUS={shlex.quote(str(cpus_cfg))}')
print(f'CONFIG_CASE={shlex.quote(str(case_val))}')
print(f'CONFIG_OUTPUT_PATH={shlex.quote(out_path)}')
PY
    )"
else
    echo "[run.sh] WARNING: Config not readable (permission?) falling back to defaults" >&2
fi

ensure_conda_env() {
    local target_env="$1"
    if [ -z "$target_env" ]; then
        echo "[run.sh] No conda env specified; skipping activation" >&2
        return 0
    fi
    local current_env
    current_env=$(conda info --json 2>/dev/null | python -c "import sys, json; print(json.load(sys.stdin).get('active_prefix_name', ''))" || echo "")
    if [ -z "$current_env" ] || [ "$current_env" != "$target_env" ]; then
            echo "[run.sh] Activating conda env $target_env" >&2
            __conda_setup="$(conda shell.bash hook 2> /dev/null)" || true
            if [ -n "${__conda_setup}" ]; then
                eval "${__conda_setup}"
                conda activate "$target_env"
            else
                echo "[run.sh] WARNING: Could not initialize conda shell hook" >&2
            fi
    else
            echo "[run.sh] Conda env $target_env already active" >&2
    fi
}

PRIMARY_CASE="${CONFIG_CASE}"
load_modules() {
    local modules_line="$1"
    if [ -z "$modules_line" ]; then
        echo "[run.sh] No modules to load" >&2
        return 0
    fi
    # Purge first for a clean state
    module purge || true
    for m in $modules_line; do
        echo "[run.sh] module load $m" >&2
        module load "$m"
    done
}

GEN_SUBMIT=0
JOB_NAME=""   # derive later from config if empty (TC.<case>)
TIME_LIMIT=""  # include only if provided via CLI
PARTITION=""   # include only if provided via CLI
CPUS=""        # include only if provided
ACCOUNT=""     # include only if provided
OUTPUT_PREFIX=""  # derive from case if empty

while [[ $# -gt 0 ]]; do
    case "$1" in
        --generate-submit)
            GEN_SUBMIT=1; shift ;;
        --job-name)
            JOB_NAME="$2"; shift 2 ;;
        --time)
            TIME_LIMIT="$2"; shift 2 ;;
        --partition)
            PARTITION="$2"; shift 2 ;;
        --cpus)
            CPUS="$2"; shift 2 ;;
        --account)
            ACCOUNT="$2"; shift 2 ;;
        --output-prefix)
            OUTPUT_PREFIX="$2"; shift 2 ;;
        *)
            echo "[run.sh] Unknown argument: $1" >&2; exit 2 ;;
    esac
done

echo "[run.sh] Modules: ${RUNTIME_MODULES:-<none>}" >&2
echo "[run.sh] Conda env: ${RUNTIME_CONDA_ENV:-<none>} (submit mode: $GEN_SUBMIT)" >&2
echo "[run.sh] Case: ${CONFIG_CASE:-<none>}" >&2
echo "[run.sh] Output path: ${CONFIG_OUTPUT_PATH:-<none>}" >&2
echo "[run.sh] Runtime cpus (config): ${RUNTIME_CPUS:-<none>}" >&2

# Derive defaults after CLI parsing
if [ -z "$JOB_NAME" ]; then
    if [ -n "$PRIMARY_CASE" ]; then JOB_NAME="TC.${PRIMARY_CASE}"; else JOB_NAME="TC.pipeline"; fi
fi
if [ -z "$OUTPUT_PREFIX" ]; then
    if [ -n "$PRIMARY_CASE" ]; then OUTPUT_PREFIX="TC.${PRIMARY_CASE}"; else OUTPUT_PREFIX="tc_job"; fi
fi
if [ -z "$CPUS" ] && [ -n "${RUNTIME_CPUS:-}" ]; then
    CPUS="$RUNTIME_CPUS"
fi
export TC_CPUS="${CPUS:-1}"

if [ $GEN_SUBMIT -eq 1 ]; then
    SUBMIT_FILE="submit.sh"
    echo "[run.sh] Generating $SUBMIT_FILE for sbatch (job=${JOB_NAME})" >&2
    # Ensure output path exists
    [ -n "$CONFIG_OUTPUT_PATH" ] && mkdir -p "$CONFIG_OUTPUT_PATH"
        if [ -n "$PRIMARY_CASE" ]; then
            mkdir -p "$CONFIG_OUTPUT_PATH/$PRIMARY_CASE"
            LOG_OUT_PATH="$CONFIG_OUTPUT_PATH/$PRIMARY_CASE/${OUTPUT_PREFIX}.out"
            LOG_ERR_PATH="$CONFIG_OUTPUT_PATH/$PRIMARY_CASE/${OUTPUT_PREFIX}.err"
        else
            LOG_OUT_PATH="$CONFIG_OUTPUT_PATH/${OUTPUT_PREFIX}.out"
            LOG_ERR_PATH="$CONFIG_OUTPUT_PATH/${OUTPUT_PREFIX}.err"
        fi
    {
        echo "#!/bin/bash"
        echo "#SBATCH --job-name=${JOB_NAME}"
        echo "#SBATCH --nodes=1"
        [ -n "$PARTITION" ] && echo "#SBATCH --partition=${PARTITION}"
        [ -n "$CPUS" ] && echo "#SBATCH --cpus-per-task=${CPUS}"
        echo "#SBATCH --ntasks=1"
        [ -n "$TIME_LIMIT" ] && echo "#SBATCH --time=${TIME_LIMIT}"
        [ -n "$ACCOUNT" ] && echo "#SBATCH --account=${ACCOUNT}"
        if [ -n "$CONFIG_OUTPUT_PATH" ]; then
            echo "#SBATCH --output=${LOG_OUT_PATH}"
            echo "#SBATCH --error=${LOG_ERR_PATH}"
        else
            echo "#SBATCH --output=${OUTPUT_PREFIX}.out"
            echo "#SBATCH --error=${OUTPUT_PREFIX}.err"
        fi
        echo "set -euo pipefail"
        echo "SCRIPT_DIR=\"\$(cd \"\$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\""
        echo "CONFIG_FILE=\"${CONFIG_FILE}\""
        echo "RUNTIME_CONDA_ENV=\"${RUNTIME_CONDA_ENV}\""
        echo "RUNTIME_MODULES=\"${RUNTIME_MODULES}\""
        echo 'ensure_conda_env() { local target_env="$1"; if [ -z "$target_env" ]; then return 0; fi; local current_env; current_env=$(conda info --json 2>/dev/null | python -c "import sys,json; print(json.load(sys.stdin).get(\"active_prefix_name\",\"\"))" || echo ""); if [ -z "$current_env" ] || [ "$current_env" != "$target_env" ]; then __conda_setup="$(conda shell.bash hook 2>/dev/null)" || true; [ -n "${__conda_setup}" ] && eval "${__conda_setup}" && conda activate "$target_env"; fi; }'
        echo 'load_modules() { local modules_line="$1"; [ -z "$modules_line" ] && return 0; module purge || true; for m in $modules_line; do module load "$m"; done; }'
        echo 'echo "[submit] Starting job on $(date)"'
        echo 'echo "[submit] Conda env: ${RUNTIME_CONDA_ENV}"'
        echo 'echo "[submit] Modules: ${RUNTIME_MODULES}"'
        echo 'ensure_conda_env "${RUNTIME_CONDA_ENV}"'
        echo 'python "${SCRIPT_DIR}/tc_algorithm.py"'
        echo 'load_modules "${RUNTIME_MODULES}"'
        echo '( cd "${SCRIPT_DIR}" && ./tracking.sh )'
        echo 'ensure_conda_env "${RUNTIME_CONDA_ENV}"'
        echo 'python "${SCRIPT_DIR}/TC_lifetime.py"'
        echo 'echo "[submit] Finished at $(date)"'
    } > "$SUBMIT_FILE"
    chmod +x "$SUBMIT_FILE"
    echo "[run.sh] submit.sh created. Submit with: sbatch $SUBMIT_FILE" >&2
    exit 0
fi

# Phase 1: Python detection / preprocessing
ensure_conda_env "${RUNTIME_CONDA_ENV}"
python "${SCRIPT_DIR}/tc_algorithm.py"

# Phase 2: Fortran tracking (requires modules)
load_modules "${RUNTIME_MODULES}"
(
    cd "${SCRIPT_DIR}" || exit 1
    ./tracking.sh
)

# Phase 3: Post-processing lifetime (may need Python env again if modules changed PATH)
ensure_conda_env "${RUNTIME_CONDA_ENV}"
python "${SCRIPT_DIR}/TC_lifetime.py"

echo "[run.sh] Completed successfully" >&2

