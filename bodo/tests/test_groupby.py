# Copyright (C) 2019 Bodo Inc. All rights reserved.
import datetime
import random
import re
import string
import sys
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

import bodo
from bodo.tests.utils import (
    check_caching,
    check_func,
    check_parallel_coherency,
    convert_non_pandas_columns,
    count_array_OneDs,
    count_array_REPs,
    count_parfor_OneDs,
    count_parfor_REPs,
    dist_IR_contains,
    gen_random_decimal_array,
    gen_random_list_string_array,
    gen_random_string_array,
    get_start_end,
)
from bodo.utils.typing import BodoError


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
                    "A": [2.1, -1.5, 0.0, -1.5, 2.1, 2.1, 1.5],
                    "B": [-8.3, np.nan, 3.8, 1.3, 5.4, np.nan, -7.0],
                    "C": [3.4, 2.5, 9.6, 1.5, -4.3, 4.3, -3.7],
                }
            ),
            marks=pytest.mark.slow,
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
            "A": [2, 1, 1, 1, 2, 2, 1],
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
    ---
    The agg(("cumsum", "cumprod")) is disabled at the time being because of a pandas bug:
    https://github.com/pandas-dev/pandas/issues/35490
    """
    assert re.compile(r"1.1.*").match(
        pd.__version__
    ), "revisit the agg((cumsum, cumprod)) at next pandas version"

    def impl1(df):
        df2 = df.groupby("A")["B"].agg(("cumsum", "cumprod"))
        return df2

    def impl2(df):
        df2 = df.groupby("A")["B"].cumsum()
        return df2

    #    check_func(impl1, (df_null,), sort_output=True, check_dtype=False)
    check_func(impl2, (df_null,), sort_output=True, check_dtype=False)


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
        "B": [-8, 2, 3, 1, 5, 6, 7],
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


@pytest.mark.slow
def test_sum_string(memory_leak_check):
    def impl(df):
        A = df.groupby("A").sum()
        return A

    df1 = pd.DataFrame(
        {
            "A": [1, 1, 1, 2, 3, 3, 4, 0, 5, 0, 11],
            "B": ["a", "b", "c", "d", "", "AA", "ABC", "AB", "c", "F", "GG"],
        }
    )
    check_func(impl, (df1,), sort_output=True)


def test_random_decimal_sum_min_max_last(is_slow_run, memory_leak_check):
    """We do not have decimal as index. Therefore we have to use as_index=False"""

    def impl1(df):
        df_ret = df.groupby("A", as_index=False).nunique()
        return df_ret["B"].copy()

    def impl2(df):
        A = df.groupby("A", as_index=False).last()
        return A

    def impl3(df):
        A = df.groupby("A", as_index=False)["B"].first()
        return A

    def impl4(df):
        A = df.groupby("A", as_index=False)["B"].count()
        return A

    def impl5(df):
        A = df.groupby("A", as_index=False).max()
        return A

    def impl6(df):
        A = df.groupby("A", as_index=False).min()
        return A

    def impl7(df):
        A = df.groupby("A", as_index=False)["B"].mean()
        return A

    def impl8(df):
        A = df.groupby("A", as_index=False)["B"].median()
        return A

    def impl9(df):
        A = df.groupby("A", as_index=False)["B"].var()
        return A

    # We need to drop column A because the column A is replaced by std(A)
    # in pandas due to a pandas bug.
    def impl10(df):
        A = df.groupby("A", as_index=False)["B"].std()
        return A.drop(columns="A")

    random.seed(5)
    n = 10
    df1 = pd.DataFrame(
        {
            "A": gen_random_decimal_array(1, n),
            "B": gen_random_decimal_array(2, n),
        }
    )

    # Direct checks for which pandas has equivalent functions.
    check_func(impl1, (df1,), sort_output=True, reset_index=True)
    if not is_slow_run:
        return
    check_func(impl2, (df1,), sort_output=True, reset_index=True)
    check_func(impl3, (df1,), sort_output=True, reset_index=True)
    check_func(impl4, (df1,), sort_output=True, reset_index=True)
    check_func(impl5, (df1,), sort_output=True, reset_index=True)
    check_func(impl6, (df1,), sort_output=True, reset_index=True)

    # For mean/median/var/std we need to map the types.
    check_func(
        impl7,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    check_func(
        impl8,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    check_func(
        impl9,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )
    check_func(
        impl10,
        (df1,),
        sort_output=True,
        reset_index=True,
        convert_columns_to_pandas=True,
    )


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
        for i in range(n):
            len_str = random.randint(1, 10)
            val_A = random.randint(1, 10)
            val_B = "".join(random.choices(string.ascii_uppercase, k=len_str))
            eList_A.append(val_A)
            eList_B.append(val_B)
        return pd.DataFrame({"A": eList_A, "B": eList_B})

    df1 = random_dataframe(100)
    check_func(impl1, (df1,), sort_output=True)
    check_func(impl2, (df1,), sort_output=True)
    check_func(impl3, (df1,), sort_output=True)
    check_func(impl4, (df1,), sort_output=True)
    check_func(impl5, (df1,), sort_output=True)


def test_groupby_missing_entry(is_slow_run, memory_leak_check):
    """The columns which cannot be processed cause special problems as they are
    sometimes dropped instead of failing
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
            "B": ["aa", "bb", "cc", "", "L"] * 3,
            "C": [3, 1, 2, 0, -3] * 3,
        }
    )
    df3 = pd.DataFrame(
        {"A": [3, 2, 3, 1, 11] * 3, "B": ["aa", "bb", "cc", "", "AA"] * 3}
    )
    check_func(test_drop_sum, (df1,), sort_output=True, check_typing_issues=False)
    if not is_slow_run:
        return
    check_func(test_drop_sum, (df2,), sort_output=True, check_typing_issues=False)
    check_func(test_drop_sum, (df3,), sort_output=True, check_typing_issues=False)
    check_func(test_drop_count, (df1,), sort_output=True, check_typing_issues=False)
    check_func(test_drop_count, (df2,), sort_output=True, check_typing_issues=False)
    check_func(test_drop_count, (df3,), sort_output=True, check_typing_issues=False)


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


