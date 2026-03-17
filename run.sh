#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${SCRIPT_DIR}/config.yaml}"

echo "[run.sh] Using config: ${CONFIG_FILE}" >&2
[ -f "${CONFIG_FILE}" ] || { echo "[run.sh] FATAL: config.yaml not found at ${CONFIG_FILE}" >&2; exit 1; }

RUNTIME_ENV_MANAGER=""
RUNTIME_ENV_NAME=""
RUNTIME_MODULES=""
RUNTIME_COMPILER=""
RUNTIME_COMPILER_FLAGS=""
RUNTIME_MODULE_USE=""
RUNTIME_ACCOUNT=""
RUNTIME_PARTITION=""
RUNTIME_CPUS=""
CONFIG_CASE=""
CONFIG_CASES=""
CONFIG_OUTPUT_PATH=""

CASE_OVERRIDES=()
SUBMIT_NOW=0
JOB_NAME=""
TIME_LIMIT=""
PARTITION=""
CPUS=""
ACCOUNT=""
OUTPUT_PREFIX=""
PYTHON_CMD_OVERRIDE=""

parse_config() {
  eval "$(awk '
    function trim(s){gsub(/^ +| +$/, "", s); return s}
    BEGIN{in_runtime=0; collecting_flags=0; collecting_modules=0; collecting_cases=0}
    /^[\t ]*$/ {next}
    /^[ ]*#/ {next}
    /^runtime:[ ]*$/ {in_runtime=1; next}
    /^[^ \t]/ && $0 !~ /^runtime:/ {in_runtime=0}
    {
      line=$0
      if (collecting_flags) {
        if (line ~ /^[ ]{2,}-.*/ || line ~ /^[ ]{2,}[A-Za-z0-9_]+:/) {
          collecting_flags=0
        } else if (line ~ /^[ ]{2,}.*/) {
          sub(/^[ ]+/, "", line)
          compiler_flags=compiler_flags " " line
          next
        } else {
          collecting_flags=0
        }
      }

      if (collecting_modules) {
        if (line ~ /^[ ]{2,}- /) {
          m=line; sub(/^[ ]+- /, "", m)
          load_modules=(load_modules ? load_modules " " m : m)
          next
        } else if (line ~ /^[ ]{2,}[A-Za-z0-9_]+:/ || line ~ /^[^ ]/) {
          collecting_modules=0
        }
      }

      if (!in_runtime) {
        if (collecting_cases) {
          if (line ~ /^[ ]{2}- /) {
            c=line; sub(/^[ ]{2}- /, "", c); c=trim(c)
            cases_val=(cases_val ? cases_val " " c : c)
            next
          } else if (line ~ /^[^ ]/) {
            collecting_cases=0
          }
        }

        if      (line ~ /^case:/)         {sub(/case:/, "", line); case_val=trim(line)}
        else if (line ~ /^cases:[ ]*$/)   {collecting_cases=1}
        else if (line ~ /^output_path:/)  {sub(/output_path:/, "", line); out_path=trim(line)}
      } else {
        if      (line ~ /^[ ]+env_manager:/)   {sub(/^[ ]+env_manager:/, "", line); env_manager=trim(line)}
        else if (line ~ /^[ ]+env_name:/)      {sub(/^[ ]+env_name:/, "", line); env_name=trim(line)}
        else if (line ~ /^[ ]+conda_env:/)     {sub(/^[ ]+conda_env:/, "", line); legacy_conda_env=trim(line)}
        else if (line ~ /^[ ]+cpus:/)          {sub(/^[ ]+cpus:/, "", line); cpus=trim(line)}
        else if (line ~ /^[ ]+account:/)       {sub(/^[ ]+account:/, "", line); acct=trim(line)}
        else if (line ~ /^[ ]+partition:/)     {sub(/^[ ]+partition:/, "", line); part=trim(line)}
        else if (line ~ /^[ ]+compiler:/)      {sub(/^[ ]+compiler:/, "", line); compiler=trim(line)}
        else if (line ~ /^[ ]+compiler_flags:/){if (index(line, ">-")>0) collecting_flags=1; else {sub(/^[ ]+compiler_flags:/, "", line); compiler_flags=trim(line)}}
        else if (line ~ /^[ ]+module_use:/)    {sub(/^[ ]+module_use:/, "", line); module_use=trim(line)}
        else if (line ~ /^[ ]+load_modules:/)  {collecting_modules=1}
      }
    }
    END{
      if (env_name == "" && legacy_conda_env != "") env_name = legacy_conda_env
      if (env_manager == "" && legacy_conda_env != "") env_manager = "conda"

      gsub(/^[ ]+|[ ]+$/, "", compiler_flags)
      gsub(/"/, "\\\"", compiler_flags)
      gsub(/"/, "\\\"", env_manager)
      gsub(/"/, "\\\"", env_name)
      gsub(/"/, "\\\"", load_modules)
      gsub(/"/, "\\\"", compiler)
      gsub(/"/, "\\\"", module_use)
      gsub(/"/, "\\\"", acct)
      gsub(/"/, "\\\"", part)
      gsub(/"/, "\\\"", cpus)
      gsub(/"/, "\\\"", case_val)
      gsub(/"/, "\\\"", cases_val)
      gsub(/"/, "\\\"", out_path)

      print "RUNTIME_ENV_MANAGER=\"" env_manager "\""
      print "RUNTIME_ENV_NAME=\"" env_name "\""
      print "RUNTIME_MODULES=\"" load_modules "\""
      print "RUNTIME_COMPILER=\"" compiler "\""
      print "RUNTIME_COMPILER_FLAGS=\"" compiler_flags "\""
      print "RUNTIME_CPUS=\"" cpus "\""
      print "CONFIG_CASE=\"" case_val "\""
      print "CONFIG_CASES=\"" cases_val "\""
      print "CONFIG_OUTPUT_PATH=\"" out_path "\""
      print "RUNTIME_MODULE_USE=\"" module_use "\""
      print "RUNTIME_ACCOUNT=\"" acct "\""
      print "RUNTIME_PARTITION=\"" part "\""
    }
  ' "${CONFIG_FILE}")"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --case) CASE_OVERRIDES+=("$2"); shift 2 ;;
      --cases)
        IFS=',' read -r -a _batch <<< "$2"
        for c in "${_batch[@]}"; do
          c="${c// /}"
          [ -n "$c" ] && CASE_OVERRIDES+=("$c")
        done
        shift 2
        ;;
      --job-name) JOB_NAME="$2"; shift 2 ;;
      --time) TIME_LIMIT="$2"; shift 2 ;;
      --partition) PARTITION="$2"; shift 2 ;;
      --cpus) CPUS="$2"; shift 2 ;;
      --account) ACCOUNT="$2"; shift 2 ;;
      --output-prefix) OUTPUT_PREFIX="$2"; shift 2 ;;
      --python-cmd) PYTHON_CMD_OVERRIDE="$2"; shift 2 ;;
      --submit) SUBMIT_NOW=1; shift ;;
      *) echo "[run.sh] Unknown argument: $1" >&2; exit 2 ;;
    esac
  done
}

