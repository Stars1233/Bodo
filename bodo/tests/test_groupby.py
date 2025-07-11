import datetime
import random
import string
from decimal import Decimal

import numba
import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

import bodo
from bodo.tests.utils import (
    DeadcodeTestPipeline,
    DistTestPipeline,
    check_func,
    check_parallel_coherency,
    convert_non_pandas_columns,
    dist_IR_contains,
    gen_nonascii_list,
    gen_random_decimal_array,
    gen_random_list_string_array,
    get_start_end,
    has_udf_call,
    pytest_mark_pandas,
)
from bodo.utils.typing import BodoError

# Note: this file tests a large mix of features that are critical
# for BodoSQL, but also a large number that are only relevent
# for Python. The former needs to be tested, but the latter can
# be given special marks so it is infrequently tested.


@pytest.fixture(
    params=[
        pd.DataFrame(
            {
                "A": [2, 1, np.nan, 1, 2, 2, 1],
                "B": [-8, 2, 3, 1, 5, 6, 7],
                "C": [3, 5, 6, 5, 4, 4, 3],
            }
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 1, 2, 2, 1],
                    "B": pd.Series(np.full(7, np.nan), dtype="Int64"),
                    "C": [3, 5, 6, 5, 4, 4, 3],
                }
            ),
            marks=pytest.mark.slow,
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 1, 2, 2, 1],
                    "B": pd.Series(
                        [float(i) if i % 2 == 0 else None for i in range(7)],
                        dtype="Float64",
                    ),
                    "C": [3, 5, 6, 5, 4, 4, 3],
                }
            ),
            marks=pytest.mark.slow,
            id="nullable_float",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2.1, -1.5, 0.0, -1.5, 2.1, 2.1, 1.5],
                    "B": [-8.3, np.nan, 3.8, 1.3, 5.4, np.nan, -7.0],
                    "C": [3.4, 2.5, 9.6, 1.5, -4.3, 4.3, -3.7],
                }
            ),
            marks=pytest.mark.slow,
        ),
        # Categorical key
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Categorical(["A", "B", "A", "A", "B", None, "B"]),
                    "B": [-8.3, np.nan, 3.8, 1.3, 5.4, np.nan, -7.0],
                    "C": [3.4, 2.5, 9.6, 1.5, -4.3, 4.3, -3.7],
                }
            ),
            marks=pytest.mark.slow,
        ),
        # Categorical key and value
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Categorical(["AA", "BB", "", "AA", None], ordered=True),
                    "B": pd.Categorical([1, 2, 5, None, 5], ordered=True),
                    "C": pd.Categorical(
                        pd.concat(
                            [
                                pd.Series(
                                    pd.date_range(
                                        start="2/1/2015", end="2/24/2021", periods=4
                                    )
                                ),
                                pd.Series(data=[None], index=[4]),
                            ]
                        ),
                        ordered=True,
                    ),
                    "D": pd.Categorical(
                        pd.concat(
                            [
                                pd.Series(pd.timedelta_range(start="1 day", periods=4)),
                                pd.Series(data=[None], index=[4]),
                            ]
                        ),
                        ordered=True,
                    ),
                    "E": pd.Categorical([None, 4.3, 9.5, None, 7.2], ordered=True),
                }
            ),
            id="categorical_value_df",
        ),
        # Binary key and decimal values
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Series(
                        [b"", b"", b"a", b"a", b"a", b"c", b"c", bytes(3)] * 2
                    ),
                    "B": pd.Series([0, 2, 3, 1, 13, 6, 2, 4] * 2),
                    "C": pd.Series([-1.2, 0.2, 3.0, 0.41, 0.13, 60.3, 2.0, 0.4444] * 2),
                }
            ),
            id="binary_key_df",
        ),
        # Binary value and decimal key
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Series([0, 0, 1, 1, 1, 2, 2, 4] * 2),
                    "B": pd.Series([-1.2, 0.2, 3.0, 0.41, 0.13, 60.3, 2.0, 0.4444] * 2),
                    # Binary column must not be B, as only B is aggregated in the test_agg_single_col tests
                    # Since the column is droped for several agregations, this can result in an empty return
                    "C": pd.Series(
                        [b"", b"", b"a", b"a", b"a", b"c", b"c", bytes(3)] * 2
                    ),
                }
            ),
            id="binary_value_df",
            marks=pytest.mark.skip("Needs support for max, min, see [BE-1252]"),
        ),
    ]
)
def test_df(request):
    return request.param


@pytest.fixture(
    params=[
        pd.DataFrame(
            {
                "A": [2, 1, np.nan, 1, 2, 2, 1],
                "B": [-8, 2, 3, 1, 5, 6, 7],
                "C": [3, 5, 6, 5, 4, 4, 3],
            }
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 1, 2, 2, 1],
                    "B": [-8, np.nan, 3, np.nan, 5, 6, 7],
                    "C": [3, 5, 6, 5, 4, 4, 3],
                }
            ),
            marks=pytest.mark.slow,
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2.1, -1.5, 0.0, -1.5, 2.1, 2.1, 1.5],
                    "B": [-8.3, np.nan, 3.8, 1.3, 5.4, np.nan, -7.0],
                    "C": [3.4, 2.5, 9.6, 1.5, -4.3, 4.3, -3.7],
                }
            ),
            marks=pytest.mark.slow,
        ),
    ]
)
def test_df_int_no_null(request):
    """
    Testing data for functions that does not support nullable integer columns
    with nulls only

    Ideally, all testing function using test_df_int_no_null as inputs
    should support passing tests with test_df
    """
    return request.param


@pytest.mark.slow
@pytest.mark.df_lib
def test_nullable_int(memory_leak_check):
    def impl(df):
        A = df.groupby("A").sum()
        return A

    def impl_select_colB(df):
        A = df.groupby("A")["B"].sum()
        return A

    def impl_select_colE(df):
        A = df.groupby("A")["E"].sum()
        return A

    def impl_select_colH(df):
        A = df.groupby("A")["H"].sum()
        return A

    df = pd.DataFrame(
        {
            "A": pd.array([2, 1, 1, 1, 21, 2, 11], "Int32"),
            "B": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, np.nan, 20]), dtype="Int8"
            ),
            "C": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, np.nan, 20]), dtype="Int16"
            ),
            "D": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, np.nan, 20]), dtype="Int32"
            ),
            "E": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, np.nan, 20]), dtype="Int64"
            ),
            "F": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, np.nan, 20]), dtype="UInt8"
            ),
            "G": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, np.nan, 20]), dtype="UInt16"
            ),
            "H": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, np.nan, 20]), dtype="UInt32"
            ),
            "I": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, np.nan, 20]), dtype="UInt64"
            ),
        }
    )

    check_func(impl, (df,), sort_output=True)
    # pandas 1.0 has a regression here: output is int64 instead of Int8
    # so we disable check_dtype
    check_func(impl_select_colB, (df,), sort_output=True, check_dtype=False)
    check_func(impl_select_colE, (df,), sort_output=True)
    # pandas 1.0 has a regression here: output is int64 instead of UInt32
    check_func(impl_select_colH, (df,), sort_output=True, check_dtype=False)


@pytest.mark.df_lib
def test_groupby_sum_integer_upcast(memory_leak_check):
    """
    Test that we upcast output of groupby sum on integers to their
    64-bit variants and don't overflow.
    """

    def impl(df):
        A = df.groupby("A").sum()
        return A

    df = pd.DataFrame(
        {
            "A": pd.array([1, 1, 1, 2, 2, 2, 2], "Int32"),
            "Int8": pd.Series(
                np.array([124, 124, 127, 54, 125, -4, np.nan]), dtype="Int8"
            ),
            "int8": pd.Series(np.array([124, 124, 127, 54, 125, -4, 20]), dtype="int8"),
            "Int16": pd.Series(
                np.array([16000, 16000, 16000, 32000, 32000, -16000, np.nan]),
                dtype="Int16",
            ),
            "int16": pd.Series(
                np.array([16000, 16000, 16000, 32000, 32000, -16000, 9099]),
                dtype="int16",
            ),
            "Int32": pd.Series(
                np.array(
                    [
                        1_000_000_000,
                        1_000_000_000,
                        1_000_000_000,
                        2_000_000_000,
                        2_000_000_000,
                        -1_000_000_000,
                        np.nan,
                    ]
                ),
                dtype="Int32",
            ),
            "int32": pd.Series(
                np.array(
                    [
                        1_000_000_000,
                        1_000_000_000,
                        1_000_000_000,
                        2_000_000_000,
                        2_000_000_000,
                        -1_000_000_000,
                        8790,
                    ]
                ),
                dtype="int32",
            ),
            "UInt8": pd.Series(
                np.array([124, 124, 127, 54, 125, 120, np.nan]), dtype="UInt8"
            ),
            "uint8": pd.Series(
                np.array([124, 124, 127, 54, 125, 120, 20]), dtype="uint8"
            ),
            "UInt16": pd.Series(
                np.array([16000, 16000, 16000, 32000, 32000, 1000, np.nan]),
                dtype="UInt16",
            ),
            "uint16": pd.Series(
                np.array([16000, 16000, 16000, 32000, 32000, 1000, 9099]),
                dtype="uint16",
            ),
            "UInt32": pd.Series(
                np.array(
                    [
                        1_000_000_000,
                        1_000_000_000,
                        1_000_000_000,
                        2_000_000_000,
                        2_000_000_000,
                        1_000_000_000,
                        np.nan,
                    ]
                ),
                dtype="UInt32",
            ),
            "uint32": pd.Series(
                np.array(
                    [
                        1_000_000_000,
                        1_000_000_000,
                        1_000_000_000,
                        2_000_000_000,
                        2_000_000_000,
                        1_000_000_000,
                        8790,
                    ]
                ),
                dtype="uint32",
            ),
        }
    )

    check_func(impl, (df,), sort_output=True)


@pytest.mark.slow
@pytest.mark.df_lib
def test_groupby_nullable_float(memory_leak_check):
    def impl(df):
        A = df.groupby("A").sum()
        return A

    def impl_select_colB(df):
        A = df.groupby("A")["B"].sum()
        return A

    def impl_select_colC(df):
        A = df.groupby("A")["C"].sum()
        return A

    df = pd.DataFrame(
        {
            "A": pd.array([2, 1, 1, 1, 21, 2, 11], "Int64"),
            "B": pd.Series(
                np.array([np.nan, 3.14, 2.0, np.nan, np.nan, np.nan, 20]),
                dtype="Float32",
            ),
            "C": pd.Series(
                np.array([np.nan, 3.14, 2.0, np.nan, np.nan, np.nan, 20]),
                dtype="Float64",
            ),
        }
    )

    check_func(impl, (df,), sort_output=True)
    check_func(impl_select_colB, (df,), sort_output=True)
    check_func(impl_select_colC, (df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.parametrize(
    "df_null",
    [
        pd.DataFrame(
            {"A": [2, 1, 1, 1], "B": pd.Series(np.full(4, np.nan), dtype="Int64")},
            index=[32, 45, 56, 76],
        ),
        pytest.param(
            pd.DataFrame(
                {"A": [1, 1, 1, 1], "B": pd.Series([1, 2, 3, 4], dtype="Int64")},
                index=[3, 4, 5, 6],
            ),
            marks=pytest.mark.slow,
        ),
    ],
)
def test_return_type_nullable_cumsum_cumprod(df_null, memory_leak_check):
    """
    Test Groupby when one row is a nullable-int-bool.
    A current problem is that cumsum/cumprod with pandas return an array of float for Int64
    in input. That is why we put check_dtype=False here.
    """

    def impl1(df):
        df2 = df.groupby("A")["B"].agg(("cumsum", "cumprod"))
        return df2

    def impl2(df):
        df2 = df.groupby("A")["B"].cumsum()
        return df2

    check_func(impl1, (df_null,), sort_output=True, check_dtype=False)
    check_func(impl2, (df_null,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
def test_groupby_df_numpy_bool(memory_leak_check):
    """
    Test calling groupby using a scalar column bool,
    which generates a numpy boolean array.
    This tests that our typing determines array type
    by actual array and not looking at elem dtype.
    """

    def impl():
        df = pd.DataFrame(
            {
                "s_suppkey": np.arange(1000) % 10,
                "$f1": True,
            }
        )
        return df.groupby(["s_suppkey"], as_index=False, dropna=False).min()

    check_func(impl, (), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_all_null_keys(memory_leak_check):
    """
    Test Groupby when all rows have null keys (returns empty dataframe)
    We use reset_index=True since the index is empty and so we have a type problem otherwise
    """

    def impl(df):
        A = df.groupby("A").count()
        return A

    df = pd.DataFrame(
        {"A": pd.Series(np.full(7, np.nan), dtype="Int64"), "B": [2, 1, 1, 1, 2, 2, 1]}
    )

    check_func(impl, (df,), sort_output=True, reset_index=True)


udf_in_df = pd.DataFrame(
    {
        "A": [2, 1, 1, 1, 2, 2, 1],
        2: [-8, 2, 3, 1, 5, 6, 7],
        "C": [1.2, 2.4, np.nan, 2.2, 5.3, 3.3, 7.2],
    }
)


def test_agg(memory_leak_check):
    """
    Test Groupby.agg(): one user defined func and all cols
    """

    def impl(df):
        A = df.groupby("A").agg(lambda x: x.max() - x.min())
        return A

    # check_dtype=False since Bodo returns float for Series.min/max. TODO: fix min/max
    check_func(impl, (udf_in_df,), sort_output=True, check_dtype=False)
    udf_in_df2 = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": pd.array([-8, 2, 3, 1, 5, 6, 7], "Int64"),
        }
    )
    check_func(impl, (udf_in_df2,), sort_output=True, check_dtype=False)


def test_agg_set_error(memory_leak_check):
    """
    Test Groupby.agg() with constant set input but no single
    output column.
    """

    def impl(df):
        return df.groupby("A").agg({"max"})

    with pytest.raises(
        BodoError,
        match="must select exactly one column when more than one function is supplied",
    ):
        bodo.jit(impl)(udf_in_df)


@pytest.mark.parametrize(
    "df",
    [
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Series([True, False, True, False, False] * 20),
                    "B": ["A"] * 100,
                }
            ),
            id="1_group_no_null",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Series(
                        [True, False, True, None, True, False], dtype=pd.BooleanDtype()
                    ),
                    "B": list("AAAABB"),
                }
            ),
            id="2_groups_with_null",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Series(
                        [None if i % 3 > i % 4 else i % 2 == 0 for i in range(100)],
                        dtype=pd.BooleanDtype(),
                    ),
                    "B": (list("ABCDE") + [None] + list("GHIJ")) * 10,
                }
            ),
            id="10_groups_with_null",
        ),
    ],
)
def test_sum_bool(df, memory_leak_check):
    """
    Test groupby with pd.NamedAgg() for sums of booleans
    """

    def impl(df):
        return df.groupby(["B"], as_index=False, dropna=False).agg(
            output=pd.NamedAgg(column="A", aggfunc="sum"),
        )

    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest.mark.parametrize(
    "df",
    [
        pytest.param(
            pd.DataFrame(
                {
                    "A": ["A"] * 10,
                    "B": pd.Series([i**2 for i in range(10)]),
                }
            ),
            id="1_group-no_null",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": ["A"] * 10,
                    "B": pd.Series(
                        [None if i % 4 == 3 else i**2 for i in range(10)],
                        dtype=pd.Int32Dtype(),
                    ),
                }
            ),
            id="1_group-with_null",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": list("ABCDEFGHIJ") * 10,
                    "B": pd.Series(
                        [
                            np.arctanh((i - 50) / 75) * i ** (1 / (1 + i % 10))
                            for i in range(100)
                        ]
                    ),
                }
            ),
            id="10_groups-no_null",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": list("ABCDEFGHIJ") * 10,
                    "B": pd.Series(
                        [
                            (
                                None
                                if (i**2) % 17 > 13
                                else np.arctanh((i - 50) / 75) * i ** (1 / (1 + i % 10))
                            )
                            for i in range(100)
                        ]
                    ),
                }
            ),
            id="10_groups-with_null",
        ),
    ],
)
def test_kurtosis_skew(df, memory_leak_check):
    """
    Test groupby with pd.NamedAgg() for kurtosis and skew
    """

    def impl(df):
        return df.groupby(["A"], as_index=False, dropna=False).agg(
            out_1=pd.NamedAgg(column="B", aggfunc="kurtosis"),
            out_2=pd.NamedAgg(column="B", aggfunc="skew"),
        )

    # A function that simulates the aggregation above since kurtosis is not
    # natively supported in groupby.aggs
    def py_impl(df):
        result = df.groupby(["A"], as_index=False, dropna=False).apply(
            lambda group: pd.DataFrame(
                {
                    "A": [group["A"].iloc[0]],
                    "out_1": group["B"].kurtosis(),
                    "out_2": group["B"].skew(),
                }
            )
        )
        result.index = result.index.droplevel(1)
        return result

    answer = py_impl(df)

    check_func(impl, (df,), py_output=answer, sort_output=True, reset_index=True)