def test_agg_series_input(memory_leak_check):
    """
    Test Groupby.agg(): make sure input to UDF is a Series, not Array
    """

    def impl(df):
        # using `count` since Arrays don't support it
        A = df.groupby("A").agg(lambda x: x.count())
        return A

    # check_dtype=False since Pandas returns float64 for count sometimes for some reason
    check_func(impl, (udf_in_df,), sort_output=True, check_dtype=False)


def test_agg_bool_expr(memory_leak_check):
    """
    Test Groupby.agg(): make sure boolean expressions work (#326)
    """

    def impl(df):
        return df.groupby("A")["B"].agg(lambda x: ((x == "A") | (x == "B")).sum())

    df = pd.DataFrame({"A": [1, 2, 1, 2] * 2, "B": ["A", "B", "C", "D"] * 2})
    check_func(impl, (df,), sort_output=True)


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
    """For the cumsum operation, the number of rows remains the same and the index is preserved.
    ---
    At the present time the agg(("cumsum", "cumprod")) is broken in pandas. See
    https://github.com/pandas-dev/pandas/issues/35490
    """
    assert re.compile(r"1.1.*").match(
        pd.__version__
    ), "revisit the agg((cumsum, cumprod)) at next pandas version"

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


#    check_func(test_impl_both, (df_index,), sort_output=True, check_dtype=False)
#    check_func(test_impl_all, (df_index,), sort_output=True, check_dtype=False)


# TODO: add memory leak check when cumsum leak issue resolved
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


# TODO: add memory leak check when cumsum leak issue resolved
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
    check_func(f1, (df2,), check_dtype=False)
    check_func(f2, (df2,), check_dtype=False)