resolve_cases() {
  local primary_case="${CONFIG_CASE}"
  if [ "${#CASE_OVERRIDES[@]}" -gt 0 ]; then
    CASES=("${CASE_OVERRIDES[@]}")
  elif [ -n "${CONFIG_CASES:-}" ]; then
    read -r -a CASES <<< "${CONFIG_CASES}"
  elif [ -n "$primary_case" ]; then
    CASES=("$primary_case")
  else
    echo "[run.sh] ERROR: No case provided. Set 'cases' (preferred) or 'case' in config.yaml, or pass --case/--cases." >&2
    exit 2
  fi
}

apply_runtime_defaults() {
  [ -z "$CPUS" ] && [ -n "${RUNTIME_CPUS:-}" ] && CPUS="$RUNTIME_CPUS"
  [ -z "$ACCOUNT" ] && [ -n "${RUNTIME_ACCOUNT:-}" ] && ACCOUNT="$RUNTIME_ACCOUNT"
  [ -z "$PARTITION" ] && [ -n "${RUNTIME_PARTITION:-}" ] && PARTITION="$RUNTIME_PARTITION"

  if [ -z "$JOB_NAME" ]; then
    if [ "${#CASES[@]}" -eq 1 ]; then
      JOB_NAME="TC.${CASES[0]}"
    else
      JOB_NAME="TC.pipeline"
    fi
  fi

  if [ -z "$OUTPUT_PREFIX" ]; then
    if [ "${#CASES[@]}" -eq 1 ]; then
      OUTPUT_PREFIX="TC.${CASES[0]}"
    else
      OUTPUT_PREFIX="TC.pipeline"
    fi
  fi

  export TC_CPUS="${CPUS:-1}"
}

