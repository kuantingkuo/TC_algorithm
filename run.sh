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

module purge
module use /home/j07cyt00/codecs/modulefiles
module load rcec/stack-impi netcdf-fortran
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