# TODO: add memory leak check when cumsum leak issue resolved
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
    check_func(f, (df,), check_dtype=False)


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
            df2_merge_A_sort, df2_merge_B_sort, check_dtype=False
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
            return "NaT"
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

    # for some reason pandas does not make index a column with impl4:
    # https://github.com/pandas-dev/pandas/issues/25011
    pandas_df = impl4(df)
    pandas_df.reset_index(inplace=True)  # convert A index to column
    pandas_df = pandas_df.sort_values(by="A").reset_index(drop=True)
    bodo_df = bodo.jit(impl4)(df)
    bodo_df = bodo_df.sort_values(by="A").reset_index(drop=True)
    pd.testing.assert_frame_equal(pandas_df, bodo_df)


def test_agg_select_col_fast(memory_leak_check):
    """
    Test Groupby.agg() with explicitly select one (str)column
    """

    def impl_str(df):
        A = df.groupby("A")["B"].agg(lambda x: (x == "a").sum())
        return A

    df_str = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": ["a", "b", "c", "c", "b", "c", "a"]}
    )

    check_func(impl_str, (df_str,), sort_output=True)


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
    df_str = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": ["a", "b", "c", "c", "b", "c", "a"]}
    )
    check_func(impl_num, (df_int,), sort_output=True, check_dtype=False)
    check_func(impl_num, (df_float,), sort_output=True, check_dtype=False)
    check_func(test_impl, (11,), sort_output=True, check_dtype=False)


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


def test_agg_len_mix(memory_leak_check):
    """
    Test Groupby.agg(): use of len() in a UDF mixed with another parfor
    """

    def impl(df):
        A = df.groupby("A").agg(lambda x: x.sum() / len(x))
        return A

    check_func(impl, (udf_in_df,), sort_output=True, check_dtype=False)


def test_agg_multi_udf(memory_leak_check):
    """
    Test Groupby.agg() multiple user defined functions
    ---
    The agg(("cumsum", "cumprod")) is currently broken because of a bug in pandas.
    https://github.com/pandas-dev/pandas/issues/35490
    """
    assert re.compile(r"1.1.*").match(
        pd.__version__
    ), "revisit the agg((cumsum, cumprod)) at next pandas version"

    def impl(df):
        def id1(x):
            return (x <= 2).sum()

        def id2(x):
            return (x > 2).sum()

        return df.groupby("A")["B"].agg((id1, id2))

    def impl2(df):
        def id1(x):
            return (x <= 2).sum()

        def id2(x):
            return (x > 2).sum()

        return df.groupby("A")["B"].agg(("var", id1, id2, "sum"))

    def impl3(df):
        return df.groupby("A")["B"].agg(
            (lambda x: x.max() - x.min(), lambda x: x.max() + x.min())
        )

    def impl4(df):
        return df.groupby("A")["B"].agg(("cumprod", "cumsum"))

    df = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": [1, 2, 3, 4, 5, 6, 7]},
        index=[7, 8, 9, 2, 3, 4, 5],
    )

    check_func(impl, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)
    # check_dtype=False since Bodo returns float for Series.min/max. TODO: fix min/max
    check_func(impl3, (df,), sort_output=True, check_dtype=False)


#    check_func(impl4, (df,), sort_output=True)


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
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": ["a", "b", "c", "c", "b", "c", "a"]}
    )
    check_func(impl_num, (df_int,), sort_output=True, check_dtype=False)
    if not is_slow_run:
        return
    check_func(impl_num, (df_float,), sort_output=True, check_dtype=False)
    check_func(impl_str, (df_str,), sort_output=True, check_dtype=False)
    check_func(test_impl, (11,), sort_output=True, check_dtype=False)


