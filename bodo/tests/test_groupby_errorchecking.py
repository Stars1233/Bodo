# Copyright (C) 2019 Bodo Inc. All rights reserved.
import datetime

import numpy as np
import pandas as pd
import pytest

import bodo
from bodo.utils.typing import BodoError

# ------------------------------ df.groupby() ------------------------------ #


def test_groupby_supply_by(memory_leak_check):
    """
    Test groupby(): 'by' is supplied
    """

    def impl1(df):
        return df.groupby()

    def impl2(df):
        return df.groupby(by=None)

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(BodoError, match="'by' must be supplied"):
        bodo.jit(impl1)(df)
    with pytest.raises(BodoError, match="'by' must be supplied"):
        bodo.jit(impl2)(df)


def test_groupby_by_const_str_or_str_list(memory_leak_check):
    """
    Test groupby(): 'by' is of type const str or str list

    """

    def impl(df):
        return df.groupby(by=1)

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError,
        match="'by' parameter only supports a constant column label or column labels",
    ):
        bodo.jit(impl)(df)


def test_groupby_by_labels(memory_leak_check):
    """
    Test groupby(): 'by' is a valid label or label lists
    """

    def impl(df):
        return df.groupby(by=["A", "D"])

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(BodoError, match="invalid key .* for 'by'"):
        bodo.jit(impl)(df)


def test_groupby_axis_default(memory_leak_check):
    """
    Test groupby(): 'axis' cannot be values other than integer value 0
    """

    def impl1(df):
        return df.groupby(by=["A"], axis=1).sum()

    def impl2(df):
        return df.groupby(by=["A"], axis="1").sum()

    df = pd.DataFrame({"A": [1, 2, 2], "C": [3, 1, 2]})
    with pytest.raises(
        BodoError, match="'axis' parameter only supports integer value 0"
    ):
        bodo.jit(impl1)(df)
    with pytest.raises(
        BodoError, match="'axis' parameter only supports integer value 0"
    ):
        bodo.jit(impl2)(df)


def test_groupby_sum_date(memory_leak_check):
    """dates are currently not supported"""

    def impl(df):
        return df.groupby("A").sum()

    df1 = pd.DataFrame({"A": [2, 1, 1], "B": pd.date_range("2019-1-3", "2019-1-5")})
    with pytest.raises(
        BodoError, match="is not supported in groupby built-in function"
    ):
        bodo.jit(impl)(df1)


def test_groupby_supply_level(memory_leak_check):
    """
    Test groupby(): 'level' cannot be supplied
    """

    def impl(df):
        return df.groupby(by=["A", "C"], level=2)

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError, match="'level' is not supported since MultiIndex is not supported."
    ):
        bodo.jit(impl)(df)


def test_groupby_as_index_bool(memory_leak_check):
    """
    Test groupby(): 'as_index' must be a constant bool
    """

    def impl(df):
        return df.groupby(by=["A", "C"], as_index=2)

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(BodoError, match="'as_index' parameter must be a constant bool"):
        bodo.jit(impl)(df)


def test_groupby_sort_default(memory_leak_check):
    """
    Test groupby(): 'sort' cannot have values other than boolean value False
    """

    def impl1(df):
        return df.groupby(by=["A", "C"], sort=1)

    def impl2(df):
        return df.groupby(by=["A", "C"], sort=True)

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError, match="'sort' parameter only supports default value False"
    ):
        bodo.jit(impl1)(df)
    with pytest.raises(
        BodoError, match="'sort' parameter only supports default value False"
    ):
        bodo.jit(impl2)(df)


def test_groupby_group_keys_true(memory_leak_check):
    """
    Test groupby(): 'group_keys' cannot have values other than boolean value True
    """

    def impl1(df):
        return df.groupby(by=["A", "C"], group_keys=2)

    def impl2(df):
        return df.groupby(by=["A", "C"], group_keys=False)

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError, match="'group_keys' parameter only supports default value True"
    ):
        bodo.jit(impl1)(df)
    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError, match="'group_keys' parameter only supports default value True"
    ):
        bodo.jit(impl2)(df)


def test_groupby_squeeze_false(memory_leak_check):
    """
    Test groupby(): 'squeeze' cannot have values other than boolean value False
    """

    def impl1(df):
        return df.groupby(by=["A", "C"], squeeze=0)

    def impl2(df):
        return df.groupby(by=["A", "C"], squeeze=True)

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError, match="'squeeze' parameter only supports default value False"
    ):
        bodo.jit(impl1)(df)
    with pytest.raises(
        BodoError, match="'squeeze' parameter only supports default value False"
    ):
        bodo.jit(impl2)(df)


