#/bin/bash

hostdir=`pwd`/tracking/

# Read cases from config.yaml
cases=$(awk '/cases:/ {flag=1; next} /^[[:space:]]*-/ && flag {print $2} /^[^[:space:]]/ && flag && !/cases:/ {flag=0}' config.yaml)
outpath=$(awk -F': ' '/output_path:/ {print $2}' config.yaml)

for expn in $cases; do
    expfolder="${outpath}/${expn}"
    expname=$expn
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

    # Create the ctl file
    cd ${outdir}
    cp ${hostdir}/DATA/irt_objects_mask.ctl ${hostdir}/DATA/irt_tracks_mask.ctl .
    yr="2017"
    mo="09"
    dy="07"
    ddMMyyyy=$(LC_TIME=en_US.utf8 date +%d%b%Y -d "${yr}-${mo}-${dy}")
    sed -i "s/ddMMyyyy/${ddMMyyyy}/g" irt_objects_mask.ctl
    sed -i "s/ddMMyyyy/${ddMMyyyy}/g" irt_tracks_mask.ctl
    cd ${hostdir}

done
