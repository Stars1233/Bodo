# Copyright (C) 2023 Bodo Inc. All rights reserved.
"""Test Bodo's array kernel utilities for BodoSQL comparison operations
"""
import operator

import numpy as np
import pandas as pd
import pytest

import bodo
from bodo.libs.bodosql_array_kernels import *
from bodo.tests.utils import check_func


@pytest.fixture(
    params=(
        "equal",
        pytest.param("not_equal", marks=pytest.mark.slow),
        "less_than",
        pytest.param("greater_than", marks=pytest.mark.slow),
        pytest.param("less_than_or_equal", marks=pytest.mark.slow),
        "greater_than_or_equal",
    ),
)
def comparison_func_name(request):
    return request.param


@pytest.fixture(
    params=(
        pytest.param(
            pd.Series(["world", "helo", None, "hello", "🢇🄐,🏈𠆶💑😅"] * 4).values,
            id="string",
        ),
        pytest.param(pd.array([1, 4, -153, None, 34312] * 4, dtype="Int64"), id="int"),
        pytest.param(
            pd.Series(
                [
                    pd.Timestamp("2022-11-04"),
                    None,
                    pd.Timestamp("2022-11-25 06:50:51"),
                    pd.Timestamp("2022-11-25 06:50:53"),
                    pd.Timestamp("2023-11-11"),
                ]
                * 4
            ).values,
            id="Timestamp-Naive",
        ),
        pytest.param(
            pd.Series(
                [
                    pd.Timestamp("2022-11-04", tz="US/Pacific"),
                    None,
                    pd.Timestamp("2022-11-25 06:50:51", tz="US/Pacific"),
                    pd.Timestamp("2022-11-25 06:50:53", tz="US/Pacific"),
                    pd.Timestamp("2023-11-11", tz="US/Pacific"),
                ]
                * 4
            ).array,
            id="Timestamp-TZ-Aware",
        ),
    )
)
def arr_value(request):
    return request.param


def test_comparison_funcs(comparison_func_name, arr_value, memory_leak_check):
    """
    Tests all of bodosql comparison array kernels with various input types.
    """
    # Create the function for at least 1 array.
    func_text = f"""def test_impl(arg0, arg1):
        return pd.Series(bodo.libs.bodosql_array_kernels.{comparison_func_name}(arg0, arg1))
    """
    loc_vars = {}
    exec(func_text, {"bodo": bodo, "pd": pd}, loc_vars)
    test_impl = loc_vars["test_impl"]
    # Create the function for all scalars
    func_text = f"""def scalar_impl(arg0, arg1):
        return bodo.libs.bodosql_array_kernels.{comparison_func_name}(arg0, arg1)
    """
    exec(func_text, {"bodo": bodo}, loc_vars)
    scalar_impl = loc_vars["scalar_impl"]

    def gen_scalar_fn(comparison_func_name: str):
        """Generate a scalar function used to simulate the array
        kernel behavior in Python. this function will be applied to every
        element of the input.

        Args:
            comparison_func_name (str): Name of the function to generate.
        """
        op_map = {
            "equal": operator.eq,
            "not_equal": operator.ne,
            "less_than": operator.lt,
            "greater_than": operator.gt,
            "less_than_or_equal": operator.le,
            "greater_than_or_equal": operator.ge,
        }
        op = op_map[comparison_func_name]

        def impl(arg0, arg1):
            if pd.isna(arg0) or pd.isna(arg1):
                return None
            else:
                if isinstance(arg0, np.datetime64):
                    arg0 = pd.Timestamp(arg0)
                if isinstance(arg1, np.datetime64):
                    arg1 = pd.Timestamp(arg1)
                return op(arg0, arg1)

        return impl

    scalar_fn = gen_scalar_fn(comparison_func_name)
    # Test once with the same array
    arg0 = arg1 = arr_value
    # answer = vectorized_sol((arg0, arg1), scalar_fn, "boolean")
    # check_func(test_impl, (arg0, arg1), py_output=answer, reset_index=True)
    # Test once with two different arrays
    arg1 = arg0[::-1].ravel()
    answer = vectorized_sol((arg0, arg1), scalar_fn, "boolean")
    check_func(test_impl, (arg0, arg1), py_output=answer, reset_index=True)

    # Test with an array and a scalar
    scalar = arg0[0]
    if isinstance(scalar, np.datetime64):
        scalar = pd.Timestamp(scalar)

    arg1 = scalar
    answer = vectorized_sol((arg0, arg1), scalar_fn, "boolean")
    check_func(test_impl, (arg0, arg1), py_output=answer, reset_index=True)
    answer = vectorized_sol((arg1, arg0), scalar_fn, "boolean")
    check_func(test_impl, (arg1, arg0), py_output=answer, reset_index=True)

    # Test with two scalars
    answer = vectorized_sol((scalar, scalar), scalar_fn, "boolean")
    check_func(scalar_impl, (scalar, scalar), py_output=answer, reset_index=True)


def test_comparison_funcs_optional_types(comparison_func_name, memory_leak_check):
    """
    Tests optional type support for all of the support bodosql comparison
    array kernels.
    """
    func_text = f"""def test_impl(A, B, flag0, flag1):
        arg0 = A if flag0 else None
        arg1 = B if flag1 else None
        return bodo.libs.bodosql_array_kernels.{comparison_func_name}(arg0, arg1)
    """
    loc_vars = {}
    exec(func_text, {"bodo": bodo}, loc_vars)
    test_impl = loc_vars["test_impl"]
    arg0 = 27
    arg1 = 54
    scalar_answer = (
        True
        if comparison_func_name in ("not_equal", "less_than", "less_than_or_equal")
        else False
    )
    for flag0 in [True, False]:
        for flag1 in [True, False]:
            answer = scalar_answer if flag0 and flag1 else None
            check_func(test_impl, (arg0, arg1, flag0, flag1), py_output=answer)