# Copyright (C) 2024 Bodo Inc. All rights reserved.
"""Worker process to handle compiling and running python functions with
Bodo - note that this module should only be run with MPI.Spawn and not invoked
directly"""

import logging
import os
import sys
import typing as pt

import cloudpickle
import numba

import bodo
from bodo.mpi4py import MPI
from bodo.submit.spawner import ArgMetadata, BodoSQLContextMetadata, debug_msg
from bodo.submit.utils import CommandType, poll_for_barrier
from bodo.submit.worker_state import set_is_worker


def _recv_arg(arg: pt.Union[pt.Any, ArgMetadata], spawner_intercomm: MPI.Intercomm):
    """Receive argument if it is a DataFrame/Series/Index/array value.

    Args:
        arg: argument value or metadata
        spawner_intercomm: spawner intercomm handle

    Returns:
        Any: received function argument
    """
    if isinstance(arg, ArgMetadata):
        if arg.is_broadcast:
            return bodo.libs.distributed_api.bcast(None, root=0, comm=spawner_intercomm)
        else:
            return bodo.libs.distributed_api.scatterv(
                None, root=0, comm=spawner_intercomm
            )

    if isinstance(arg, BodoSQLContextMetadata):
        from bodosql import BodoSQLContext

        tables = {
            tname: _recv_arg(tmeta, spawner_intercomm)
            for tname, tmeta in arg.tables.items()
        }
        return BodoSQLContext(tables, arg.catalog, arg.default_tz)

    # Handle distributed data nested inside tuples
    if isinstance(arg, tuple):
        return tuple(_recv_arg(v, spawner_intercomm) for v in arg)

    return arg


def _send_output(
    res,
    is_distributed: pt.Union[bool, pt.Union[list, tuple]],
    spawner_intercomm: MPI.Intercomm,
):
    """Send function output to spawner. Uses gatherv for distributed data and also
    handles tuples.

    Args:
        res: output to send to spawner
        is_distributed: distribution info for output
        spawner_intercomm: MPI intercomm for spawner
    """

    if isinstance(res, tuple):
        assert isinstance(
            is_distributed, (tuple, list)
        ), "_send_output(): invalid output distributed flags type"
        for val, dist in zip(res, is_distributed):
            _send_output(val, dist, spawner_intercomm)
        return

    if is_distributed:
        # Combine distributed results with gatherv
        bodo.gatherv(res, root=0, comm=spawner_intercomm)
    else:
        if bodo.get_rank() == 0:
            # Send non-distributed results
            spawner_intercomm.send(res, dest=0)


def exec_func_handler(
    comm_world: MPI.Intracomm, spawner_intercomm: MPI.Intercomm, logger: logging.Logger
):
    """Callback to compile and execute the function being sent over
    driver_intercomm by the spawner"""

    # Receive function arguments
    (args, kwargs) = spawner_intercomm.bcast(None, 0)
    args = tuple(_recv_arg(arg, spawner_intercomm) for arg in args)
    kwargs = {name: _recv_arg(arg, spawner_intercomm) for name, arg in kwargs.items()}

    # Receive function dispatcher
    pickled_func = spawner_intercomm.bcast(None, 0)
    debug_worker_msg(logger, "Received pickled pyfunc from spawner.")

    caught_exception = None
    res = None
    func = None
    try:
        func = cloudpickle.loads(pickled_func)
        # ensure that we have a CPUDispatcher to compile and execute code
        assert isinstance(
            func, numba.core.registry.CPUDispatcher
        ), "Unexpected function type"
    except Exception as e:
        logger.error(f"Exception while trying to receive code: {e}")
        # TODO: check that all ranks raise an exception
        # forward_exception(e, comm_world, spawner_intercomm)
        func = None
        caught_exception = e

    if caught_exception is None:
        try:
            # Try to compile and execute it. Catch and share any errors with the spawner.
            debug_worker_msg(logger, "Compiling and executing func")
            res = func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Exception while trying to execute code: {e}")
            caught_exception = e

    poll_for_barrier(spawner_intercomm)
    has_exception = caught_exception is not None
    debug_worker_msg(logger, f"Propagating exception {has_exception=}")
    # Propagate any exceptions
    spawner_intercomm.gather(caught_exception, root=0)

    is_distributed = False
    if func is not None and len(func.signatures) > 0:
        # There should only be one signature compiled for the input function
        sig = func.signatures[0]
        assert sig in func.overloads
        # Extract return value distribution from metadata
        is_distributed = func.overloads[sig].metadata["is_return_distributed"]
    debug_worker_msg(logger, f"Gathering result {is_distributed=}")

    if bodo.get_rank() == 0:
        spawner_intercomm.send(is_distributed, dest=0)

    _send_output(res, is_distributed, spawner_intercomm)


