import numpy as np
import pandas as pd
import pytest
from mpi4py import MPI

import bodo
import bodo.io.snowflake
import bodo.tests.utils
from bodo.libs.stream_groupby import (
    delete_groupby_state,
    get_op_pool_bytes_allocated,
    get_op_pool_bytes_pinned,
    get_partition_state,
    groupby_build_consume_batch,
    groupby_produce_output_batch,
    init_groupby_state,
)
from bodo.tests.utils import _get_dist_arg, temp_env_override

# NOTE: Once we're no longer actively working on Groupby Spill Support, all these tests can be marked as "slow".


def groupby_common_impl(df, func_names, f_in_offsets, f_in_cols, op_pool_size_bytes):
    keys_inds = bodo.utils.typing.MetaType((0,))
    out_col_meta_l = ["key"] + [f"out_{i}" for i in range(len(func_names))]
    out_col_meta = bodo.utils.typing.ColNamesMetaType(tuple(out_col_meta_l))
    len_kept_cols = len(df.columns)
    kept_cols = bodo.utils.typing.MetaType(tuple(range(len_kept_cols)))
    batch_size = 4000
    fnames = bodo.utils.typing.MetaType(tuple(func_names))
    f_in_offsets = bodo.utils.typing.MetaType(tuple(f_in_offsets))
    f_in_cols = bodo.utils.typing.MetaType(tuple(f_in_cols))

    def impl(df, op_pool_size_bytes):
        groupby_state = init_groupby_state(
            -1, keys_inds, fnames, f_in_offsets, f_in_cols, op_pool_size_bytes
        )
        is_last1 = False
        _iter_1 = 0
        T1 = bodo.hiframes.table.logical_table_to_table(
            bodo.hiframes.pd_dataframe_ext.get_dataframe_all_data(df),
            (),
            kept_cols,
            len_kept_cols,
        )
        _temp1 = bodo.hiframes.table.local_len(T1)
        while not is_last1:
            T2 = bodo.hiframes.table.table_local_filter(
                T1, slice((_iter_1 * batch_size), ((_iter_1 + 1) * batch_size))
            )
            is_last1 = (_iter_1 * batch_size) >= _temp1
            T3 = bodo.hiframes.table.table_subset(T2, kept_cols, False)
            is_last1 = groupby_build_consume_batch(groupby_state, T3, is_last1)
            ### Uncomment for debugging purposes ###
            # bytes_pinned = get_op_pool_bytes_pinned(groupby_state)
            # bytes_allocated = get_op_pool_bytes_allocated(groupby_state)
            # bodo.parallel_print(
            #     f"Build Iter {_iter_1}: bytes_pinned: {bytes_pinned}, bytes_allocated: {bytes_allocated}"
            # )
            # partition_state = get_partition_state(groupby_state)
            # bodo.parallel_print(
            #     f"Build Iter {_iter_1} partition_state: ", partition_state
            # )
            ###
            _iter_1 = _iter_1 + 1

        final_partition_state = get_partition_state(groupby_state)
        is_last2 = False
        _table_builder = bodo.libs.table_builder.init_table_builder_state(-1)
        while not is_last2:
            out_table, is_last2 = groupby_produce_output_batch(groupby_state, True)
            bodo.libs.table_builder.table_builder_append(_table_builder, out_table)
        final_bytes_pinned = get_op_pool_bytes_pinned(groupby_state)
        final_bytes_allocated = get_op_pool_bytes_allocated(groupby_state)
        delete_groupby_state(groupby_state)
        out_table = bodo.libs.table_builder.table_builder_finalize(_table_builder)
        index_var = bodo.hiframes.pd_index_ext.init_range_index(
            0, len(out_table), 1, None
        )
        df = bodo.hiframes.pd_dataframe_ext.init_dataframe(
            (out_table,), index_var, out_col_meta
        )
        return (
            df,
            final_partition_state,
            final_bytes_pinned,
            final_bytes_allocated,
        )

    # We need a wrapper so that fnames, etc. are treated as globals.
    return bodo.jit(distributed=["df"])(impl)(df, op_pool_size_bytes)