@pytest.mark.parametrize(
    "df_sep_and_expected_out",
    [
        pytest.param(
            (
                pd.DataFrame(
                    {
                        "A": list("ABCDEF"),
                        "B": pd.Series(list(np.arange(3)) * 2),
                        "C": pd.arrays.ArrowStringArray(
                            pa.array(
                                [""] * 6,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                    }
                ),
                pd.DataFrame({"AGG_OUTPUT_0": ["AD", "BE", "CF"]}),
            ),
            id="no-null",
        ),
        pytest.param(
            (
                pd.DataFrame(
                    {
                        "A": ["A", None, None] + [None, "B", None] + [None, "C", None],
                        "B": pd.Series(list(np.arange(3)) * 3),
                        "C": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["__different_sep__"] * 9,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                    }
                ),
                pd.DataFrame({"AGG_OUTPUT_0": ["A", "B__different_sep__C", ""]}),
            ),
            id="some-null",
        ),
        pytest.param(
            (
                pd.DataFrame(
                    {
                        # A few strings are needed so that the dtype can be inferred
                        "A": pd.Series((["A"] + [None] * 2) * 5, dtype=str),
                        "B": pd.Series(list(np.arange(3)) * 5),
                        "C": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["__different_sep__"] * 15,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                    }
                ),
                pd.DataFrame(
                    {
                        "AGG_OUTPUT_0": pd.Series(
                            [
                                "A__different_sep__A__different_sep__A__different_sep__A__different_sep__A"
                            ]
                            + [""] * 2,
                            dtype=str,
                        )
                    }
                ),
            ),
            id="all-null",
        ),
        pytest.param(
            (
                pd.DataFrame(
                    {
                        "A": ["½⅓¼⅕⅙⅐⅛⅑ ⅔⅖ ¾⅗ ⅘ ⅚⅝ ⅞", "₩", None]
                        + [None, "B", None]
                        + [None, "Å Ů ẘ ẙ", None],
                        "B": pd.Series(list(np.arange(3)) * 3),
                        "C": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["__« »__"] * 9,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                    }
                ),
                pd.DataFrame(
                    {
                        "AGG_OUTPUT_0": [
                            "½⅓¼⅕⅙⅐⅛⅑ ⅔⅖ ¾⅗ ⅘ ⅚⅝ ⅞",
                            "₩__« »__B__« »__Å Ů ẘ ẙ",
                            "",
                        ]
                    }
                ),
            ),
            id="listagg_nonascii",
        ),
        pytest.param(
            (
                pd.DataFrame(
                    {
                        "A": [""] * 9,
                        "B": pd.Series(list(np.arange(3)) * 3),
                        "C": pd.arrays.ArrowStringArray(
                            pa.array(
                                [""] * 9,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                    }
                ),
                pd.DataFrame({"AGG_OUTPUT_0": [""] * 3}),
            ),
            id="all-empty-edgecase",
        ),
    ],
)
def test_listagg_sep(df_sep_and_expected_out, memory_leak_check):
    """
    Test simple groupby with listagg + separator
    """
    df, expected = df_sep_and_expected_out

    def impl(df):
        return df.groupby(["B"], dropna=False).agg(
            AGG_OUTPUT_0=bodo.utils.utils.ExtendedNamedAgg(
                column="A", aggfunc="listagg", additional_args=("C", (), (), ())
            )
        )

    check_func(
        impl,
        (df,),
        check_names=False,
        reset_index=True,
        py_output=expected,
    )


@pytest.mark.parametrize(
    "df_sep_and_expected_out",
    [
        pytest.param(
            (
                pd.DataFrame(
                    {
                        "A": ["A"] * 3 + ["B"] * 3,
                        "B": pd.Series(list(np.arange(3)) * 2),
                        "C": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["_sep_"] * 6,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                        "C2": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["_sep2_"] * 6,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                        "D": np.arange(6),
                        "int_col": [1] * 6,
                        "int_col2": [1] * 6,
                    }
                ),
                (("D",), (True,), ("first",)),
                (("D",), (False,), ("first",)),
                pd.DataFrame(
                    {
                        "sum_col": [2] * 3,
                        "AGG_OUTPUT_0": ["A_sep_B", "A_sep_B", "A_sep_B"],
                        "max_col": [1] * 3,
                        "AGG_OUTPUT_1": ["B_sep2_A", "B_sep2_A", "B_sep2_A"],
                        "max_col_2": [1] * 3,
                    }
                ),
            ),
            id="simple-1col-sort",
        ),
        pytest.param(
            (
                pd.DataFrame(
                    {
                        "A": list(string.ascii_uppercase[:6]),
                        "B": [1] * 6,
                        "C": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["_ó_"] * 6,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                        "C2": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["_é_"] * 6,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                        "D": list(np.arange(3)) * 2,
                        "E": [1] * 3 + [2] * 3,
                        "int_col": [1] * 6,
                        "int_col2": [1] * 6,
                    }
                ),
                (
                    (
                        "E",
                        "D",
                    ),
                    (True, False),
                    ("first", "first"),
                ),
                (("D", "E"), (False, False), ("first", "first")),
                pd.DataFrame(
                    {
                        "sum_col": [6],
                        "AGG_OUTPUT_0": [
                            "C_ó_B_ó_A_ó_F_ó_E_ó_D",
                        ],
                        "max_col": [1],
                        "AGG_OUTPUT_1": ["F_é_C_é_E_é_B_é_D_é_A"],
                        "max_col_2": [1],
                    }
                ),
            ),
            id="test-2col-sort",
        ),
        pytest.param(
            (
                pd.DataFrame(
                    {
                        "A": list(string.ascii_uppercase[:6]),
                        "B": [1] * 6,
                        "C": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["_ó_"] * 6,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                        "C2": pd.arrays.ArrowStringArray(
                            pa.array(
                                ["_é_"] * 6,
                                type=pa.dictionary(pa.int32(), pa.string()),
                            )
                        ),
                        "D": [None, 1, None, 2, None, 3],
                        "E": [1, None, 2, None, 3, None],
                        "int_col": [1] * 6,
                        "int_col2": [1] * 6,
                    }
                ),
                (
                    (
                        "E",
                        "D",
                    ),
                    (True, True),
                    ("first", "first"),
                ),
                (("D", "E"), (True, False), ("last", "first")),
                pd.DataFrame(
                    {
                        "sum_col": [6],
                        "AGG_OUTPUT_0": [
                            "B_ó_D_ó_F_ó_A_ó_C_ó_E",
                        ],
                        "max_col": [1],
                        "AGG_OUTPUT_1": ["B_é_D_é_F_é_E_é_C_é_A"],
                        "max_col_2": [1],
                    }
                ),
            ),
            id="test-2col-na-sort",
        ),
    ],
)
def test_listagg_within_group_sorting(df_sep_and_expected_out, memory_leak_check):
    """
    Test groupby with listagg with withing group sorting.
    """
    df, additional_args_1, additional_args_2, expected = df_sep_and_expected_out

    orderby_cols_1, ascending_1, nulls_first_1 = additional_args_1
    orderby_cols_2, ascending_2, nulls_first_2 = additional_args_2

    def impl(df):
        return df.groupby(["B"], dropna=False).agg(
            sum_col=pd.NamedAgg(column="int_col", aggfunc="sum"),
            AGG_OUTPUT_0=bodo.utils.utils.ExtendedNamedAgg(
                column="A",
                aggfunc="listagg",
                additional_args=("C", orderby_cols_1, ascending_1, nulls_first_1),
            ),
            max_col=pd.NamedAgg(column="int_col", aggfunc="max"),
            AGG_OUTPUT_1=bodo.utils.utils.ExtendedNamedAgg(
                column="A",
                aggfunc="listagg",
                additional_args=("C2", orderby_cols_2, ascending_2, nulls_first_2),
            ),
            max_col_2=pd.NamedAgg(column="int_col2", aggfunc="max"),
        )

    check_func(
        impl,
        (df,),
        check_names=False,
        reset_index=True,
        py_output=expected,
    )


def test_listagg_non_duplicate():
    """
    Tests a specific issue wherein listagg would be treated as being a duplicate aggregation,
    despite having different additional arguments."""
    df = pd.DataFrame(
        {
            "A": list(string.ascii_uppercase[:6]),
            "B": [1] * 6,
            "C": pd.arrays.ArrowStringArray(
                pa.array(
                    ["_ó_"] * 6,
                    type=pa.dictionary(pa.int32(), pa.string()),
                )
            ),
            "D": [None, 1, None, 2, None, 3],
            "E": [1, None, 2, None, 3, None],
        }
    )

    def impl(df):
        return df.groupby(["B"], dropna=False).agg(
            AGG_OUTPUT_0=bodo.utils.utils.ExtendedNamedAgg(
                column="A",
                aggfunc="listagg",
                additional_args=("C", ("D", "E"), (True, True), ("first", "first")),
            ),
            AGG_OUTPUT_1=bodo.utils.utils.ExtendedNamedAgg(
                column="A",
                aggfunc="listagg",
                additional_args=("C", ("D", "E"), (True, False), ("first", "first")),
            ),
            AGG_OUTPUT_2=bodo.utils.utils.ExtendedNamedAgg(
                column="A",
                aggfunc="listagg",
                additional_args=("C", ("D", "E"), (True, True), ("first", "last")),
            ),
        )

    expected_output = pd.DataFrame(
        {
            "AGG_OUTPUT_0": [
                "A_ó_C_ó_E_ó_B_ó_D_ó_F",
            ],
            "AGG_OUTPUT_1": ["E_ó_C_ó_A_ó_B_ó_D_ó_F"],
            "AGG_OUTPUT_2": ["A_ó_C_ó_E_ó_B_ó_D_ó_F"],
        }
    )

    check_func(
        impl, (df,), sort_output=True, reset_index=True, py_output=expected_output
    )


@pytest.mark.slow
def test_sum_string(memory_leak_check):
    def impl(df):
        A = df.groupby("A").sum()
        return A

    df1 = pd.DataFrame(
        {
            "A": [1, 1, 1, 2, 3, 3, 4, 0, 5, 0, 11],
            "B": ["a", "b", "c", "d", "", "AA"] + gen_nonascii_list(5),
            "C": [1] * 11,
        }
    )
    check_func(impl, (df1,), sort_output=True)


@pytest.mark.slow
def test_sum_binary(memory_leak_check):
    """Tests sum on dataframes containing binary columns."""

    def impl(df):
        A = df.groupby("A").sum()
        return A

    df1 = pd.DataFrame(
        {
            "A": [1, 1, 1, 2, 3, 3, 4, 0, 5, 0, 11],
            "B": [b"a", b"b", b"c", b"d", b"", b"AA", b"ABC", b"AB", b"c", b"F", b"GG"],
            "C": [1] * 11,
        }
    )
    check_func(impl, (df1,), sort_output=True)


# ------ impls used within the test function "random_decimal_sum_min_max_last" below ------


def random_decimal_sum_min_max_last_impl1(df):
    df_ret = df.groupby("A", as_index=False).nunique()
    return df_ret["B"].copy()


def random_decimal_sum_min_max_last_impl2(df):
    A = df.groupby("A", as_index=False).last()
    return A


def random_decimal_sum_min_max_last_impl3(df):
    A = df.groupby("A", as_index=False)["B"].first()
    return A


def random_decimal_sum_min_max_last_impl4(df):
    A = df.groupby("A", as_index=False)["B"].count()
    return A


def random_decimal_sum_min_max_last_impl5(df):
    A = df.groupby("A", as_index=False).max()
    return A


def random_decimal_sum_min_max_last_impl6(df):
    A = df.groupby("A", as_index=False).min()
    return A


def random_decimal_sum_min_max_last_impl7(df):
    A = df.groupby("A", as_index=False)["B"].mean()
    return A


def random_decimal_sum_min_max_last_impl8(df):
    A = df.groupby("A", as_index=False)["B"].median()
    return A


def random_decimal_sum_min_max_last_impl9(df):
    A = df.groupby("A", as_index=False)["B"].var()
    return A


# We need to drop column A because the column A is replaced by std(A)
# in pandas due to a pandas bug.
def random_decimal_sum_min_max_last_impl10(df):
    A = df.groupby("A", as_index=False)["B"].std()
    return A.drop(columns="A")


@pytest.mark.parametrize(
    "impl",
    [
        pytest.param(random_decimal_sum_min_max_last_impl1, id="impl1"),
        pytest.param(
            random_decimal_sum_min_max_last_impl2, id="impl2", marks=pytest.mark.slow
        ),
        pytest.param(
            random_decimal_sum_min_max_last_impl3, id="impl3", marks=pytest.mark.slow
        ),
        pytest.param(
            random_decimal_sum_min_max_last_impl4, id="impl4", marks=pytest.mark.slow
        ),
        pytest.param(
            random_decimal_sum_min_max_last_impl5, id="impl5", marks=pytest.mark.slow
        ),
        pytest.param(
            random_decimal_sum_min_max_last_impl6, id="impl6", marks=pytest.mark.slow
        ),
        pytest.param(
            random_decimal_sum_min_max_last_impl7, id="impl7", marks=pytest.mark.slow
        ),
        pytest.param(
            random_decimal_sum_min_max_last_impl8, id="impl8", marks=pytest.mark.slow
        ),
        pytest.param(
            random_decimal_sum_min_max_last_impl9, id="impl9", marks=pytest.mark.slow
        ),
        pytest.param(
            random_decimal_sum_min_max_last_impl10, id="impl10", marks=pytest.mark.slow
        ),
    ],
)
def test_random_decimal_sum_min_max_last(impl, memory_leak_check):
    """We do not have decimal as index. Therefore we have to use as_index=False"""

    random.seed(5)
    n = 10
    df1 = pd.DataFrame(
        {
            "A": gen_random_decimal_array(1, n),
            "B": gen_random_decimal_array(2, n),
        }
    )

    if impl in {
        random_decimal_sum_min_max_last_impl1,
        random_decimal_sum_min_max_last_impl2,
        random_decimal_sum_min_max_last_impl3,
        random_decimal_sum_min_max_last_impl4,
        random_decimal_sum_min_max_last_impl5,
        random_decimal_sum_min_max_last_impl6,
    }:
        # Direct checks for which pandas has equivalent functions.
        check_func(impl, (df1,), sort_output=True, reset_index=True)
    elif impl in {
        random_decimal_sum_min_max_last_impl7,
        random_decimal_sum_min_max_last_impl8,
        random_decimal_sum_min_max_last_impl9,
        random_decimal_sum_min_max_last_impl10,
    }:
        # For mean/median/var/std we need to map the types.
        check_func(
            impl,
            (df1,),
            sort_output=True,
            reset_index=True,
            convert_columns_to_pandas=True,
            check_dtype=False,
        )
    else:
        raise ValueError("Unexpected impl")


def test_decimal_sum():
    """Test groupby sum on decimal input, where overflow throws an error"""

    def impl(df):
        return df.groupby("A", as_index=False)["B"].sum()

    B = pd.array(
        pa.array(["0.01", "0.03", None] * 10).cast(pa.decimal128(38, 37)),
        dtype=pd.ArrowDtype(pa.decimal128(37, 37)),
    )
    df = pd.DataFrame({"A": [1, 2, 3] * 10, "B": B})
    B_out = pd.array(
        pa.array(["0.1", "0.3", "0"]).cast(pa.decimal128(38, 37)),
        dtype=pd.ArrowDtype(pa.decimal128(38, 37)),
    )
    py_output = pd.DataFrame({"A": [1, 2, 3], "B": B_out})
    check_func(
        impl,
        (df,),
        sort_output=True,
        reset_index=True,
        check_dtype=False,
        py_output=py_output,
    )

    # Group 2 will overflow
    B = pd.array(
        pa.array(["0.01", "3.3", None] * 10).cast(pa.decimal128(38, 37)),
        dtype=pd.ArrowDtype(pa.decimal128(38, 37)),
    )
    df = pd.DataFrame({"A": [1, 2, 3] * 10, "B": B})
    with pytest.raises(
        RuntimeError, match="Overflow detected in groupby sum of Decimal data"
    ):
        bodo.jit(impl)(df)


def test_random_string_sum_min_max_first_last(memory_leak_check):
    def impl1(df):
        A = df.groupby("A").sum()
        return A

    def impl2(df):
        A = df.groupby("A").min()
        return A

    def impl3(df):
        A = df.groupby("A").max()
        return A

    def impl4(df):
        A = df.groupby("A").first()
        return A

    def impl5(df):
        A = df.groupby("A").last()
        return A

    def random_dataframe(n):
        random.seed(5)
        eList_A = []
        eList_B = []
        # String cols are dropped for sum, so we need an extra column to avoid empty output in that case
        eList_C = []
        for _ in range(n):
            len_str = random.randint(1, 10)
            k2 = random.randint(1, len_str)
            nonascii_val_B = " ".join(random.sample(gen_nonascii_list(k2), k2))
            val_A = random.randint(1, 10)
            val_B = nonascii_val_B.join(
                random.choices(string.ascii_uppercase, k=(len_str - k2))
            )
            eList_A.append(val_A)
            eList_B.append(val_B)
            eList_C.append(1)
        # add a group with all NA values in string data column
        eList_A.append(0)
        eList_A.append(0)
        eList_B.append(None)
        eList_B.append(None)
        eList_C.append(3)
        eList_C.append(4)
        return pd.DataFrame({"A": eList_A, "B": eList_B, "C": eList_C})

    df1 = random_dataframe(100)
    # Pandas 2.0 outputs 0 for all NA string column which is wrong
    check_func(
        impl1, (df1,), sort_output=True, py_output=df1.groupby("A").sum().replace(0, "")
    )
    check_func(impl2, (df1,), sort_output=True)
    check_func(impl3, (df1,), sort_output=True)
    check_func(impl4, (df1,), sort_output=True)
    check_func(impl5, (df1,), sort_output=True)


def test_random_binary_sum_min_max_first_last(memory_leak_check):
    def impl1(df):
        A = df.groupby("A").sum()
        return A

    # Needs support for max and min for binary data. See BE-1252

    # def impl2(df):
    #     A = df.groupby("A").min()
    #     return A

    # def impl3(df):
    #     A = df.groupby("A").max()
    #     return A

    def impl4(df):
        A = df.groupby("A").first()
        return A

    def impl5(df):
        A = df.groupby("A").last()
        return A

    def random_dataframe(n):
        random.seed(5)
        eList_A = []
        eList_B = []
        # String cols are dropped for sum, so we need an extra column to avoid empty output in that case
        eList_C = []
        for i in range(n):
            val_A = random.randint(1, 10)
            val_B = bytes(random.randint(1, 10))
            eList_A.append(val_A)
            eList_B.append(val_B)
            eList_C.append(1)
        return pd.DataFrame({"A": eList_A, "B": eList_B, "C": eList_C})

    df1 = random_dataframe(100)
    check_func(impl1, (df1,), sort_output=True)
    # check_func(impl2, (df1,), sort_output=True)
    # check_func(impl3, (df1,), sort_output=True)
    check_func(impl4, (df1,), sort_output=True)
    check_func(impl5, (df1,), sort_output=True)


@pytest_mark_pandas
def test_groupby_missing_entry(is_slow_run, memory_leak_check):
    """The columns which cannot be processed cause special problems as they are
    sometimes dropped instead of failing. This behavior is expected to raise an error
    in future versions of Pandas.
    """

    def test_drop_sum(df):
        return df.groupby("A").sum()

    def test_drop_count(df):
        return df.groupby("A").count()

    df1 = pd.DataFrame(
        {"A": [3, 2, 3], "B": pd.date_range("2017-01-03", periods=3), "C": [3, 1, 2]}
    )
    df2 = pd.DataFrame(
        {
            "A": [3, 2, 3, 1, 11] * 3,
            2: ["aa", "bb", "cc", "", "L"] * 3,
            "C": [3, 1, 2, 0, -3] * 3,
        }
    )
    df3 = pd.DataFrame(
        {
            "A": [3, 2, 3, 1, 11] * 3,
            "B": ["aa", "bb", "cc", "", "AA"] * 3,
            "C": [3, 1, 2, 0, -3] * 3,
        }
    )
    # TODO[BSE-2010] raise error on groupby sum of datetime to match Pandas 2
    # check_func(test_drop_sum, (df1,), sort_output=True, check_typing_issues=False)
    if not is_slow_run:
        return
    check_func(test_drop_sum, (df2,), sort_output=True, check_typing_issues=False)
    check_func(test_drop_sum, (df3,), sort_output=True, check_typing_issues=False)
    check_func(test_drop_count, (df1,), sort_output=True, check_typing_issues=False)
    check_func(test_drop_count, (df2,), sort_output=True, check_typing_issues=False)
    check_func(test_drop_count, (df3,), sort_output=True, check_typing_issues=False)


@pytest_mark_pandas
def test_agg_str_key(memory_leak_check):
    """
    Test Groupby.agg() with string keys
    """

    def impl(df):
        A = df.groupby("A").agg(lambda x: x.sum())
        return A

    df = pd.DataFrame(
        {
            "A": ["AA", "B", "B", "B", "AA", "AA", "B"],
            "B": [-8, 2, 3, 1, 5, 6, 7],
        }
    )
    check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
def test_agg_nonascii_str_key(memory_leak_check):
    """
    Test Groupby.agg() with non-ASCII string keys
    """

    def impl(df):
        A = df.groupby("A").agg(lambda x: x.sum())
        return A

    df = pd.DataFrame(
        {
            "A": ["AA", "B", "B", "B", "AA", "AA", "B"],
            " ".join(gen_nonascii_list(1)): [-8, 2, 3, 1, 5, 6, 7],
        }
    )
    check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
def test_agg_binary_key(memory_leak_check):
    """
    Test Groupby.agg() with binary keys
    """

    def impl(df):
        A = df.groupby("A").agg(lambda x: x.sum())
        return A

    df = pd.DataFrame(
        {
            "A": [b"AA", b"B", b"B", b"B", b"AA", b"AA", b"B"],
            "B": [-8, 2, 3, 1, 5, 6, 7],
        }
    )
    check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
def test_agg_series_input(memory_leak_check):
    """
    Test Groupby.agg(): make sure input to UDF is a Series, not Array
    """

    def impl(df):
        # using `count` since Arrays don't support it
        A = df.groupby("A").agg(lambda x: x.count())
        return A

    # check_dtype=False since Pandas returns float64 for count sometimes for some reason
    # no nullable float since NAs aren't handled in UDFs yet
    check_func(
        impl,
        (udf_in_df,),
        sort_output=True,
        check_dtype=False,
        convert_to_nullable_float=False,
    )


@pytest_mark_pandas
def test_agg_bool_expr(memory_leak_check):
    """
    Test Groupby.agg(): make sure boolean expressions work (#326)
    """

    def impl(df):
        return df.groupby("A")["B"].agg(lambda x: ((x == "A") | (x == "B")).sum())

    df = pd.DataFrame({"A": [1, 2, 1, 2] * 2, "B": ["A", "B", "C", "D"] * 2})
    check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.parametrize(
    "df_index",
    [
        pd.DataFrame(
            {
                "A": [np.nan, 1.0, np.nan, 1.0, 2.0, 2.0, 2.0],
                "B": [1, 2, 3, 2, 1, 1, 1],
                "C": [3, 5, 6, 5, 4, 4, 3],
            },
            index=[-1, 2, -3, 0, 4, 5, 2],
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [np.nan, 1.0, np.nan, 1.0, 2.0, 2.0, 2.0],
                    "B": [1, 2, 3, 2, 1, 1, 1],
                    "C": [3, 5, 6, 5, 4, 4, 3],
                },
                index=["a", "b", "c", "d", "e", "f", "g"],
            ),
            marks=pytest.mark.slow,
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [np.nan, 1.0, np.nan, 1.0, 2.0, 2.0, 2.0],
                    "B": [1, 2, 3, 2, 1, 1, 1],
                    "C": [3, 5, 6, 5, 4, 4, 3],
                },
                index=["e", "r", "x", "u", "v", "w", "z"],
            ),
            marks=pytest.mark.slow,
        ),
    ],
)
def test_cumsum_index_preservation(df_index, memory_leak_check):
    """For the cumsum operation, the number of rows remains the same and the index is preserved."""

    def test_impl_basic(df1):
        df2 = df1.groupby("B").cumsum()
        return df2

    def test_impl_both(df1):
        df2 = df1.groupby("B")["C"].agg(("cumprod", "cumsum"))
        return df2

    def test_impl_all(df1):
        df2 = df1.groupby("B").agg(
            {"A": ["cumprod", "cumsum"], "C": ["cumprod", "cumsum"]}
        )
        return df2

    check_func(test_impl_basic, (df_index,), sort_output=True, check_dtype=False)
    check_func(test_impl_both, (df_index,), sort_output=True, check_dtype=False)
    check_func(test_impl_all, (df_index,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_cumsum_random_index(memory_leak_check):
    def test_impl(df1):
        df2 = df1.groupby("B").cumsum()
        return df2

    def get_random_dataframe_A(n):
        eListA = []
        eListB = []
        for i in range(n):
            eValA = random.randint(1, 10)
            eValB = random.randint(1, 10)
            eListA.append(eValA)
            eListB.append(eValB)
        return pd.DataFrame({"A": eListA, "B": eListB})

    def get_random_dataframe_B(n):
        eListA = []
        eListB = []
        eListC = []
        for i in range(n):
            eValA = random.randint(1, 10)
            eValB = random.randint(1, 10)
            eValC = random.randint(1, 10) + 20
            eListA.append(eValA)
            eListB.append(eValB)
            eListC.append(eValC)
        return pd.DataFrame({"A": eListA, "B": eListB}, index=eListC)

    def get_random_dataframe_C(n):
        eListA = []
        eListB = []
        eListC = []
        for i in range(n):
            eValA = random.randint(1, 10)
            eValB = random.randint(1, 10)
            eValC = chr(random.randint(ord("a"), ord("z")))
            eListA.append(eValA)
            eListB.append(eValB)
            eListC.append(eValC)
        return pd.DataFrame({"A": eListA, "B": eListB}, index=eListC)

    random.seed(5)
    n = 100
    df1 = get_random_dataframe_A(n)
    df2 = get_random_dataframe_B(n)
    df3 = get_random_dataframe_C(n)

    # We have to reset the index for df1 since its index is trivial.
    check_func(test_impl, (df1,), sort_output=True, check_dtype=False, reset_index=True)
    check_func(test_impl, (df2,), sort_output=True, check_dtype=False)
    check_func(test_impl, (df3,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_cumsum_reverse_shuffle_list_string(memory_leak_check):
    """We want to use here the classical scheme of the groupby for cumsum.
    We trigger it by using strings which are not supported by the Exscan scheme"""

    def f(df):
        df["C"] = df.groupby("A").B.cumsum()
        return df

    random.seed(5)
    n = 100
    colA = [random.randint(0, 10) for _ in range(n)]

    df = pd.DataFrame({"A": colA, "B": gen_random_list_string_array(3, n)})
    bodo_f = bodo.jit(f)
    # We use the output of bodo because the functionality is missing from pandas
    df_out = bodo_f(df)
    check_func(f, (df,), convert_columns_to_pandas=True, py_output=df_out)


@pytest_mark_pandas
@pytest.mark.slow
def test_cumsum_reverse_shuffle_string(memory_leak_check):
    """We want to use here the classical scheme of the groupby for cumsum.
    We trigger it by using strings which are not supported by the Exscan scheme"""

    def f(df):
        df["C"] = df.groupby("A").B.cumsum()
        return df

    random.seed(5)
    n = 10
    colA = [random.randint(0, 10) for _ in range(n)]
    colB = [
        "".join(random.choices(["A", "B", "C"], k=random.randint(3, 10)))
        for _ in range(n)
    ]
    df = pd.DataFrame({"A": colA, "B": colB})
    bodo_f = bodo.jit(f)
    # We use the output of bodo because the functionality is missing from pandas
    df_out = bodo_f(df)
    check_func(f, (df,), py_output=df_out)


@pytest_mark_pandas
@pytest.mark.slow
def test_cumsum_reverse_shuffle_large_numpy(memory_leak_check):
    """We want to use here the classical scheme of the groupby for cumsum.
    We trigger it by using strings which are not supported by the Exscan scheme"""

    def f(df):
        df["C"] = df.groupby("A").B.cumsum()
        return df

    random.seed(5)
    n = 10000
    n_key = 10000
    colA = [random.randint(0, n_key) for _ in range(n)]
    colB = [random.randint(0, 50) for _ in range(n)]
    df = pd.DataFrame({"A": colA, "B": colB})
    check_func(f, (df,))


@pytest_mark_pandas
def test_sum_categorical_key(memory_leak_check):
    """Testing of categorical keys and their missing value"""

    def f(df):
        return df.groupby("A", as_index=False).sum()

    def get_categorical_column(prob, n):
        elist = []
        for _ in range(n):
            if random.random() < prob:
                value = None
            else:
                value = "".join(random.choices(["A", "B", "C"], k=3))
            elist.append(value)
        return pd.Categorical(elist)

    random.seed(5)
    n = 100
    # Select NaN with probability 10% and otherwise single characters.
    colA = get_categorical_column(0.1, n)
    colB = [random.randint(0, 10) for _ in range(n)]
    df = pd.DataFrame({"A": colA, "B": colB})
    check_func(f, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_all_categorical_count(memory_leak_check):
    """Testing of categorical keys and their missing value.
    Also the count itself is done for a categorical column with missing value"""

    def f(df):
        return df.groupby("A", as_index=False).count()

    def get_categorical_column(prob, n):
        elist = []
        for _ in range(n):
            if random.random() < prob:
                value = None
            else:
                value = "".join(random.choices(["A", "B", "C"], k=3))
            elist.append(value)
        return pd.Categorical(elist)

    random.seed(5)
    n = 100
    # Select NaN with probability 10% and otherwise single characters.
    colA = get_categorical_column(0.1, n)
    colB = get_categorical_column(0.1, n)
    df = pd.DataFrame({"A": colA, "B": colB})
    check_func(f, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_cumsum_exscan_categorical_random(memory_leak_check):
    """For categorical and cumsum, a special code path allows for better performance"""

    def f1(df):
        return df.groupby("A").cumsum(skipna=False)

    def f2(df):
        return df.groupby("A").cumsum(skipna=True)

    def random_f_nan():
        if random.random() < 0.1:
            return np.nan
        return random.random()

    def get_random_nullable_column(n):
        elist = []
        for _ in range(n):
            prob = random.randint(1, 10)
            if prob == 1:
                elist.append(None)
            else:
                elist.append(prob)
        return pd.array(elist, dtype="UInt16")

    def get_random_categorical_column(prob_none, n):
        elist = []
        for _ in range(n):
            prob = random.randint(1, 10)
            if prob == prob_none:
                elist.append(None)
            else:
                elist.append("".join(random.choices(["A", "B", "C"], k=3)))
        return pd.Categorical(elist)

    random.seed(5)
    n = 10
    list_A1 = get_random_categorical_column(-1, n)
    list_A2 = get_random_categorical_column(1, n)
    list_B_i = [random.randint(1, 100) for _ in range(n)]
    list_C_f = [random.random() for _ in range(n)]
    list_D_f_nan = [random_f_nan() for _ in range(n)]
    list_E_i_null = get_random_nullable_column(n)
    df1 = pd.DataFrame(
        {
            "A": list_A1,
            "B": list_B_i,
            "C": list_C_f,
            "D": list_D_f_nan,
            "E": list_E_i_null,
        }
    )
    df2 = pd.DataFrame(
        {"A": list_A2, "C": list_C_f, "D": list_D_f_nan, "E": list_E_i_null}
    )
    check_func(f1, (df1,), check_dtype=False)
    check_func(f2, (df1,), check_dtype=False)
    # Replace NaN in Pandas output (Pandas bug) to avoid output comparison issues
    check_func(
        f1,
        (df2,),
        check_dtype=False,
        py_output=df2.groupby("A")
        .cumsum(skipna=False)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )
    check_func(
        f2,
        (df2,),
        check_dtype=False,
        py_output=df2.groupby("A")
        .cumsum(skipna=True)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )


@pytest_mark_pandas
@pytest.mark.slow
def test_cumsum_exscan_multikey_random(memory_leak_check):
    """For cumulative sum of integers, a special code that create a categorical key column
    allows for better performance"""

    def f(df):
        return df.groupby(["A", "B"]).cumsum()

    def random_f_nan():
        if random.random() < 0.1:
            return np.nan
        return random.random()

    def get_random_nullable_column(n):
        elist = []
        for _ in range(n):
            prob = random.randint(1, 10)
            if prob == 1:
                elist.append(None)
            else:
                elist.append(prob)
        return pd.array(elist, dtype="UInt16")

    random.seed(5)
    n = 100
    list_A_key1 = get_random_nullable_column(n)
    list_B_key2 = get_random_nullable_column(n)
    list_C_f = [random.random() for _ in range(n)]
    list_D_f_nan = [random_f_nan() for _ in range(n)]
    list_E_i_null = get_random_nullable_column(n)
    df = pd.DataFrame(
        {
            "A": list_A_key1,
            "B": list_B_key2,
            "C": list_C_f,
            "D": list_D_f_nan,
            "E": list_E_i_null,
        }
    )
    check_func(
        f,
        (df,),
        check_dtype=False,
        py_output=df.groupby(["A", "B"])
        .cumsum()
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )


@pytest_mark_pandas
@pytest.mark.slow
def test_sum_max_min_list_string_random(memory_leak_check):
    """Tests for columns being a list of strings.
    We have to use as_index=False since list of strings are mutable
    and index are immutable so cannot be an index"""

    def test_impl1(df1):
        df2 = df1.groupby("A", as_index=False).sum()
        return df2

    def test_impl2(df1):
        df2 = df1.groupby("A", as_index=False).max()
        return df2

    def test_impl3(df1):
        df2 = df1.groupby("A", as_index=False).min()
        return df2

    def test_impl4(df1):
        df2 = df1.groupby("A", as_index=False).first()
        return df2

    def test_impl5(df1):
        df2 = df1.groupby("A", as_index=False).last()
        return df2

    def test_impl6(df1):
        df2 = df1.groupby("A", as_index=False).count()
        return df2

    def test_impl7(df1):
        df2 = df1.groupby("A", as_index=False)["B"].agg(("sum", "min", "max", "last"))
        return df2

    def test_impl8(df1):
        df2 = df1.groupby("A", as_index=False).nunique()
        return df2

    random.seed(5)

    n = 10
    df1 = pd.DataFrame(
        {
            "A": gen_random_list_string_array(2, n),
            "B": gen_random_list_string_array(2, n),
            # String cols are now dropped for sum, so we need an extra column to avoid empty output
            "C": [1] * n,
        }
    )

    def check_fct(the_fct, df1, select_col_comparison):
        bodo_fct = bodo.jit(the_fct)
        # Computing images via pandas and pandas but applying the merging of columns
        df1_merge = convert_non_pandas_columns(df1)
        df2_merge_preA = the_fct(df1_merge)
        df2_merge_A = df2_merge_preA[select_col_comparison]
        df2_merge_preB = convert_non_pandas_columns(bodo_fct(df1))
        df2_merge_B = df2_merge_preB[select_col_comparison]
        # Now comparing the results.
        list_col_names = df2_merge_A.columns.to_list()
        df2_merge_A_sort = df2_merge_A.sort_values(by=list_col_names).reset_index(
            drop=True
        )
        df2_merge_B_sort = df2_merge_B.sort_values(by=list_col_names).reset_index(
            drop=True
        )
        pd.testing.assert_frame_equal(
            df2_merge_A_sort,
            df2_merge_B_sort,
            check_dtype=False,
            check_column_type=False,
        )
        # Now doing the parallel check
        check_parallel_coherency(the_fct, (df1,), sort_output=True, reset_index=True)

    # For nunique, we face the problem of difference of formatting between nunique
    # in Bodo and in Pandas.
    check_func(
        test_impl1,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    check_func(
        test_impl2,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    check_func(
        test_impl3,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    check_func(
        test_impl4,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    check_func(
        test_impl5,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    check_func(
        test_impl6,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    # For test_impl7, we have an error in as_index=False function, that is:
    # df1.groupby("A", as_index=False)["B"].agg(("sum", "min", "max"))
    #
    # The problem is that pandas does it in a way that we consider erroneous.
    check_fct(test_impl7, df1, ["sum", "min", "max", "last"])

    # For test_impl8 we face the problem that pandas returns a wrong column
    # for the A. multiplicities are given (always 1) instead of the values.
    check_fct(test_impl8, df1, ["B"])


@pytest_mark_pandas
def test_groupby_datetime_miss(memory_leak_check):
    """Testing the groupby with columns having datetime with missing entries
    TODO: need to support the cummin/cummax cases after pandas is corrected"""

    def test_impl1(df):
        A = df.groupby("A", as_index=False).min()
        return A

    def test_impl2(df):
        A = df.groupby("A", as_index=False).max()
        return A

    def test_impl3(df):
        A = df.groupby("A").first()
        return A

    def test_impl4(df):
        A = df.groupby("A").last()
        return A

    def test_impl5(df):
        A = df.groupby("A").count()
        return A

    random.seed(5)

    def get_small_list(shift, elen):
        small_list_date = []
        for _ in range(elen):
            e_year = random.randint(shift, shift + 20)
            e_month = random.randint(1, 12)
            e_day = random.randint(1, 28)
            small_list_date.append(datetime.datetime(e_year, e_month, e_day))
        return small_list_date

    def get_random_entry(small_list):
        if random.random() < 0.2:
            return pd.NaT
        else:
            pos = random.randint(0, len(small_list) - 1)
            return small_list[pos]

    n_big = 100
    col_a = []
    col_b = []
    small_list_a = get_small_list(1940, 5)
    small_list_b = get_small_list(1920, 20)
    for idx in range(n_big):
        col_a.append(get_random_entry(small_list_a))
        col_b.append(get_random_entry(small_list_b))
    df1 = pd.DataFrame({"A": pd.Series(col_a), "B": pd.Series(col_b)})

    check_func(
        test_impl1, (df1,), sort_output=True, check_dtype=False, reset_index=True
    )
    check_func(
        test_impl2, (df1,), sort_output=True, check_dtype=False, reset_index=True
    )
    # TODO: solve the bug below. We should not need to have a reset_index=True
    check_func(test_impl3, (df1,), sort_output=True)
    check_func(test_impl4, (df1,), sort_output=True)
    check_func(test_impl5, (df1,), sort_output=True)


def test_agg_as_index_fast(memory_leak_check):
    """
    Test Groupby.agg() on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(df):
        A = df.groupby("A", as_index=False).agg(lambda x: x.max() - x.min())
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [1.2, 2.4, np.nan, 2.2, 5.3, 3.3, 7.2],
        }
    )

    check_func(impl1, (df,), sort_output=True, check_dtype=False, reset_index=True)


@pytest.mark.slow
def test_agg_as_index(memory_leak_check):
    """
    Test Groupby.agg() on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl2(df):
        A = df.groupby("A", as_index=False)["B"].agg(lambda x: x.max() - x.min())
        return A

    def impl3(df):
        A = df.groupby("A", as_index=False)["B"].agg({"B": "sum"})
        return A

    def impl3b(df):
        A = df.groupby(["A", "B"], as_index=False)["C"].agg({"C": "sum"})
        return A

    def impl4(df):
        def id1(x):
            return (x <= 2).sum()

        def id2(x):
            return (x > 2).sum()

        A = df.groupby("A", as_index=False)["B"].agg((id1, id2))
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [1.2, 2.4, np.nan, 2.2, 5.3, 3.3, 7.2],
        }
    )

    # disabled because this doesn't work in pandas 1.0 (looks like a bug)
    # check_func(impl2, (df,), sort_output=True, check_dtype=False)
    check_func(impl3, (df,), sort_output=True, reset_index=True)
    check_func(impl3b, (df,), sort_output=True, reset_index=True)
    check_func(impl4, (df,), sort_output=True, reset_index=True)


@pytest.mark.skip
def test_agg_dt64(memory_leak_check):
    """
    Test using groupby.agg with dt64 column values. [BE-735]
    """

    def test_impl(df):
        A = df.groupby("A").agg(lambda x: x.max())
        return A

    df = pd.DataFrame(
        {
            "A": [1, 2, 3, 2, 1],
            "B": pd.Series(pd.date_range(start="1/1/2018", end="1/08/2018", periods=5)),
        }
    )
    check_func(test_impl, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_agg_td64(memory_leak_check):
    """
    Test using groupby.agg with td64 column values. [BE-733]
    """

    def test_impl(df):
        A = df.groupby("A").agg(lambda x: x.sum())
        return A

    df = pd.DataFrame(
        {
            "A": [1, 2, 3, 2, 1],
            "B": pd.Series(pd.timedelta_range(start="1 day", periods=5)),
        }
    )
    check_func(test_impl, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_agg_select_col_fast(memory_leak_check):
    """
    Test Groupby.agg() with explicitly select one (str)column
    """

    def impl_str(df):
        A = df.groupby("A")["B"].agg(lambda x: (x == "a").sum())
        return A

    df_str = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": ["a", "b", "c", "c", "b", "c", "a"],
            "C": gen_nonascii_list(7),
        }
    )

    check_func(impl_str, (df_str,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_agg_select_col(memory_leak_check):
    """
    Test Groupby.agg() with explicitly select one column
    """

    def impl_num(df):
        A = df.groupby("A")["B"].agg(lambda x: x.max() - x.min())
        return A

    def test_impl(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].agg(lambda x: x.max() - x.min())
        return A

    df_int = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1], "B": [1, 2, 3, 4, 5, 6, 7]})
    df_float = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": [1.2, 2.4, np.nan, 2.2, 5.3, 3.3, 7.2]}
    )
    check_func(impl_num, (df_int,), sort_output=True, check_dtype=False)
    check_func(impl_num, (df_float,), sort_output=True, check_dtype=False)
    check_func(test_impl, (11,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
def test_agg_no_parfor(memory_leak_check):
    """
    Test Groupby.agg(): simple UDF with no parfor
    """

    def impl1(df):
        A = df.groupby("A").agg(lambda x: 1)
        return A

    def impl2(df):
        A = df.groupby("A").agg(lambda x: len(x))
        return A

    check_func(impl1, (udf_in_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (udf_in_df,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
def test_agg_len_mix(memory_leak_check):
    """
    Test Groupby.agg(): use of len() in a UDF mixed with another parfor
    """

    def impl(df):
        A = df.groupby("A").agg(lambda x: x.sum() / len(x))
        return A

    check_func(
        impl,
        (udf_in_df,),
        sort_output=True,
        check_dtype=False,
        convert_to_nullable_float=False,
    )


def agg_multi_udf_impl(df):
    def id1(x):
        return (x <= 2).sum()

    def id2(x):
        return (x > 2).sum()

    return df.groupby("A")["B"].agg((id1, id2))


def agg_multi_udf_impl2(df):
    def id1(x):
        return (x <= 2).sum()

    def id2(x):
        return (x > 2).sum()

    return df.groupby("A")["B"].agg(("var", id1, id2, "sum"))


# check_dtype=False for impl3 since Bodo returns float for Series.min/max. TODO: fix min/max
def agg_multi_udf_impl3(df):
    return df.groupby("A")["B"].agg(
        (lambda x: x.max() - x.min(), lambda x: x.max() + x.min())
    )


def agg_multi_udf_impl4(df):
    return df.groupby("A")["B"].agg(("cumprod", "cumsum"))


@pytest_mark_pandas
@pytest.mark.parametrize(
    "impl",
    [
        agg_multi_udf_impl,
        agg_multi_udf_impl2,
        agg_multi_udf_impl3,
        agg_multi_udf_impl4,
    ],
)
def test_agg_multi_udf(impl, memory_leak_check):
    """
    Test Groupby.agg() multiple user defined functions
    """

    df = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": [1, 2, 3, 4, 5, 6, 7]},
        index=[7, 8, 9, 2, 3, 4, 5],
    )

    if impl in {agg_multi_udf_impl2, agg_multi_udf_impl3}:
        check_func(impl, (df,), sort_output=True, check_dtype=False)
    elif impl in {agg_multi_udf_impl, agg_multi_udf_impl4}:
        check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
def test_series_groupby_max_min_cat(memory_leak_check):
    """
    Tests support for GroupBy.max/min on Ordered Categorical Data. This tests
    both categories known and unknown at compile time.
    """

    def test_impl1(S):
        return S.groupby(level=0).max()

    def test_impl2(S):
        return S.groupby(level=0).min()

    def test_impl3(S):
        # Generate categories at runtime
        cats = np.sort(bodo.allgatherv(S.dropna().unique()))
        S = pd.Series(pd.Categorical(S, cats, ordered=True))
        return S.groupby(level=0).max()

    def test_impl4(S):
        # Generate categories at runtime
        cats = np.sort(bodo.allgatherv(S.dropna().unique()))
        S = pd.Series(pd.Categorical(S, cats, ordered=True))
        return S.groupby(level=0).min()

    S1 = pd.Series(pd.Categorical([1, 2, 5, None, 2] * 4, ordered=True))
    S2 = pd.Series(pd.array([1, 2, 5, None, 2] * 4))
    check_func(
        test_impl1,
        (S1,),
        sort_output=True,
        py_output=test_impl1(S1).astype(S1.dtype),
        check_names=False,
    )
    check_func(
        test_impl2,
        (S1,),
        sort_output=True,
        py_output=test_impl2(S1).astype(S1.dtype),
        check_names=False,
    )
    check_func(
        test_impl3,
        (S2,),
        sort_output=True,
        reset_index=True,
        py_output=test_impl1(S1).astype(S1.dtype),
        check_names=False,
        check_categorical=False,
    )
    check_func(
        test_impl4,
        (S2,),
        sort_output=True,
        reset_index=True,
        py_output=test_impl2(S1).astype(S1.dtype),
        check_names=False,
        check_categorical=False,
    )


@pytest_mark_pandas
@pytest.mark.slow
def test_aggregate(memory_leak_check):
    """
    Test Groupby.aggregate(): one user defined func and all cols
    """

    def impl(df):
        A = df.groupby("A").aggregate(lambda x: x.max() - x.min())
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [1.2, 2.4, np.nan, 2.2, 5.3, 3.3, 7.2],
        }
    )

    check_func(impl, (df,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_aggregate_as_index(memory_leak_check):
    """
    Test Groupby.aggregate() on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(df):
        A = df.groupby("A", as_index=False).aggregate(lambda x: x.max() - x.min())
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [1.2, 2.4, np.nan, 2.2, 5.3, 3.3, 7.2],
        }
    )

    check_func(impl1, (df,), sort_output=True, check_dtype=False, reset_index=True)


@pytest_mark_pandas
def test_aggregate_select_col(is_slow_run, memory_leak_check):
    """
    Test Groupby.aggregate() with explicitly select one column
    """

    def impl_num(df):
        A = df.groupby("A")["B"].aggregate(lambda x: x.max() - x.min())
        return A

    def impl_str(df):
        A = df.groupby("A")["B"].aggregate(lambda x: (x == "a").sum())
        return A

    def test_impl(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].aggregate(lambda x: x.max() - x.min())
        return A

    df_int = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1], "B": [1, 2, 3, 4, 5, 6, 7]})
    df_float = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": [1.2, 2.4, np.nan, 2.2, 5.3, 3.3, 7.2]}
    )
    df_str = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": ["a", "b", "c", "c", "b", "c", "a"],
            "C": gen_nonascii_list(7),
        }
    )
    check_func(impl_num, (df_int,), sort_output=True, check_dtype=False)
    if not is_slow_run:
        return
    check_func(impl_num, (df_float,), sort_output=True, check_dtype=False)
    check_func(impl_str, (df_str,), sort_output=True, check_dtype=False)
    check_func(test_impl, (11,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
def test_groupby_agg_general_udf(memory_leak_check):
    """
    Test groupy.agg with mix of general UDFs, regular UDF and builtin aggregation functions
    """

    def impl(df):
        def f(x):  # regular UDF
            return sum(x) ** 2

        def g(x):  # general UDF
            z = x.iloc[1]
            z += x.iloc[0] + x.iloc[2]
            return x.iloc[0] + z

        def h(x):  # general UDF
            return sum(x / len(x))

        def i(x):  # general UDF
            res = 0
            for i in range(len(x)):
                if x.iloc[i] < 5:
                    res += 1
                elif x.iloc[i] < 8:
                    res += 2
                else:
                    res += 3
            return res

        res = df.groupby("A")["B"].agg(("var", h, f, i, "sum", g))
        return res

    df = pd.DataFrame({"A": [0, 0, 1, 1, 1, 0], "B": [3, 10, 20, 4, 5, 1]})
    # Note: var always outputs nullable float in Bodo, so we disable the check_dtype
    check_func(
        impl,
        (df,),
        sort_output=True,
        convert_to_nullable_float=False,
        check_dtype=False,
    )


# ------------ Test function implementations to be used in test_groupby_agg_const_dict_part1 ------------
def groupby_agg_const_dict_impl(df):
    df2 = df.groupby("A")[["B", "C"]].agg({"B": "count", "C": "sum"})
    return df2


def groupby_agg_const_dict_impl2(df):
    df2 = df.groupby("A").agg({"B": "count", "C": "sum"})
    return df2


def groupby_agg_const_dict_impl3(df):
    df2 = df.groupby("A").agg({"B": "median"})
    return df2


def groupby_agg_const_dict_impl4(df):
    df2 = df.groupby("A").agg({"B": ["median"]})
    return df2


def groupby_agg_const_dict_impl5(df):
    df2 = df.groupby("A").agg({"D": "nunique", "B": "median", "C": "var"})
    return df2


def groupby_agg_const_dict_impl6(df):
    df2 = df.groupby("A").agg({"B": ["median", "nunique"]})
    return df2


def groupby_agg_const_dict_impl7(df):
    df2 = df.groupby("A").agg({"B": ["count", "var", "prod"], "C": ["std", "sum"]})
    return df2


def groupby_agg_const_dict_impl8(df):
    df2 = df.groupby("A", as_index=False).agg(
        {"B": ["count", "var", "prod"], "C": ["std", "sum"]}
    )
    return df2


def groupby_agg_const_dict_impl9(df):
    df2 = df.groupby("A").agg({"B": ["count", "var", "prod"], "C": "std"})
    return df2


def groupby_agg_const_dict_impl10(df):
    df2 = df.groupby("A").agg({"B": ["count", "var", "prod"], "C": ["std"]})
    return df2


def groupby_agg_const_dict_impl11(df):
    df2 = df.groupby("A").agg(
        {"B": ["count", "median", "prod"], "C": ["nunique", "sum"]}
    )
    return df2


def groupby_agg_const_dict_impl12(df):
    def id1(x):
        return (x >= 2).sum()

    df2 = df.groupby("D").agg({"B": "var", "A": id1, "C": "sum"})
    return df2


def groupby_agg_const_dict_impl13(df):
    df2 = df.groupby("D").agg({"B": lambda x: x.max() - x.min(), "A": "sum"})
    return df2


def groupby_agg_const_dict_impl14(df):
    df2 = df.groupby("A").agg(
        {
            "D": lambda x: (x == "BB").sum(),
            "B": lambda x: x.max() - x.min(),
            "C": "sum",
        }
    )
    return df2


def groupby_agg_const_dict_impl15(df):
    df2 = df.groupby("A").agg({"B": "cumsum", "C": "cumprod"})
    return df2


# reuse a complex dict to test typing transform for const dict removal
def groupby_agg_const_dict_impl16(df):
    d = {"B": [lambda a: a.sum(), "mean"]}
    df1 = df.groupby("A").agg(d)
    df2 = df.groupby("C").agg(d)
    return df1, df2


# reuse and return a const dict to test typing transform
def groupby_agg_const_dict_impl17(df):
    d = {"B": "sum"}
    df1 = df.groupby("A").agg(d)
    df2 = df.groupby("C").agg(d)
    return df1, df2, d


# test tuple of UDFs inside agg dict
def groupby_agg_const_dict_impl18(df):
    return df.groupby("A").agg(
        {
            "C": (lambda x: (x >= 3).sum(),),
            "B": (lambda x: x.sum(), lambda x: (x < 6.1).sum()),
        }
    )


@pytest_mark_pandas
@pytest.mark.parametrize(
    "cur_impl",
    [
        groupby_agg_const_dict_impl,
        groupby_agg_const_dict_impl2,
        groupby_agg_const_dict_impl3,
        groupby_agg_const_dict_impl4,
        groupby_agg_const_dict_impl5,
        groupby_agg_const_dict_impl6,
        groupby_agg_const_dict_impl7,
        groupby_agg_const_dict_impl8,
        groupby_agg_const_dict_impl9,
        groupby_agg_const_dict_impl10,
        groupby_agg_const_dict_impl11,
        groupby_agg_const_dict_impl12,
        groupby_agg_const_dict_impl13,
        groupby_agg_const_dict_impl14,
        groupby_agg_const_dict_impl15,
        groupby_agg_const_dict_impl16,
        groupby_agg_const_dict_impl17,
        groupby_agg_const_dict_impl18,
    ],
)
def test_groupby_agg_const_dict(cur_impl, memory_leak_check):
    """
    Test groupy.agg with function spec passed as constant dictionary
    """

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "D": ["AA", "B", "BB", "B", "AA", "AA", "B"],
            "B": [-8.1, 2.1, 3.1, 1.1, 5.1, 6.1, 7.1],
            "C": [3, 5, 6, 5, 4, 4, 3],
        },
        index=np.arange(10, 17),
    )
    if (
        cur_impl is groupby_agg_const_dict_impl
        or cur_impl is groupby_agg_const_dict_impl2
    ):
        check_func(cur_impl, (df,), sort_output=True)
    elif cur_impl is groupby_agg_const_dict_impl8:
        check_func(
            cur_impl,
            (df,),
            sort_output=True,
            reset_index=True,
            convert_to_nullable_float=False,
            check_dtype=False,
        )
    elif cur_impl is groupby_agg_const_dict_impl16:
        # just check for compilation errors
        # can't use check_func since lambda name in MultiIndex doesn't match Pandas
        # TODO: fix lambda name
        # check_func(impl16, (df,), sort_output=True, reset_index=True)
        bodo.jit(cur_impl)(df)
    elif cur_impl is groupby_agg_const_dict_impl17:
        # TODO: enable is_out_distributed after fixing gatherv issue for tuple output
        check_func(
            cur_impl,
            (df,),
            sort_output=True,
            dist_test=False,
            convert_to_nullable_float=False,
        )
    else:
        # For median, var, etc. we output a Float64 even if the input is float64.
        # As a result we disable the dtype check.
        check_func(
            cur_impl,
            (df,),
            sort_output=True,
            convert_to_nullable_float=False,
            check_dtype=False,
        )


@pytest_mark_pandas
def test_groupby_agg_func_list(memory_leak_check):
    # TODO: Restore memory leak check
    """
    Test groupy.agg with list of functions in const dict input
    """

    def impl(df):
        return df.groupby("A").agg(
            {
                "C": [lambda x: (x >= 3).sum()],
                "B": [lambda x: x.sum(), lambda x: (x < 6.1).sum()],
            }
        )

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "D": ["AA", "B", "BB", "B", "AA", "AA", "B"],
            "B": [-8.1, 2.1, 3.1, 1.1, 5.1, 6.1, 7.1],
            "C": [3, 5, 6, 5, 4, 4, 3],
        },
        index=np.arange(10, 17),
    )
    check_func(impl, (df,), sort_output=True, check_dtype=False)
    # make sure regular optimized UDF path is taken
    bodo_func = bodo.jit(pipeline_class=DistTestPipeline)(impl)
    bodo_func(df)
    f_ir = bodo_func.overloads[bodo_func.signatures[0]].metadata["preserved_ir"]
    # general UDF codegen adds call to cpp_cb_general as a global
    assert not dist_IR_contains(f_ir, "global(cpp_cb_general:")


@pytest_mark_pandas
def test_groupby_agg_nullable_or(memory_leak_check):
    """
    Test groupy.agg with & and | can take the optimized path
    """

    def impl(df):
        return df.groupby("A").agg(
            {
                "D": lambda x: ((x == "AA") | (x <= "BB")).sum(),
            }
        )

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "D": ["AA", "B", "BB", "B", "AA", "AA", "B"],
            "B": [-8.1, 2.1, 3.1, 1.1, 5.1, 6.1, 7.1],
            "C": [3, 5, 6, 5, 4, 4, 3],
        },
        index=np.arange(10, 17),
    )
    check_func(impl, (df,), sort_output=True, check_dtype=False)
    # make sure regular optimized UDF path is taken
    bodo_func = bodo.jit(pipeline_class=DistTestPipeline)(impl)
    bodo_func(df)
    f_ir = bodo_func.overloads[bodo_func.signatures[0]].metadata["preserved_ir"]
    # general UDF codegen adds call to cpp_cb_general as a global
    assert not dist_IR_contains(f_ir, "global(cpp_cb_general:")


@pytest.mark.parametrize(
    "df",
    [
        pd.DataFrame(
            {
                "A": [2, 1, 1, 1, 2, 2, 1],
                "D": ["AA", "B", "BB", "B", "AA", "AA", "B"],
                "B": [-8.1, 2.1, 3.1, 1.1, 5.1, 6.1, 7.1],
                "C": [3, 5, 6, 5, 4, 4, 3],
                "E": [3, 3, 3, 4, 5, 6, 2],
                "F": [b"AA", b"B", b"BB", b"B", b"AA", b"AA", b"B"],
            },
            index=np.arange(10, 17),
        ),
        # There are many different paths that the gb nunique heuristic can
        # take (see gb_nunique_preprocess in _groupby.cpp). To try to test
        # most or all of these paths, this dataframe makes it so that for
        # np3 there are 20 unique groups locally but the ratio of
        # groups/num_local_rows is small enough so that
        # shuffle_before_update=false, and column B has enough duplicates
        # per group locally on each rank so that the nunique algorithm
        # decides to drop them before shuffling, but not for column D.
        # And the number of groups is large enough that nunique decides to
        # shuffle based on keys (instead of keys+values). Shuffling based
        # on keys+values is the common case in CI and is tested elsewhere.
        pytest.param(
            pd.DataFrame(
                {"A": list(range(20)) * 12, "D": list(range(40)) * 6, "B": [0] * 240}
            ),
            marks=pytest.mark.slow,
        ),
    ],
)
def test_groupby_nunique(df, memory_leak_check):
    """
    Test nunique only and with groupy.agg (nunique_mode:0, 1,2)
    """

    def impl0(df):
        """Test nunique alone (nunique_mode=0)"""
        df2 = df.groupby("A").agg({"D": "nunique"})
        return df2

    def impl1(df):
        """Test nunique with median (nunique_mode=1)"""
        df2 = df.groupby("A").agg({"D": "nunique", "B": "median"})
        return df2

    def impl2(df):
        """Test nunique with sum (nunique_mode=2)"""
        df2 = df.groupby("A").agg({"D": "nunique", "B": "sum"})
        return df2

    def impl3(df):
        """Test multiple nunique (nunique_mode=0)"""
        df2 = df.groupby("A").nunique()
        return df2

    check_func(impl0, (df,), sort_output=True)
    # Bodo currently always outputs a nullable float for median
    check_func(
        impl1,
        (df,),
        sort_output=True,
        convert_to_nullable_float=False,
        check_dtype=False,
    )
    check_func(impl2, (df,), sort_output=True, convert_to_nullable_float=False)
    check_func(impl3, (df,), sort_output=True)


def test_groupby_nunique_dropna(memory_leak_check):
    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 4, 2, 2, 1],
            # Nullable string
            "D": ["AA", None, "BB", "B", "AA", "AA", "B"],
            # Nullable float
            "B": [-8.1, 2.1, 3.1, 1.1, 5.1, 6.1, np.nan],
            # Nullable int
            "E": pd.Series([2, 1, 1, 4, None, 2, 1], dtype="Int32"),
            # Nullable dt64
            "G": pd.Series(
                [
                    pd.Timestamp(year=2021, month=6, day=1, hour=4),
                    pd.Timestamp(year=2021, month=6, day=1),
                    None,
                    pd.Timestamp(year=2020, month=2, day=4, microsecond=40),
                    pd.Timestamp(2020, 2, 4),
                    pd.Timestamp(year=2020, month=2, day=4),
                    None,
                ]
            ),
            # Nullable td64
            "F": pd.Series(
                [
                    pd.Timedelta(days=0),
                    pd.Timedelta(days=0, seconds=14),
                    None,
                    pd.Timedelta(days=-1, hours=6),
                    None,
                    pd.Timedelta(days=-1, hours=6),
                    pd.Timedelta(days=-1),
                ]
            ),
            # Nullable boolean
            "C": pd.Series([True, None, None, None, True, True, True], dtype="boolean"),
            # Nullable Binary
            "H": [b"AA", b"B", None, b"B", None, b"AA", b"B"],
        },
    )

    def impl0(df):
        """Test nunique dropna=False"""
        df2 = df.groupby("A").nunique(dropna=False)
        return df2

    def impl1(df):
        """Test nunique dropna=True (the default)"""
        df2 = df.groupby("A").nunique(dropna=True)
        return df2

    check_func(impl0, (df,), sort_output=True)
    check_func(impl1, (df,), sort_output=True)


def g(x):
    return (x == "a").sum()


@pytest_mark_pandas
@pytest.mark.slow
def test_agg_global_func(memory_leak_check):
    """
    Test Groupby.agg() with a global function as UDF
    """

    def impl_str(df):
        A = df.groupby("A")["B"].agg(g)
        return A

    df_str = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": ["a", "b", "c", "c", "b", "c", "a"],
            "C": gen_nonascii_list(7),
        }
    )

    check_func(impl_str, (df_str,), sort_output=True)
    # make sure regular optimized UDF path is taken
    bodo_func = bodo.jit(pipeline_class=DistTestPipeline)(impl_str)
    bodo_func(df_str)
    f_ir = bodo_func.overloads[bodo_func.signatures[0]].metadata["preserved_ir"]
    # general UDF codegen adds call to cpp_cb_general as a global
    assert not dist_IR_contains(f_ir, "global(cpp_cb_general:")


def test_count(memory_leak_check):
    """
    Test Groupby.count()
    """

    def impl1(df):
        A = df.groupby("A").count()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").count()
        return A

    df_int = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, np.nan, 3, 1, np.nan, 6, 7],
            "C": [1.1, 2.4, 3.1, -1.9, 2.3, 3.0, -2.4],
        }
    )
    df_str = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", None, "bb", "aa", None, "ggg", "rr"],
            "C": gen_nonascii_list(7),
        }
    )

    df_bool = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [True, None, False, True, None, False, False],
            "C": [True, True, False, True, True, False, False],
        }
    )
    df_dt = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    df_bin = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [b"", bytes(13), None, b"asd", b"wesds", b"asdk", None],
            "C": [b"alkj", b"lkjhg", b"w345", b"aszxd", b"poiu", bytes(5), b"lkjhg"],
        }
    )
    check_func(impl1, (df_int,), sort_output=True)
    check_func(impl1, (df_str,), sort_output=True)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl1, (df_dt,), sort_output=True)
    check_func(impl1, (df_bin,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_count_select_col(memory_leak_check):
    """
    Test Groupby.count() with explicitly select one column
    TODO: after groupby.count() properly ignores nulls, adds np.nan to df_str
    """

    def impl1(df):
        A = df.groupby("A")["B"].count()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].count()
        return A

    df_int = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, np.nan, 3, 1, np.nan, 6, 7],
            "C": [1.1, 2.4, 3.1, -1.9, 2.3, 3.0, -2.4],
        }
    )

    df_str = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", None, "bb", "aa", None, "ggg", "rr"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
            "D": gen_nonascii_list(7),
        }
    )
    df_bool = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [True, None, False, True, None, False, False],
            "C": [True, True, False, True, True, False, False],
        }
    )
    df_dt = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    df_bin = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [b"", bytes(13), None, b"asd", b"wesds", b"asdk", None],
            "C": [b"alkj", b"lkjhg", b"w345", b"aszxd", b"poiu", bytes(5), b"lkjhg"],
        }
    )
    check_func(impl1, (df_int,), sort_output=True, check_dtype=False)
    check_func(impl1, (df_str,), sort_output=True, check_dtype=False)
    check_func(impl1, (df_bool,), sort_output=True, check_dtype=False)
    check_func(impl1, (df_dt,), sort_output=True, check_dtype=False)
    check_func(impl1, (df_bin,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


@pytest.mark.parametrize(
    "df_med",
    [
        pd.DataFrame({"A": [1, 1, 1, 1], "B": [1, 2, 3, 4]}),
        pytest.param(
            pd.DataFrame({"A": [0, 1, 0, 1], "B": [np.nan, 2, np.nan, 4]}),
            marks=pytest.mark.slow,
        ),
        pytest.param(
            pd.DataFrame({"A": [1, 2, 2, 1, 1], "B": [1, 5, 4, 4, 3]}),
            marks=pytest.mark.slow,
        ),
        pytest.param(
            pd.DataFrame({"A": [1, 1, 1, 1, 1], "B": [1, 2, 3, 4, np.nan]}),
            marks=pytest.mark.slow,
        ),
    ],
)
def test_median_simple(df_med, memory_leak_check):
    """
    Test Groupby.median() with a single entry.
    """

    def impl1(df):
        A = df.groupby("A")["B"].median()
        return A

    check_func(impl1, (df_med,), sort_output=True, check_dtype=False)


@pytest.mark.slow
def test_median_large_random_numpy(memory_leak_check):
    """
    Test Groupby.median() with a large random numpy vector
    """

    def get_random_array(n, sizlen):
        elist = []
        for i in range(n):
            eval = random.randint(1, sizlen)
            if eval == 1:
                eval = None
            elist.append(eval)
        return np.array(elist, dtype=np.float64)

    def impl1(df):
        A = df.groupby("A")["B"].median()
        return A

    random.seed(5)
    nb = 100
    df1 = pd.DataFrame({"A": get_random_array(nb, 10), "B": get_random_array(nb, 100)})
    check_func(impl1, (df1,), sort_output=True, check_dtype=False)


@pytest.mark.slow
def test_median_nullable_int_bool(memory_leak_check):
    """
    Test Groupby.median() with a large random sets of nullable_int_bool
    """

    def impl1(df):
        df2 = df.groupby("A")["B"].median()
        return df2

    nullarr = pd.Series([1, 2, 3, 4, None, 1, 2], dtype="UInt16")
    df1 = pd.DataFrame({"A": [1, 1, 1, 1, 1, 2, 2], "B": nullarr})
    # Pandas 1.2.0 adds inferring a nullable float array type
    # TODO: Add support for proper type checking
    check_func(impl1, (df1,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
@pytest.mark.parametrize(
    "df_uniq",
    [
        pd.DataFrame(
            {"A": [2, 1, 1, 1, 2, 2, 1], "B": [-8, np.nan, 3, 1, np.nan, 6, 7]}
        ),
        pd.DataFrame(
            {
                "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
                "B": ["ccc", None, "bb", "aa", None, "ggg", "rr"],
            }
        ),
        pd.DataFrame(
            {
                "A": [
                    b"a",
                    b"aaa",
                    b"aaa",
                    b"aaa",
                    b"asdfa",
                    b"a",
                    b"aaa",
                    b"lkjds",
                    b"cfghjk",
                    b"mnbvbcfgjh",
                    b"poiuh",
                ],
                "B": [
                    b"ccc",
                    None,
                    b"bb",
                    b"aa",
                    None,
                    b"ggg",
                    b"rr",
                    b"sdalk",
                    b"sdlks",
                    b"qwergj",
                    b"ghytrf",
                ],
            }
        ),
    ],
)
def test_nunique_select_col(df_uniq, memory_leak_check):
    """
    Test Groupby.nunique() with explicitly selected one column. Boolean are broken in pandas so the
    test is removed.
    TODO: Implementation of Boolean test when pandas is corrected.
    """

    def impl1(df):
        A = df.groupby("A")["B"].nunique()
        return A

    def impl2(df):
        A = df.groupby("A")["B"].nunique(dropna=True)
        return A

    def impl3(df):
        A = df.groupby("A")["B"].nunique(dropna=False)
        return A

    check_func(impl1, (df_uniq,), sort_output=True, reset_index=True)
    check_func(impl2, (df_uniq,), sort_output=True, reset_index=True)
    check_func(impl3, (df_uniq,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_nunique_select_col_missing_keys(memory_leak_check):
    """
    Test Groupby.nunique() with explicitly select one column. Some keys are missing
    for this test
    """

    def impl1(df):
        A = df.groupby("A")["B"].nunique()
        return A

    df_int = pd.DataFrame(
        {"A": [np.nan, 1, np.nan, 1, 2, 2, 1], "B": [-8, np.nan, 3, 1, np.nan, 6, 7]}
    )
    df_str = pd.DataFrame(
        {
            "A": [None, "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", None, "bb", "aa", None, "ggg", "rr"],
        }
    )
    df_bin = pd.DataFrame(
        {
            "A": [
                b"aaa",
                None,
                b"baaa",
                b"baaa",
                b"aaa",
                None,
                b"aaa",
                b"asdf",
                b"anmb",
                b"asdjhfsdf",
            ],
            "B": [
                b"ccc",
                None,
                b"bb",
                b"aa",
                None,
                b"ggg",
                b"rr",
                b"aksjdhg",
                b"aasdfnmb",
                b"adjmnbfsdf",
            ],
        }
    )
    check_func(impl1, (df_int,), sort_output=True, reset_index=True)
    check_func(impl1, (df_str,), sort_output=True, reset_index=True)
    check_func(impl1, (df_bin,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_filtered_count(memory_leak_check):
    """
    Test Groupby.count() with filtered dataframe
    """

    def test_impl(df, cond):
        df2 = df[cond]
        c = df2.groupby("A").count()
        return df2, c

    bodo_func = bodo.jit(test_impl)
    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, np.nan, 5, 6, 7],
            "C": [2, 3, -1, 1, 2, 3, -1],
        }
    )
    cond = df.A > 1
    res = test_impl(df, cond)
    h_res = bodo_func(df, cond)
    pd.testing.assert_frame_equal(res[0], h_res[0], check_column_type=False)
    pd.testing.assert_frame_equal(res[1], h_res[1], check_column_type=False)


def test_as_index_count(memory_leak_check):
    """
    Test Groupby.count() on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(df):
        df2 = df.groupby("A", as_index=False).count()
        return df2

    def impl2(df):
        df2 = df.groupby("A", as_index=False)["C"].count()
        return df2

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, np.nan, 5, 6, 7],
            "C": [2, 3, -1, 1, 2, 3, -1],
        }
    )
    check_func(impl1, (df,), sort_output=True, reset_index=True)
    check_func(impl2, (df,), sort_output=True, reset_index=True)


def test_named_agg_nunique(memory_leak_check):
    """
    Test nunique groupby with pd.NamedAgg() relabeling
    """

    def impl(df):
        return df.groupby("A", as_index=False).agg(
            SUPPLIER_CNT=pd.NamedAgg(column="B", aggfunc="nunique")
        )

    df = pd.DataFrame(
        {
            "A": [1, 2, 3, 4, 5, 6],
            "B": pd.Series(pd.date_range(start="1/1/2018", end="1/4/2018", periods=6)),
        }
    )
    check_func(impl, (df,), sort_output=True, reset_index=True)


def test_named_agg(memory_leak_check):
    """
    Test groupby with pd.NamedAgg() relabeling
    """

    def impl1(df):
        return df.groupby("A").agg(D=pd.NamedAgg(column="B", aggfunc="min"))

    def impl2(df):
        return df.groupby("A", as_index=False).agg(
            D=pd.NamedAgg(column="B", aggfunc=lambda A: A.sum()),
            F=pd.NamedAgg(column="C", aggfunc="max"),
            E=pd.NamedAgg(column="B", aggfunc="min"),
        )

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, np.nan, 5, 6, 7],
            "C": [2, 3, -1, 1, 2, 3, -1],
        }
    )
    check_func(impl1, (df,), sort_output=True, reset_index=True)
    check_func(
        impl2,
        (df,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )


def test_bool_sum_simple(memory_leak_check):
    """
    Test groupby with pd.groupby().sum() for sums of booleans
    """

    def impl(df):
        return df.groupby(["B"]).sum()

    df = pd.DataFrame(
        {
            "A": pd.Series(
                [True, False, None, True, True, False], dtype=pd.BooleanDtype()
            ).repeat(10),
            "B": pd.Series([1, 2, 0, 5, 1, 2]).repeat(10),
        }
    )

    check_func(impl, (df,), sort_output=True, reset_index=True)


# https://dev.azure.com/bodo-inc/Bodo/_test/analytics?definitionId=5&contextType=build
# test_groupby_apply on average takes 11.14 min, or 668.4 seconds
@pytest_mark_pandas
@pytest.mark.timeout(1000)
def test_groupby_apply(is_slow_run, memory_leak_check):
    """
    Test Groupby.apply() for UDFs that return a dataframes
    """

    # kw arg
    def impl1(df):
        df2 = df.groupby("A").apply(
            lambda x, V: pd.DataFrame(
                {f"AA{V}": [x.C.mean(), x.C.sum()], "BB": [V, x["C"].iloc[0]]}
            ),
            V=11,
        )
        return df2

    # const size series output, input not used
    def impl7(df):
        df2 = df.groupby(["A", "B"]).apply(
            lambda x: pd.Series([1, 2, 3]),
        )
        return df2

    # scalar return
    def impl11(df):
        df2 = df.groupby(["A", "B"]).apply(
            lambda x: 3.3,
        )
        return df2

    # no arg, explicit select
    def impl2(df):
        df2 = df.groupby("A")[["C", "D"]].apply(
            lambda x: pd.DataFrame(
                {"AA": [x.C.mean(), x.C.sum()], "BB": [3, x["C"].iloc[0]]}
            ),
        )
        return df2

    # positional arg, as_index=False
    def impl3(df):
        df2 = df.groupby("A", as_index=False).apply(
            lambda x, v: pd.DataFrame(
                {"AA": [x.C.mean(), x.C.sum()], "BB": [v, x["C"].iloc[0]]}
            ),
            11,
        )
        return df2

    # both positional and kw args, multiple keys
    def impl4(df):
        df2 = df.groupby(["A", "B"]).apply(
            lambda x, v, W: pd.DataFrame(
                {"AA": [x.C.mean(), x.C.sum()], "BB": [v + W, x["C"].iloc[0]]}
            ),
            11,
            W=14,
        )
        return df2

    # Series input
    def impl5(df):
        df2 = df.groupby(["A", "B"]).C.apply(
            lambda x, V: pd.DataFrame(
                {"AA": [x.mean(), x.sum()], "BB": [1.2, x.iloc[0]]}
            ),
            V=11,
        )
        return df2

    # Series output
    def impl6(df):
        df2 = df.groupby(["A", "C"]).B.apply(
            lambda x, V: x + V,
            V="xx",
        )
        return df2

    # const size series output, as_index=False, input not used
    def impl8(df):
        df2 = df.groupby(["A", "B"], as_index=False).apply(
            lambda x: pd.Series((1, "A", 3)),
        )
        return df2

    # const size series output, single key, input not used
    def impl9(df):
        df2 = df.groupby(["A"]).apply(
            lambda x: pd.Series([1, 2, 3]),
        )
        return df2

    # const size series output, single key, as_index=False, input not used
    def impl10(df):
        df2 = df.groupby(["A"], as_index=False).apply(
            lambda x: pd.Series([1, 2, 3]),
        )
        return df2

    # scalar return, as_index=False
    def impl12(df):
        df2 = df.groupby(["A", "B"], as_index=False).apply(
            lambda x: 3.3,
        )
        df2.columns = ["A", "B", "C"]  # set name since Pandas sets NaN for data column
        return df2

    # similar to BodoSQL generated code for window functions
    def impl13(in_df):
        def _bodo_f(df):
            df = df.loc[:, ["B", "C"]]
            final_index = df.index
            df["OUTPUT_COL"] = np.arange(1, len(df) + 1)
            sorted_df = df.sort_values(
                by=[
                    "B",
                ],
                ascending=[
                    False,
                ],
                na_position="first",
            )
            arr = sorted_df["C"]
            retval = pd.DataFrame(
                {
                    "AGG_OUTPUT_0": arr,
                },
                index=final_index,
            )
            return retval

        return in_df.groupby(["A"], as_index=False, dropna=False).apply(_bodo_f)[
            "AGG_OUTPUT_0"
        ]

    df = pd.DataFrame(
        {
            "A": [1, 4, 4, 11, 4, 1],
            "B": ["AB", "DD", "E", "A", "DD", "AB"],
            "C": [1.1, 2.2, 3.3, 4.4, 5.5, -1.1],
            "D": [3, 1, 2, 4, 5, 5],
            "E": [b"AB", b"DD", bytes(3), b"A", b"DD", b"AB"],
        }
    )
    check_func(impl1, (df,), sort_output=True, convert_to_nullable_float=False)

    # acc_loop: as_index=False, Series output. (Key has string column)
    def impl14(df):
        df2 = df.groupby(["A", "B"], as_index=False).B.apply(
            lambda x, V: x + V,
            V="xx",
        )
        return df2

    check_func(impl14, (df,), sort_output=True, reset_index=True)

    # acc_loop: as_index=True, DataFrame output. (Key has string column)
    def impl15(df):
        df2 = df.groupby(["A", "B"]).B.apply(
            lambda x, V: x + V,
            V="xx",
        )
        return df2

    check_func(impl15, (df,), sort_output=True, reset_index=True)

    # row_loop: as_index=True, index is single column, output: Series
    def impl16(df):
        df2 = df.groupby(["B"]).apply(
            lambda x: 3.3,
        )
        return df2

    check_func(impl16, (df,), sort_output=True, check_dtype=False)
    # TODO [BE-2246]: Match output dtype by checking null info.

    check_func(impl7, (df,), sort_output=True, reset_index=True, check_dtype=False)
    check_func(
        impl11,
        (df,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    check_func(impl13, (df,), sort_output=True, reset_index=True, check_dtype=False)
    if not is_slow_run:
        return
    check_func(impl2, (df,), sort_output=True, convert_to_nullable_float=False)
    # NOTE: Pandas assigns group numbers in sorted order to Index but we don't match it
    # since requires expensive sorting
    check_func(
        impl3,
        (df,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    check_func(impl4, (df,), sort_output=True, convert_to_nullable_float=False)
    check_func(impl5, (df,), sort_output=True, convert_to_nullable_float=False)
    # NOTE: Pandas bug: drops the key arrays from output Index if it's Series sometimes
    # (as of 1.1.5)
    check_func(impl6, (df,), sort_output=True, reset_index=True)
    check_func(impl8, (df,), sort_output=True, reset_index=True)
    # TODO [BE-2246]: Match output dtype by checking null info.
    check_func(impl9, (df,), sort_output=True, reset_index=True, check_dtype=False)
    # TODO [BE-2246]: Match output dtype by checking null info.
    check_func(impl10, (df,), sort_output=True, reset_index=True, check_dtype=False)
    check_func(impl12, (df,), sort_output=True, reset_index=True)


df_type = bodo.typeof(pd.DataFrame({"AA": [1.1], "BB": [4.1]}))


@bodo.wrap_python(df_type)
def g_wrapped(df):
    return pd.DataFrame({"AA": [df.C.mean(), df.C.sum()], "BB": [3.1, df["C"].iloc[0]]})


def test_groupby_apply_wrap_python(memory_leak_check):
    """Test groupby apply with a wrap_python UDF"""

    def impl1(df):
        df2 = df.groupby("A").apply(g_wrapped)
        return df2

    df = pd.DataFrame(
        {
            "A": [1, 4, 4, 11, 4, 1],
            "B": ["AB", "DD", "E", "A", "DD", "AB"],
            "C": [1.1, 2.2, 3.3, 4.4, 5.5, -1.1],
        }
    )
    check_func(impl1, (df,), sort_output=True, reset_index=True, check_dtype=False)


@pytest.mark.skipif(
    bodo.get_size() == 1,
    reason="Test should only run on more than one rank",
)
def test_groupby_apply_global_dict(memory_leak_check):
    """make sure returning dictionary-encoded arrays to output with input's
    global dictionary doesn't cause hangs.
    See https://bodo.atlassian.net/browse/BSE-2566
    """

    def impl(in_df):
        def _bodo_f(df):
            arr = df["B"]
            return pd.DataFrame(
                {
                    "AGG_OUTPUT_0": arr,
                },
                index=df.index,
            )

        return in_df.groupby(["A"], as_index=False, dropna=False).apply(_bodo_f)

    df = pd.DataFrame(
        {
            "A": [1, 1, 1, 1],
            "B": ["A", "A", "B", "B"],
        }
    )
    check_func(
        impl,
        (df,),
        sort_output=True,
        reset_index=True,
        use_dict_encoded_strings=True,
        only_1D=True,
    )


@pytest.mark.parametrize(
    "array",
    [
        # array(int)
        pd.array(
            [[1], [1, None, 2], None, [3, 4], [None]],
            dtype=pd.ArrowDtype(pa.large_list(pa.int32())),
        ),
        # array(struct(array))
        pd.array(
            [
                [{"A": 0, "B": [1]}, None, {"A": 10, "B": [1, 11]}],
                None,
                [{"A": 1, "B": [1, 3, 2]}, {"A": 5, "B": [2]}],
                [{"A": 4, "B": [0, 1]}],
                [None],
            ],
            dtype=pd.ArrowDtype(
                pa.large_list(
                    pa.struct(
                        [
                            pa.field("A", pa.int32()),
                            pa.field("B", pa.large_list(pa.int64())),
                        ]
                    )
                )
            ),
        ),
        # array(string)
        pd.array(
            [
                pd.array(["asfdav", None, "abc"], dtype="string[pyarrow]"),
                pd.array(["1423", "aa3"], dtype="string[pyarrow]"),
                pd.array(["!@#$"], dtype="string[pyarrow]"),
                None,
                pd.array(["0.9305", None], dtype="string[pyarrow]"),
            ],
            dtype=pd.ArrowDtype(pa.large_list(pa.string())),
        ),
        # map
        pd.array(
            [
                [{"A": 0, "B": 1}, {"A": 0, "B": 1, "C": 2}],
                [{}, {"A": 0}],
                [
                    {"A": 1, "B": 0},
                ],
                None,
                [{}, {"B": 1, "A": 0}],
            ],
            dtype=pd.ArrowDtype(pa.large_list(pa.map_(pa.large_string(), pa.int64()))),
        ),
    ],
)
def test_reverse_shuffle_nested_arrays(array, memory_leak_check):
    """Test reverse shuffle used in groupby/apply for nested array types"""

    def impl(df):
        return df.groupby("A").apply(lambda x: pd.DataFrame({"C": x["B"]}))

    df = pd.DataFrame({"A": [1, 3, 1, 4, 0], "B": array})
    check_func(impl, (df,), sort_output=True, reset_index=True, check_dtype=False)


@pytest.mark.skip(reason="[BE-1531] test fails in CI")
def test_groupby_apply_objmode():
    """
    Test Groupby.apply() with objmode inside UDF
    """

    bodo.numba.types.test_df_type = bodo.DataFrameType(
        (bodo.string_array_type, bodo.float64[::1]),
        bodo.NumericIndexType(bodo.int64, bodo.none),
        ("B", "C"),
    )

    def apply_func(df):
        with bodo.objmode(df2="test_df_type"):
            df2 = df[["B", "C"]]
        return df2

    def impl1(df):
        return df.groupby("A").apply(apply_func)

    df = pd.DataFrame(
        {
            "A": [1, 4, 4, 11, 4, 1],
            "B": ["AB", "DD", "E", "A", "DD", "AB"],
            "C": [1.1, 2.2, 3.3, 4.4, 5.5, -1.1],
            "D": [3, 1, 2, 4, 5, 5],
        }
    )
    check_func(impl1, (df,), sort_output=True, reset_index=True)

    def analysis_func(df):
        return 3

    def apply_func(df):
        with bodo.objmode(out="int64"):
            out = analysis_func(df)
        return pd.Series([out])

    def main_func(df):
        res = df.groupby("A").apply(apply_func)
        return res

    # test for BE-290
    df = pd.DataFrame({"A": [1.0, 2, 3, 1.0, 5], "B": [4.0, 5, 6, 2, 1]})
    j_func = numba.njit(pipeline_class=DeadcodeTestPipeline, parallel=True)(main_func)
    pd.testing.assert_frame_equal(j_func(df), main_func(df), check_column_type=False)
    fir = j_func.overloads[j_func.signatures[0]].metadata["preserved_ir"]
    assert not has_udf_call(fir)

    def analysis_func2(ar):
        return np.array([9, 8, 7, 6, 5])

    @bodo.jit
    def objmode_wrapper(df):
        with bodo.objmode(out="int64[::1]"):
            out = analysis_func2(df)
        return out

    @bodo.jit
    def apply_func2(df):
        out = objmode_wrapper(df)
        return pd.Series(out)

    def main_func2(df):
        res = df.groupby("A").apply(apply_func2)
        return res

    # test for BE-290
    df = pd.DataFrame({"A": [1.0, 2, 3, 1.0, 5], "B": [4.0, 5, 6, 2, 1]})
    j_func = numba.njit(pipeline_class=DeadcodeTestPipeline, parallel=True)(main_func2)
    # NOTE: output results don't match Pandas since it creates a dataframe but
    # Bodo creates a Series in this case (TODO: fix if possible)
    j_func(df)
    fir = j_func.overloads[j_func.signatures[0]].metadata["preserved_ir"]
    assert not has_udf_call(fir)


@pytest_mark_pandas
def test_groupby_apply_arg_dist(memory_leak_check):
    """
    Make sure extra arguments to Groupby.apply() are replicated
    """

    def impl1(df, n):
        df2 = pd.DataFrame({"A": np.arange(n)})
        return df.groupby("A").apply(lambda x, df2: df2.A.sum(), df2=df2)

    df = pd.DataFrame(
        {
            "A": [1, 4, 4, 11, 4, 1],
            "B": ["AB", "DD", "E", "A", "DD", "AB"],
            "C": [b"AB", b"DD", b"E", b"A", b"DD", b"AB"],
        }
    )
    check_func(impl1, (df, 10), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_groupby_multiindex(memory_leak_check):
    """Test groupby with a multiindex having more than one col."""
    df = pd.DataFrame(
        {
            "A": [2, 1, 9, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )

    def impl(df):
        s = df.groupby(["A", "B"]).mean()
        return s

    check_func(impl, (df,), sort_output=True, check_dtype=False, reset_index=True)


@pytest_mark_pandas
def test_groupby_pipe(memory_leak_check):
    """
    Test Groupby.pipe()
    """

    def impl1(df):
        return df.groupby("A").pipe(lambda g: g.sum())

    # test *args, **kwargs
    def impl2(df, a, b):
        return df.groupby("A").pipe(lambda g, a, b: g.sum() + a + b, a, b=b)

    # test chaining
    def impl3(df, a, b):
        return (
            df.groupby("A")
            .pipe(lambda g, a: g.sum() + a, a)
            .pipe(lambda df, b: (df + b).B, b=b)
            .pipe(lambda S: S.sum())
        )

    df = pd.DataFrame(
        {
            "A": [1, 4, 4, 11, 4, 1],
            "B": [1, 2, 3, 4, 5, 6],
        }
    )
    check_func(impl1, (df,), sort_output=True, reset_index=True)
    check_func(impl2, (df, 1, 2), sort_output=True, reset_index=True)
    check_func(impl3, (df, 1, 2), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_single_col_reset_index(test_df, memory_leak_check):
    """We need the reset_index=True because otherwise the order is scrambled"""

    # sum is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A")["B"].sum().reset_index()
        return A

    check_func(impl1, (test_df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_nonvar_column_names(memory_leak_check):
    """Test column names that cannot be variable names to make sure groupby code
    generation sanitizes variable names properly.
    """

    def impl1(df):
        A = df.groupby("A: A")["B: B"].sum()
        return A

    df = pd.DataFrame(
        {
            "A: A": [2, 1, 1, 1, 2, 2, 1],
            "B: B": [-8, 2, 3, np.nan, 5, 6, 7],
            "C: C": [2, 3, -1, 1, 2, 3, -1],
        }
    )
    check_func(impl1, (df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_cumsum_large_random_numpy(memory_leak_check):
    def get_random_array(n, sizlen):
        elist = []
        for i in range(n):
            eval = random.randint(1, sizlen)
            if eval == 1:
                eval = None
            elist.append(eval)
        return np.array(elist, dtype=np.float64)

    def impl1(df):
        A = df.groupby("A")["B"].cumsum()
        return A

    def impl2(df):
        A = df.groupby("A")["B"].cumsum(skipna=True)
        return A

    def impl3(df):
        A = df.groupby("A")["B"].cumsum(skipna=False)
        return A

    random.seed(5)
    nb = 100
    df1 = pd.DataFrame(
        {"A": get_random_array(nb, 10), "B": get_random_array(nb, 100)},
        index=get_random_array(nb, 100),
    )
    # Replace NaN in Pandas output (Pandas bug) to avoid output comparison issues
    check_func(
        impl1,
        (df1,),
        sort_output=True,
        py_output=df1.groupby("A")["B"]
        .cumsum()
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )
    check_func(
        impl2,
        (df1,),
        sort_output=True,
        py_output=df1.groupby("A")["B"]
        .cumsum(skipna=True)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )
    check_func(
        impl3,
        (df1,),
        sort_output=True,
        py_output=df1.groupby("A")["B"]
        .cumsum(skipna=False)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )


@pytest_mark_pandas
@pytest.mark.slow
def test_cummin_cummax_large_random_numpy(memory_leak_check):
    """A bunch of tests related to cummin/cummax functions."""

    def get_random_array(n, sizlen):
        elist = []
        for i in range(n):
            eval = random.randint(1, sizlen)
            if eval == 1:
                eval = None
            elist.append(eval)
        return np.array(elist, dtype=np.float64)

    def impl1(df):
        A = df.groupby("A")["B"].agg(("cummin", "cummax"))
        return A

    def impl2(df):
        A = df.groupby("A").cummin()
        return A

    def impl3(df):
        A = df.groupby("A").cummax()
        return A

    def impl4(df):
        A = df.groupby("A")["B"].cummin()
        return A

    def impl5(df):
        A = df.groupby("A")["B"].cummax()
        return A

    def impl6(df):
        A = df.groupby("A").agg({"B": "cummin"})
        return A

    # The as_index option has no bearing for cumulative operations but better be safe than sorry.
    def impl7(df):
        A = df.groupby("A", as_index=True)["B"].cummin()
        return A

    # ditto
    def impl8(df):
        A = df.groupby("A", as_index=False)["B"].cummin()
        return A

    random.seed(5)
    nb = 100
    df1 = pd.DataFrame({"A": get_random_array(nb, 10), "B": get_random_array(nb, 100)})
    # Need reset_index as none is set on input.
    check_func(
        impl1,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    check_func(
        impl2,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    check_func(
        impl3,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    check_func(
        impl4,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    check_func(
        impl5,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    check_func(
        impl6,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    check_func(
        impl7,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_to_nullable_float=False,
    )
    # Pandas 2 creates a Series in output for some reason in this case (seems like a bug).
    # TODO[BSE-2060] investigate
    # check_func(
    #     impl8,
    #     (df1,),
    #     sort_output=True,
    #     reset_index=True,
    #     convert_to_nullable_float=False,
    # )


@pytest_mark_pandas
def test_groupby_cumsum_simple(memory_leak_check):
    """
    Test Groupby.cumsum(): a simple case
    """

    def impl(df):
        df2 = df.groupby("A")["B"].cumsum()
        return df2

    df1 = pd.DataFrame(
        {"A": [1, 1, 1, 1, 1], "B": [1, 2, 3, 4, 5]}, index=np.arange(42, 47)
    )
    check_func(impl, (df1,), sort_output=True)


@pytest_mark_pandas
def test_groupby_cumprod_simple(memory_leak_check):
    """
    Test Groupby.cumprod(): a simple case
    """

    def impl(df):
        df2 = df.groupby("A")["B"].cumprod()
        return df2

    df1 = pd.DataFrame(
        {"A": [1, 1, 1, 1, 1], "B": [1, 2, 3, 4, 5]}, index=np.arange(15, 20)
    )
    check_func(impl, (df1,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_cumsum(memory_leak_check):
    """
    Test Groupby.cumsum()
    """

    def impl1(df):
        df2 = df.groupby("A").cumsum(skipna=False)
        return df2

    def impl2(df):
        df2 = df.groupby("A").cumsum(skipna=True)
        return df2

    df1 = pd.DataFrame(
        {
            "A": [0, 1, 3, 2, 1, 0, 4, 0, 2, 0],
            "B": [-8, np.nan, 3, 1, np.nan, 6, 7, 3, 1, 2],
            "C": [-8, 2, 3, 1, 5, 6, 7, 3, 1, 2],
        },
        index=np.arange(32, 42),
    )
    df2 = pd.DataFrame(
        {
            "A": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            "B": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            "C": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        },
        index=np.arange(42, 52),
    )
    df3 = pd.DataFrame(
        {
            "A": [0.3, np.nan, 3.5, 0.2, np.nan, 3.3, 0.2, 0.3, 0.2, 0.2],
            "B": [-1.1, 1.1, 3.2, 1.1, 5.2, 6.8, 7.3, 3.4, 1.2, 2.4],
            "C": [-8.1, 2.3, 5.3, 1.1, 0.5, 4.6, 1.7, 4.3, -8.1, 5.3],
        },
        index=np.arange(52, 62),
    )
    check_func(
        impl1,
        (df1,),
        sort_output=True,
        py_output=df1.groupby("A")
        .cumsum(skipna=False)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )
    check_func(
        impl1,
        (df2,),
        sort_output=True,
        py_output=df2.groupby("A")
        .cumsum(skipna=False)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )
    check_func(
        impl1,
        (df3,),
        sort_output=True,
        py_output=df3.groupby("A")
        .cumsum(skipna=False)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )
    check_func(
        impl2,
        (df1,),
        sort_output=True,
        py_output=df1.groupby("A")
        .cumsum(skipna=True)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )
    check_func(
        impl2,
        (df2,),
        sort_output=True,
        py_output=df2.groupby("A")
        .cumsum(skipna=True)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )
    check_func(
        impl2,
        (df3,),
        sort_output=True,
        py_output=df3.groupby("A")
        .cumsum(skipna=True)
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_multi_intlabels_cumsum_int(memory_leak_check):
    """
    Test Groupby.cumsum() on int columns
    multiple labels for 'by'
    """

    def impl(df):
        df2 = df.groupby(["A", "B"])["C"].cumsum()
        return df2

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 1, -8, 1, 5, 1, 7],
            "C": [3, np.nan, 6, 5, 4, 4, 3],
        },
        index=np.arange(10, 17),
    )
    check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_multi_labels_cumsum_multi_cols(memory_leak_check):
    """
    Test Groupby.cumsum()
    multiple labels for 'by', multiple cols to cumsum
    """

    def impl(df):
        df2 = df.groupby(["A", "B"])[["C", "D"]].cumsum()
        return df2

    df = pd.DataFrame(
        {
            "A": [np.nan, 1.0, np.nan, 1.0, 2.0, 2.0, 2.0],
            "B": pd.array([1, 2, 3, 2, 1, 1, 1], "Int64"),
            "C": pd.array([3, 5, 6, 5, 4, 4, 3], "Int64"),
            "D": [3.1, 1.1, 6.0, np.nan, 4.0, np.nan, 3],
        },
        index=np.arange(10, 17),
    )
    check_func(
        impl,
        (df,),
        sort_output=True,
        check_dtype=False,
        py_output=df.groupby(["A", "B"])[["C", "D"]]
        .cumsum()
        .map(lambda a: pd.NA if (a is not pd.NA) and np.isnan(a) else a),
    )


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_as_index_cumsum(memory_leak_check):
    """
    Test Groupby.cumsum() on groupby() as_index=False
    for both dataframe and series returns
    TODO: add np.nan to "A" after groupby null keys are properly ignored
          for cumsum
    """

    def impl1(df):
        df2 = df.groupby("A", as_index=False).cumsum()
        return df2

    def impl2(df):
        df2 = df.groupby("A", as_index=False)["C"].cumsum()
        return df2

    df = pd.DataFrame(
        {
            "A": [3.0, 1.0, 4.1, 1.0, 2.0, 2.0, 2.0],
            "B": [1, 2, 3, 2, 1, 1, 1],
            "C": [3, np.nan, 6, 5, 4, 4, 3],
            "D": [3.1, 1.1, 6.0, np.nan, 4.0, np.nan, 3],
        },
        index=np.arange(10, 17),
    )
    check_func(impl1, (df,), sort_output=True)
    # Pandas 2 creates a Series in output for some reason in this case (seems like a bug).
    # TODO[BSE-2060] investigate
    # check_func(impl2, (df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_cumsum_all_nulls_col(memory_leak_check):
    """
    Test Groupby.cumsum() on column with all null entries
    TODO: change by to "A" after groupby null keys are properly ignored
          for cumsum
    """

    def impl(df):
        df2 = df.groupby("B").cumsum()
        return df2

    df = pd.DataFrame(
        {
            "A": [np.nan, 1.0, np.nan, 1.0, 2.0, 2.0, 2.0],
            "B": [1, 2, 3, 2, 1, 1, 1],
            "C": [3, 5, 6, 5, 4, 4, 3],
            "D": [np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan],
        },
        index=np.arange(10, 17),
    )
    check_func(impl, (df,), sort_output=True)


def test_max(test_df, memory_leak_check):
    """
    Test Groupby.max()
    """

    def impl1(df):
        A = df.groupby("A").max()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").max()
        return A

    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, None, False, True, None, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )
    df_str = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", "ff", "bb", "rr", "ggg", "aa", "aa"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
        }
    )

    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl1, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl2, (11,))


@pytest.mark.slow
def test_max_one_col(test_df, memory_leak_check):
    """
    Test Groupby.max() with one column selected
    """

    def impl1(df):
        A = df.groupby("A")["B"].max()
        return A

    def impl2(n):  # noqa: F841
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].max()
        return A

    df_bool = pd.DataFrame(  # noqa: F841
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, None, False, True, None, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    # seems like Pandas 1.0 has a regression and returns float64 for Int64 in this case
    check_dtype = True
    if any(pd.Int64Dtype() == v for v in test_df.dtypes.to_list()):
        check_dtype = False

    check_func(impl1, (test_df,), sort_output=True, check_dtype=check_dtype)
    # check_func(impl1, (df_bool,), sort_output=True)
    # check_func(impl2, (11,))


@pytest.mark.slow
def test_groupby_as_index_max(memory_leak_check):
    """
    Test max on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).max()
        return df2

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].max()
        return df2

    check_func(impl1, (11,), sort_output=True, reset_index=True)
    check_func(impl2, (11,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_max_date(memory_leak_check):
    """
    Test Groupby.max() on datetime and datetime.date column
    for both dataframe and series returns
    """

    def impl1(df):
        df2 = df.groupby("A", as_index=False).max()
        return df2

    def impl2(df):
        df2 = df.groupby("A", as_index=False)["B"].max()
        return df2

    df1 = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    df2 = pd.DataFrame(
        {
            "A": [2, 5, 5, 5, 2, 2, 10],
            "B": [
                datetime.date(2018, 1, 24),
                datetime.date(1983, 1, 3),
                datetime.date(1966, 4, 27),
                datetime.date(1999, 12, 7),
                datetime.date(1966, 4, 27),
                datetime.date(2004, 7, 8),
                datetime.date(2020, 11, 17),
            ],
        }
    )
    check_func(impl1, (df1,), sort_output=True, reset_index=True)
    check_func(impl1, (df2,), sort_output=True, reset_index=True)
    check_func(impl2, (df1,), sort_output=True, reset_index=True)
    check_func(impl2, (df2,), sort_output=True, reset_index=True)


@pytest.mark.smoke
def test_mean(test_df, memory_leak_check):
    """
    Test Groupby.mean()
    """

    # mean is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A").mean()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_mean_one_col(test_df, memory_leak_check):
    """
    Test Groupby.mean() with one column selected
    """

    # mean is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A")["B"].mean()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].mean()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_as_index_mean(memory_leak_check):
    """
    Test mean on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).mean()
        return df2

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].mean()
        return df2

    check_func(impl1, (11,), sort_output=True, check_dtype=False, reset_index=True)
    check_func(impl2, (11,), sort_output=True, check_dtype=False, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_mean_median_other_supported_types(memory_leak_check):
    """Test Groupby.mean()/median() with cases not in test_df"""

    def impl1(df):
        A = df.groupby("A").mean()
        return A

    def impl2(df):
        A = df.groupby("A").median()
        return A

    # Empty
    df = pd.DataFrame({"A": [], "B": []})
    check_func(impl1, (df,), sort_output=True, check_dtype=False)
    check_func(impl2, (df,), sort_output=True, check_dtype=False)

    # Zero columns
    df_empty = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]})
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl1)(df_empty)
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl2)(df_empty)

    # Test different column types in same dataframe
    df_mix = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 3],
            "B": [1.1, 2.2, 3.3, 4.4, 1.1],
            "C": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
        }
    )
    check_func(impl1, (df_mix,), sort_output=True, check_dtype=False)
    check_func(impl2, (df_mix,), sort_output=True, check_dtype=False)
    # Decimal
    # Pandas with Decimal throws: DataError: No numeric types to aggregate
    df_decimal = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 2],
            "B": pd.Series(
                [
                    Decimal("1.6"),
                    Decimal("-0.2"),
                    Decimal("44.2"),
                    None,
                    Decimal("0"),
                ]
            ),
        }
    )
    # Change type to float64 for py_output
    check_func(
        impl1,
        (df_decimal,),
        sort_output=True,
        reset_index=True,
        py_output=impl1(df_decimal.astype({"B": "float64"})),
        check_dtype=False,
    )

    # Median supports decimal natively
    expected = pd.DataFrame(
        {
            "B": [
                Decimal("0.800000000000000000000"),
                Decimal("22.000000000000000000000"),
            ],
        },
        index=[1, 2],
    ).rename_axis("A")

    check_func(
        impl2,
        (df_decimal,),
        sort_output=True,
        reset_index=True,
        py_output=expected,
        check_dtype=False,
    )


def test_min(test_df, memory_leak_check):
    """
    Test Groupby.min()
    """

    def impl1(df):
        A = df.groupby("A").min()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").min()
        return A

    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, None, False, True, None, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    df_str = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", "ff", "bb", "rr", "bb", "ggg", "aa"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
        }
    )
    check_func(impl1, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


@pytest.mark.slow
def test_min_max_other_supported_types(memory_leak_check):
    """Test Groupby.min()/max() with other types not in df_test"""

    # TODO: [BE-435] HA: Once all these groupby functions are done, merge the dataframe examples with df_test
    def impl1(df):
        A = df.groupby("A").min()
        return A

    def impl2(df):
        A = df.groupby("A").max()
        return A

    # Empty
    df = pd.DataFrame({"A": [], "B": []})
    check_func(impl1, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)

    # Zero columns
    df_empty = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]})
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl1)(df_empty)
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl2)(df_empty)

    # timedelta
    df_td = pd.DataFrame(
        {
            "A": [1, 2, 3, 2, 1],
            "B": pd.Series(pd.timedelta_range(start="1 day", periods=5)),
        }
    )
    check_func(impl1, (df_td,), sort_output=True)
    check_func(impl2, (df_td,), sort_output=True)

    # nullable bool
    df_n_bool = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": pd.Series(
                [False, True, True, None, True, True, False], dtype="boolean"
            ),
        }
    )
    check_func(impl1, (df_n_bool,), sort_output=True)
    check_func(impl2, (df_n_bool,), sort_output=True)

    # timedelta with NaT
    df_td = pd.DataFrame(
        {
            "A": [1, 2, 3, 2, 1],
            "B": pd.concat(
                (
                    pd.Series(pd.timedelta_range(start="1 day", periods=4)),
                    pd.Series(data=[np.timedelta64("nat")], index=[4]),
                )
            ),
        }
    )
    check_func(impl1, (df_td,), sort_output=True)
    check_func(impl2, (df_td,), sort_output=True)

    # TODO: need min max support for binary, see BE-1252
    # df_bin = pd.DataFrame(
    #     {
    #         "A": [2, 1, 1, 2, 3],
    #         "B": [1.1, 2.2, 3.3, 4.4, 1.1],
    #         "C": [b"ab", np.nan, b"ef", b"gh", b"ijk"],
    #     }
    # )
    # check_func(impl1, (df_bin,), sort_output=True)
    # check_func(impl2, (df_bin,), sort_output=True)

    # Test different column types in same dataframe
    df_mix = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 3],
            "B": [1.1, 2.2, 3.3, 4.4, 1.1],
            "C": ["ab", "cd"] + gen_nonascii_list(3),
        }
    )
    check_func(impl1, (df_mix,), sort_output=True)
    check_func(impl2, (df_mix,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_min_one_col(test_df, memory_leak_check):
    """
    Test Groupby.min() with one column selected
    """

    def impl1(df):
        A = df.groupby("A")["B"].min()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].min()
        return A

    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, None, False, True, None, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    # seems like Pandas 1.0 has a regression and returns float64 for Int64 in this case
    check_dtype = True
    if any(pd.Int64Dtype() == v for v in test_df.dtypes.to_list()):
        check_dtype = False

    check_func(impl1, (test_df,), sort_output=True, check_dtype=check_dtype)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_as_index_min(memory_leak_check):
    """
    Test min on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).min()
        return df2

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].min()
        return df2

    check_func(impl1, (11,), sort_output=True, reset_index=True)
    check_func(impl2, (11,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_min_datetime(memory_leak_check):
    """
    Test Groupby.min() on datetime column
    for both dataframe and series returns
    """

    def impl1(df):
        df2 = df.groupby("A", as_index=False).min()
        return df2

    def impl2(df):
        df2 = df.groupby("A", as_index=False)["B"].min()
        return df2

    df = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    check_func(impl1, (df,), sort_output=True, reset_index=True)
    check_func(impl2, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_optional_heterogenous_series_apply(memory_leak_check):
    """
    Test groupby.apply works when heterogenous series requires an optional type
    """

    def impl(df):
        df1 = pd.DataFrame(
            {"B": df["B"], "A": df["A"], "C": df["C"], "$f3": (df["A"] == np.int32(1))}
        )

        def __bodo_dummy___sql_groupby_apply_fn_1(df):
            S0 = df["A"][df["$f3"]]
            S1 = df["C"][df["$f3"]]
            var0 = S0.mean() if len(S0) > 0 else None
            var1 = S0.sum() if len(S1) > 0 else None
            return pd.Series(
                (var0, var1), index=pd.Index(("single_avg_a", "single_sum_c"))
            )

        df2 = df1.groupby(["B"], as_index=False, dropna=False).apply(
            __bodo_dummy___sql_groupby_apply_fn_1
        )
        return df2

    df = pd.DataFrame(
        {"A": [1, 2, 3] * 4, "B": [4, 5, 6, 7] * 3, "C": [7, 8, 9, 10, 11, 12] * 2}
    )
    # Pandas returns float64 instead of Nullable int.
    check_func(impl, (df,), sort_output=True, reset_index=True, check_dtype=False)


@pytest_mark_pandas
def test_optional_homogenous_series_apply(memory_leak_check):
    """
    Test groupby.apply works when a homogenous series requires an optional type
    """

    def impl(df):
        df1 = pd.DataFrame(
            {"B": df["B"], "A": df["A"], "$f3": (df["A"] == np.int32(1))}
        )

        def __bodo_dummy___sql_groupby_apply_fn_1(df):
            S0 = df["A"][df["$f3"]]
            var0 = S0.sum() if len(S0) > 0 else None
            return pd.Series((var0,), index=pd.Index(("single_sum_a",)))

        df2 = df1.groupby(["B"], as_index=False, dropna=False).apply(
            __bodo_dummy___sql_groupby_apply_fn_1
        )
        return df2

    df = pd.DataFrame({"A": [1, 2, 3] * 4, "B": [4, 5, 6, 7] * 3})
    # TODO [BE-2246]: Match output dtype by checking null info.
    check_func(impl, (df,), sort_output=True, reset_index=True, check_dtype=False)


@pytest_mark_pandas
def test_prod(test_df, memory_leak_check):
    """
    Test Groupby.prod()
    """

    # prod is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A").prod()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").prod()
        return A

    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            # This column is disabled because pandas removes it
            # from output. This could be a bug in pandas. TODO: enable when it
            # is fixed
            # "B": [True, None, False, True, None, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    check_func(impl1, (test_df,), sort_output=True)
    # Pandas 1.2.0 converts the all boolean values to integers
    # TODO: Change in Bodo
    check_func(impl1, (df_bool,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_prod_one_col(test_df, memory_leak_check):
    """
    Test Groupby.prod() with one column selected
    """

    # prod is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A")["B"].prod()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].prod()
        return A

    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "C": [True, None, False, True, None, False, False, True],
            "B": [True, True, False, True, True, False, False, False],
        }
    )

    # seems like Pandas 1.0 has a regression and returns float64 for Int64 in this case
    check_dtype = True
    if any(pd.Int64Dtype() == v for v in test_df.dtypes.to_list()):
        check_dtype = False
    check_func(impl1, (test_df,), sort_output=True, check_dtype=check_dtype)
    # Pandas 1.2.0 converts the all boolean values to integers
    # TODO: Change in Bodo
    check_func(impl1, (df_bool,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_as_index_prod(memory_leak_check):
    """
    Test prod on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).prod()
        return df2

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].prod()
        return df2

    check_func(impl1, (11,), sort_output=True, reset_index=True)
    check_func(impl2, (11,), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_sum_prod_empty_mix(memory_leak_check):
    """Test Groupby.sum()/prod() with cases not in test_df"""

    def impl1(df):
        A = df.groupby("A").sum()
        return A

    def impl2(df):
        A = df.groupby("A").prod()
        return A

    # Empty
    df = pd.DataFrame({"A": [], "B": []})
    check_func(impl1, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)

    # Zero columns
    df_empty = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]})
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl1)(df_empty)
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl2)(df_empty)

    # Test different column types in same dataframe
    df_mix = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 3],
            "B": [1.1, 2.2, 3.3, 4.4, 1.1],
            "C": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
        }
    )
    check_func(impl1, (df_mix,), sort_output=True)
    check_func(impl2, (df_mix,), sort_output=True)