def test_groupby_observed_false(memory_leak_check):
    """
    Test groupby(): 'observed' cannot have values other than boolean value False
    """

    def impl1(df):
        return df.groupby(by=["A", "C"], observed=0)

    def impl2(df):
        return df.groupby(by=["A", "C"], observed=True)

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError, match="'observed' parameter only supports default value False"
    ):
        bodo.jit(impl1)(df)
    with pytest.raises(
        BodoError, match="'observed' parameter only supports default value False"
    ):
        bodo.jit(impl2)(df)


# ------------------------------ Groupby._() ------------------------------ #


def test_groupby_column_selection(memory_leak_check):
    """
    Test Groupby[]: selected column must exist in the Dataframe
    """

    def impl(df):
        return df.groupby(by=["A"])["B"]

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(BodoError, match="selected column .* not found in dataframe"):
        bodo.jit(impl)(df)


def test_groupby_column_selection_attr(memory_leak_check):
    """
    Test Groupby.col: selected column must exist in the dataframe
    """

    def impl(df):
        return df.groupby(by=["A"]).B

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(BodoError, match="groupby: invalid attribute"):
        bodo.jit(impl)(df)


def test_groupby_columns_selection(memory_leak_check):
    """
    Test Groupby[]: selceted column(s) must exist in the Dataframe
    """

    def impl(df):
        return df.groupby(by=["A"])["B", "C"]

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(BodoError, match="selected column .* not found in dataframe"):
        bodo.jit(impl)(df)


def test_groupby_agg_func(memory_leak_check):
    """
    Test Groupby.agg(): func must be specified
    """

    def impl(df):
        return df.groupby(by=["A"]).agg()

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(BodoError, match="Must provide 'func'"):
        bodo.jit(impl)(df)


def test_groupby_agg_multi_funcs(memory_leak_check):
    """
    Test Groupby.agg(): when more than one functions are supplied, a column must be explictely selected
    """

    def impl(df):
        return df.groupby(by=["A"]).agg((lambda x: len(x), lambda x: len(x)))

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError,
        match="must select exactly one column when more than one functions supplied",
    ):
        bodo.jit(impl)(df)


def test_groupby_agg_func_input_type(memory_leak_check):
    """
    Test Groupby.agg(): error should be raised when user defined function cannot be applied
    """

    def impl(df):
        return df.groupby(by=["A"]).agg(lambda x: x.max() - x.min())

    df = pd.DataFrame({"A": [1, 2, 2], "B": [1, 2, 2], "C": ["aba", "aba", "aba"]})
    with pytest.raises(
        BodoError,
        match="column C .* unsupported/not a valid input type for user defined function",
    ):
        bodo.jit(impl)(df)


def test_groupby_agg_func_udf(memory_leak_check):
    """
    Test Groupby.agg(): error should be raised when 'func' is not a user defined function
    """

    def impl(df):
        return df.groupby(by=["A"]).agg(np.sum)

    df = pd.DataFrame({"A": [1, 2, 2], "B": [1, 2, 2], "C": ["aba", "aba", "aba"]})
    with pytest.raises(BodoError, match=".* 'func' must be user defined function"):
        bodo.jit(impl)(df)


def test_groupby_agg_funcs_udf(memory_leak_check):
    """
    Test Groupby.agg(): error should be raised when 'func' tuple contains non user defined functions
    """

    def impl(df):
        return df.groupby(by=["A"]).agg(np.sum, np.sum)

    df = pd.DataFrame({"A": [1, 2, 2], "B": [1, 2, 2], "C": ["aba", "aba", "aba"]})
    with pytest.raises(BodoError, match=".* 'func' must be user defined function"):
        bodo.jit(impl)(df)


def test_groupby_aggregate_func_required_parameter(memory_leak_check):
    """
    Test Groupby.aggregate(): func must be specified
    """

    def impl(df):
        return df.groupby(by=["A"]).aggregate()

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(BodoError, match="Must provide 'func'"):
        bodo.jit(impl)(df)


def test_groupby_aggregate_multi_funcs(memory_leak_check):
    """
    Test Groupby.aggregate(): when more than one functions are supplied, a column must be explictely selected
    """

    def impl(df):
        return df.groupby(by=["A"]).aggregate((lambda x: len(x), lambda x: len(x)))

    df = pd.DataFrame({"A": [1, 2, 2], "C": ["aa", "b", "c"], "E": ["aa", "bb", "cc"]})
    with pytest.raises(
        BodoError,
        match="must select exactly one column when more than one functions supplied",
    ):
        bodo.jit(impl)(df)


