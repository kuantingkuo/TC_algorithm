#!/bin/bash
set -euo pipefail

hostdir="$(pwd)/tracking/"

# Priority: CLI arg -> env var -> config.yaml
case_val="${1:-${TC_CASE:-}}"
if [ -z "$case_val" ]; then
    case_val=$(awk -F': ' '/^case:/ {print $2}' config.yaml | head -n1)
fi
if [ -z "$case_val" ]; then
    echo "[tracking.sh] ERROR: Missing required case (cli/env/config)." >&2
    exit 1
fi

outpath=$(awk -F': ' '/output_path:/ {print $2}' config.yaml)
compile_lock_file=$(awk -F': ' '/^[[:space:]]+compile_lock_file:/ {print $2}' config.yaml | head -n1)
if [ -z "${compile_lock_file}" ]; then
    compile_lock_file="/tmp/tc_algorithm_compile.lock"
fi

expfolder="${outpath}/${case_val}"
expname="$case_val"
TCdata="${expfolder}/${expname}.TC.nc"
build_meta_file="${expfolder}/.irt_build_dir"

track_workspace="./tracking_data_${case_val}"
cd "${hostdir}"
mkdir -p "${track_workspace}"
rm -rf "${track_workspace}"/*
ln -sf "${TCdata}" "${track_workspace}/TC.nc"

tracking_bin_dir="${hostdir}/tracking2"
if [ -f "${build_meta_file}" ]; then
    read -r tracking_bin_dir < "${build_meta_file}"
fi

TRACKING_DATA_DIR="${hostdir}/${track_workspace#./}" \
TRACKING_BIN_DIR="${tracking_bin_dir}" \
TRACKING_LOCK_FILE="${compile_lock_file}" \
"${hostdir}/tracking2/iterate.sh"

outdir="${outpath}/${expname}"
mkdir -p "${outdir}"
cd "${track_workspace}"
cp irt_objects_mask.dat irt_objects_output.txt "${outdir}"
cp irt_tracks_mask.dat irt_tracks_output.txt "${outdir}"
cp irt_tracklinks_output.txt "${outdir}"