# TODO[BE-2098]: leaks memory for categorical variables
def test_first_last(test_df):
    """
    Test Groupby.first() and Groupby.last()
    """

    def impl1(df):
        A = df.groupby("A").first()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").first()
        return A

    def impl3(df):
        A = df.groupby("A").last()
        return A

    def impl4(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").last()
        return A

    df_str = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", None, "bb", "aa", None, "ggg", "rr"],
            "C": gen_nonascii_list(7),
        }
    )

    df_dict_nan = pd.DataFrame(
        {
            "A": pd.Series(["A", "B", None, "C", "D"] * 5),
            "B": pd.Series(["AB", "BC", None, "C", "DE"] * 5),
        }
    )

    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, None, False, True, None, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )
    df_dt = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    df_bin = pd.DataFrame(
        {
            "A": [1, 1, 3, 3, 2, 1, 2],
            "B": [b"ccc", None, b"bb", b"aa", None, b"ggg", b"rr"],
            "C": [b"cc", b"aa", b"aa", b"bb", b"vv", b"cc", b"cc"],
        }
    )

    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl1, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl1, (df_dict_nan,), sort_output=True, check_typing_issues=False)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl1, (df_dt,), sort_output=True)
    check_func(impl1, (df_bin,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)

    check_func(impl3, (test_df,), sort_output=True)
    check_func(impl3, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl3, (df_dict_nan,), sort_output=True, check_typing_issues=False)
    check_func(impl3, (df_bool,), sort_output=True)
    check_func(impl3, (df_dt,), sort_output=True)
    check_func(impl3, (df_bin,), sort_output=True)
    check_func(impl4, (11,), sort_output=True)


@pytest.mark.slow
def test_first_last_supported_types(memory_leak_check):
    """Test Groupby.first()/last() with other types not in test_df"""

    def impl1(df):
        A = df.groupby("A").first()
        return A

    def impl2(df):
        A = df.groupby("A").last()
        return A

    # Empty
    df = pd.DataFrame({"A": [], "B": []})
    check_func(impl1, (df,), sort_output=True)

    # Zero columns
    df_empty = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]})
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl1)(df_empty)
        bodo.jit(impl2)(df_empty)

    # timedelta
    df_td = pd.DataFrame(
        {
            "A": [1, 2, 3, 2, 1],
            "B": pd.Series(pd.timedelta_range(start="1 day", periods=5)),
        }
    )
    check_func(impl1, (df_td,), sort_output=True)
    check_func(impl2, (df_td,), sort_output=True)

    # nullable bool
    df_n_bool = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": pd.Series(
                [False, True, True, None, True, True, False], dtype="boolean"
            ),
        }
    )
    check_func(impl1, (df_n_bool,), sort_output=True)
    check_func(impl2, (df_n_bool,), sort_output=True)

    # Decimal
    from decimal import Decimal

    df_decimal = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 2],
            "B": pd.Series(
                [Decimal("1.6"), Decimal("-0.2"), Decimal("44.2"), None, Decimal("0")]
            ),
        }
    )
    check_func(impl1, (df_decimal,), sort_output=True)
    check_func(impl2, (df_decimal,), sort_output=True)

    # timedelta with NaT
    df_td = pd.DataFrame(
        {
            "A": [1, 2, 3, 2, 1],
            "B": pd.concat(
                (
                    pd.Series(pd.timedelta_range(start="1 day", periods=4)),
                    pd.Series(data=[np.timedelta64("nat")], index=[4]),
                )
            ),
        }
    )
    check_func(impl1, (df_td,), sort_output=True)
    check_func(impl2, (df_td,), sort_output=True)

    # nullable Binary
    df_bin = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 3] * 2,
            "C": [b"ab", b"cd", None, b"gh", b"ijk"] * 2,
        }
    )
    check_func(impl1, (df_bin,), sort_output=True)
    check_func(impl2, (df_bin,), sort_output=True)

    # Test different column types in same dataframe
    def impl_mix(df):
        A = df.groupby("A")[["B", "C"]].first()
        return A

    df_mix = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 3],
            "B": [1.1, 2.2, 3.3, 4.4, 1.1],
            "C": ["ab", "cd"] + gen_nonascii_list(3),
        }
    )
    check_func(impl_mix, (df_mix,), sort_output=True)


