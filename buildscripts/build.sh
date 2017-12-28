#!/bin/bash

source activate $CONDA_ENV

# install Numba in a directory to avoid import conflict
mkdir req_install
pushd req_install
git clone https://github.com/IntelLabs/numba
pushd numba
git checkout hpat_req
python setup.py install
popd
popd

pushd parquet_reader
PREFIX=$CONDA_PREFIX make
PREFIX=$CONDA_PREFIX make install
popd

# build HPAT
DAALROOT=$CONDA_PREFIX HDF5_DIR=$CONDA_PREFIX python setup.py install
