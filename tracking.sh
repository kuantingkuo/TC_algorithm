#!/bin/bash

hostdir="$(pwd)/tracking/"

# Require singular 'case' key in config.yaml
case_val=$(awk -F': ' '/^case:/ {print $2}' config.yaml | head -n1)
if [ -z "$case_val" ]; then
    echo "[tracking.sh] ERROR: Missing required 'case:' key in config.yaml" >&2
    exit 1
fi
outpath=$(awk -F': ' '/output_path:/ {print $2}' config.yaml)
expfolder="${outpath}/${case_val}"
expname=$case_val
TCdata="${expfolder}/${expname}.TC.nc"
echo ${TCdata}

    # Prepare input/output folder
    cd ${hostdir}
    if [ ! -d ./tracking_data ]; then mkdir ./tracking_data; fi
    rm -rf ./tracking_data/*
    ln -sf ${TCdata} ./tracking_data/TC.nc
    #ln -sf /data/W.eddie/SPCAM/LANDmask.dat ./tracking_data/lsm.dat

    # Run tracking
    cd tracking2
    ./iterate.sh
    cd ${hostdir}

    # Copy the results
    outdir="${outpath}/${expname}"
    mkdir -p ${outdir}
    cd tracking_data
    cp irt_objects_mask.dat irt_objects_output.txt ${outdir}
    cp irt_tracks_mask.dat irt_tracks_output.txt ${outdir}
    cp irt_tracklinks_output.txt ${outdir}
    cd ${hostdir}

#    # Create the ctl file
#    cd ${outdir}
#    cp ${hostdir}/DATA/irt_objects_mask.ctl ${hostdir}/DATA/irt_tracks_mask.ctl .
#    yr="2017"
#    mo="09"
#    dy="07"
#    ddMMyyyy=$(LC_TIME=en_US.utf8 date +%d%b%Y -d "${yr}-${mo}-${dy}")
#    sed -i "s/ddMMyyyy/${ddMMyyyy}/g" irt_objects_mask.ctl
#    sed -i "s/ddMMyyyy/${ddMMyyyy}/g" irt_tracks_mask.ctl
    cd ${hostdir}