# TODO[BE-2098]: leaks memory for categorical variables
@pytest.mark.slow
def test_first_last_one_col(test_df):
    """
    Test Groupby.first() and Groupby.last() with one column selected
    """

    def impl1(df):
        A = df.groupby("A")["B"].first()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].first()
        return A

    def impl3(df):
        A = df.groupby("A")["B"].last()
        return A

    def impl4(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].last()
        return A

    df_str = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", None, "bb", "aa", None, "ggg", "rr"],
            "C": gen_nonascii_list(7),
        }
    )

    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, None, False, True, None, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )
    df_dt = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    df_bin = pd.DataFrame(
        {
            "A": [1, 1, 3, 3, 2, 1, 2],
            "B": [b"ccc", None, b"bb", b"aa", None, b"ggg", b"rr"],
            "C": [b"cc", b"aa", b"aa", b"bb", b"vv", b"cc", b"cc"],
        }
    )

    # seems like Pandas 1.0 has a regression and returns float64 for Int64 in this case
    check_dtype = True
    if any(pd.Int64Dtype() == v for v in test_df.dtypes.to_list()):
        check_dtype = False

    check_func(impl1, (test_df,), sort_output=True, check_dtype=check_dtype)
    check_func(impl1, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl1, (df_dt,), sort_output=True)
    check_func(impl1, (df_bin,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)

    check_func(impl3, (test_df,), sort_output=True, check_dtype=check_dtype)
    check_func(impl3, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl3, (df_bool,), sort_output=True)
    check_func(impl3, (df_dt,), sort_output=True)
    check_func(impl3, (df_bin,), sort_output=True)
    check_func(impl4, (11,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_as_index_first_last(memory_leak_check):
    """
    Test first and last on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).first()
        return df2

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].first()
        return df2

    def impl3(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).last()
        return df2

    def impl4(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].last()
        return df2

    check_func(impl1, (11,), sort_output=True, reset_index=True)
    check_func(impl2, (11,), sort_output=True, reset_index=True)
    check_func(impl3, (11,), sort_output=True, reset_index=True)
    check_func(impl4, (11,), sort_output=True, reset_index=True)


def test_std(test_df_int_no_null, memory_leak_check):
    """
    Test Groupby.std()
    """

    def impl1(df):
        # NOTE: pandas fails here if one of the data columns is Int64 with all
        # nulls. That is why this test uses test_df_int_no_null
        A = df.groupby("A").std()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").std()
        return A

    check_func(
        impl1,
        (test_df_int_no_null,),
        sort_output=True,
        reset_index=True,
        check_dtype=False,
    )
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_std_one_col(test_df, memory_leak_check):
    """
    Test Groupby.std() with one column selected
    """

    # TODO: std _is_ supported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A")["B"].std()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].std()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_as_index_std(memory_leak_check):
    """
    Test std on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).std()
        return df2

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].std()
        return df2

    check_func(impl1, (11,), sort_output=True, check_dtype=False, reset_index=True)
    check_func(impl2, (11,), sort_output=True, check_dtype=False, reset_index=True)


def test_sum(test_df, memory_leak_check):
    """
    Test Groupby.sum()
    """

    # sum is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A").sum()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").sum()
        return A

    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_sum_one_col(test_df, memory_leak_check):
    """
    Test Groupby.sum() with one column selected
    """

    # sum is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A")["B"].sum()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].sum()
        return A

    df_str = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", "ff", "bb", "rr", "bb", "ggg", "aa"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
        }
    )

    check_func(impl1, (df_str,), sort_output=True)
    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