def worker_loop(
    comm_world: MPI.Intracomm, spawner_intercomm: MPI.Intercomm, logger: logging.Logger
):
    """Main loop for the worker to listen and receive commands from driver_intercomm"""
    # Stored last data value received from scatterv/bcast for testing gatherv purposes
    last_received_data = None

    while True:
        debug_worker_msg(logger, "Waiting for command")
        # TODO Change this to a wait that doesn't spin cycles
        # unnecessarily (e.g. see end_py in bodo/dl/utils.py)
        command = spawner_intercomm.bcast(None, 0)
        debug_worker_msg(logger, f"Received command: {command}")

        if command == CommandType.EXEC_FUNCTION.value:
            exec_func_handler(comm_world, spawner_intercomm, logger)
        elif command == CommandType.EXIT.value:
            debug_worker_msg(logger, "Exiting...")
            return
        elif command == CommandType.BROADCAST.value:
            last_received_data = bodo.libs.distributed_api.bcast(
                None, root=0, comm=spawner_intercomm
            )
            debug_worker_msg(logger, "Broadcast done")
        elif command == CommandType.SCATTER.value:
            last_received_data = bodo.libs.distributed_api.scatterv(
                None, root=0, comm=spawner_intercomm
            )
            debug_worker_msg(logger, "Scatter done")
        elif command == CommandType.GATHER.value:
            bodo.libs.distributed_api.gatherv(
                last_received_data, root=0, comm=spawner_intercomm
            )
            debug_worker_msg(logger, "Gather done")
        else:
            raise ValueError(f"Unsupported command '{command}!")


def debug_worker_msg(logger, msg):
    """Add worker number to message and send it to logger"""
    debug_msg(logger, f"Bodo Worker {bodo.get_rank()} {msg}")


if __name__ == "__main__":
    set_is_worker()
    # See comment in spawner about STDIN and MPI_Spawn
    # To allow some way to access stdin for debugging with pdb, the environment
    # variable BODO_WORKER0_INPUT can be set to a pipe, e.g.:
    # Run the following in a shell
    #   mkfifo /tmp/input # create a FIFO pipe
    #   export BODO_WORKER0_INPUT=/tmp/input
    #   export BODO_NUM_WORKERS=1
    #   python -u some_script_that_has_breakpoint_in_code_executed_by_worker.py
    # In a separate shell, do:
    #   cat > /tmp/input
    # Now you can write to the stdin of rank 0 by submitting input in the second
    # shell. Note that the worker will hang until there is at least one writer on
    # the pipe.
    if bodo.get_rank() == 0 and (infile := os.environ.get("BODO_WORKER0_INPUT")):
        fd = os.open(infile, os.O_RDONLY)
        os.dup2(fd, 0)
    else:
        sys.stdin.close()

    log_lvl = int(os.environ.get("BODO_WORKER_VERBOSE_LEVEL", "0"))
    bodo.set_verbose_level(log_lvl)

    comm_world: MPI.Intracomm = MPI.COMM_WORLD
    spawner_intercomm: MPI.Intercomm | None = comm_world.Get_parent()

    worker_loop(
        comm_world,
        spawner_intercomm,
        bodo.user_logging.get_current_bodo_verbose_logger(),
    )