# TODO: add memory leak check when cumsum leak issue resolved
def test_groupby_agg_const_dict():
    """
    Test groupy.agg with function spec passed as constant dictionary
    """

    def impl(df):
        df2 = df.groupby("A")[["B", "C"]].agg({"B": "count", "C": "sum"})
        return df2

    def impl2(df):
        df2 = df.groupby("A").agg({"B": "count", "C": "sum"})
        return df2

    def impl3(df):
        df2 = df.groupby("A").agg({"B": "median"})
        return df2

    def impl4(df):
        df2 = df.groupby("A").agg({"B": ["median"]})
        return df2

    def impl5(df):
        df2 = df.groupby("A").agg({"D": "nunique", "B": "median", "C": "var"})
        return df2

    def impl6(df):
        df2 = df.groupby("A").agg({"B": ["median", "nunique"]})
        return df2

    def impl7(df):
        df2 = df.groupby("A").agg({"B": ["count", "var", "prod"], "C": ["std", "sum"]})
        return df2

    def impl8(df):
        df2 = df.groupby("A", as_index=False).agg(
            {"B": ["count", "var", "prod"], "C": ["std", "sum"]}
        )
        return df2

    def impl9(df):
        df2 = df.groupby("A").agg({"B": ["count", "var", "prod"], "C": "std"})
        return df2

    def impl10(df):
        df2 = df.groupby("A").agg({"B": ["count", "var", "prod"], "C": ["std"]})
        return df2

    def impl11(df):
        df2 = df.groupby("A").agg(
            {"B": ["count", "median", "prod"], "C": ["nunique", "sum"]}
        )
        return df2

    def impl12(df):
        def id1(x):
            return (x >= 2).sum()

        df2 = df.groupby("D").agg({"B": "var", "A": id1, "C": "sum"})
        return df2

    def impl13(df):
        df2 = df.groupby("D").agg({"B": lambda x: x.max() - x.min(), "A": "sum"})
        return df2

    def impl14(df):
        df2 = df.groupby("A").agg(
            {
                "D": lambda x: (x == "BB").sum(),
                "B": lambda x: x.max() - x.min(),
                "C": "sum",
            }
        )
        return df2

    def impl15(df):
        df2 = df.groupby("A").agg({"B": "cumsum", "C": "cumprod"})
        return df2

    # reuse a complex dict to test typing transform for const dict removal
    def impl16(df):
        d = {"B": [lambda a: a.sum(), "mean"]}
        df1 = df.groupby("A").agg(d)
        df2 = df.groupby("C").agg(d)
        return df1, df2

    # reuse and return a const dict to test typing transform
    def impl17(df):
        d = {"B": "sum"}
        df1 = df.groupby("A").agg(d)
        df2 = df.groupby("C").agg(d)
        return df1, df2, d

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "D": ["AA", "B", "BB", "B", "AA", "AA", "B"],
            "B": [-8.1, 2.1, 3.1, 1.1, 5.1, 6.1, 7.1],
            "C": [3, 5, 6, 5, 4, 4, 3],
        },
        index=np.arange(10, 17),
    )
    check_func(impl, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)
    check_func(impl3, (df,), sort_output=True)
    check_func(impl4, (df,), sort_output=True)
    check_func(impl5, (df,), sort_output=True)
    check_func(impl6, (df,), sort_output=True)
    check_func(impl7, (df,), sort_output=True)
    check_func(impl8, (df,), sort_output=True, reset_index=True)
    check_func(impl9, (df,), sort_output=True)
    check_func(impl10, (df,), sort_output=True)
    check_func(impl11, (df,), sort_output=True)
    check_func(impl12, (df,), sort_output=True)
    check_func(impl13, (df,), sort_output=True)
    check_func(impl14, (df,), sort_output=True)
    check_func(impl15, (df,), sort_output=True)
    # can't use check_func since lambda name in MultiIndex doesn't match Pandas
    # TODO: fix lambda name
    # check_func(impl16, (df,), sort_output=True, reset_index=True)
    bodo.jit(impl16)(df)  # just check for compilation errors
    # TODO: enable is_out_distributed after fixing gatherv issue for tuple output
    check_func(impl17, (df,), sort_output=True, dist_test=False)


def test_groupby_agg_caching(memory_leak_check):
    """Test compiling function that uses groupby.agg(udf) with cache=True
    and loading from cache"""

    def impl(df):
        A = df.groupby("A").agg(lambda x: x.max() - x.min())
        return A

    df = pd.DataFrame({"A": [0, 0, 1, 1, 0], "B": range(5)})
    py_out = impl(df)
    bodo_out1, bodo_out2 = check_caching(
        sys.modules[__name__], "test_groupby_agg_caching", impl, (df,)
    )
    pd.testing.assert_frame_equal(py_out, bodo_out1)
    pd.testing.assert_frame_equal(py_out, bodo_out2)