@pytest_mark_pandas
def test_select_col_attr(memory_leak_check):
    """
    Test Groupby with column selected using getattr instead of getitem
    """

    def impl(df):
        A = df.groupby("A").B.sum()
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
def test_select_col_single_list(memory_leak_check):
    """
    Test Groupby with single column selected but using a list (should return a DataFrame
    not Series)
    """

    def impl(df):
        A = df.groupby("A")[["B"]].sum()
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_as_index_sum(memory_leak_check):
    """
    Test sum on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({1: np.ones(n, np.int64), 2: np.arange(n)})
        df2 = df.groupby(1, as_index=False).sum()
        return df2

    def impl2(n):
        df = pd.DataFrame({3: np.ones(n, np.int64), -3: np.arange(n)})
        df2 = df.groupby(3, as_index=False)[-3].sum()
        return df2

    check_func(impl1, (11,), sort_output=True, reset_index=True)
    check_func(impl2, (11,), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_agg_nested_tup_colnames(memory_leak_check):
    """
    Test Groupby.agg() combination that produces nested tuple names (see #2424)
    """

    def impl(df):
        df1 = df.groupby("A").agg({"B": ["sum"]}).reset_index()
        res = df1.groupby("A").agg({("B", "sum"): ["sum"]})
        # replacing output name since Pandas doesn't preserve nested tuple names
        res.columns = ["C"]
        return res

    np.random.seed(3)
    nums = np.concatenate([np.arange(50), np.arange(50)])
    df = pd.DataFrame({"A": nums, "B": np.random.random(100)})
    check_func(impl, (df,), sort_output=True, reset_index=True, check_names=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_multi_intlabels_sum(memory_leak_check):
    """
    Test df.groupby() multiple labels of string columns
    and Groupy.sum() on integer column
    """

    def impl(df):
        A = df.groupby(["A", "C"])["B"].sum()
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    check_func(impl, (df,), sort_output=True)


# TODO: add memory leak check when issues addressed
@pytest_mark_pandas
def test_groupby_multi_key_to_index(memory_leak_check):
    """
    Make sure df.groupby() with multiple keys creates a MultiIndex index in output
    """

    def impl(df):
        A = df.groupby(["A", "C"])["B"].sum()
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    # not using check_func(... sort_output=True) since it drops index, but we need to
    # make sure proper index is being created
    # TODO: avoid dropping index in check_func(... sort_output=True) when indexes are
    # supported properly for various APIs
    pd.testing.assert_series_equal(
        bodo.jit(impl)(df).sort_index(), impl(df).sort_index()
    )


@pytest_mark_pandas
def test_groupby_multi_strlabels(memory_leak_check):
    """
    Test df.groupby() multiple labels of string columns
    with as_index=False, and Groupy.sum() on integer column
    """

    def impl(df):
        df2 = df.groupby(["A", "B"], as_index=False)["C"].sum()
        return df2

    df = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", "a", "a", "aa", "ccc", "ggg", "a"],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_multiselect_sum(memory_leak_check):
    """
    Test groupy.sum() on explicitly selected columns using a constant
    list (#198)
    """

    def impl(df):
        df2 = df.groupby("A")[["B", "C"]].sum()
        return df2

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    check_func(impl, (df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_agg_multikey_parallel(memory_leak_check):
    """
    Test groupby multikey with distributed df
    """

    def test_impl(df):
        A = df.groupby(["A", "C"])["B"].sum()
        return A.sum()

    bodo_func = bodo.jit(distributed_block=["df"])(test_impl)
    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    start, end = get_start_end(len(df))
    h_res = bodo_func(df.iloc[start:end])
    p_res = test_impl(df)
    assert h_res == p_res


def test_var(test_df, memory_leak_check):
    """
    Test Groupby.var()
    """

    # var is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A").var()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").var()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_var_std_supported_types(memory_leak_check):
    """
    Test Groupby.var()
    """

    def impl1(df):
        A = df.groupby("A").var()
        return A

    def impl2(df):
        A = df.groupby("A").std()
        return A

    # Empty dataframe
    df = pd.DataFrame({"A": [], "B": []})
    check_func(impl1, (df,), sort_output=True, check_dtype=False)
    check_func(impl2, (df,), sort_output=True, check_dtype=False)

    # Zero columns
    df_empty = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]})
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl1)(df_empty)
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl2)(df_empty)

    # Test different column types in same dataframe
    df_mix = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 3],
            "B": [1.1, 2.2, 3.3, 4.4, 1.1],
            "C": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
        }
    )
    check_func(impl1, (df_mix,), sort_output=True, check_dtype=False)
    check_func(impl2, (df_mix,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_var_one_col(test_df, memory_leak_check):
    """
    Test Groupby.var() with one column selected
    """

    # var is unsupported by Pandas groupby on categorical columns
    if isinstance(test_df.iloc[:, 0].dtype, pd.CategoricalDtype):
        return

    def impl1(df):
        A = df.groupby("A")["B"].var()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].var()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
def test_groupby_key_value_shared(memory_leak_check):
    """
    Test using the key column in a groupby operation.
    """

    def test_impl(df):
        return df.groupby("A")["A"].count().reset_index(drop=True)

    df = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", "a", "a", "aa", "ccc", "ggg", "a"],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    check_func(test_impl, (df,), sort_output=True)


def test_groupby_key_value_shared_named_agg(memory_leak_check):
    """
    Test using a key column in a groupby operation with the
    NamedAgg syntax.
    """

    def test_impl(df):
        return (
            df.groupby(["A", "B"], as_index=False)
            .agg(cnt=pd.NamedAgg(column="A", aggfunc="count"))
            .sort_values(by=["A", "B", "cnt"])
            .reset_index(drop=True)
        )

    df = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", "a", "a", "aa", "ccc", "ggg", "a"],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    check_func(test_impl, (df,))


def test_idxmin_idxmax(memory_leak_check):
    """
    Test Groupby.idxmin() and Groupby.idxmax()
    """

    def impl1(df):
        A = df.groupby("group").idxmin()
        return A

    def impl2(df):
        A = df.groupby("group").agg(
            {"values_1": "idxmin", "values_2": lambda x: x.max() - x.min()}
        )
        return A

    def impl3(df):
        A = df.groupby("group").idxmax()
        return A

    def impl4(df):
        A = df.groupby("group").agg(
            {"values_1": "idxmax", "values_2": lambda x: x.max() - x.min()}
        )
        return A

    df1 = pd.DataFrame(
        {
            "values_1": [10.51, 103.11, 55.48, 23.3, 53.2, 12.3, 7200.722],
            "values_2": [37, 19, 1712, 55, 668, 489, 25],
            "group": [0, 1, 1, 0, 0, 11, 1],
        },
        index=["A", "B", "C", "D", "E", "F", "G"],
    )

    df2 = pd.DataFrame(
        {
            "values_1": [10.51, 103.11, 55.48, 23.3, 53.2, 12.3, 3.67],
            "values_2": [37, 19, 1712, 55, 668, 489, 18],
            "group": [0, 1, 1, 0, 0, 11, 1],
        }
    )

    df3 = pd.DataFrame(
        {
            "values_1": [10.51, 55.48, 103.11, 23.3, 53.2, 12.3, 50.23],
            "values_2": [37, 19, 1712, 55, 668, 489, 1713],
            "group": [0, 1, 1, 0, 0, 11, 1],
        },
        index=[33, 4, 3, 7, 11, 127, 0],
    )

    check_func(impl1, (df1,), sort_output=True)
    check_func(impl1, (df2,), sort_output=True)
    check_func(impl1, (df3,), sort_output=True)
    check_func(impl2, (df1,), sort_output=True)

    check_func(impl3, (df1,), sort_output=True)
    check_func(impl3, (df2,), sort_output=True)
    check_func(impl3, (df3,), sort_output=True)
    check_func(impl4, (df1,), sort_output=True)


@pytest.mark.parametrize(
    "df",
    [
        # Test different column types in same dataframe
        pd.DataFrame(
            {
                "A": [2, 1, 1, 2, 3],
                "B": [1.1, 2.2, 3.3, 4.4, 1.1],
            }
        ),
        # nullable int
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 3],
                    "B": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # nullable float
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 3],
                    "B": pd.Series([1.1, 2.2, 3.3, None, 5.5], dtype="Float64"),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # boolean
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 1, 2, 2, 1],
                    "B": [True, True, False, True, True, False, False],
                }
            ),
            marks=pytest.mark.slow,
        ),
    ],
)
def test_idxmin_idxmax_supported_types(df, memory_leak_check):
    """
    Test Groupby.idxmin() and Groupby.idxmax()
    """

    def impl1(df):
        A = df.groupby("A").idxmin()
        return A

    def impl2(df):
        A = df.groupby("A").idxmax()
        return A

    # Empty dataframe
    # TODO: [BE-547] bodo keeps columns result.shape = (0, 1), while Pandas doesn't (0,0)
    # df = pd.DataFrame({"A": [], "B": []})
    # check_func(impl1, (df,), sort_output=True)
    # check_func(impl2, (df,), sort_output=True)

    check_func(impl1, (df,), sort_output=True, reset_index=True)
    check_func(impl2, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_as_index_var(memory_leak_check):
    """
    Test var on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), 11: np.arange(n)})
        df2 = df.groupby("A", as_index=False).var()
        return df2

    def impl2(n):
        df = pd.DataFrame({4: np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby(4, as_index=False)["B"].var()
        return df2

    check_func(impl1, (11,), sort_output=True, check_dtype=False, reset_index=True)
    check_func(impl2, (11,), sort_output=True, check_dtype=False, reset_index=True)


@pytest_mark_pandas
def test_const_list_inference(memory_leak_check):
    """
    Test passing non-const list that can be inferred as constant to groupby()
    """

    def impl1(df):
        return df.groupby(["A"] + ["B"]).sum()

    def impl2(df):
        return df.groupby(list(set(df.columns) - {"A", "C"})).sum()

    # test df schema change by setting a column
    def impl3(n):
        df = pd.DataFrame({"A": np.arange(n), "B": np.ones(n)})
        df["D"] = 4
        return df.groupby("D").sum()

    # groupby in a loop to trigger loop unrolling
    def impl_unroll(df):
        s = 0
        for c in df.columns:
            s += df.groupby(c).count().iloc[:, 0].max()
        return s

    # make sure const list is not updated inplace
    def impl4(df):
        l = ["A"]
        l.append("B")
        return df.groupby(l).sum()

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1, 3, 0],
            "B": ["a", "b", "c", "c", "b", "c", "a", "AA", "A2"],
            "C": [1, 3, 1, 2, -4, 0, 5, 6, 7],
        }
    )

    # Maybe we can avoid those reset_index=True
    check_func(impl1, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)
    check_func(impl3, (11,), sort_output=True)
    check_func(impl_unroll, (df,))
    with pytest.raises(
        BodoError,
        match="argument 'by' requires a constant value but variable 'l' is updated inplace using 'append'",
    ):
        bodo.jit(impl4)(df)


