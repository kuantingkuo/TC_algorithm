#!/bin/bash
# Compatibility entry point; prefer the frozen workflow's track stage.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
CASE_ARGS=()
[ -z "${1:-}" ] || CASE_ARGS=(--case "$1")
exec conda run --no-capture-output -n forge python -m tcflow.stages track --config "${TC_CONFIG:-${CONFIG_FILE:-${SCRIPT_DIR}/config.yaml}}" "${CASE_ARGS[@]}"