print_summary() {
  echo "[run.sh] Modules: ${RUNTIME_MODULES:-<none>}" >&2
  echo "[run.sh] Env manager: ${RUNTIME_ENV_MANAGER:-<none>}" >&2
  echo "[run.sh] Env name: ${RUNTIME_ENV_NAME:-<none>}" >&2
  echo "[run.sh] Case: ${CONFIG_CASE:-<none>}" >&2
  echo "[run.sh] Cases (config): ${CONFIG_CASES:-<none>}" >&2
  echo "[run.sh] Output path: ${CONFIG_OUTPUT_PATH:-<none>}" >&2
  echo "[run.sh] Runtime cpus (config): ${RUNTIME_CPUS:-<none>}" >&2
  echo "[run.sh] Module use path: ${RUNTIME_MODULE_USE:-<none>}" >&2
  echo "[run.sh] Slurm account (config): ${RUNTIME_ACCOUNT:-<none>}" >&2
  echo "[run.sh] Slurm partition (config): ${RUNTIME_PARTITION:-<none>}" >&2
  echo "[run.sh] Python override: ${PYTHON_CMD_OVERRIDE:-<auto>}" >&2
  echo "[run.sh] Cases to process: ${CASES[*]}" >&2
}

generate_case_submit() {
  local active_case="$1"
  local submit_file="$2"
  local case_job_name="$3"
  local case_output_prefix="$4"

  mkdir -p "$CONFIG_OUTPUT_PATH/$active_case"
  local log_out_path="$CONFIG_OUTPUT_PATH/$active_case/${case_output_prefix}.out"
  local log_err_path="$CONFIG_OUTPUT_PATH/$active_case/${case_output_prefix}.err"

  cat > "$submit_file" <<EOF
#!/bin/bash
#SBATCH --job-name=${case_job_name}
#SBATCH --nodes=1
$( [ -n "$PARTITION" ] && echo "#SBATCH --partition=${PARTITION}" )
$( [ -n "$CPUS" ] && echo "#SBATCH --cpus-per-task=${CPUS}" )
$( [ -n "$TIME_LIMIT" ] && echo "#SBATCH --time=${TIME_LIMIT}" )
$( [ -n "$ACCOUNT" ] && echo "#SBATCH --account=${ACCOUNT}" )
#SBATCH --output=${log_out_path}
#SBATCH --error=${log_err_path}
set -euo pipefail
SCRIPT_DIR="${SCRIPT_DIR}"
CONFIG_FILE="${CONFIG_FILE}"
RUNTIME_ENV_MANAGER="${RUNTIME_ENV_MANAGER}"
RUNTIME_ENV_NAME="${RUNTIME_ENV_NAME}"
RUNTIME_MODULES="${RUNTIME_MODULES}"
RUNTIME_MODULE_USE="${RUNTIME_MODULE_USE}"
RUNTIME_ACCOUNT="${RUNTIME_ACCOUNT}"
RUNTIME_PARTITION="${RUNTIME_PARTITION}"
PYTHON_CMD_OVERRIDE="${PYTHON_CMD_OVERRIDE}"
TC_CASE="${active_case}"
export TC_CASE

module_available() {
  type -t module >/dev/null 2>&1
}

activate_python_env() {
  local env_manager="\$1"
  local env_name="\$2"
  case "\$env_manager" in
    ""|none|current|system)
      echo "[submit] Using current shell Python environment"
      return 0
      ;;
    conda)
      if ! command -v conda >/dev/null 2>&1; then
        echo "[submit][WARN] conda not available; skipping activation for \${env_name:-<none>}" >&2
        return 0
      fi
      [ -n "\$env_name" ] || { echo "[submit][WARN] env_name is empty for conda; skipping activation" >&2; return 0; }
      __conda_setup="\$(conda shell.bash hook 2>/dev/null)" || true
      if [ -z "\${__conda_setup}" ]; then
        base_dir="\$(conda info --base 2>/dev/null || true)"
        [ -n "\$base_dir" ] && [ -f "\$base_dir/etc/profile.d/conda.sh" ] && . "\$base_dir/etc/profile.d/conda.sh"
      else
        eval "\${__conda_setup}"
      fi
      conda activate "\$env_name" || { echo "[submit][WARN] Failed to activate conda env \$env_name" >&2; return 1; }
      echo "[submit] Activated conda env: \$env_name"
      return 0
      ;;
    micromamba)
      if ! command -v micromamba >/dev/null 2>&1; then
        echo "[submit][WARN] micromamba not available; skipping activation for \${env_name:-<none>}" >&2
        return 0
      fi
      [ -n "\$env_name" ] || { echo "[submit][WARN] env_name is empty for micromamba; skipping activation" >&2; return 0; }
      __micromamba_setup="\$(micromamba shell hook --shell bash 2>/dev/null)" || true
      [ -n "\${__micromamba_setup}" ] && eval "\${__micromamba_setup}"
      micromamba activate "\$env_name" || { echo "[submit][WARN] Failed to activate micromamba env \$env_name" >&2; return 1; }
      echo "[submit] Activated micromamba env: \$env_name"
      return 0
      ;;
    *)
      echo "[submit][WARN] Unknown env manager: \$env_manager; using current shell environment" >&2
      return 0
      ;;
  esac
}