def test_groupby_aggregate_func_udf(memory_leak_check):
    """
    Test Groupby.aggregate(): error should be raised when 'func' is not a user defined function
    """

    def impl(df):
        return df.groupby(by=["A"]).aggregate(np.sum)

    df = pd.DataFrame({"A": [1, 2, 2], "B": [1, 2, 2], "C": ["aba", "aba", "aba"]})
    with pytest.raises(BodoError, match=".* 'func' must be user defined function"):
        bodo.jit(impl)(df)


def test_groupby_aggregate_funcs_udf(memory_leak_check):
    """
    Test Groupby.aggregate(): error should be raised when 'func' tuple contains non user defined functions
    """

    def impl(df):
        return df.groupby(by=["A"]).aggregate(np.sum, np.sum)

    df = pd.DataFrame({"A": [1, 2, 2], "B": [1, 2, 2], "C": ["aba", "aba", "aba"]})
    with pytest.raises(BodoError, match=".* 'func' must be user defined function"):
        bodo.jit(impl)(df)


def test_groupby_built_in_col_type(memory_leak_check):
    """
    Test Groupby.prod()
    and mean(), prod(), std(), sum(), var() should have same behaviors
    They all accept only integer, float, and boolean as column dtypes
    """

    def impl(df):
        return df.groupby(by=["A"]).prod()

    df = pd.DataFrame({"A": [1, 2, 2], "B": ["aba", "aba", "aba"]})
    with pytest.raises(
        BodoError,
        match="column type of strings or list of strings is not supported in groupby built-in function prod",
    ):
        bodo.jit(impl)(df)


def test_groupby_cumsum_col_type(memory_leak_check):
    """
    Test Groupby.cumsum() only accepts integers and floats
    """

    def impl(df):
        return df.groupby(by=["A"]).cumsum()

    df = pd.DataFrame({"A": [1, 2, 2], "B": [True, False, True]})
    with pytest.raises(
        BodoError,
        match="Groupby.cumsum.* only supports columns of types integer, float, string or liststring",
    ):
        bodo.jit(impl)(df)


def test_groupby_median_type_check(memory_leak_check):
    """
    Test Groupby.median() testing the input type argument
    """

    def impl(df):
        return df.groupby("A")["B"].median()

    df1 = pd.DataFrame({"A": [1, 1, 1, 1], "B": ["a", "b", "c", "d"]})
    df2 = pd.DataFrame({"A": [1, 1, 1, 1], "B": [True, False, True, False]})
    with pytest.raises(
        BodoError,
        match="For median, only column of integer, float or Decimal type are allowed",
    ):
        bodo.jit(impl)(df1)
    with pytest.raises(
        BodoError,
        match="For median, only column of integer, float or Decimal type are allowed",
    ):
        bodo.jit(impl)(df2)


def test_groupby_cumsum_argument_check(memory_leak_check):
    """
    Test Groupby.cumsum() testing for skipna argument
    """

    def impl1(df):
        return df.groupby("A")["B"].cumsum(skipna=0)

    def impl2(df):
        return df.groupby("A")["B"].cumsum(wrongarg=True)

    df = pd.DataFrame({"A": [1, 1, 1, 1], "B": [1, 2, 3, 4]})
    with pytest.raises(
        BodoError, match="For cumsum argument of skipna should be a boolean"
    ):
        bodo.jit(impl1)(df)
    with pytest.raises(BodoError, match="argument to cumsum can only be skipna"):
        bodo.jit(impl2)(df)


def test_groupby_cumsum_argument_duplication_check(memory_leak_check):
    """
    Test Groupby.cumsum() testing for skipna argument
    """

    def impl(df):
        return df.groupby("A")["B"].agg(("cumsum", "cumsum"))

    df = pd.DataFrame({"A": [1, 1, 1, 1], "B": [1, 2, 3, 4]})
    with pytest.raises(
        BodoError, match="aggregate with duplication in output is not allowed"
    ):
        bodo.jit(impl)(df)


def test_groupby_cumprod_argument_check(memory_leak_check):
    """
    Test Groupby.cumprod() testing for skipna argument
    """

    def impl1(df):
        return df.groupby("A")["B"].cumprod(skipna=0)

    def impl2(df):
        return df.groupby("A")["B"].cumprod(wrongarg=True)

    df = pd.DataFrame({"A": [1, 1, 1, 1], "B": [1, 2, 3, 4]})
    with pytest.raises(
        BodoError, match="For cumprod argument of skipna should be a boolean"
    ):
        bodo.jit(impl1)(df)
    with pytest.raises(BodoError, match="argument to cumprod can only be skipna"):
        bodo.jit(impl2)(df)