def g(x):
    return (x == "a").sum()


@pytest.mark.slow
def test_agg_global_func(memory_leak_check):
    """
    Test Groupby.agg() with a global function as UDF
    """

    def impl_str(df):
        A = df.groupby("A")["B"].agg(g)
        return A

    df_str = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": ["a", "b", "c", "c", "b", "c", "a"]}
    )

    check_func(impl_str, (df_str,), sort_output=True)


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
            "B": ["ccc", np.nan, "bb", "aa", np.nan, "ggg", "rr"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
        }
    )
    df_bool = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [True, np.nan, False, True, np.nan, False, False],
            "C": [True, True, False, True, True, False, False],
        }
    )
    df_dt = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    check_func(impl1, (df_int,), sort_output=True)
    check_func(impl1, (df_str,), sort_output=True)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl1, (df_dt,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


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
            "B": ["ccc", np.nan, "bb", "aa", np.nan, "ggg", "rr"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
        }
    )
    df_bool = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [True, np.nan, False, True, np.nan, False, False],
            "C": [True, True, False, True, True, False, False],
        }
    )
    df_dt = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    check_func(impl1, (df_int,), sort_output=True)
    check_func(impl1, (df_str,), sort_output=True)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl1, (df_dt,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


@pytest.mark.parametrize(
    "df_med",
    [
        pd.DataFrame({"A": [1, 1, 1, 1], "B": [1, 2, 3, 4]}),
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

    check_func(impl1, (df_med,), sort_output=True)


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
    check_func(impl1, (df1,), sort_output=True)


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
    check_func(impl1, (df1,), sort_output=True)


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
                "B": ["ccc", np.nan, "bb", "aa", np.nan, "ggg", "rr"],
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

    check_func(impl1, (df_uniq,), sort_output=True)
    check_func(impl2, (df_uniq,), sort_output=True)
    check_func(impl3, (df_uniq,), sort_output=True)


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
            "A": [np.nan, "b", "b", "b", "aa", "aa", "b"],
            "B": ["ccc", np.nan, "bb", "aa", np.nan, "ggg", "rr"],
        }
    )
    check_func(impl1, (df_int,), sort_output=True)
    check_func(impl1, (df_str,), sort_output=True)


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
    pd.testing.assert_frame_equal(res[0], h_res[0])
    pd.testing.assert_frame_equal(res[1], h_res[1])


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


def test_single_col_reset_index(test_df, memory_leak_check):
    """We need the reset_index=True because otherwise the order is scrambled"""

    def impl1(df):
        A = df.groupby("A")["B"].sum().reset_index()
        return A

    check_func(impl1, (test_df,), sort_output=True, reset_index=True)


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
    check_func(impl1, (df1,), sort_output=True)
    check_func(impl2, (df1,), sort_output=True)
    check_func(impl3, (df1,), sort_output=True)


@pytest.mark.slow
def test_cummin_cummax_large_random_numpy(memory_leak_check):
    """A bunch of tests related to cummin/cummax functions.
    ---
    The agg(("cummin", "cummax")) is currently broken because of a bug in pandas.
    https://github.com/pandas-dev/pandas/issues/35490
    """
    assert re.compile(r"1.1.*").match(
        pd.__version__
    ), "revisit the agg((cummin, cummax)) at next pandas version"

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
    #    check_func(impl1, (df1,), sort_output=True, reset_index=True)
    check_func(impl2, (df1,), sort_output=True, reset_index=True)
    check_func(impl3, (df1,), sort_output=True, reset_index=True)
    check_func(impl4, (df1,), sort_output=True, reset_index=True)
    check_func(impl5, (df1,), sort_output=True, reset_index=True)
    check_func(impl6, (df1,), sort_output=True, reset_index=True)
    check_func(impl7, (df1,), sort_output=True, reset_index=True)
    check_func(impl8, (df1,), sort_output=True, reset_index=True)


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
    check_func(impl1, (df1,), sort_output=True)
    check_func(impl1, (df2,), sort_output=True)
    check_func(impl1, (df3,), sort_output=True)
    check_func(impl2, (df1,), sort_output=True)
    check_func(impl2, (df2,), sort_output=True)
    check_func(impl2, (df3,), sort_output=True)


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
            "B": [1, 2, 3, 2, 1, 1, 1],
            "C": [3, 5, 6, 5, 4, 4, 3],
            "D": [3.1, 1.1, 6.0, np.nan, 4.0, np.nan, 3],
        },
        index=np.arange(10, 17),
    )
    check_func(impl, (df,), sort_output=True)


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
    check_func(impl2, (df,), sort_output=True)


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
            "B": [True, np.nan, False, True, np.nan, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    check_func(impl1, (test_df,), sort_output=True)
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

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].max()
        return A

    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, np.nan, False, True, np.nan, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    # seems like Pandas 1.0 has a regression and returns float64 for Int64 in this case
    check_dtype = True
    if pd.Int64Dtype() in test_df.dtypes.to_list():
        check_dtype = False
    check_func(impl1, (test_df,), sort_output=True, check_dtype=check_dtype)


