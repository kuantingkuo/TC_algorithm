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
RUNTIME_MODULE_USE=""  # optional additional modulefiles path
RUNTIME_ACCOUNT=""  # optional default Slurm account
RUNTIME_PARTITION=""  # optional default Slurm partition
CONFIG_CASE=""
CONFIG_OUTPUT_PATH=""

# Simple conda activation helper (no python fallback logic)
ensure_conda_env() {
    local target_env="$1"
    [ -z "$target_env" ] && return 0
    command -v conda >/dev/null 2>&1 || { echo "[run.sh] WARNING: conda not in PATH; skipping activation ($target_env)" >&2; return 0; }
    if ! type -t conda >/dev/null 2>&1 || ! conda info --base >/dev/null 2>&1; then
        if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
            # shellcheck disable=SC1091
            . "$HOME/miniconda3/etc/profile.d/conda.sh"
        else
            base_dir="$(conda info --base 2>/dev/null || true)"
            [ -n "$base_dir" ] && . "$base_dir/etc/profile.d/conda.sh" 2>/dev/null || true
        fi
    fi
    local current_env
    current_env=$(conda info --json 2>/dev/null | python -c "import sys,json; print(json.load(sys.stdin).get('active_prefix_name',''))" 2>/dev/null || echo "")
    if [ "$current_env" != "$target_env" ]; then
        echo "[run.sh] Activating conda env $target_env" >&2
        conda activate "$target_env" || echo "[run.sh] WARNING: failed to activate $target_env" >&2
    else
        echo "[run.sh] Conda env $target_env already active" >&2
    fi
}

# Pure bash/awk extraction of required keys (no PyYAML dependency)
# NOTE: This is a lightweight parser tailored to current config structure; not a general YAML parser.
if [ -r "${CONFIG_FILE}" ]; then
    eval "$(awk '
    function trim(s){gsub(/^ +| +$/,"",s);return s}
    BEGIN{
        in_runtime=0; collecting_flags=0; collecting_modules=0;
    }
    /^[\t ]*$/ {next}
    /^[ ]*#/ {next}
    # Detect runtime section start
    /^runtime:[ ]*$/ {in_runtime=1; next}
    # New top-level key ends runtime section
    /^[^ \t]/ && $0 !~ /^runtime:/ {in_runtime=0}
    {
        line=$0
        if(collecting_flags){
            if(line ~ /^[ ]{2,}-.*/ || line ~ /^[ ]{2,}[A-Za-z0-9_]+:/){collecting_flags=0} else if(line ~ /^[ ]{2,}.*/){ sub(/^[ ]+/,"",line); compiler_flags=compiler_flags" "line; next } else {collecting_flags=0}
        }
        if(collecting_modules){
            if(line ~ /^[ ]{2,}- /){ m=line; sub(/^[ ]+- /,"",m); load_modules=(load_modules?load_modules" "m:m); next } else if(line ~ /^[ ]{2,}[A-Za-z0-9_]+:/){collecting_modules=0} else if(line ~ /^[^ ]/){collecting_modules=0}
        }
        if(!in_runtime){
            if(line ~ /^case:/){sub(/case:/,"",line); case_val=trim(line)}
            else if(line ~ /^output_path:/){sub(/output_path:/,"",line); out_path=trim(line)}
        } else {
            if(line ~ /^[ ]+conda_env:/){sub(/^[ ]+conda_env:/,"",line); env=trim(line)}
            else if(line ~ /^[ ]+cpus:/){sub(/^[ ]+cpus:/,"",line); cpus=trim(line)}
            else if(line ~ /^[ ]+account:/){sub(/^[ ]+account:/,"",line); acct=trim(line)}
            else if(line ~ /^[ ]+partition:/){sub(/^[ ]+partition:/,"",line); part=trim(line)}
            # parallel_time removed (always parallel)
            else if(line ~ /^[ ]+compiler:/){sub(/^[ ]+compiler:/,"",line); compiler=trim(line)}
            else if(line ~ /^[ ]+compiler_flags:/){
                 if(index(line,">-")>0){ collecting_flags=1; next } else { sub(/^[ ]+compiler_flags:/,"",line); compiler_flags=trim(line) }
            }
            else if(line ~ /^[ ]+module_use:/){sub(/^[ ]+module_use:/,"",line); module_use=trim(line)}
            else if(line ~ /^[ ]+load_modules:/){ collecting_modules=1 }
        }
    }
    END{
        # Normalize compiler_flags; escape embedded double quotes for shell eval
        gsub(/^[ ]+|[ ]+$/,"",compiler_flags); gsub(/"/,"\\\"",compiler_flags)
    gsub(/"/,"\\\"",env); gsub(/"/,"\\\"",load_modules); gsub(/"/,"\\\"",compiler); gsub(/"/,"\\\"",module_use); gsub(/"/,"\\\"",acct); gsub(/"/,"\\\"",part)
        gsub(/"/,"\\\"",cpus); gsub(/"/,"\\\"",case_val); gsub(/"/,"\\\"",out_path)
        print "RUNTIME_CONDA_ENV=\"" env "\""
        print "RUNTIME_MODULES=\"" load_modules "\""
        print "RUNTIME_COMPILER=\"" compiler "\""
        print "RUNTIME_COMPILER_FLAGS=\"" compiler_flags "\""
        print "RUNTIME_CPUS=\"" cpus "\""
        print "CONFIG_CASE=\"" case_val "\""
        print "CONFIG_OUTPUT_PATH=\"" out_path "\""
        print "RUNTIME_MODULE_USE=\"" module_use "\""
        print "RUNTIME_ACCOUNT=\"" acct "\""
        print "RUNTIME_PARTITION=\"" part "\""
    }
    ' "${CONFIG_FILE}")"