def _test_helper(
    df,
    expected_out,
    expected_partition_state,
    expected_output_size,
    func_names,
    f_in_offsets,
    f_in_cols,
    op_pool_size_bytes,
    expected_log_message,
    capfd,
    multi_rank,
):
    """
    Helper for testing.

    Args:
        multi_rank (bool, optional): Whether this is a
            multi-rank test. If it is, we use allgather
            the output on all ranks before comparing
            with expected output.
    """
    comm = MPI.COMM_WORLD
    with temp_env_override({"BODO_DEBUG_STREAM_GROUPBY_PARTITIONING": "1"}):
        try:
            (
                output,
                final_partition_state,
                final_bytes_pinned,
                final_bytes_allocated,
            ) = groupby_common_impl(
                _get_dist_arg(df) if multi_rank else df,
                func_names,
                f_in_offsets,
                f_in_cols,
                op_pool_size_bytes,
            )
            if multi_rank:
                global_output = bodo.allgatherv(output)
            else:
                global_output = output
        except Exception:
            out, err = capfd.readouterr()
            with capfd.disabled():
                print(f"STDOUT:\n{out}")
                print(f"STDERR:\n{err}")
            raise

    out, err = capfd.readouterr()

    # Verify that the expected log message is present.
    assert_success = True
    if expected_log_message is not None:
        assert_success = expected_log_message in err
    assert_success = comm.allreduce(assert_success, op=MPI.LAND)
    assert assert_success

    assert (
        global_output.shape[0] == expected_output_size
    ), f"Final output size ({global_output.shape[0]}) is not as expected ({expected_output_size})"

    # After the build step, all memory should've been released:
    assert_success = final_bytes_pinned == 0
    assert_success = comm.allreduce(assert_success, op=MPI.LAND)
    assert (
        assert_success
    ), f"Final bytes pinned by the Operator BufferPool ({final_bytes_pinned}) is not 0!"

    assert_success = final_bytes_allocated == 0
    assert_success = comm.allreduce(assert_success, op=MPI.LAND)
    assert (
        assert_success
    ), f"Final bytes allocated by the Operator BufferPool ({final_bytes_allocated}) is not 0!"

    assert_success = final_partition_state == expected_partition_state
    assert_success = comm.allreduce(assert_success, op=MPI.LAND)
    assert (
        assert_success
    ), f"Final partition state ({final_partition_state}) is not as expected ({expected_partition_state})"

    pd.testing.assert_frame_equal(
        global_output.sort_values("key").reset_index(drop=True),
        expected_out.sort_values("key").reset_index(drop=True),
        check_dtype=False,
        check_index_type=False,
        atol=0.1,
    )


@pytest.mark.skipif(bodo.get_size() > 1, reason="Only calibrated for single core case")
def test_split_during_append_table_acc_funcs(capfd, memory_leak_check):
    """
    Test that re-partitioning works correctly when it happens
    during AppendBuildBatch on an input batch.
    We trigger this by using specific key column values, array
    sizes and the size of the operator pool.
    In particular, we use functions that always go through Accumulate
    path regardless of the dtypes of the running values.
    """

    df = pd.DataFrame(
        {
            "A": pd.array([1, 2, 3, 4, 5, 6, 5, 4] * 4000, dtype="Int64"),
            "B": np.array(
                [1, 3, 5, 11, 1, 3, 5, 3, 4, 78, 23, 120, 87, 34, 52, 34] * 2000,
                dtype=np.float32,
            ),
            "C": pd.array(
                [
                    "tapas",
                    "bravas",
                    "pizza",
                    "omelette",
                    "salad",
                    "spinach",
                    "celery",
                ]
                * 4000
                + ["sandwich", "burrito", "ramen", "carrot-cake"] * 1000
            ),
        }
    )

    func_names = ["median", "sum", "nunique"]
    f_in_offsets = [0, 1, 2, 3]
    f_in_cols = [
        1,
        1,
        2,
    ]
    expected_out = df.groupby("A", as_index=False).agg(
        {"B": ["median", "sum"], "C": ["nunique"]}
    )
    expected_out.reset_index(inplace=True, drop=True)
    expected_out.columns = ["key"] + [f"out_{i}" for i in range(3)]
    expected_output_size = 6

    # This will cause partition split during the "AppendBuildBatch[3]"
    op_pool_size_bytes = 2 * 1024 * 1024
    expected_partition_state = [(2, 0), (2, 1), (1, 1)]

    # Verify that we split a partition during AppendBuildBatch.
    expected_log_msg = "[DEBUG] GroupbyState::AppendBuildBatch[3]: Encountered OperatorPoolThresholdExceededError.\n[DEBUG] Splitting partition 0."

    _test_helper(
        df,
        expected_out,
        expected_partition_state,
        expected_output_size,
        func_names,
        f_in_offsets,
        f_in_cols,
        op_pool_size_bytes,
        expected_log_msg,
        capfd,
        False,
    )