#    check_func(impl1, (df_bool,), sort_output=True)
#    check_func(impl2, (11,))


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


def test_mean(test_df, memory_leak_check):
    """
    Test Groupby.mean()
    """

    def impl1(df):
        A = df.groupby("A").mean()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").mean()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


@pytest.mark.slow
def test_mean_one_col(test_df, memory_leak_check):
    """
    Test Groupby.mean() with one column selected
    """

    def impl1(df):
        A = df.groupby("A")["B"].mean()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].mean()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


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
            "B": [True, np.nan, False, True, np.nan, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


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
            "B": [True, np.nan, False, True, np.nan, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    # seems like Pandas 1.0 has a regression and returns float64 for Int64 in this case
    check_dtype = True
    if pd.Int64Dtype() in test_df.dtypes.to_list():
        check_dtype = False
    check_func(impl1, (test_df,), sort_output=True, check_dtype=check_dtype)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


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


def test_prod(test_df, memory_leak_check):
    """
    Test Groupby.prod()
    """

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
            # "B": [True, np.nan, False, True, np.nan, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )

    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


@pytest.mark.slow
def test_prod_one_col(test_df, memory_leak_check):
    """
    Test Groupby.prod() with one column selected
    """

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
            "C": [True, np.nan, False, True, np.nan, False, False, True],
            "B": [True, True, False, True, True, False, False, False],
        }
    )

    # seems like Pandas 1.0 has a regression and returns float64 for Int64 in this case
    check_dtype = True
    if pd.Int64Dtype() in test_df.dtypes.to_list():
        check_dtype = False
    check_func(impl1, (test_df,), sort_output=True, check_dtype=check_dtype)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


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


def test_first_last(test_df, memory_leak_check):
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
            "B": ["ccc", np.nan, "bb", "aa", np.nan, "ggg", "rr"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
        }
    )
    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, np.nan, False, True, np.nan, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )
    df_dt = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )
    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl1, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl1, (df_dt,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)
    check_func(impl3, (test_df,), sort_output=True)
    check_func(impl3, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl3, (df_bool,), sort_output=True)
    check_func(impl3, (df_dt,), sort_output=True)
    check_func(impl4, (11,), sort_output=True)