else
    echo "[run.sh] WARNING: Config not readable (permission?) falling back to defaults" >&2
fi

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

JOB_NAME=""   # derive later from config if empty (TC.<case>)
TIME_LIMIT=""  # include only if provided via CLI
PARTITION=""   # include only if provided via CLI
CPUS=""        # include only if provided
ACCOUNT=""     # include only if provided
OUTPUT_PREFIX=""  # derive from case if empty

while [[ $# -gt 0 ]]; do
    case "$1" in
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
echo "[run.sh] Conda env: ${RUNTIME_CONDA_ENV:-<none>}" >&2
echo "[run.sh] Case: ${CONFIG_CASE:-<none>}" >&2
echo "[run.sh] Output path: ${CONFIG_OUTPUT_PATH:-<none>}" >&2
echo "[run.sh] Runtime cpus (config): ${RUNTIME_CPUS:-<none>}" >&2
echo "[run.sh] Module use path: ${RUNTIME_MODULE_USE:-<none>}" >&2
echo "[run.sh] Slurm account (config): ${RUNTIME_ACCOUNT:-<none>}" >&2
echo "[run.sh] Slurm partition (config): ${RUNTIME_PARTITION:-<none>}" >&2

# Derive defaults after CLI parsing
if [ -z "$JOB_NAME" ]; then
    if [ -n "$PRIMARY_CASE" ]; then JOB_NAME="TC.${PRIMARY_CASE}"; else JOB_NAME="TC.pipeline"; fi
fi
if [ -z "$OUTPUT_PREFIX" ]; then
    if [ -n "$PRIMARY_CASE" ]; then OUTPUT_PREFIX="TC.${PRIMARY_CASE}"; else OUTPUT_PREFIX="tc_job"; fi
fi
if [ -z "$CPUS" ] && [ -n "${RUNTIME_CPUS:-}" ]; then CPUS="$RUNTIME_CPUS"; fi
if [ -z "$ACCOUNT" ] && [ -n "${RUNTIME_ACCOUNT:-}" ]; then ACCOUNT="$RUNTIME_ACCOUNT"; fi
if [ -z "$PARTITION" ] && [ -n "${RUNTIME_PARTITION:-}" ]; then PARTITION="$RUNTIME_PARTITION"; fi
export TC_CPUS="${CPUS:-1}"

SUBMIT_FILE="submit.sh"
echo "[run.sh] Generating $SUBMIT_FILE for sbatch (job=${JOB_NAME})" >&2
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
    echo "RUNTIME_MODULE_USE=\"${RUNTIME_MODULE_USE}\""
    echo "RUNTIME_ACCOUNT=\"${RUNTIME_ACCOUNT}\""
    echo "RUNTIME_PARTITION=\"${RUNTIME_PARTITION}\""
    echo 'ensure_conda_env() {'
    echo '  local target_env="$1"'
    echo '  [ -z "$target_env" ] && return 0'
    echo '  if ! type -t conda >/dev/null 2>&1; then return 0; fi'
    echo '  __conda_setup="$(conda shell.bash hook 2>/dev/null)" || true'
    echo '  [ -n "${__conda_setup}" ] && eval "${__conda_setup}"'
    echo '  if ! conda activate "$target_env" >/dev/null 2>&1; then'
    echo '    # Retry noisy (do not fail job immediately; caller can decide)'
    echo '    if ! conda activate "$target_env"; then'
    echo '      echo "[submit][WARN] Failed to activate conda env $target_env" >&2'
    echo '      return 1'
    echo '    fi'
    echo '  fi'
    echo '  return 0'
    echo '}'
    echo 'load_modules() { local modules_line="$1"; [ -z "$modules_line" ] && return 0; module purge || true; for m in $modules_line; do module load "$m"; done; }'
    echo 'echo "[submit] Starting job on $(date)"'
    echo 'module purge || true'
    echo '[ -n "${RUNTIME_MODULE_USE}" ] && echo "[submit] module use ${RUNTIME_MODULE_USE}" && module use "${RUNTIME_MODULE_USE}"'
    echo 'set +u  # allow conda hook to reference unset vars'
    echo 'ensure_conda_env "${RUNTIME_CONDA_ENV}"'
    echo 'set -u'
    echo 'echo "[submit] Activated conda env: ${RUNTIME_CONDA_ENV}"'
    echo 'python "${SCRIPT_DIR}/tc_algorithm.py"'
    echo 'load_modules "${RUNTIME_MODULES}"'
    echo 'echo "[submit] Loaded modules: ${RUNTIME_MODULES}"'
    echo '( cd "${SCRIPT_DIR}" && ./tracking.sh )'
    echo 'module purge || true'
    echo 'set +u'
    echo 'ensure_conda_env "${RUNTIME_CONDA_ENV}"'
    echo 'set -u'
    echo 'echo "[submit] Re-activated conda env: ${RUNTIME_CONDA_ENV}"'
    echo 'python "${SCRIPT_DIR}/TC_lifetime.py"'
    echo 'echo "[submit] Finished at $(date)"'
} > "$SUBMIT_FILE"
chmod +x "$SUBMIT_FILE"
echo "[run.sh] submit.sh created. Submit with: sbatch $SUBMIT_FILE" >&2
exit 0

