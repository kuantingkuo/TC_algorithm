#!/bin/bash

exp=RCE
if [[ "$exp" == "RCE" || "$exp" == "AQUA" ]]; then # for global
    ln -sf irt_objects_mask_all.ctl irt_objects_mask.ctl
    ln -sf irt_tracks_mask_all.ctl irt_tracks_mask.ctl 
    ln -sf lsm_all.ctl lsm.ctl
    ln -sf lsm_aqua.dat lsm.dat
    ln -sf TC_all.ctl TC.ctl
else
    ln -sf irt_objects_mask_mid.ctl irt_objects_mask.ctl
    ln -sf irt_tracks_mask_mid.ctl irt_tracks_mask.ctl 
    ln -sf lsm_mid.ctl lsm.ctl
    ln -sf lsm_mid.dat lsm.dat
    ln -sf TC_mid.ctl TC.ctl
fi