@pytest.mark.skipif(bodo.get_size() > 1, reason="Only calibrated for single core case")
def test_split_during_append_table_str_running_vals(capfd, memory_leak_check):
    """
    Test that re-partitioning works correctly when it happens
    during AppendBuildBatch on an input batch.
    We trigger this by using specific key column values, array
    sizes and the size of the operator pool.
    In particular, we use functions that usually go through the
    Aggregation path, but won't because one or more running values
    are STRING/DICT
    """

    df = pd.DataFrame(
        {
            "A": pd.array([1, 2, 3, 4, 5, 6, 5, 4] * 4000, dtype="Int64"),
            "B": pd.array(
                [
                    "tapas",
                    "bravas",
                    "pizza",
                    "omelette",
                    "salad",
                    "spinach",
                    "celery",
                ]
                * 4000
                + ["sandwich", "burrito", "ramen", "carrot-cake"] * 1000
            ),
            "C": np.array(
                [1, 3, 5, 11, 1, 3, 5, 3, 4, 78, 23, 120, 87, 34, 52, 34] * 2000,
                dtype=np.float32,
            ),
            "D": np.arange(32000, dtype=np.int32),
        }
    )
    func_names = [
        "max",
        "min",
        "sum",
        "count",
        "mean",
        "var",
        "std",
        "kurtosis",
        "skew",
    ]
    f_in_cols = [1, 1, 2, 2, 2, 2, 2, 3, 3]
    f_in_offsets = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    expected_out = df.groupby("A", as_index=False).agg(
        {
            "B": ["max", "min"],
            "C": ["sum", "count", "mean", "var", "std"],
            "D": [pd.Series.kurt, "skew"],
        }
    )
    expected_out.reset_index(inplace=True, drop=True)
    expected_out.columns = ["key"] + [f"out_{i}" for i in range(9)]
    expected_output_size = 6

    # This will cause partition split during the "AppendBuildBatch[3]"
    op_pool_size_bytes = 2 * 1024 * 1024
    expected_partition_state = [(2, 0), (2, 1), (1, 1)]

    # Verify that we split a partition during AppendBuildBatch.
    expected_log_msg = "[DEBUG] GroupbyState::AppendBuildBatch[3]: Encountered OperatorPoolThresholdExceededError.\n[DEBUG] Splitting partition 0."
    _test_helper(
        df,
        expected_out,
        expected_partition_state,
        expected_output_size,
        func_names,
        f_in_offsets,
        f_in_cols,
        op_pool_size_bytes,
        expected_log_msg,
        capfd,
        False,
    )


@pytest.mark.skipif(bodo.get_size() > 1, reason="Only calibrated for single core case")
def test_split_during_finalize_build_acc_funcs(capfd, memory_leak_check):
    """
    Test that re-partitioning works correctly when it happens
    during FinalizeBuild.
    In particular, we use functions that always go through Accumulate
    path regardless of the dtypes of the running values.
    """

    df = pd.DataFrame(
        {
            "A": pd.array(list(np.arange(1000)) * 32, dtype="Int64"),
            "B": np.array(
                [1, 3, 5, 11, 1, 3, 5, 3, 4, 78, 23, 120, 87, 34, 52, 34] * 2000,
                dtype=np.float32,
            ),
            "C": pd.array(
                [
                    "tapas",
                    "bravas",
                    "pizza",
                    "omelette",
                    "salad",
                    "spinach",
                    "celery",
                ]
                * 4000
                + ["sandwich", "burrito", "ramen", "carrot-cake"] * 1000
            ),
        }
    )
    func_names = ["median", "sum", "nunique"]
    f_in_offsets = [0, 1, 2, 3]
    f_in_cols = [
        1,
        1,
        2,
    ]
    expected_out = df.groupby("A", as_index=False).agg(
        {"B": ["median", "sum"], "C": ["nunique"]}
    )
    expected_out.reset_index(inplace=True, drop=True)
    expected_out.columns = ["key"] + [f"out_{i}" for i in range(3)]
    expected_output_size = 1000

    # This will cause partition split during the "FinalizeBuild"
    op_pool_size_bytes = 3 * 1024 * 1024
    expected_partition_state = [(1, 0), (1, 1)]
    # Verify that we split a partition during FinalizeBuild.
    expected_log_msg = "[DEBUG] GroupbyState::FinalizeBuild: Encountered OperatorPoolThresholdExceededError while finalizing partition 0."

    _test_helper(
        df,
        expected_out,
        expected_partition_state,
        expected_output_size,
        func_names,
        f_in_offsets,
        f_in_cols,
        op_pool_size_bytes,
        expected_log_msg,
        capfd,
        False,
    )