load_modules() {
  local modules_line="\$1"
  [ -z "\$modules_line" ] && return 0
  if ! module_available; then
    echo "[submit][WARN] module command not available; skipping module load" >&2
    return 0
  fi
  module purge || true
  for m in \$modules_line; do module load "\$m"; done
}

resolve_python_cmd() {
  if [ -n "\${PYTHON_CMD_OVERRIDE:-}" ]; then
    if [ -x "\${PYTHON_CMD_OVERRIDE}" ]; then
      echo "\${PYTHON_CMD_OVERRIDE}"
      return 0
    fi
    if command -v "\${PYTHON_CMD_OVERRIDE}" >/dev/null 2>&1; then
      command -v "\${PYTHON_CMD_OVERRIDE}"
      return 0
    fi
    echo "[submit][WARN] PYTHON_CMD_OVERRIDE not usable: \${PYTHON_CMD_OVERRIDE}" >&2
  fi
  if command -v python >/dev/null 2>&1; then command -v python; return 0; fi
  if command -v python3 >/dev/null 2>&1; then command -v python3; return 0; fi
  return 1
}

echo "[submit] Starting job on \$(date) (case=\${TC_CASE})"
if module_available; then module purge || true; fi
if [ -n "\${RUNTIME_MODULE_USE}" ]; then
  if module_available; then
    echo "[submit] module use \${RUNTIME_MODULE_USE}"
    module use "\${RUNTIME_MODULE_USE}"
  else
    echo "[submit][WARN] module use requested but module command is unavailable" >&2
  fi
fi

set +u
activate_python_env "\${RUNTIME_ENV_MANAGER}" "\${RUNTIME_ENV_NAME}"
set -u

PYTHON_BIN="\$(resolve_python_cmd)" || { echo "[submit][FATAL] python/python3 not found in PATH" >&2; exit 127; }
"\${PYTHON_BIN}" "\${SCRIPT_DIR}/tc_algorithm.py" --case "\${TC_CASE}"

load_modules "\${RUNTIME_MODULES}"
echo "[submit] Loaded modules: \${RUNTIME_MODULES}"
( cd "\${SCRIPT_DIR}" && ./tracking.sh "\${TC_CASE}" )

