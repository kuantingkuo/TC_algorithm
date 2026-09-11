#!/bin/bash
# Bootstrap/configuration parsing always runs in forge, including on login nodes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# HPC module stacks may export a PYTHONPATH for a different Python ABI. In
# particular, F1's Spack stack can expose Python 3.10 packages to forge's
# Python 3.13. The workflow only needs this repository on PYTHONPATH.
unset PYTHONHOME
export PYTHONNOUSERSITE=1
export PYTHONPATH="${SCRIPT_DIR}"
case "${1:-}" in
  doctor|inspect-input|prepare|run|execute) ;;
  *) set -- prepare "$@" ;;
esac
exec conda run --no-capture-output -n forge python -m tcflow "$@"
