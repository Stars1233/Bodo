import ipyparallel as ipp
from mpi4py import MPI

def mpi_example():
    comm = MPI.COMM_WORLD
    return f"Hello World from rank {comm.Get_rank()}. total ranks={comm.Get_size()}"

def test_ipyparallel():
    # request an MPI cluster with 4 engines
    with ipp.Cluster(engines='mpi', n=4) as rc:
        # get a broadcast_view on the cluster which is best
        # suited for MPI style computation
        view = rc.broadcast_view()
        # run the mpi_example function on all engines in parallel
        r = view.apply_sync(mpi_example)
        # Retrieve and print the result from the engines
        print("\n".join(r))
        # at this point, the cluster processes have been shutdown

if __name__ == "__main__":
    test_ipyparallel()