if module_available; then module purge || true; fi
set +u
activate_python_env "\${RUNTIME_ENV_MANAGER}" "\${RUNTIME_ENV_NAME}"
set -u

PYTHON_BIN="\$(resolve_python_cmd)" || { echo "[submit][FATAL] python/python3 not found in PATH" >&2; exit 127; }
"\${PYTHON_BIN}" "\${SCRIPT_DIR}/TC_lifetime.py" --case "\${TC_CASE}"

echo "[submit] Finished at \$(date)"
EOF

  chmod +x "$submit_file"
}

generate_submit_all_wrapper() {
  local wrapper="submit.sh"
  {
    echo "#!/bin/bash"
    echo "set -euo pipefail"
    echo "SCRIPT_DIR=\"\$(cd \"\$(dirname \"\${BASH_SOURCE[0]}\")\" && pwd)\""
    echo "if ! command -v sbatch >/dev/null 2>&1; then"
    echo "  echo \"[submit-all] ERROR: sbatch not found in PATH\" >&2"
    echo "  exit 2"
    echo "fi"
    echo "submit_files=("
    for sf in "${SUBMIT_FILES[@]}"; do
      echo "  \"${sf}\""
    done
    echo ")"
    echo "for sf in \"\${submit_files[@]}\"; do"
    echo "  echo \"[submit-all] Submitting \${sf}\""
    echo "  ( cd \"\${SCRIPT_DIR}\" && sbatch \"\${sf}\" )"
    echo "done"
  } > "$wrapper"

  chmod +x "$wrapper"
}

submit_jobs_if_requested() {
  [ "$SUBMIT_NOW" -eq 1 ] || return 0
  if ! command -v sbatch >/dev/null 2>&1; then
    echo "[run.sh] ERROR: --submit requested but sbatch is not available in PATH." >&2
    exit 2
  fi

  if [ "${#CASES[@]}" -eq 1 ]; then
    echo "[run.sh] Submitting ${SUBMIT_FILES[0]}" >&2
    sbatch "${SUBMIT_FILES[0]}"
  else
    echo "[run.sh] Submitting all jobs via submit.sh" >&2
    bash submit.sh
  fi
}

print_generation_summary() {
  if [ "${#SUBMIT_FILES[@]}" -eq 1 ]; then
    echo "[run.sh] ${SUBMIT_FILES[0]} created. Submit with: sbatch ${SUBMIT_FILES[0]}" >&2
    return
  fi

  echo "[run.sh] Generated ${#SUBMIT_FILES[@]} submit scripts and submit.sh:" >&2
  echo "  - submit.sh (submit all: bash submit.sh)" >&2
  for sf in "${SUBMIT_FILES[@]}"; do
    echo "  - $sf (submit: sbatch $sf)" >&2
  done
}

parse_config
parse_args "$@"
resolve_cases
apply_runtime_defaults
print_summary

[ -n "$CONFIG_OUTPUT_PATH" ] && mkdir -p "$CONFIG_OUTPUT_PATH"

CASES_COUNT="${#CASES[@]}"
SUBMIT_FILES=()
for active_case in "${CASES[@]}"; do
  safe_case="${active_case//[^A-Za-z0-9._-]/_}"
  if [ "$CASES_COUNT" -eq 1 ]; then
    submit_file="submit.sh"
    case_job_name="$JOB_NAME"
    case_output_prefix="$OUTPUT_PREFIX"
  else
    submit_file="submit_${safe_case}.sh"
    case_job_name="${JOB_NAME}.${active_case}"
    case_output_prefix="${OUTPUT_PREFIX}.${active_case}"
  fi

  echo "[run.sh] Generating ${submit_file} for case=${active_case} (job=${case_job_name})" >&2
  generate_case_submit "$active_case" "$submit_file" "$case_job_name" "$case_output_prefix"
  SUBMIT_FILES+=("$submit_file")
done

if [ "$CASES_COUNT" -gt 1 ]; then
  generate_submit_all_wrapper
fi

submit_jobs_if_requested

if [ "$SUBMIT_NOW" -eq 0 ]; then
  print_generation_summary
fi

exit 0