# global key list for groupby() testing
g_keys = ["A", "B"]


@pytest_mark_pandas
def test_global_list(memory_leak_check):
    """
    Test passing a global list to groupby()
    """

    # freevar key list for groupby() testing
    f_keys = ["A", "B"]

    # global case
    def impl1(df):
        return df.groupby(g_keys).sum()

    # freevar case
    def impl2(df):
        return df.groupby(f_keys).sum()

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1, 3, 0],
            "B": ["a", "b", "c", "c", "b", "c", "a", "AA", "A2"],
            "C": [1, 3, 1, 2, -4, 0, 5, 6, 7],
        }
    )

    check_func(impl1, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)


df_global = pd.DataFrame({"A": [1, 2, 1], "B": [1.1, 2.2, 3.3]})


@pytest_mark_pandas
def test_global_df(memory_leak_check):
    """test groupby on a global dataframe object"""

    def impl():
        return df_global.groupby("A").sum()

    check_func(impl, (), sort_output=True, only_seq=True)


@pytest_mark_pandas
def test_literal_args(memory_leak_check):
    """
    Test forcing groupby() key list and as_index to be literals if jit arguments
    """

    # 'by' arg
    def impl1(df, keys):
        return df.groupby(keys).sum()

    # both 'by' and 'as_index'
    def impl2(df, keys, as_index):
        return df.groupby(by=keys, as_index=as_index).sum()

    # computation on literal arg
    def impl3(df, keys, as_index):
        return df.groupby(by=keys + ["B"], as_index=as_index).sum()

    # getitem index
    def impl4(df, idx):
        return df.groupby("A")[idx].sum()

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [1, 3, 3, 1, 3, 1, 3],
            "C": [1, 3, 1, 2, -4, 0, 5],
        }
    )

    check_func(impl1, (df, ["A", "B"]), sort_output=True)
    check_func(impl2, (df, "A", False), sort_output=True, reset_index=True)
    check_func(impl2, (df, ["A", "B"], True), sort_output=True)
    check_func(impl3, (df, ["A"], True), sort_output=True)
    check_func(impl4, (df, "B"), sort_output=True)
    check_func(impl4, (df, ["B", "C"]), sort_output=True)


@pytest_mark_pandas
def test_schema_change(memory_leak_check):
    """
    Test df schema change for groupby() to make sure errors are not thrown
    """

    # schema change in dict agg case
    def impl1(df):
        df["AA"] = np.arange(len(df))
        return df.groupby(["A"]).agg({"AA": "sum", "B": "count"})

    # schema change for groupby object getitem
    def impl2(df):
        df["AA"] = np.arange(len(df))
        return df.groupby(["A"]).AA.sum()

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": ["a", "b", "c", "c", "b", "c", "a"],
            "C": [1, 3, 1, 2, -4, 0, 5],
        }
    )

    check_func(impl1, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)


@pytest_mark_pandas
def test_groupby_empty_funcs(memory_leak_check):
    """Test groupby that has no function to execute (issue #1590)"""

    def impl(df):
        first_df = df.groupby("A", as_index=False)["B"].max()
        return len(first_df)

    df = pd.DataFrame({"A": [0, 0, 0, 1, 1, 1], "B": range(6)})
    assert impl(df) == bodo.jit(impl)(df)


@pytest_mark_pandas
def test_groupby_in_loop(memory_leak_check):
    """Test groupby inside a loop, where input shape info is not available in array
    analysis"""

    def impl(df):
        s = 0
        for _ in range(3):
            df2 = df.groupby("A").sum()
            s += df2.B.sum()
        return s

    df = pd.DataFrame({"A": [0, 0, 0, 1, 1, 1], "B": range(6)})
    assert impl(df) == bodo.jit(impl)(df)


@pytest_mark_pandas
def test_groupby_dead_col_multifunc(memory_leak_check):
    """Test dead column elimination in groupbys with UDFs (issues #1724, #1732, #1750)"""

    # a dict item is unused
    def impl1(df):
        out_df = df.groupby("C").agg({"B": lambda x: x.max() - x.min(), "A": "min"})
        return len(out_df.iloc[:, 0])

    # all output of a dict item that is a list are unused
    def impl2(df):
        out_df = df.groupby("C", as_index=False).agg(
            {"B": lambda x: x.max() - x.min(), "A": ["min", lambda x: x.sum()]}
        )
        return len(out_df.iloc[:, 1])

    # tuple item is unused
    def impl3(df):
        out_df = df.groupby("C")["B"].agg(
            (lambda x: x.max() - x.min(), "min", lambda x: x.sum())
        )
        return len(out_df.iloc[:, 1])

    # all tuple items are unused
    def impl4(df):
        out_df = df.groupby("C")["B"].agg(
            (lambda x: x.max() - x.min(), "min", lambda x: x.sum())
        )
        return len(out_df.index)

    # dead single agg func inside a dictionary with agg func list to make output names
    # all tuples
    def impl5(df):
        out_df = df.groupby("C").agg({"A": ["min", "max"], "B": "sum"})
        return len(out_df.iloc[:, 0])

    def impl6(df):
        out_df = df.groupby("A", as_index=False).agg(
            B=pd.NamedAgg(column="B", aggfunc=lambda A: A.sum()),
            E=pd.NamedAgg(column="B", aggfunc="min"),
            F=pd.NamedAgg(column="C", aggfunc="max"),
        )
        return len(out_df.iloc[:, 0])

    df = pd.DataFrame(
        {
            "A": [0, 0, 1, 1, 0, 0, 1, 0],
            "B": [0.3, 0.7, 0.123, 0.66, 0.7, 0.1, 0.15, 0.23],
            "C": [3, 7, 123, 66, 7, 1, 15, 23],
        }
    )
    assert impl1(df) == bodo.jit(impl1)(df)
    assert impl2(df) == bodo.jit(impl2)(df)
    assert impl3(df) == bodo.jit(impl3)(df)
    assert impl4(df) == bodo.jit(impl4)(df)
    assert impl5(df) == bodo.jit(impl5)(df)
    assert impl6(df) == bodo.jit(impl6)(df)


@pytest_mark_pandas
def test_groupby_shift_cat(memory_leak_check):
    """Checks that groupby.shift is supported
    when the target column is categorical."""

    def test_impl(df):
        df2 = df.groupby("A")["B"].shift(-1)
        return df2

    # Check with all categorical dtypes for index conversion
    df = pd.DataFrame(
        {
            "A": [1, 1, 1, 4, 5],
            "B": pd.Categorical(["LB1", "LB2", "LB1", None, "LB2"], ordered=True),
            "C": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    check_func(test_impl, (df,))


@pytest_mark_pandas
def test_groupby_shift_unknown_cats(memory_leak_check):
    """Checks that groupby.shift is supported
    when the target column is categorical."""

    def test_impl(df):
        df["B"] = df["B"].astype("category")
        df2 = df.groupby("A")["B"].shift(-1)
        return df2

    df1 = pd.DataFrame(
        {
            "A": [1, 1, 1, 4, 5],
            "B": ["LB1", "LB2", "LB1", None, "LB2"],
            "C": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    df2 = pd.DataFrame(
        {
            "A": [1, 1, 1, 4, 5],
            "B": [2, 3, 4, -2, None],
            "C": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    df3 = pd.DataFrame(  # noqa: F841
        {
            "A": [1, 1, 1, 4, 5],
            "B": np.array([2, 3, 4, 5, 8], dtype=np.uint8),
            "C": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    df4 = pd.DataFrame(
        {
            "A": [1, 1, 1, 4, 5],
            "B": pd.date_range(start="2/1/2015", end="2/24/2021", periods=5),
            "C": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    df5 = pd.DataFrame(
        {
            "A": [1, 1, 1, 4, 5],
            "B": pd.timedelta_range(start="1 day", periods=5),
            "C": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    df6 = pd.DataFrame(
        {
            "A": [1, 1, 1, 4, 5],
            "B": [2.5, 3.3, 4.1, 5.0, np.nan],
            "C": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    check_func(test_impl, (df1,), copy_input=True)
    check_func(test_impl, (df2,), copy_input=True)
    # TODO: Fix dtype. Bodo creates a Int64Index, Pandas UInt64Index
    # check_dtype/check_categorical doesn't work for testing
    # check_func(test_impl, (df3,), copy_input=True)
    check_func(test_impl, (df4,), copy_input=True)
    check_func(test_impl, (df5,), copy_input=True)
    check_func(test_impl, (df6,), copy_input=True)


@pytest.mark.skip(reason="[BE-961] TODO: Return nullable int")
def test_groupby_shift_int():
    """
    Test Groupby.shift(): a simple case
    """

    def impl(df):
        df2 = df.groupby("A")["B"].shift()
        return df2

    df1 = pd.DataFrame(
        {"A": [1, 2, 2, 1, 1], "B": [10, 20, 30, 40, 50]}, index=np.arange(42, 47)
    )
    # Can't test  becuase of nan vs. -1??
    # check_func(impl, (df1,), check_dtype=False, reset_index=True)
    print("\ndf1:\n", df1)
    print("Pandas result:\n", impl(df1))
    print("-----\nBodo result:\n")
    print(bodo.jit(distributed=["df"])(impl)(df1))

    def impl2(df):
        df2 = df.groupby("A").shift(-2)
        return df2

    df_multicol = pd.DataFrame(
        {
            "A": [1, 2, 2, 1, 1],
            "B": [10, 20, 30, 40, 50],
            "C": [100, 200, 300, 400, 500],
        }
    )
    print("\ndf_multicol:\n", df_multicol)
    print("Pandas result:\n", impl2(df_multicol))
    print("-----\nBodo result:\n")
    print(bodo.jit(distributed=["df"])(impl2)(df_multicol))


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_shift_timedelta(memory_leak_check):
    def impl2(df):
        df2 = df.groupby("A").shift(-2)
        return df2

    df = pd.DataFrame(
        {
            "A": [
                datetime.timedelta(3, 3, 3),
                datetime.timedelta(2, 2, 2),
                datetime.timedelta(1, 1, 1),
                None,
                datetime.timedelta(5, 5, 5),
            ],
            "B": [
                datetime.timedelta(3, 3, 3),
                datetime.timedelta(2, 2, 2),
                datetime.timedelta(1, 1, 1),
                None,
                datetime.timedelta(5, 5, 5),
            ],
        }
    )
    check_func(impl2, (df,))


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_shift_binary(memory_leak_check):
    """tests groupby shift for dataframes containing binary data"""

    def impl2(df):
        df2 = df.groupby("A").shift(-2)
        return df2

    df = pd.DataFrame(
        {
            "A": [1, 1, 1, 2, 2] * 2,
            "B": [b"hkjl", b"jkhb", None, bytes(4), b"mhgt"] * 2,
        }
    )
    check_func(impl2, (df,))


@pytest_mark_pandas
def test_groupby_shift_simple(memory_leak_check):
    def impl(df):
        df2 = df.groupby("A").shift()
        return df2

    df3 = pd.DataFrame(
        {
            "A": [0.3, np.nan, 3.5, 0.2, np.nan, 3.3, 0.2, 0.3, 0.2, 0.2],
            "B": [-1.1, 1.1, 3.2, 1.1, 5.2, 6.8, 7.3, 3.4, 1.2, 2.4],
            "C": [-8.1, 2.3, 5.3, 1.1, 0.5, 4.6, 1.7, 4.3, -8.1, 5.3],
        },
        index=np.arange(52, 62),
    )
    check_func(impl, (df3,))


@pytest_mark_pandas
def test_groupby_shift_dead_index(memory_leak_check):
    """Test dead output Index case for groupby shift which returns an Index."""

    def impl(df):
        return df.groupby("A")["B"].shift().values

    df3 = pd.DataFrame(
        {
            "A": [0.3, np.nan, 3.5, 0.2, np.nan, 3.3, 0.2, 0.3, 0.2, 0.2],
            "B": [-1.1, 1.1, 3.2, 1.1, 5.2, 6.8, 7.3, 3.4, 1.2, 2.4],
            "C": [-8.1, 2.3, 5.3, 1.1, 0.5, 4.6, 1.7, 4.3, -8.1, 5.3],
        },
        index=np.arange(52, 62),
    )
    check_func(impl, (df3,))


@pytest_mark_pandas
@pytest.mark.parametrize(
    "periods",
    [0, 2, -2],
)
def test_groupby_shift_main(periods):
    def impl2(df):
        df2 = df.groupby("A").shift(periods)
        return df2

    def impl3(df):
        df2 = df.groupby("A").shift(periods=periods)
        return df2

    df3 = pd.DataFrame(
        {
            "A": [0.3, np.nan, 3.5, 0.2, np.nan, 3.3, 0.2, 0.3, 0.2, 0.2],
            "B": [-1.1, 1.1, 3.2, 1.1, 5.2, 6.8, 7.3, 3.4, 1.2, 2.4],
            "C": [-8.1, 2.3, 5.3, 1.1, 0.5, 4.6, 1.7, 4.3, -8.1, 5.3],
        },
        index=np.arange(52, 62),
    )
    check_func(impl2, (df3,))
    check_func(impl3, (df3,))

    siz = 10
    datetime_arr_1 = pd.date_range("1917-01-01", periods=siz)
    date_arr = datetime_arr_1.date
    df1_datetime = pd.DataFrame(
        {"A": [1, 2, 1, 4, 5, 6, 4, 6, 6, 1], "B": datetime_arr_1}
    )
    df1_date = pd.DataFrame({"A": np.arange(siz), "B": date_arr})

    check_func(impl2, (df1_datetime,))
    check_func(impl3, (df1_datetime,))

    check_func(impl2, (df1_date,))
    check_func(impl3, (df1_date,))

    df1_str = pd.DataFrame(
        {
            "A": [1, 1, 1, 2, 3, 3, 4, 0, 5, 0, 11],
            "B": ["a", "b", "c", "d", "", "AA", "ABC", "AB", "c", "F", "GG"],
        }
    )
    check_func(impl2, (df1_str,))
    check_func(impl3, (df1_str,))

    n = 10
    random.seed(5)
    df_ls = pd.DataFrame(
        {
            "A": gen_random_list_string_array(2, n),
            "B": gen_random_list_string_array(2, n),
        }
    )
    check_func(impl2, (df_ls,))
    check_func(impl3, (df_ls,))


@pytest.mark.parametrize("by", ["A", "B", ["A", "B"]])
def test_groupby_size(by, memory_leak_check):
    def impl(df):
        result = df.groupby(by=by).size()
        return result

    np.random.seed(3)
    df = pd.DataFrame(np.random.choice(20, (1000, 4)), columns=list("ADBC"))
    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_groupby_size_single_column(memory_leak_check):
    """test groupby size() for dataframes with single column"""

    def impl(df):
        return df.groupby("A").size()

    df = pd.DataFrame({"A": [1, 2, 3] * 3})
    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_size(memory_leak_check):
    def impl(df):
        result = df.groupby("class", as_index=False).size()
        return result

    df2 = pd.DataFrame(
        [
            ("bird", "Falconiformes", b"a", 389.0),
            ("bird", "nan", b"b", 24.0),
            ("mammal", "Carnivora", b"c", 80.2),
            ("mammal", "Primates", b"d", np.nan),
            ("mammal", "Carnivora", b"e", 58),
        ],
        index=["falcon", "parrot", "lion", "monkey", "leopard"],
        columns=("class", "order", "bin_id", "max_speed"),
    )

    check_func(impl, (df2,), sort_output=True, reset_index=True)


def test_size_remove_dead(memory_leak_check):
    """make sure dead "size" column can be removed without error"""

    def impl(df):
        df2 = df.groupby("A", as_index=False).size()
        return df2.A

    df = pd.DataFrame({"A": [2, 1, 1, 1, 2, 3], "B": [1, 2, 3, 4, 5, 1]})

    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest.mark.parametrize(
    "df_null",
    [
        pd.DataFrame(
            {"A": [2, 1, 1, 1], "B": pd.Series(np.full(4, np.nan), dtype="Int64")},
            index=[32, 45, 56, 76],
        ),
        pytest.param(
            pd.DataFrame(
                {"A": [1, 1, 1, 1], "B": pd.Series([1, 2, 3, 4], dtype="Int64")},
                index=[3, 4, 5, 6],
            ),
            marks=pytest.mark.slow,
        ),
    ],
)
def test_size_agg(df_null, memory_leak_check):
    def impl1(df):
        df2 = df.groupby("A")["B"].agg(("size", "sum"))
        return df2

    check_func(impl1, (df_null,), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_cumulatives_supported_cases(memory_leak_check):
    """
    Test Groupby.cummin, cummax, cumsum, cumprod
    """

    def impl1(df):
        A = df.groupby("A").cummin()
        return A

    def impl2(df):
        A = df.groupby("A").cummax()
        return A

    def impl3(df):
        A = df.groupby("A").cumsum()
        return A

    def impl4(df):
        A = df.groupby("A").cumprod()
        return A

    # Empty dataframe
    df = pd.DataFrame({"A": [], "B": []})
    check_func(impl1, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)
    check_func(impl3, (df,), sort_output=True)
    check_func(impl4, (df,), sort_output=True)

    # Zero columns
    df_empty = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]})
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl1)(df_empty)
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl2)(df_empty)
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl3)(df_empty)
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl4)(df_empty)

    # Test different column types in same dataframe
    df_mix = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 3],
            "B": [1.1, 2.2, 3.3, 4.4, 1.1],
            "C": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
        }
    )
    check_func(impl1, (df_mix,), sort_output=True, check_dtype=False)
    check_func(impl2, (df_mix,), sort_output=True, check_dtype=False)
    check_func(impl3, (df_mix,), sort_output=True, check_dtype=False)
    check_func(impl4, (df_mix,), sort_output=True, check_dtype=False)


@pytest.fixture(
    params=[
        # Decimal
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 2],
                    "B": pd.Series(
                        [
                            Decimal("1.6"),
                            Decimal("-0.2"),
                            Decimal("44.2"),
                            None,
                            Decimal("0"),
                        ]
                    ),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # datetime
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 3],
                    "B": pd.date_range(start="2018-04-24", end="2018-04-29", periods=5),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # timedelta
        pytest.param(
            pd.DataFrame(
                {
                    "A": [1, 2, 3, 2, 1],
                    "B": pd.Series(pd.timedelta_range(start="1 day", periods=5)),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # Categorical
        pytest.param(
            pd.DataFrame(
                {
                    "A": [16, 1, 1, 1, 16, 16],
                    "B": pd.Categorical([1, 2, 5, 5, 3, 3], ordered=True),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # Timestamp
        pytest.param(
            pd.DataFrame(
                {
                    "A": [16, 1, 1, 1, 16],
                    "B": [
                        pd.Timestamp("20130101 09:00:00"),
                        pd.Timestamp("20130101 09:00:02"),
                        pd.Timestamp("20130101 09:00:03"),
                        pd.Timestamp("20130101 09:00:05"),
                        pd.Timestamp("20130101 09:00:06"),
                    ],
                }
            ),
            marks=pytest.mark.slow,
        ),
        # string
        pytest.param(
            pd.DataFrame(
                {
                    "A": [16, 1, 1, 1, 16, 16, 1, 40],
                    "B": ["ab", "cd", "ef", "gh", "mm", "a", "abc", "x"],
                }
            ),
            marks=pytest.mark.slow,
        ),
        # nullable
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 3],
                    "B": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # boolean
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 1, 2, 2, 1],
                    "B": [True, True, False, True, True, False, False],
                }
            ),
            marks=pytest.mark.slow,
        ),
        # list
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 1, 1],
                    "B": pd.Series([[1, 2], [3], [5, 4, 6], [-1, 3, 4], [1], [1, 2]]),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # Tuple
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 1, 2],
                    "B": pd.Series([(1, 2), (3, 4), (5, 4), (-1, 3), (1, 5), (1, 2)]),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # nullable boolean
        pytest.param(
            pd.DataFrame(
                {"A": [2, 1, 1, 2, 3], "B": pd.array([True, False, None, True, True])}
            )
        ),
        # nullable float
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 3],
                    "B": pd.array([1.1, 2.2, None, 4.4, 1.1], dtype="Float64"),
                }
            )
        ),
        # binary
        pytest.param(
            pd.DataFrame(
                {
                    "A": [16, 1, 1, 1, 16, 16, 1, 40],
                    "B": [b"ab", b"cd", b"ef", None, b"mm", b"a", b"abc", b"x"],
                }
            ),
            marks=pytest.mark.slow,
            id="bin_test",
        ),
        # Empty dataframe
        pytest.param(
            pd.DataFrame({"A": [], "B": []}),
            marks=pytest.mark.slow,
        ),
        # Test different column types in same dataframe
        pytest.param(
            pd.DataFrame(
                {
                    "A": [2, 1, 1, 2, 3],
                    "B": [1.1, 2.2, 3.3, 4.4, 1.1],
                    "C": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
                }
            ),
            marks=pytest.mark.slow,
        ),
        # Zero columns
        pytest.param(
            pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]}),
            marks=pytest.mark.slow,
        ),
    ],
)
def test_size_df(request):
    return request.param


