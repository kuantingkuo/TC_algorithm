#!/bin/bash
#SBATCH --job-name=0529          # Specify job name
#SBATCH --partition=shared     # Specify partition name
#SBATCH --ntasks=1             # Specify max. number of tasks to be invoked
#SBATCH --cpus-per-task=3      # Specify number of CPUs per task
#SBATCH --time=08:00:00        # Set a limit on the total run time
#SBATCH --account=bm0982       # Charge resources on this project account
#SBATCH --output=track0529.o%j    # File name for standard output
#SBATCH --error=track0529.o%j     # File name for standard error output

set -euo pipefail

DATENPFAD=${TRACKING_DATA_DIR:-${PWD}/../tracking_data}
BIN_DIR=${TRACKING_BIN_DIR:-${PWD}}
LOCK_FILE=${TRACKING_LOCK_FILE:-/tmp/tc_algorithm_compile.lock}

mkdir -p "${DATENPFAD}"

binaries_ready() {
    [ -x "${BIN_DIR}/irt_objects_release.x" ] && \
    [ -x "${BIN_DIR}/irt_advection_field_release.x" ] && \
    [ -x "${BIN_DIR}/irt_tracks_release.x" ] && \
    [ -x "${BIN_DIR}/irt_trackmask_release.x" ] && \
    [ -x "${BIN_DIR}/irt_tracklinks_release.x" ]
}

exec 200>"${LOCK_FILE}"
flock -x 200
trap 'flock -u 200' EXIT

if ! binaries_ready; then
    (cd "${BIN_DIR}" && bash ./compile.sh)
fi

cp "${BIN_DIR}/irt_parameters.f90" "${DATENPFAD}/"
cd "${DATENPFAD}"

"${BIN_DIR}/irt_objects_release.x" 1
"${BIN_DIR}/irt_tracks_release.x"
sort -n -k2 irt_tracks_nohead_output.txt > irt_tracks_sorted.txt
"${BIN_DIR}/irt_trackmask_release.x"
"${BIN_DIR}/irt_tracklinks_release.x"