@pytest.mark.slow
def test_first_last_one_col(test_df, memory_leak_check):
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
            "B": ["ccc", np.nan, "bb", "aa", np.nan, "ggg", "rr"],
            "C": ["cc", "aa", "aa", "bb", "vv", "cc", "cc"],
        }
    )
    df_bool = pd.DataFrame(
        {
            "A": [16, 1, 1, 1, 16, 16, 1, 40],
            "B": [True, np.nan, False, True, np.nan, False, False, True],
            "C": [True, True, False, True, True, False, False, False],
        }
    )
    df_dt = pd.DataFrame(
        {"A": [2, 1, 1, 1, 2, 2, 1], "B": pd.date_range("2019-1-3", "2019-1-9")}
    )

    # seems like Pandas 1.0 has a regression and returns float64 for Int64 in this case
    check_dtype = True
    if pd.Int64Dtype() in test_df.dtypes.to_list():
        check_dtype = False
    check_func(impl1, (test_df,), sort_output=True, check_dtype=check_dtype)
    check_func(impl1, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl1, (df_bool,), sort_output=True)
    check_func(impl1, (df_dt,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)
    check_func(impl3, (test_df,), sort_output=True, check_dtype=check_dtype)
    check_func(impl3, (df_str,), sort_output=True, check_typing_issues=False)
    check_func(impl3, (df_bool,), sort_output=True)
    check_func(impl3, (df_dt,), sort_output=True)
    check_func(impl4, (11,), sort_output=True)


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


@pytest.mark.slow
def test_std_one_col(test_df, memory_leak_check):
    """
    Test Groupby.std() with one column selected
    ---
    For the df.groupby("A")["B"].std() we have an error for test_df1
    This is due to a bug in pandas. See
    https://github.com/pandas-dev/pandas/issues/35516
    """
    assert re.compile(r"1.1.*").match(
        pd.__version__
    ), "revisit the df.groupby(A)[B].std() issue at next pandas version"

    def impl1(df):
        #        A = df.groupby("A")["B"].std()
        A = df.groupby("A")["B"].var()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].std()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


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

    def impl1(df):
        A = df.groupby("A").sum()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").sum()
        return A

    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


@pytest.mark.slow
def test_sum_one_col(test_df, memory_leak_check):
    """
    Test Groupby.sum() with one column selected
    """

    def impl1(df):
        A = df.groupby("A")["B"].sum()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].sum()
        return A

    check_func(impl1, (test_df,), sort_output=True)
    check_func(impl2, (11,), sort_output=True)


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


