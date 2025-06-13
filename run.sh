#!/bin/bash

workdir=${PWD}

module purge
current_env=$(conda info --json | python -c "import sys, json; print(json.load(sys.stdin)['active_prefix_name'])")
if [ "$current_env" != "forge" ]; then
    __conda_setup="$('conda' 'shell.bash' 'hook' 2> /dev/null)"
    eval "$__conda_setup"
    conda activate forge
fi

python tc_algorithm.py

# Reminder: Set up your environment modules according to your system configuration.
#module purge
#module use /home/j07cyt00/codecs/modulefiles
#module load rcec/stack-impi netcdf-fortran
module load Intel-oneAPI-2022.1
module load szip/2.1.1 hdf5/1.12.0 netcdf/4.7.4 mpi/2021.5.0 pnetcdf/1.12.2
cd $workdir
./tracking.sh

cd $workdir
module purge
current_env=$(conda info --json | python -c "import sys, json; print(json.load(sys.stdin)['active_prefix_name'])")
if [ "$current_env" != "forge" ]; then
    __conda_setup="$('conda' 'shell.bash' 'hook' 2> /dev/null)"
    eval "$__conda_setup"
    conda activate forge
fi
python TC_lifetime.py