@pytest_mark_pandas
def test_size_supported_types(test_size_df, memory_leak_check):
    """
    Test Groupby.size
    Supports all since it doesn't care about type of column
    """

    def impl1(df):
        A = df.groupby("A").size()
        return A

    check_func(impl1, (test_size_df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_count_supported_cases(memory_leak_check):
    """
    Test Groupby.count
    """

    def impl1(df):
        A = df.groupby("A").count()
        return A

    # Empty dataframe
    df = pd.DataFrame({"A": [], "B": []})
    check_func(impl1, (df,), sort_output=True, check_dtype=False)

    # Zero columns
    df_empty = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]})
    with pytest.raises(BodoError, match="No columns in output"):
        bodo.jit(impl1)(df_empty)

    # Test different column types in same dataframe
    df_mix = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 3],
            "B": [1.1, 2.2, 3.3, 4.4, 1.1],
            "C": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
            "D": [b"ab", b"cd", b"ef", None, b"mm"],
        }
    )
    check_func(impl1, (df_mix,), sort_output=True, check_dtype=False)


@pytest_mark_pandas
def test_value_counts(memory_leak_check):
    """Test groupby.value_counts"""

    # SeriesGroupBy
    def impl1(df):
        ans = df.groupby(["C", "X"])["D"].value_counts()
        return ans

    # Pandas restriction DataFrameGroupBy
    def impl2(df):
        ans = df.groupby(["X"]).value_counts()
        return ans

    # Pandas restriction as_index=False not allowed
    def impl3(df):
        ans = df.groupby(["X"], as_index=False).value_counts()
        return ans

    df = pd.DataFrame(
        {
            "X": [1, 1, 1, 3, 2, 3],
            "C": ["ab", "ab", "ab", "cd", "ef", "cd"],
            "E": [b"ab", b"ab", b"ab", b"cd", b"ef", b"cd"],
            "D": [5, 6, 5, 1, 4, 1],
        }
    )
    check_func(impl1, (df,), sort_output=True)
    with pytest.raises(BodoError, match="'DataFrameGroupBy' object has no attribute"):
        bodo.jit(impl2)(df)

    with pytest.raises(BodoError, match="'DataFrameGroupBy' object has no attribute"):
        bodo.jit(impl3)(df)


@pytest_mark_pandas
@pytest.mark.slow
def test_nunique_supported_types(test_size_df, memory_leak_check):
    """
    Test Groupby.nunique
    Skipping: dataframe that has no columns in output,
              columns with categorical, tuple, and list type cases
    """

    if len(test_size_df.columns) == 1 or (
        isinstance(test_size_df["B"].dtype, pd.CategoricalDtype)
        or (len(test_size_df) > 0 and isinstance(test_size_df["B"][0], (tuple, list)))
    ):
        return

    def impl1(df):
        A = df.groupby("A").nunique()
        return A

    check_func(impl1, (test_size_df,), sort_output=True)


@pytest_mark_pandas
def test_nunique_categorical(memory_leak_check):
    """
    Test groupby.nunique on a column with categorical data.
    """

    def impl1(df):
        A = df.groupby("A").nunique()
        return A

    def impl2(df):
        A = df.groupby("A").nunique(dropna=False)
        return A

    df = pd.DataFrame(
        {
            "A": ["a", "b", "c", "d", "e"] * 16,
            "B": pd.Categorical(["abc", "3e3", None, "rewrwe"] * 20),
        }
    )

    check_func(impl1, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)


def test_nunique_dict(memory_leak_check):
    """
    Test groupby.nunique on a column with dictionary encode data.
    """

    def impl1(df):
        A = df.groupby("A").nunique()
        return A

    def impl2(df):
        A = df.groupby("A").nunique(dropna=False)
        return A

    df = pd.DataFrame(
        {
            "A": ["a", "b", "c", "d", "e"] * 16,
            "B": pd.array(["abc", "3e3", None, "rewrwe"] * 20),
        }
    )

    check_func(impl1, (df,), sort_output=True, use_dict_encoded_strings=True)
    check_func(impl2, (df,), sort_output=True, use_dict_encoded_strings=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_shift_supported_types(test_size_df, memory_leak_check):
    """
    Test Groupby.shift
    Skipping: dataframe that has no columns in output,
              columns with tuple, and list type cases
    """

    if len(test_size_df.columns) == 1 or (
        len(test_size_df) > 0 and isinstance(test_size_df["B"][0], (tuple, list))
    ):
        return

    def impl1(df):
        A = df.groupby("A").shift()
        return A

    check_func(impl1, (test_size_df,), sort_output=True)


@pytest_mark_pandas
@pytest.mark.parametrize(
    "df",
    [
        # Empty dataframe
        pd.DataFrame({"A": [], "B": []}),
        # Test different column types in same dataframe
        pd.DataFrame(
            {
                "A": [2, 1, 1, 2, 3],
                "B": [1.1, 2.2, 3.3, 4.4, 1.1],
                "C": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
            }
        ),
        # nullable boolean
        pd.DataFrame(
            {"A": [2, 1, 1, 2, 3], "B": pd.array([True, False, None, True, True])}
        ),
        # nullable int
        pd.DataFrame(
            {
                "A": [2, 1, 1, 2, 3],
                "B": pd.Series([1, 2, 3, 4, 5], dtype="Int64"),
            }
        ),
        # nullable float
        pd.DataFrame(
            {
                "A": [2, 1, 1, 2, 3],
                "B": pd.Series([1.1, 2.2, 3.3, 4.4, 5.5], dtype="float64"),
            }
        ),
        # boolean
        pd.DataFrame(
            {
                "A": [2, 1, 1, 1, 2, 2, 1],
                "B": [True, True, False, True, True, False, False],
            }
        ),
    ],
)
@pytest.mark.slow
def test_agg_supported_types(df, memory_leak_check):
    """
    Test Groupby.agg()
    """

    def impl1(df):
        A = df.groupby("A").agg(lambda x: x.sum())
        return A

    check_func(impl1, (df,), sort_output=True, check_dtype=False, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
@pytest.mark.parametrize(
    "df",
    [
        pd.DataFrame(
            {
                "A": ["foo", "foo", "foo", "bar", "foo", "bar"],
                "C": [1, 5, 5, 2, 5, 5],
                "D": [2.0, 5.0, 8.0, 1.0, 2.0, 9.0],
            }
        ),
        # StringIndex
        pd.DataFrame(
            {
                "A": ["foo", "foo", "foo", "bar", "foo", "bar"],
                "C": [1, 5, 5, 2, 5, 5],
                "D": [2.0, 5.0, 8.0, 1.0, 2.0, 9.0],
            },
            pd.Index(["A", "BB", "ABC", "", "FF", "ABCDF"]),
        ),
    ],
)
@pytest.mark.parametrize(
    "func",
    [
        "sum",
        "min",
        "max",
        "count",
        "mean",
        "std",
        "first",
        "last",
        "prod",
        "var",
        "nunique",
        "median",
    ],
)
def test_groupby_transform(df, func, memory_leak_check):
    """Test groupby.transform"""

    def impl(df):
        A = df.groupby("A").transform(func)
        return A

    check_func(impl, (df,), check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_transform_count(memory_leak_check):
    """Test groupby().transform('count') with multiple datatypes"""

    def impl_count(df):
        A = df.groupby("A").transform("count")
        return A

    df = pd.DataFrame(
        {
            "A": ["foo", "foo", "foo", "bar", "foo", "bar"],
            "B": pd.Series(pd.timedelta_range(start="1 day", periods=6)),
            "C": [True, False, False, False, True, True],
            "D": ["foo", "foo", "foo", "bar", "foo", "bar"],
            "H": [b"foo", b"foo", b"foo", b"bar", b"foo", b"bar"],
            "E": [-8.3, np.nan, 3.8, 1.3, 5.4, np.nan],
            "G": pd.Series(np.array([np.nan, 8, 2, np.nan, np.nan, 20]), dtype="Int8"),
            "F": pd.Series(pd.array([1.1, 2.2, 3.3, None, 5.5, 6.6], dtype="Float64")),
        }
    )
    check_func(impl_count, (df,), check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_transform_nullable(memory_leak_check):
    """Test groupby().transform with nullable and string datatypes"""

    def impl_min(df):
        A = df.groupby("A").transform("min")
        return A

    def impl_max(df):
        A = df.groupby("A").transform("max")
        return A

    def impl_first(df):
        A = df.groupby("A").transform("first")
        return A

    def impl_last(df):
        A = df.groupby("A").transform("last")
        return A

    def impl_nunique(df):
        A = df.groupby("A")["D"].transform("nunique")
        return A

    def impl_sum(df):
        A = df.groupby("A").transform("sum")
        return A

    df = pd.DataFrame(
        {
            "A": ["foo", "asd", "foo", "bar", "foo", "bar", "asd", "xyz"],
            "D": ["fo", "foo", "test", "", "xfo", "xbar", "foo", "qwer"],
            "G": pd.Series(
                np.array([np.nan, 8, 2, np.nan, np.nan, 20, 30, -1]), dtype="Int8"
            ),
            "Q": [True, False, None, True] * 2,
        }
    )
    check_func(impl_min, (df,))
    check_func(impl_max, (df,))
    check_func(impl_first, (df,))
    check_func(impl_last, (df,))
    check_func(impl_nunique, (df,))
    # NOTE: Pandas 1.5 doesn't return sum output for non-numerics
    check_func(impl_sum, (df[["A", "G"]],), check_dtype=False)


@pytest_mark_pandas
@pytest.mark.slow
@pytest.mark.parametrize("dropna", [True, False])
def test_groupby_apply_na_key(dropna, memory_leak_check):
    """Test groupby.apply with NA keys"""

    def impl_apply(df):
        A = df.groupby("A", dropna=dropna).apply(
            lambda x: 3.3,
        )
        return A

    df = pd.DataFrame(
        {
            "A": pd.Series([np.nan, 1, 11, 1, 11, np.nan, np.nan, 3, 3], dtype="Int64"),
            "B": [2.2, 3.3, 4.4, 3.3, 3.3, 4.4, 5.5, 6.6, 6.6],
        }
    )
    check_func(impl_apply, (df,), sort_output=True, check_dtype=False, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
@pytest.mark.parametrize(
    "df",
    [
        # int
        pytest.param(
            pd.DataFrame(
                {
                    "A": [np.nan, 1, 11, 1, 11, np.nan, np.nan],
                    "B": [2.2, 3.3, 4.4, 3.3, 3.3, 4.4, 5.5],
                }
            ),
            id="integer",
        ),
        # float
        pytest.param(
            pd.DataFrame(
                {
                    "A": [np.nan, 1.1, 2.2, 1.1, 2.2, np.nan, np.nan],
                    "B": [2.2, 3.3, 4.4, 3.3, 3.3, 4.4, 5.5],
                }
            ),
            id="float",
        ),
        # nullable int
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Series(
                        [np.nan, 1, 11, 1, 11, np.nan, np.nan, 3, 3], dtype="Int64"
                    ),
                    "B": [2.2, 3.3, 4.4, 3.3, 3.3, 4.4, 5.5, 6.6, 6.6],
                }
            ),
            id="nullable_integer",
        ),
        # nullable float
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.Series(
                        [np.nan, 1.1, 2.2, 1.1, 2.2, np.nan, np.nan, 3.3, 3.3],
                        dtype="Float64",
                    ),
                    "B": [2.2, 3.3, 4.4, 3.3, 3.3, 4.4, 5.5, 6.6, 6.6],
                }
            ),
            id="nullable_float",
        ),
        # timedelta
        pytest.param(
            pd.DataFrame(
                {
                    "A": [
                        datetime.timedelta(3, 3, 3),
                        datetime.timedelta(2, 2, 2),
                        datetime.timedelta(1, 1, 1),
                        None,
                        datetime.timedelta(5, 5, 5),
                        None,
                        None,
                    ],
                    "B": [2.2, 3.3, 4.4, 3.3, 3.3, 4.4, 5.5],
                }
            ),
            id="timedelta",
        ),
        # datetime
        pytest.param(
            pd.DataFrame(
                {
                    "A": pd.concat(
                        [
                            pd.Series(
                                pd.date_range(
                                    start="2/1/2015", end="2/24/2016", periods=6
                                )
                            ),
                            pd.Series(data=[pd.NaT]),
                        ]
                    ),
                    "B": [2.2, 5.5, 5.5, 11.1, 12.2, 5.5, 2.2],
                }
            ),
            id="timestamp",
        ),
        # String
        pytest.param(
            pd.DataFrame(
                {
                    "A": ["CC", "aa", "b", None, "aa", None, "aa", "CC"],
                    "B": [10.2, 11.1, 1.1, 2.2, 2.2, 1.3, 3.4, 4.5],
                },
            ),
            id="string",
        ),
        # Binary
        pytest.param(
            pd.DataFrame(
                {
                    "A": [b"CC", b"aa", b"b", None, b"aa", None, b"aa", b"CC"],
                    "B": [10.2, 11.1, 1.1, 2.2, 2.2, 1.3, 3.4, 4.5],
                },
            ),
            id="binary",
        ),
        # Boolean
        pytest.param(
            pd.DataFrame(
                {
                    "A": [None, False, True, True, True],
                    "B": [1.0, 2.0, 2, 1, 3],
                },
            ),
            id="boolean",
        ),
        # String Repeat keys
        pytest.param(
            pd.DataFrame(
                {
                    "A": ["CC", "aa", "b", None] * 20,
                    "B": [10.2, 11.1, 1.1, 2.2] * 20,
                },
            ),
            id="string_repeats",
        ),
    ],
)
def test_groupby_na_key(df, memory_leak_check):
    """
    Test groupby(dropna=False)
    """
    if bodo.get_size() > 2 and set(df["A"]) == {None, True, False}:
        # This produces empty output on one rank with np3 so we skip to avoid
        # hangs
        return

    # CumOpColSet
    def impl_cumsum(df):
        A = df.groupby("A", dropna=False).cumsum()
        return A

    check_func(
        impl_cumsum, (df,), sort_output=True, check_dtype=False, reset_index=True
    )

    def impl_shift(df):
        A = df.groupby("A", dropna=False).shift(2)
        return A

    check_func(impl_shift, (df,), sort_output=True, check_dtype=False, reset_index=True)

    # TransformColSet
    def impl_transform(df):
        A = df.groupby("A", dropna=False).transform("sum")
        return A

    check_func(
        impl_transform, (df,), sort_output=True, check_dtype=False, reset_index=True
    )

    # UdfColSet
    def impl_agg(df):
        A = df.groupby("A", dropna=False).agg(lambda x: x.max() - x.min())
        return A

    check_func(impl_agg, (df,), sort_output=True, check_dtype=False, reset_index=True)

    # BasicColSet
    def impl_max(df):
        A = df.groupby("A", dropna=False).max()
        return A

    check_func(impl_max, (df,), sort_output=True, check_dtype=False, reset_index=True)

    # MedianColSet
    def impl_median(df):
        A = df.groupby("A", dropna=False).median()
        return A

    check_func(
        impl_median, (df,), sort_output=True, check_dtype=False, reset_index=True
    )

    # NUniqueColSet
    def impl_nunique(df):
        A = df.groupby("A", dropna=False).nunique()
        return A

    check_func(
        impl_nunique,
        (df,),
        sort_output=True,
        check_dtype=False,
        reset_index=True,
    )

    # MeanColSet
    def impl_mean(df):
        A = df.groupby("A", dropna=False).mean()
        return A

    check_func(impl_mean, (df,), sort_output=True, check_dtype=False, reset_index=True)

    # VarStdColSet
    def impl_std(df):
        A = df.groupby("A", dropna=False).std()
        return A

    check_func(impl_std, (df,), sort_output=True, check_dtype=False, reset_index=True)

    # IdxMinMaxColSet
    def impl_idxmin(df):
        A = df.groupby("A", dropna=False).idxmin()
        return A

    check_func(
        impl_idxmin, (df,), sort_output=True, check_dtype=False, reset_index=True
    )


def test_head(memory_leak_check):
    """
    Test Groupby.head
    Supports all types since it doesn't care about type of column
    """

    def impl1(df):
        A = df.groupby("A").head(2)
        return A

    def impl2(df):
        A = df.groupby(["A", "B"]).head(2)
        return A

    def impl3(df):
        A = df.groupby(["G", "I", "H"])[["C", "F", "G", "A"]].head(1)
        return A

    def impl4(df):
        # Check head uses the default arg of 5.
        A = df.groupby("A").head()
        return A

    df = pd.DataFrame(
        {
            "A": [2, 2, 1, 2, 2, 1, 1],
            "B": pd.Series(
                [
                    Decimal("1.6"),
                    None,
                    Decimal("1.6"),
                    Decimal("44.2"),
                    Decimal("1.6"),
                    Decimal("4.3"),
                    Decimal("0"),
                ]
            ),
            "C": pd.date_range(start="2018-04-24", end="2018-04-29", periods=7),
            "D": pd.Series(pd.timedelta_range(start="1 day", periods=7)),
            "F": [
                pd.Timestamp("20130101 09:00:00"),
                pd.Timestamp("20130101 09:00:02"),
                pd.Timestamp("20130101 09:00:03"),
                pd.Timestamp("20130101 09:00:05"),
                pd.Timestamp("20130101 09:00:06"),
                pd.Timestamp("20130101 09:10:06"),
                pd.Timestamp("20130101 19:10:06"),
            ],
            "G": ["ab", "ab", "ef", "ab", "mm", "ef", "mm"],
            "H": pd.Series([1, 1, np.nan, 1, 2, np.nan, 2], dtype="Int64"),
            "I": [True, True, False, True, True, False, False],
            "J": pd.array([True, False, None, True, True, False, True]),
            "K": [b"ab", b"cd", None, b"ef", b"mm", b"", b"xxx"],
            "L": pd.array(
                [float(i) if i % 2 else None for i in range(7)], dtype="float64"
            ),
        }
    )
    check_func(impl1, (df,))
    check_func(impl2, (df,))
    check_func(impl3, (df,))
    check_func(impl4, (df,))

    df_empty = pd.DataFrame({"A": [], "B": []})
    check_func(impl1, (df_empty,))


@pytest_mark_pandas
@pytest.mark.slow
def test_head_cat(memory_leak_check):
    """
    Test Groupby.head with categorical column.
    This is in its own test since it does not pass memory_leak_check.
    """

    def impl1(df):
        A = df.groupby("A").head(1)
        return A

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 2, 2],
            "E": pd.Categorical([1, 2, 5, 5, 3], ordered=True),
        }
    )
    check_func(impl1, (df,))


@pytest_mark_pandas
@pytest.mark.slow
def test_head_idx(datapath, memory_leak_check):
    """
    Test Groupby.head with index explicitly set.
    """

    filename = datapath("example.csv")

    def impl1():
        df = pd.read_csv(filename, index_col="two")
        A = df.groupby("one").head(1)
        return A

    check_func(impl1, ())


