.. _build_bodo_source:


Building Bodo from Source
-------------------------

On Mac/Linux
~~~~~~~~~~~~~~
We use `Anaconda <https://www.anaconda.com/download/>`_ distribution of
Python for setting up Bodo. These commands install Bodo and its dependencies
such as Numba on Ubuntu Linux::

    # Linux: wget https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh
    # Mac: wget https://repo.continuum.io/miniconda/Miniconda3-latest-MacOSX-x86_64.sh -O miniconda.sh
    chmod +x miniconda.sh
    ./miniconda.sh -b
    export PATH=$HOME/miniconda3/bin:$PATH
    conda create -n DEV python=3.8 numpy scipy pandas=1.1.0 boost-cpp cmake h5py mpich mpi -c conda-forge
    source activate DEV
    # Linux: conda install gcc_linux-64 gxx_linux-64 gfortran_linux-64 -c conda-forge
    # Mac: conda install clang_osx-64 clangxx_osx-64 gfortran_osx-64 -c conda-forge
    # NOTE: for development/debugging purposes, it's best to install Numba from source instead
    conda install numba=0.51.0 -c conda-forge
    conda install -c conda-forge hdf5=*=*mpich* pyarrow=1.0.1 pymysql sqlalchemy
    # If you get the error "zsh: no matches found: hdf5=*=*mpich*" (typically on Mac), try the following instead:
    # conda install -c conda-forge "hdf5=*=*mpich*" pyarrow=1.0.1 pymysql sqlalchemy
    # The following is required for s3 related development and tests
    # conda install -c conda-forge boto3 botocore s3fs
    git clone https://github.com/Bodo-inc/Bodo.git
    cd Bodo
    # build Bodo
    python setup.py develop

For HDFS related development, use the :ref:`docker image <docker-images>`.

On Windows
~~~~~~~~~~

* Install Visual Studio Community 2017 (15.9.18)
* From the Visual Studio installer, install following individual components::

    Windows 10 SDK (10.0.17763.0)
    Windows Universal CRT SDK
    VC++ 2015.3 v14.00 (v140) toolset for desktop

* Install `Miniconda for Windows <https://repo.continuum.io/miniconda/Miniconda3-latest-Windows-x86_64.exe>`_.
* Start 'Anaconda (Miniconda3) prompt'
* Setup the Conda environment in Anaconda Prompt::

    conda create -n DEV python=3.8 numpy scipy pandas=1.1.0 boost-cpp cmake h5py -c conda-forge
    source activate DEV
    conda install numba=0.51.0 -c conda-forge
    conda install vc vs2015_runtime vs2015_win-64
    conda install -c defaults -c intel impi_rt impi-devel
    conda install -c conda-forge pyarrow=1.0.1
    git clone https://github.com/Bodo-inc/Bodo.git
    cd Bodo
    # build Bodo
    python setup.py develop


Troubleshooting Windows Build
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* HDF5 is currently not supported for windows version of Bodo.
* Testing for windows version is currently not available due to package conflicts.
* It might be necessary to remove all the different visual studio versions installed and fresh start above instruction.


Running Example/Test
~~~~~~~~~~~~~~~~~~~~~~~~~
A command line for running the Pi example on 4 cores::

    mpiexec -n 4 python examples/pi.py

If you run into gethostbyname failed error, try
`this fix <https://stackoverflow.com/questions/23112515/mpich2-gethostbyname-failed>`_.

Running unit tests::

    conda install pytest
    pytest -x -s -v -m "not slow"

To run s3 related unit tests, in addition::
    
    export AWS_ACCESS_KEY_ID=bodotest1
    export AWS_SECRET_ACCESS_KEY=bodosecret1

The two environment variables will be read in `conftest.py <https://github.com/Bodo-inc/Bodo/blob/master/bodo/tests/conftest.py>`_
and set for `minio <https://min.io/?gclid=Cj0KCQiAsvTxBRDkARIsAH4W_j9rNeSft9zVArxg1Zo4RAfXS31dC9Aq-amIigRAT_yAPQbKdU0RvD4aAv0UEALw_wcB>`_.

In case of issues, reinstalling in a new conda environment is recommended.

To run HDFS related unit tests, use the :ref:`docker image <docker-images>`.

Other useful packages for development::

    conda install pytest sphinx pylint jupyter xlrd xlsxwriter mpi4py ipyparallel matplotlib jupyterlab aws-sdk-cpp