def test_groupby_nunique_argument_check(memory_leak_check):
    """
    Test Groupby.nunique() testing for dropna argument
    """

    def impl1(df):
        return df.groupby("A")["B"].nunique(dropna=0)

    def impl2(df):
        return df.groupby("A")["B"].nunique(wrongarg=True)

    df = pd.DataFrame({"A": [1, 1, 1, 1], "B": [1, 2, 3, 4]})
    with pytest.raises(
        BodoError, match="argument of dropna to nunique should be a boolean"
    ):
        bodo.jit(impl1)(df)
    with pytest.raises(BodoError, match="argument to nunique can only be dropna"):
        bodo.jit(impl2)(df)


def test_groupby_datetimeoperation_checks(memory_leak_check):
    """
    Testing the operations which cannot be done for date / datetime / timedelta
    """

    def impl_sum(df):
        return df.groupby("A")["B"].sum()

    def impl_prod(df):
        return df.groupby("A")["B"].prod()

    def impl_cumsum(df):
        return df.groupby("A")["B"].cumsum()

    def impl_cumprod(df):
        return df.groupby("A")["B"].cumprod()

    siz = 10
    datetime_arr_1 = pd.date_range("1917-01-01", periods=siz)
    datetime_arr_2 = pd.date_range("2017-01-01", periods=siz)
    timedelta_arr = datetime_arr_1 - datetime_arr_2
    date_arr = datetime_arr_1.date
    df1_datetime = pd.DataFrame({"A": np.arange(siz), "B": datetime_arr_1})
    df1_date = pd.DataFrame({"A": np.arange(siz), "B": date_arr})
    df1_timedelta = pd.DataFrame({"A": np.arange(siz), "B": timedelta_arr})
    # Check for sums
    with pytest.raises(
        BodoError,
        match="column type of datetime64.* is not supported in groupby built-in function sum",
    ):
        bodo.jit(impl_sum)(df1_datetime)
    with pytest.raises(
        BodoError,
        match="column type of DatetimeDateType.* is not supported in groupby built-in function sum",
    ):
        bodo.jit(impl_sum)(df1_date)
    with pytest.raises(
        BodoError,
        match="column type of timedelta64.* is not supported in groupby built-in function sum",
    ):
        bodo.jit(impl_sum)(df1_timedelta)
    # checks for prod
    with pytest.raises(
        BodoError,
        match="column type of datetime64.* is not supported in groupby built-in function prod",
    ):
        bodo.jit(impl_prod)(df1_datetime)
    with pytest.raises(
        BodoError,
        match="column type of DatetimeDateType.* is not supported in groupby built-in function prod",
    ):
        bodo.jit(impl_prod)(df1_date)
    with pytest.raises(
        BodoError,
        match="column type of timedelta64.* is not supported in groupby built-in function prod",
    ):
        bodo.jit(impl_prod)(df1_timedelta)
    # checks for cumsum
    with pytest.raises(
        BodoError,
        match="Groupby.cumsum.* only supports columns of types integer, float, string or liststring",
    ):
        bodo.jit(impl_cumsum)(df1_datetime)
    with pytest.raises(
        BodoError,
        match="Groupby.cumsum.* only supports columns of types integer, float, string or liststring",
    ):
        bodo.jit(impl_cumsum)(df1_date)
    with pytest.raises(
        BodoError,
        match="Groupby.cumsum.* only supports columns of types integer, float, string or liststring",
    ):
        bodo.jit(impl_cumsum)(df1_timedelta)
    # checks for cumprod
    with pytest.raises(
        BodoError,
        match="Groupby.cumprod.* only supports columns of types integer and float",
    ):
        bodo.jit(impl_cumprod)(df1_datetime)
    with pytest.raises(
        BodoError,
        match="Groupby.cumprod.* only supports columns of types integer and float",
    ):
        bodo.jit(impl_cumprod)(df1_date)
    with pytest.raises(
        BodoError,
        match="Groupby.cumprod.* only supports columns of types integer and float",
    ):
        bodo.jit(impl_cumprod)(df1_timedelta)


def test_groupby_unsupported_error_checking(memory_leak_check):
    """Test that a Bodo error is raised for unsupported
    groupby methods
    """

    def test_method(df):
        return df.groupby("a").sample(n=1, random_state=1)

    with pytest.raises(BodoError, match="not supported yet"):
        bodo.jit(test_method)(
            df=pd.DataFrame(
                {"a": ["red"] * 2 + ["blue"] * 2 + ["black"] * 2, "b": range(6)}
            )
        )
