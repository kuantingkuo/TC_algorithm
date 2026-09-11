#!/bin/bash
# Manual execution only: each invocation must receive its own empty workspace.
set -euo pipefail
: "${TRACKING_DATA_DIR:?Set a private TRACKING_DATA_DIR containing TC.nc}"
: "${TRACKING_BIN_DIR:?Set TRACKING_BIN_DIR to a completed build}"
BIN_DIR="$(cd "$TRACKING_BIN_DIR" && pwd)"
cd "$TRACKING_DATA_DIR"
[ -f TC.nc ] || { echo 'TC.nc is missing' >&2; exit 1; }
[ ! -e irt_objects_mask.dat ] || { echo 'Workspace already contains results; choose a new workspace' >&2; exit 1; }
cp "$BIN_DIR/irt_parameters.f90" .
"$BIN_DIR/irt_objects_release.x" 1
"$BIN_DIR/irt_tracks_release.x"
LC_ALL=C sort -n -k2 irt_tracks_nohead_output.txt > irt_tracks_sorted.txt
"$BIN_DIR/irt_trackmask_release.x"
"$BIN_DIR/irt_tracklinks_release.x"