@pytest_mark_pandas
def test_series_reset_index(memory_leak_check):
    """
    [BE-2800] Test that Series.reset_index() handles
    MultiIndex types properly when drop=False.
    """

    def impl(df):
        b = df.groupby(["A", "C"])["B"].agg("min")
        b = b.reset_index()
        return b

    df = pd.DataFrame(
        {
            "A": [1, 1, 2, 2, 3, 3],
            "B": [1, 2, 3, 4, 5, 6],
            "C": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        }
    )
    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_groupby_asindex_no_values(memory_leak_check):
    """
    Test for BE-434. Verifies that groupby(as_index=False)
    works when there aren't any output columns.
    """

    def test_impl(df):
        return df.groupby("A", as_index=False).min()

    df_empty = pd.DataFrame({"A": [2, 1, 1, 1, 2, 2, 1]})

    # Specify sort_output because the ordering may not match.
    check_func(test_impl, (df_empty,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_groupby_agg_list_builtin(memory_leak_check):
    """
    [BE-2764] Tests support for groupby.agg with
    a constant list of builtin functions.
    """

    def impl(df):
        b = df.groupby(["A", "C"])["B"].agg(["min", "sum", "nunique", "last"])
        return b

    df = pd.DataFrame(
        {
            "A": [1, 1, 2, 2, 3, 3] * 2,
            "B": [1, 2, 3, 4, 5, 6] * 2,
            "C": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6] * 2,
        }
    )

    df_str = pd.DataFrame(
        {
            "A": ["aa", "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", "ff", "bb", "rr", "bb", "ggg", "aa"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
        }
    )
    # Specify sort_output because the ordering may not match.
    check_func(impl, (df,), sort_output=True, reset_index=True)
    check_func(impl, (df_str,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_groupby_agg_list_lambda(memory_leak_check):
    """
    [BE-2764] Tests support for groupby.agg with
    a constant list of lambda functions.
    """

    def impl(df):
        b = df.groupby(["A", "C"])["B"].agg([lambda x: x.max() - x.min()])
        return b

    df = pd.DataFrame(
        {
            "A": [1, 1, 2, 2, 3, 3] * 2,
            "B": [1, 2, 3, 4, 5, 6] * 2,
            "C": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6] * 2,
        }
    )
    # Specify sort_output because the ordering may not match.
    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_agg_set(memory_leak_check):
    """
    [BE-327] Test Groupby.agg() with constant set input
    """

    def impl(df):
        b = df.groupby(["A", "C"])["B"].agg({"max"})
        return b

    df = pd.DataFrame(
        {
            "A": [1, 1, 2, 2, 3, 3] * 2,
            "B": [1, 2, 3, 4, 5, 6] * 2,
            "C": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6] * 2,
        }
    )

    # Specify sort_output because the ordering may not match.
    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
@pytest.mark.slow
def test_groupby_ngroup(memory_leak_check):
    """Test groupby.ngroup()

    Args:
        memory_leak_check (fixture function): check memory leak in the test.

    """

    # basic case
    def impl1(df):
        result = df.groupby("A").ngroup()
        return result

    # basic case key is string
    def impl2(df):
        result = df.groupby("B").ngroup()
        return result

    # explicit select
    def impl3(df):
        result = df.groupby("A")["C"].ngroup()
        return result

    # multi-key
    def impl4(df):
        result = df.groupby(["A", "B"]).ngroup()
        return result

    # as_index=False
    def impl5(df):
        result = df.groupby("A", as_index=False).ngroup()
        return result

    df = pd.DataFrame(
        {
            "A": [1, 3, 2, 1, 2, 3],
            "B": ["AA", "B", "XXX", "AA", "XXX", "B"],
            "C": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        }
    )
    check_func(impl1, (df,), sort_output=True, reset_index=True)
    check_func(impl2, (df,), sort_output=True, reset_index=True)
    check_func(impl3, (df,), sort_output=True, reset_index=True)
    check_func(impl4, (df,), sort_output=True, reset_index=True)
    check_func(impl5, (df,), sort_output=True, reset_index=True)

    # Example to show case when index won't be numericIndex.
    df.index = ["a", "b", "c", "d", "e", "f"]
    check_func(impl1, (df,), sort_output=True, reset_index=True)


@pytest_mark_pandas
def test_groupby_num_shuffle_keys(memory_leak_check):
    """
    Tests the Bodo optional argument, _bodo_num_shuffle_keys
    produces a correct output. This argument shuffles on a
    subset of the groupby keys when manually setting certain
    operations as being similarly shuffled.

    The shuffling being consistent is not tested here. That is
    tested by the pivot_table tests as we skip a shuffle that
    would otherwise be necessary.
    """

    def impl1(df):
        return df.groupby(["A", "B"], _bodo_num_shuffle_keys=1)["C"].sum()

    def impl2(df):
        return df.groupby(["A", "B"], _bodo_num_shuffle_keys=1)["C"].agg("sum")

    def impl3(df):
        # _bodo_num_shuffle_keys should be ignored
        return df.groupby(["A", "B"], _bodo_num_shuffle_keys=1)["C"].cumsum()

    def impl4(df):
        # _bodo_num_shuffle_keys should be ignored
        return df.groupby(["A", "B"], _bodo_num_shuffle_keys=1)["C"].nunique()

    df1 = pd.DataFrame(
        {
            "A": [1, 2, 3] * 5,
            "B": [1, 2, 3, 4, 5] * 3,
            "C": np.arange(15),
        }
    )
    df2 = pd.DataFrame(
        {
            # If we only shuffle on A instead of (A, B),
            # then all of the data should be gathered on a single
            # rank. We test with this DataFrame to confirm that
            # even if some ranks are empty it produces the
            # correct output.
            "A": [1] * 15,
            "B": [1, 2, 3, 4, 5] * 3,
            "C": np.arange(15),
        }
    )
    check_func(
        impl1,
        (df1,),
        sort_output=True,
        reset_index=True,
        py_output=df1.groupby(["A", "B"])["C"].sum(),
    )
    check_func(
        impl1,
        (df2,),
        sort_output=True,
        reset_index=True,
        py_output=df2.groupby(["A", "B"])["C"].sum(),
    )
    check_func(
        impl2,
        (df1,),
        sort_output=True,
        reset_index=True,
        py_output=df1.groupby(["A", "B"])["C"].agg("sum"),
    )
    check_func(
        impl2,
        (df2,),
        sort_output=True,
        reset_index=True,
        py_output=df2.groupby(["A", "B"])["C"].agg("sum"),
    )
    check_func(
        impl3,
        (df1,),
        py_output=df1.groupby(["A", "B"])["C"].cumsum(),
    )
    check_func(
        impl3,
        (df2,),
        py_output=df2.groupby(["A", "B"])["C"].cumsum(),
    )
    check_func(
        impl4,
        (df1,),
        sort_output=True,
        reset_index=True,
        py_output=df1.groupby(["A", "B"])["C"].nunique(),
    )
    check_func(
        impl4,
        (df2,),
        sort_output=True,
        reset_index=True,
        py_output=df2.groupby(["A", "B"])["C"].nunique(),
    )


@pytest.mark.parametrize(
    "t_val, f_val, dtype",
    [
        pytest.param(-1, 0, pd.Int8Dtype(), id="int8"),
        pytest.param(5, 0, pd.UInt8Dtype(), id="uint8", marks=pytest.mark.slow),
        pytest.param(10, 0, pd.Int16Dtype(), id="int16", marks=pytest.mark.slow),
        pytest.param(255, 0, pd.UInt16Dtype(), id="uint16", marks=pytest.mark.slow),
        pytest.param(1024, 0, pd.Int32Dtype(), id="int32", marks=pytest.mark.slow),
        pytest.param(1, 0, pd.UInt32Dtype(), id="uint32", marks=pytest.mark.slow),
        pytest.param(-2048, 0, pd.Int64Dtype(), id="int64", marks=pytest.mark.slow),
        pytest.param(64, 0, pd.UInt64Dtype(), id="uint64"),
        pytest.param(True, False, pd.BooleanDtype(), id="bool"),
        pytest.param(3.14, 0.0, np.float32, id="float32"),
        pytest.param(-2.71828, 0.0, np.float64, id="float64"),
        pytest.param(Decimal("0.05"), Decimal("0.0"), None, id="decimal"),
    ],
)
def test_boolor_booland_boolxor_agg(t_val, f_val, dtype, memory_leak_check):
    """Tests calling a groupby with boolor_agg/booland_agg/boolxor_agg, functions
    used by BodoSQL and not part or regular pandas, on all possible datatypes."""

    def impl(df):
        # Note we choose all of these flag + code format because
        # these are the generated SQL flags
        return df.groupby(["key"], as_index=False, dropna=False).agg(
            or_out=pd.NamedAgg(column="data", aggfunc="boolor_agg"),
            and_out=pd.NamedAgg(column="data", aggfunc="booland_agg"),
            xor_out=pd.NamedAgg(column="data", aggfunc="boolxor_agg"),
        )

    scale = 5

    # For boolxor_agg, groups where exactly one value is t_val and the rest are
    # either f_val or None should be True, the rest should be False, or NULL
    # if the entire group is all-null. For booland_agg, all the values must
    # be true. For boolor_agg, at least one value must be true.
    original_keys = [
        "0/3",
        "1/3",
        "2/3",
        "3/3",
        "1/1",
        "2/2",
        "0/0",
        "0/1",
        "0/2",
        "1/2",
    ]
    group_keys = []
    for i in range(scale):
        for key in original_keys:
            group_keys.append(key + chr(i + 65))
    groups = pd.Series(group_keys).repeat(3).values
    df = pd.DataFrame(
        {
            "key": groups,
            "data": pd.Series(
                (
                    [f_val, f_val, f_val]
                    + [f_val, t_val, f_val]
                    + [t_val, f_val, t_val]
                    + [t_val, t_val, t_val]
                    + [None, None, t_val]
                    + [None, t_val, t_val]
                    + [None, None, None]
                    + [f_val, None, None]
                    + [None, f_val, f_val]
                    + [t_val, f_val, None]
                )
                * scale,
                dtype=dtype,
            ),
        }
    )
    expected_output = pd.DataFrame(
        {
            "key": pd.Series(group_keys),
            "or_out": pd.Series(
                [False, True, True, True, True, True, None, False, False, True] * scale,
                dtype="boolean",
            ),
            "and_out": pd.Series(
                [False, False, False, True, True, True, None, False, False, False]
                * scale,
                dtype="boolean",
            ),
            "xor_out": pd.Series(
                [False, True, False, False, True, False, None, False, False, True]
                * scale,
                dtype="boolean",
            ),
        }
    )

    check_func(
        impl,
        (df,),
        sort_output=True,
        reset_index=True,
        py_output=expected_output,
        check_names=False,
    )


@pytest.mark.parametrize(
    "data, dtype",
    [
        pytest.param(
            [42, 100, 60, 5, 15, 70, 3, 213, 6, None], pd.Int32Dtype(), id="int32"
        ),
        pytest.param(
            [41.5, 100.4, 60.0, 5.0, 15.21, 69.53, 3.2, 213.1, 5.8, None],
            pd.Float32Dtype(),
            id="floats",
        ),
        pytest.param(
            ["41.5", "100", "60", "5", "15", "69.53", "3.2", "213.1", "5.8", None],
            pd.StringDtype(),
            id="strings",
        ),
    ],
)
def test_bit_agg(data, dtype, memory_leak_check):
    """Tests the BITOR_AGG, BITAND_AGG, and BITXOR_AGG aggregation functions
        with groupby.

    Args:
        data (pd.Series): Input column
        dtype (int): Dtype of the input column (data)
        memory_leak_check (): Fixture, see `conftest.py`.
    """

    def impl(df):
        # Note this flag + code format is chosen because
        # these are the generated SQL flags
        return df.groupby(["key"], as_index=False, dropna=False).agg(
            or_out=pd.NamedAgg(column="data", aggfunc="bitor_agg"),
            and_out=pd.NamedAgg(column="data", aggfunc="bitand_agg"),
            xor_out=pd.NamedAgg(column="data", aggfunc="bitxor_agg"),
        )

    df = pd.DataFrame(
        {
            "key": pd.Series([1, 1, 1, 2, 2, 2, 3, 3, 3, 4]),
            "data": pd.Series(data, dtype=dtype),
        }
    )

    expected = pd.DataFrame(
        {
            "key": pd.Series([1, 2, 3, 4]),
            "or_out": pd.Series([126, 79, 215, None], dtype=pd.Int32Dtype()),
            "and_out": pd.Series([32, 4, 0, None], dtype=pd.Int32Dtype()),
            "xor_out": pd.Series([114, 76, 208, None], dtype=pd.Int32Dtype()),
        }
    )

    check_func(
        impl,
        (df,),
        sort_output=True,
        reset_index=True,
        py_output=expected,
        check_names=False,
        check_dtype=False,  # Output type depends on input type.
    )


@pytest.mark.parametrize(
    "data_col",
    [
        # Note string/binary is not supported inside Snowflake
        # https://docs.snowflake.com/en/sql-reference/functions/boolor_agg.html#usage-notes
        pd.Series(["afde", "Rewr"] * 6),
        # Bytes is unsupported
        pd.Series([b"afde", b"Rewr"] * 6),
        # Also the types require the ability to cast to boolean. Therefore it doesn't support
        # date (try in snowflake: select date_from_parts(2000, 1, 1)::boolean)
        pd.Series([datetime.date(2022, 10, 10), datetime.date(2022, 11, 10)] * 6),
        # time (try in snowflake: select time_from_parts(12, 55, 55)::boolean)
        pd.Series(
            [bodo.Time(12, 34, 56, precision=0), bodo.Time(12, 46, 56, precision=0)] * 6
        ),
        # timestamp (try in snowflake: select timestamp_from_parts(2000, 1, 1, 1, 1, 1)::boolean)
        pd.Series([pd.Timestamp(2022, 10, 10), pd.Timestamp(2022, 11, 10)] * 6),
        # timedelta isn't a proper type inside snowflake
        pd.Series([pd.Timedelta(1), pd.Timedelta(2)] * 6),
        # Categorical string
        pd.Series(pd.Categorical(["afde", "Rewr"] * 6)),
    ],
)
def test_boolagg_or_invalid(data_col, memory_leak_check):
    """Tests calling a groupby with boolagg_or, a function used by
    BodoSQL and not part or regular pandas, on unsupported datatypes."""

    @bodo.jit
    def impl(df):
        # Note we choose all of these flag + code format because
        # these are the generated SQL flags
        return df.groupby(["key"], as_index=False, dropna=False).agg(
            data_out=pd.NamedAgg(column="data", aggfunc="boolor_agg"),
        )

    groups = pd.Series([1, 2, 3, 4, 5, 6] * 2)
    df = pd.DataFrame(
        {
            "key": groups,
            "data": data_col,
        }
    )
    with pytest.raises(
        BodoError,
        match="boolor_agg, only columns of type integer, float, Decimal, or boolean type are allowed",
    ):
        impl(df)


@pytest.mark.tz_aware
def test_tz_aware_gb_apply(memory_leak_check):
    """
    Tests using groupby.apply with a tz-aware column as a data column on a
    supported operation.
    """

    def udf(data_df):
        # Sort by the tz-aware column
        new_df = data_df.sort_values(
            by=[
                "B",
            ],
            ascending=[
                True,
            ],
        )
        # Check the order
        return new_df["C"].iat[0]

    def impl(df):
        return df.groupby("A").apply(udf)

    df = pd.DataFrame(
        {
            "A": ["A", "B", "C", "D"] * 5,
            "B": pd.date_range(
                start="1/1/2022",
                freq="16D5H",
                periods=20,
                tz="Poland",
            ).to_series(),
            "C": list("abcdefgABCDEFGhijkML"),
        }
    )

    check_func(
        impl,
        (df,),
        sort_output=True,
        reset_index=True,
    )


@pytest.mark.parametrize(
    "df",
    [
        pytest.param(
            pd.DataFrame(
                {
                    "A": [
                        1,
                        2,
                        1,
                        0,
                        2,
                        1,
                        2,
                        2,
                    ],
                    "B": np.array(
                        [[[1, 2], [3]], [[None], [4]], [[5], [6]], None] * 2, object
                    ),
                    "C": np.array([[1, 2], [3], [4, 5, 6], [0]] * 2, object),
                    "D": pd.array([1, 2, 3, 4] * 2),
                    "E": ["xyz", "xyz", "wxy", "wxy"] * 2,
                    "F": pd.array(
                        [
                            [],
                            None,
                            ["A"],
                            ["A", None, "B"],
                            ["A"],
                            ["X", None, "Y"],
                            None,
                            [],
                        ],
                        dtype=pd.ArrowDtype(pa.large_list(pa.string())),
                    ),
                }
            ),
            id="array",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [
                        1,
                        2,
                        1,
                        0,
                        2,
                        1,
                        2,
                        2,
                    ],
                    "B": pd.array(
                        [
                            {
                                "X": "AB",
                                "Y": [1.1, 2.2],
                                "Z": [[1], None, [3, None]],
                                "W": {"A": 1, "B": "A"},
                                "Q": ["A"],
                            },
                            {
                                "X": "C",
                                "Y": [1.1],
                                "Z": [[11], None],
                                "W": {"A": 1, "B": "ABC"},
                                "Q": None,
                            },
                            None,
                            {
                                "X": "D",
                                "Y": [4.0, 6.0],
                                "Z": [[1], None],
                                "W": {"A": 1, "B": ""},
                                "Q": ["AE", "IOU", None],
                            },
                            {
                                "X": "VFD",
                                "Y": [1.2],
                                "Z": [[], [3, 1]],
                                "W": {"A": 1, "B": "AA"},
                                "Q": ["Y"],
                            },
                            {
                                "X": "LMMM",
                                "Y": [9.0, 1.2, 3.1],
                                "Z": [[10, 11], [11, 0, -3, -5]],
                                "W": {"A": 1, "B": "DFG"},
                                "Q": [],
                            },
                            {
                                "X": "LMMM",
                                "Y": [9.0, 1.2, 3.1],
                                "Z": [[10, 11], [11, 0, -3, -5]],
                                "W": {"A": 1, "B": "DFG"},
                                "Q": ["X", None, "Z"],
                            },
                            None,
                        ],
                        dtype=pd.ArrowDtype(
                            pa.struct(
                                [
                                    pa.field("X", pa.string()),
                                    pa.field("Y", pa.large_list(pa.float64())),
                                    pa.field(
                                        "Z", pa.large_list(pa.large_list(pa.int64()))
                                    ),
                                    pa.field(
                                        "W",
                                        pa.struct(
                                            [
                                                pa.field("A", pa.int8()),
                                                pa.field("B", pa.string()),
                                            ]
                                        ),
                                    ),
                                    pa.field("Q", pa.large_list(pa.string())),
                                ]
                            )
                        ),
                    ),
                }
            ),
            id="struct",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [
                        1,
                        2,
                        1,
                        0,
                        2,
                        1,
                        2,
                        2,
                    ],
                    "B": pd.Series(
                        [
                            {1: 1.4, 2: 3.1},
                            None,
                            {},
                            {11: 3.4, 21: 3.1, 9: 8.1},
                        ]
                        * 2,
                        dtype=pd.ArrowDtype(pa.map_(pa.int64(), pa.float64())),
                    ),
                    "C": pd.Series(
                        [
                            {1: [], 2: None},
                            None,
                            {},
                            {11: ["A"], 21: ["B", None], 9: ["C"]},
                        ]
                        * 2,
                        dtype=pd.ArrowDtype(
                            pa.map_(pa.int64(), pa.large_list(pa.string()))
                        ),
                    ),
                }
            ),
            id="map",
        ),
        pytest.param(
            pd.DataFrame(
                {
                    "A": [
                        1,
                        2,
                        1,
                        0,
                        2,
                        1,
                        2,
                        2,
                    ],
                    "B": [(1, 1.1), (2, 2.2), None, (4, 4.4)] * 2,
                }
            ),
            id="tuple",
            marks=pytest.mark.skip(
                "[BSE-2076] TODO: Support tuple array in Arrow boxing/unboxing"
            ),
        ),
    ],
)
@pytest.mark.parametrize(
    "fstr",
    [
        pytest.param("first", id="first"),
        pytest.param("count", id="count"),
    ],
)
def test_nested_array_data(df, memory_leak_check, fstr):
    """
    Test using groupby.apply on a column with nested array data.
    """

    def impl(df, fstr):
        return df.groupby("A", dropna=False, as_index=False).agg(fstr)

    expected_df = df.groupby("A", dropna=False, as_index=False).agg(fstr)

    check_func(
        impl,
        (df, fstr),
        py_output=expected_df,
        convert_columns_to_pandas=True,
        sort_output=True,
        reset_index=True,
    )


def test_reverse_shuffle_timestamp_tz(memory_leak_check):
    """Test reverse shuffle used in groupby/apply for TimestampTZ datatype"""

    def impl(df):
        return df.groupby("A").apply(lambda x: pd.DataFrame({"C": x["B"]}))

    tz_arr = np.array(
        [
            bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 100),
            bodo.TimestampTZ.fromUTC("2022-12-31 12:59:59", 200),
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 300),
            None,
            bodo.TimestampTZ.fromUTC("2022-12-31 12:59:59", 200),
        ]
    )
    df = pd.DataFrame({"A": [1, 3, 1, 4, 0], "B": tz_arr})
    check_func(impl, (df,), sort_output=True, reset_index=True, check_dtype=False)


def test_timestamptz_gb_key(memory_leak_check):
    """Tests groupby where key is timestamptz
    as_index=False to avoid timestamptz as an index
    dropna=False Snowflake Keeps Null as Keys
    """

    def impl(df):
        return df.groupby("B", as_index=False, dropna=False).sum()

    tz_arr = np.array(
        [
            bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
            None,
            bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 300),
            None,
        ]
        * 5
    )
    df = pd.DataFrame(
        {
            "A": ["A", "B", "C", "D"] * 5,
            "B": tz_arr,
            "C": list("abcdefgABCDEFGhijkML"),
        }
    )
    check_func(impl, (df,), sort_output=True, reset_index=True)


@pytest.mark.parametrize(
    "fstr, expected",
    [
        pytest.param(
            "count",
            pd.DataFrame({"A": ["A", "B", "C", "D"], "B": [6, 3, 6, 0]}),
            id="count",
        ),
        pytest.param(
            "size",
            pd.DataFrame({"A": ["A", "B", "C", "D"], "size": [6, 6, 6, 6]}),
            id="size",
        ),
        pytest.param(
            "first",
            pd.DataFrame(
                {
                    "A": ["A", "B", "C", "D"],
                    "B": [
                        bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
                        bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
                        bodo.TimestampTZ.fromUTC("2024-01-01 01:00:00", 0),
                        None,
                    ],
                }
            ),
            id="first",
        ),
        pytest.param(
            "last",
            pd.DataFrame(
                {
                    "A": ["A", "B", "C", "D"],
                    "B": [
                        bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 0),
                        bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
                        bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
                        None,
                    ],
                }
            ),
            id="last",
        ),
        pytest.param(
            "min",
            pd.DataFrame(
                {
                    "A": ["A", "B", "C", "D"],
                    "B": [
                        bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
                        bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
                        bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
                        None,
                    ],
                }
            ),
            id="min",
        ),
        pytest.param(
            "max",
            pd.DataFrame(
                {
                    "A": ["A", "B", "C", "D"],
                    "B": [
                        bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
                        bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
                        bodo.TimestampTZ.fromUTC("2024-01-01 01:00:00", 0),
                        None,
                    ],
                }
            ),
            id="max",
        ),
    ],
)
def test_timestamptz_gb_agg(fstr, expected, memory_leak_check):
    """Tests groupby with timestamptz column and aggregation"""

    def impl(df):
        return df.groupby("A", as_index=False, dropna=False).agg(fstr)

    # groups A and C always have values, B has some nulls, D has all nulls
    tz_arr = np.array(
        [
            bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
            None,
            bodo.TimestampTZ.fromUTC("2024-01-01 01:00:00", 0),
            None,
            bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 0),
            bodo.TimestampTZ.fromUTC("2021-01-02 03:04:05", 400),
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
            None,
        ]
        * 3
    )
    df = pd.DataFrame(
        {
            "A": ["A", "B", "C", "D"] * 6,
            "B": tz_arr,
        }
    )
    check_func(impl, (df,), py_output=expected, sort_output=True, reset_index=True)


def test_timestamptz_gb_mode(memory_leak_check):
    """Tests groupby + mode with timestamptz column"""

    # This isn't tested with the other functions above to control the values
    # per group better - if we have more tests like this, we should combine the
    # tests and make it more generic
    def impl(df):
        return df.groupby("A", as_index=False, dropna=False).agg("mode")

    gb_key = ["A"] * 4 + ["B"] * 4 + ["C"] * 4 + ["D"] * 4 + ["E"] * 4 + ["F"] * 4

    # groups A and C always have values, B has some nulls, D has all nulls
    tz_arr = np.array(
        [
            # A - all the same UTC time, but different offsets
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 0),
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 125),
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 333),
            # B - all the same local time, but different UTC times
            bodo.TimestampTZ.fromLocal("2024-01-01 00:00:00", 0),
            bodo.TimestampTZ.fromLocal("2024-01-01 00:00:00", 60),
            bodo.TimestampTZ.fromLocal("2024-01-01 00:00:00", 125),
            bodo.TimestampTZ.fromLocal("2024-01-01 00:00:00", 333),
            # C - value is the mode
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 120),
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 240),
            bodo.TimestampTZ.fromUTC("2024-01-01 01:01:01", 0),
            # D - all null
            None,
            None,
            None,
            None,
            # E - some nulls
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
            None,
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
            # F - majority null
            None,
            None,
            bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
            None,
        ]
    )
    df = pd.DataFrame({"A": gb_key, "B": tz_arr})

    expected = pd.DataFrame(
        {
            "A": ["A", "B", "C", "D", "E", "F"],
            "B": np.array(
                [
                    bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 0),
                    bodo.TimestampTZ.fromLocal("2024-01-01 00:00:00", 0),
                    bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
                    None,
                    bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
                    bodo.TimestampTZ.fromUTC("2024-01-01 00:00:00", 60),
                ]
            ),
        }
    )
    check_func(impl, (df,), py_output=expected, sort_output=True, reset_index=True)


@pytest.mark.slow
def test_many_same_type_keys(memory_leak_check):
    """
    Test the case where we have many keys of the same type. This is used
    for testing a specially templated case
    (see GROUPBY_INFO_IMPL_ALL_SAME_KEY_TYPES in _groupby_groups.cpp).
    """

    def impl(df):
        return df.groupby(["A", "B", "C", "D", "E"], as_index=False, dropna=False)[
            "F"
        ].sum()

    df = pd.DataFrame(
        {
            "A": pd.Series([0, 1, 4, 5, 9] * 10, dtype="Int64"),
            "B": pd.Series(list(np.arange(10, 20)) * 5, dtype="Int64"),
            "C": pd.Series(list(np.arange(20, 45)) * 2, dtype="Int64"),
            "D": pd.Series(list(np.arange(0, 5)) * 10, dtype="Int64"),
            "E": pd.Series(list(np.arange(90, 140)), dtype="Int64"),
            "F": pd.Series(list(np.arange(10, 20)) * 5, dtype="Int64"),
        }
    )
    check_func(impl, (df,), sort_output=True, reset_index=True, check_dtype=False)


def test_mixed_semi_structured_and_regular_keys(memory_leak_check):
    """
    Test the case where we have a mix of semi-structured and regular keys.
    """

    # This isn't tested with the other functions above to control the values
    # per group better - if we have more tests like this, we should combine the
    # tests and make it more generic
    def impl(df):
        return df.groupby(["A", "B"], as_index=False, dropna=False).agg("sum")

    df = pd.DataFrame(
        {
            "A": ["1", "1", "2", "2", "4", "4"],
            "B": pd.array([["1"], ["1"], ["2"], ["3"], ["4"], ["4"]]),
            "C": [1, 2, 3, 4, 5, 5],
        }
    )

    expected = pd.DataFrame(
        {
            "A": ["1", "2", "2", "4"],
            "B": pd.array([["1"], ["2"], ["3"], ["4"]]),
            "C": [3, 3, 4, 10],
        }
    )
    # use_dict_encoded_strings must be set to false or hash_arrow_array will
    # panic as the underlying string array will unexpectedly be dict encoded.
    check_func(
        impl,
        (df,),
        py_output=expected,
        sort_output=True,
        reset_index=True,
        use_dict_encoded_strings=False,
    )
