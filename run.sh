#!/bin/bash
# Bootstrap/configuration parsing always runs in forge, including on login nodes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
case "${1:-}" in
  doctor|inspect-input|prepare|run|execute) ;;
  *) set -- prepare "$@" ;;
esac
exec conda run --no-capture-output -n forge python -m tcflow "$@"