@pytest.mark.skipif(bodo.get_size() > 1, reason="Only calibrated for single core case")
def test_split_during_finalize_build_str_running_vals(capfd, memory_leak_check):
    """
    Test that re-partitioning works correctly when it happens
    during FinalizeBuild.
    In particular, we use functions that usually go through the
    Aggregation path, but won't because one or more running values
    are STRING/DICT
    """

    df = pd.DataFrame(
        {
            "A": pd.array(list(np.arange(1000)) * 32, dtype="Int64"),
            "B": pd.array(
                [
                    "tapas",
                    "bravas",
                    "pizza",
                    "omelette",
                    "salad",
                    "spinach",
                    "celery",
                ]
                * 4000
                + ["sandwich", "burrito", "ramen", "carrot-cake"] * 1000
            ),
            "C": np.array(
                [1, 3, 5, 11, 1, 3, 5, 3, 4, 78, 23, 120, 87, 34, 52, 34] * 2000,
                dtype=np.float32,
            ),
            "D": np.arange(32000, dtype=np.int32),
        }
    )
    func_names = [
        "max",
        "min",
        "sum",
        "count",
        "mean",
        "var",
        "std",
        "kurtosis",
        "skew",
    ]
    f_in_cols = [1, 1, 2, 2, 2, 2, 2, 3, 3]
    f_in_offsets = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

    expected_out = df.groupby("A", as_index=False).agg(
        {
            "B": ["max", "min"],
            "C": ["sum", "count", "mean", "var", "std"],
            "D": [pd.Series.kurt, "skew"],
        }
    )
    expected_out.reset_index(inplace=True, drop=True)
    expected_out.columns = ["key"] + [f"out_{i}" for i in range(9)]
    expected_output_size = 1000

    # This will cause partition split during the "FinalizeBuild"
    op_pool_size_bytes = 3 * 1024 * 1024
    expected_partition_state = [(1, 0), (1, 1)]

    # Verify that we split a partition during FinalizeBuild.
    expected_log_msg = "[DEBUG] GroupbyState::FinalizeBuild: Encountered OperatorPoolThresholdExceededError while finalizing partition 0."

    _test_helper(
        df,
        expected_out,
        expected_partition_state,
        expected_output_size,
        func_names,
        f_in_offsets,
        f_in_cols,
        op_pool_size_bytes,
        expected_log_msg,
        capfd,
        False,
    )


@pytest.mark.skipif(bodo.get_size() != 2, reason="Only calibrated for two cores case")
def test_split_during_shuffle_append_table_and_diff_part_state(
    capfd, memory_leak_check
):
    """
    Test that re-partitioning works correctly when it happens
    during AppendBuildBatch on the output of a shuffle operation.
    This test also tests that the overall algorithm works correctly
    and without hangs when the partitioning state is different on
    different ranks. In particular, in this case, rank 0 will end up
    with a single partition, but rank 1 will end up with 3 partitions.
    We trigger this by using specific key column values, array
    sizes and the size of the operator pool.
    """

    df = pd.DataFrame(
        {
            "A": pd.array([1, 2, 3, 4, 5] * 6400, dtype="Int64"),
            "B": np.array(
                [1, 3, 5, 11, 1, 3, 5, 3, 4, 78, 23, 120, 87, 34, 52, 34] * 2000,
                dtype=np.float32,
            ),
            "C": pd.array(
                [
                    "tapas",
                    "bravas",
                    "pizza",
                    "omelette",
                    "salad",
                    "spinach",
                    "celery",
                ]
                * 4000
                + ["sandwich", "burrito", "ramen", "carrot-cake"] * 1000
            ),
        }
    )

    expected_out = df.groupby("A", as_index=False).agg(
        {"B": ["median", "sum"], "C": ["nunique"]}
    )
    expected_out.reset_index(inplace=True, drop=True)
    expected_out.columns = ["key"] + [f"out_{i}" for i in range(3)]

    expected_partition_state = (
        [(0, 0)] if (bodo.get_rank() == 0) else [(2, 0), (2, 1), (1, 1)]
    )
    expected_output_size = 5
    func_names = ["median", "sum", "nunique"]
    f_in_offsets = [0, 1, 2, 3]
    f_in_cols = [1, 1, 2]

    # This will cause partition split during the "AppendBuildBatch[2]"
    op_pool_size_bytes = 1.5 * 1024 * 1024

    # Verify that we split a partition during AppendBuildBatch.
    expected_log_msg = (
        "[DEBUG] GroupbyState::AppendBuildBatch[2]: Encountered OperatorPoolThresholdExceededError.\n[DEBUG] Splitting partition 0."
        if bodo.get_rank() == 1
        else None
    )

    _test_helper(
        df,
        expected_out,
        expected_partition_state,
        expected_output_size,
        func_names,
        f_in_offsets,
        f_in_cols,
        op_pool_size_bytes,
        expected_log_msg,
        capfd,
        True,
    )