@pytest.mark.slow
def test_groupby_as_index_sum(memory_leak_check):
    """
    Test sum on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).sum()
        return df2

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].sum()
        return df2

    check_func(impl1, (11,), sort_output=True, reset_index=True)
    check_func(impl2, (11,), sort_output=True, reset_index=True)


# TODO: add memory leak check when issues addressed
@pytest.mark.slow
def test_groupby_multi_intlabels_sum():
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
def test_groupby_multi_key_to_index():
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


@pytest.mark.slow
def test_groupby_multiselect_sum(memory_leak_check):
    """
    Test groupy.sum() on explicitly selected columns using a tuple and using a constant
    list (#198)
    """

    def impl1(df):
        df2 = df.groupby("A")["B", "C"].sum()
        return df2

    def impl2(df):
        df2 = df.groupby("A")[["B", "C"]].sum()
        return df2

    df = pd.DataFrame(
        {
            "A": [2, 1, 1, 1, 2, 2, 1],
            "B": [-8, 2, 3, 1, 5, 6, 7],
            "C": [3, 5, 6, 5, 4, 4, 3],
        }
    )
    check_func(impl1, (df,), sort_output=True)
    check_func(impl2, (df,), sort_output=True)


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

    def impl1(df):
        A = df.groupby("A").var()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A").var()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


@pytest.mark.slow
def test_var_one_col(test_df, memory_leak_check):
    """
    Test Groupby.var() with one column selected
    """

    def impl1(df):
        A = df.groupby("A")["B"].var()
        return A

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        A = df.groupby("A")["B"].var()
        return A

    check_func(impl1, (test_df,), sort_output=True, check_dtype=False)
    check_func(impl2, (11,), sort_output=True, check_dtype=False)


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


@pytest.mark.slow
def test_groupby_as_index_var(memory_leak_check):
    """
    Test var on groupby() as_index=False
    for both dataframe and series returns
    """

    def impl1(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False).var()
        return df2

    def impl2(n):
        df = pd.DataFrame({"A": np.ones(n, np.int64), "B": np.arange(n)})
        df2 = df.groupby("A", as_index=False)["B"].var()
        return df2

    check_func(impl1, (11,), sort_output=True, check_dtype=False, reset_index=True)
    check_func(impl2, (11,), sort_output=True, check_dtype=False, reset_index=True)


# TODO: add memory leak check when issues addressed
def test_const_list_inference():
    """
    Test passing non-const list that can be inferred as constant to groupby()
    """

    def impl1(df):
        return df.groupby(["A"] + ["B"]).sum()

    def impl2(df):
        return df.groupby(list(set(df.columns) - set(["A", "C"]))).sum()

    # test df schema change by setting a column
    def impl3(n):
        df = pd.DataFrame({"A": np.arange(n), "B": np.ones(n)})
        df["D"] = 4
        return df.groupby("D").sum()

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
    with pytest.raises(
        BodoError,
        match="argument 'by' requires a constant value but variable 'l' is updated inplace using 'append'",
    ):
        bodo.jit(impl4)(df)


# global key list for groupby() testing
g_keys = ["A", "B"]


# TODO: add memory leak check when issues addressed
def test_global_list():
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


# TODO: add memory leak check when issues addressed
def test_literal_args():
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


def test_groupby_empty_funcs():
    """Test groupby that has no function to execute (issue #1590)"""

    def impl(df):
        first_df = df.groupby("A", as_index=False)["B"].max()
        return len(first_df)

    df = pd.DataFrame({"A": [0, 0, 0, 1, 1, 1], "B": range(6)})
    assert impl(df) == bodo.jit(impl)(df)


# ------------------------------ pivot, crosstab ------------------------------ #


_pivot_df1 = pd.DataFrame(
    {
        "A": ["foo", "foo", "foo", "foo", "foo", "bar", "bar", "bar", "bar"],
        "B": ["one", "one", "one", "two", "two", "one", "one", "two", "two"],
        "C": [
            "small",
            "large",
            "large",
            "small",
            "small",
            "large",
            "small",
            "small",
            "large",
        ],
        "D": [1, 2, 2, 6, 3, 4, 5, 6, 9],
    }
)


def test_pivot(memory_leak_check):
    def test_impl(df):
        pt = df.pivot_table(index="A", columns="C", values="D", aggfunc="sum")
        return (pt.small.values, pt.large.values)

    def test_impl2(df):
        pt = df.pivot_table(index="A", columns="C", values="D")
        return (pt.small.values, pt.large.values)

    bodo_func = bodo.jit(pivots={"pt": ["small", "large"]})(test_impl)
    assert set(bodo_func(_pivot_df1)[0]) == set(test_impl(_pivot_df1)[0])
    assert set(bodo_func(_pivot_df1)[1]) == set(test_impl(_pivot_df1)[1])

    bodo_func = bodo.jit(pivots={"pt": ["small", "large"]})(test_impl2)
    assert set(bodo_func(_pivot_df1)[0]) == set(test_impl2(_pivot_df1)[0])
    assert set(bodo_func(_pivot_df1)[1]) == set(test_impl2(_pivot_df1)[1])


def test_pivot_parallel(datapath):
    fname = datapath("pivot2.pq")

    def impl():
        df = pd.read_parquet(fname)
        pt = df.pivot_table(index="A", columns="C", values="D", aggfunc="sum")
        res = pt.small.values.sum()
        return res

    bodo_func = bodo.jit(pivots={"pt": ["small", "large"]})(impl)
    assert bodo_func() == impl()


def test_crosstab(memory_leak_check):
    def test_impl(df):
        pt = pd.crosstab(df.A, df.C)
        return (pt.small.values, pt.large.values)

    bodo_func = bodo.jit(pivots={"pt": ["small", "large"]})(test_impl)
    assert set(bodo_func(_pivot_df1)[0]) == set(test_impl(_pivot_df1)[0])
    assert set(bodo_func(_pivot_df1)[1]) == set(test_impl(_pivot_df1)[1])


def test_crosstab_parallel(datapath):
    fname = datapath("pivot2.pq")

    def impl():
        df = pd.read_parquet(fname)
        pt = pd.crosstab(df.A, df.C)
        res = pt.small.values.sum()
        return res

    bodo_func = bodo.jit(pivots={"pt": ["small", "large"]})(impl)
    assert bodo_func() == impl()
