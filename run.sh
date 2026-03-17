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
RUNTIME_ENV_MANAGER=""
RUNTIME_ENV_NAME=""
RUNTIME_MODULES=""
RUNTIME_COMPILER=""
RUNTIME_COMPILER_FLAGS=""
RUNTIME_MODULE_USE=""  # optional additional modulefiles path
RUNTIME_ACCOUNT=""  # optional default Slurm account
RUNTIME_PARTITION=""  # optional default Slurm partition
CONFIG_CASE=""
CONFIG_OUTPUT_PATH=""

# Lightweight runtime validation helper for current shell.
ensure_python_env() {
    local env_manager="$1"
    local env_name="$2"

    case "$env_manager" in
        ""|none|current|system)
            return 0
            ;;
        conda)
            command -v conda >/dev/null 2>&1 || { echo "[run.sh] WARNING: conda not in PATH; skipping activation (${env_name:-<none>})" >&2; return 0; }
            ;;
        micromamba)
            command -v micromamba >/dev/null 2>&1 || { echo "[run.sh] WARNING: micromamba not in PATH; skipping activation (${env_name:-<none>})" >&2; return 0; }
            ;;
        *)
            echo "[run.sh] WARNING: unknown env manager '$env_manager'; skipping activation" >&2
            return 0
            ;;
    esac

    [ -n "$env_name" ] || {
        echo "[run.sh] WARNING: env_manager is '$env_manager' but env_name is empty; skipping activation" >&2
        return 0
    }
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
            if(line ~ /^[ ]+env_manager:/){sub(/^[ ]+env_manager:/,"",line); env_manager=trim(line)}
            else if(line ~ /^[ ]+env_name:/){sub(/^[ ]+env_name:/,"",line); env_name=trim(line)}
            else if(line ~ /^[ ]+conda_env:/){sub(/^[ ]+conda_env:/,"",line); legacy_conda_env=trim(line)}
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
        if(env_name == "" && legacy_conda_env != "") env_name = legacy_conda_env
        if(env_manager == "" && legacy_conda_env != "") env_manager = "conda"
        # Normalize compiler_flags; escape embedded double quotes for shell eval
        gsub(/^[ ]+|[ ]+$/,"",compiler_flags); gsub(/"/,"\\\"",compiler_flags)
    gsub(/"/,"\\\"",env_manager); gsub(/"/,"\\\"",env_name); gsub(/"/,"\\\"",load_modules); gsub(/"/,"\\\"",compiler); gsub(/"/,"\\\"",module_use); gsub(/"/,"\\\"",acct); gsub(/"/,"\\\"",part)
        gsub(/"/,"\\\"",cpus); gsub(/"/,"\\\"",case_val); gsub(/"/,"\\\"",out_path)
        print "RUNTIME_ENV_MANAGER=\"" env_manager "\""
        print "RUNTIME_ENV_NAME=\"" env_name "\""
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
    if ! type -t module >/dev/null 2>&1; then
        echo "[run.sh] WARNING: module command not available; skipping module load" >&2
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
echo "[run.sh] Env manager: ${RUNTIME_ENV_MANAGER:-<none>}" >&2
echo "[run.sh] Env name: ${RUNTIME_ENV_NAME:-<none>}" >&2
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
    echo "RUNTIME_ENV_MANAGER=\"${RUNTIME_ENV_MANAGER}\""
    echo "RUNTIME_ENV_NAME=\"${RUNTIME_ENV_NAME}\""
    echo "RUNTIME_MODULES=\"${RUNTIME_MODULES}\""
    echo "RUNTIME_MODULE_USE=\"${RUNTIME_MODULE_USE}\""
    echo "RUNTIME_ACCOUNT=\"${RUNTIME_ACCOUNT}\""
    echo "RUNTIME_PARTITION=\"${RUNTIME_PARTITION}\""
    echo 'module_available() {'
    echo '  type -t module >/dev/null 2>&1'
    echo '}'
    echo 'activate_python_env() {'
    echo '  local env_manager="$1"'
    echo '  local env_name="$2"'
    echo '  case "$env_manager" in'
    echo '    ""|none|current|system)'
    echo '      echo "[submit] Using current shell Python environment"'
    echo '      return 0'
    echo '      ;;'
    echo '    conda)'
    echo '      if ! command -v conda >/dev/null 2>&1; then'
    echo '        echo "[submit][WARN] conda not available; skipping activation for ${env_name:-<none>}" >&2'
    echo '        return 0'
    echo '      fi'
    echo '      [ -n "$env_name" ] || { echo "[submit][WARN] env_name is empty for conda; skipping activation" >&2; return 0; }'
    echo '      __conda_setup="$(conda shell.bash hook 2>/dev/null)" || true'
    echo '      if [ -z "${__conda_setup}" ]; then'
    echo '        base_dir="$(conda info --base 2>/dev/null || true)"'
    echo '        [ -n "$base_dir" ] && [ -f "$base_dir/etc/profile.d/conda.sh" ] && . "$base_dir/etc/profile.d/conda.sh"'
    echo '      else'
    echo '        eval "${__conda_setup}"'
    echo '      fi'
    echo '      if ! conda activate "$env_name" >/dev/null 2>&1; then'
    echo '        if ! conda activate "$env_name"; then'
    echo '          echo "[submit][WARN] Failed to activate conda env $env_name" >&2'
    echo '          return 1'
    echo '        fi'
    echo '      fi'
    echo '      echo "[submit] Activated conda env: $env_name"'
    echo '      return 0'
    echo '      ;;'
    echo '    micromamba)'
    echo '      if ! command -v micromamba >/dev/null 2>&1; then'
    echo '        echo "[submit][WARN] micromamba not available; skipping activation for ${env_name:-<none>}" >&2'
    echo '        return 0'
    echo '      fi'
    echo '      [ -n "$env_name" ] || { echo "[submit][WARN] env_name is empty for micromamba; skipping activation" >&2; return 0; }'
    echo '      __micromamba_setup="$(micromamba shell hook --shell bash 2>/dev/null)" || true'
    echo '      [ -n "${__micromamba_setup}" ] && eval "${__micromamba_setup}"'
    echo '      if ! micromamba activate "$env_name" >/dev/null 2>&1; then'
    echo '        if ! micromamba activate "$env_name"; then'
    echo '          echo "[submit][WARN] Failed to activate micromamba env $env_name" >&2'
    echo '          return 1'
    echo '        fi'
    echo '      fi'
    echo '      echo "[submit] Activated micromamba env: $env_name"'
    echo '      return 0'
    echo '      ;;'
    echo '    *)'
    echo '      echo "[submit][WARN] Unknown env manager: $env_manager; using current shell environment" >&2'
    echo '      return 0'
    echo '      ;;'
    echo '  esac'
    echo '}'
    echo 'load_modules() {'
    echo '  local modules_line="$1"'
    echo '  [ -z "$modules_line" ] && return 0'
    echo '  if ! module_available; then'
    echo '    echo "[submit][WARN] module command not available; skipping module load" >&2'
    echo '    return 0'
    echo '  fi'
    echo '  module purge || true'
    echo '  for m in $modules_line; do module load "$m"; done'
    echo '}'
    echo 'echo "[submit] Starting job on $(date)"'
    echo 'if module_available; then module purge || true; fi'
    echo 'if [ -n "${RUNTIME_MODULE_USE}" ]; then'
    echo '  if module_available; then'
    echo '    echo "[submit] module use ${RUNTIME_MODULE_USE}"'
    echo '    module use "${RUNTIME_MODULE_USE}"'
    echo '  else'
    echo '    echo "[submit][WARN] module use requested but module command is unavailable" >&2'
    echo '  fi'
    echo 'fi'
    echo 'set +u  # allow environment hooks to reference unset vars'
    echo 'activate_python_env "${RUNTIME_ENV_MANAGER}" "${RUNTIME_ENV_NAME}"'
    echo 'set -u'
    echo 'python "${SCRIPT_DIR}/tc_algorithm.py"'
    echo 'load_modules "${RUNTIME_MODULES}"'
    echo 'echo "[submit] Loaded modules: ${RUNTIME_MODULES}"'
    echo '( cd "${SCRIPT_DIR}" && ./tracking.sh )'
    echo 'if module_available; then module purge || true; fi'
    echo 'set +u'
    echo 'activate_python_env "${RUNTIME_ENV_MANAGER}" "${RUNTIME_ENV_NAME}"'
    echo 'set -u'
    echo 'python "${SCRIPT_DIR}/TC_lifetime.py"'
    echo 'echo "[submit] Finished at $(date)"'
} > "$SUBMIT_FILE"
chmod +x "$SUBMIT_FILE"
echo "[run.sh] submit.sh created. Submit with: sbatch $SUBMIT_FILE" >&2
exit 0

