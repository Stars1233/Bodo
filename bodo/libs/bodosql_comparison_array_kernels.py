# Copyright (C) 2023 Bodo Inc. All rights reserved.
"""
Implements comparison operation array kernels that are specific to BodoSQL
"""
from numba.core import types
from numba.extending import overload

import bodo
from bodo.libs.bodosql_array_kernel_utils import *


def equal(arr0, arr1):  # pragma: no cover
    pass


def not_equal(arr0, arr1):  # pragma: no cover
    pass


def less_than(arr0, arr1):  # pragma: no cover
    pass


def greater_than(arr0, arr1):  # pragma: no cover
    pass


def less_than_or_equal(arr0, arr1):  # pragma: no cover
    pass


def greater_than_or_equal(arr0, arr1):  # pragma: no cover
    pass


def equal_util(arr0, arr1):  # pragma: no cover
    pass


def not_equal_util(arr0, arr1):  # pragma: no cover
    pass


def less_than_util(arr0, arr1):  # pragma: no cover
    pass


def greater_than_util(arr0, arr1):  # pragma: no cover
    pass


def less_than_or_equal_util(arr0, arr1):  # pragma: no cover
    pass


def greater_than_or_equal_util(arr0, arr1):  # pragma: no cover
    pass


def create_comparison_operators_func_overload(func_name):
    """Creates an overload function to support comparison operator functions
    with Snowflake SQL semantics. These SQL operators treat NULL as unknown, so if
    either input is null the output is null.

    Note: Several different types can be compared so we don't do any type checking.

    Returns:
        (function): a utility that returns an overload with the operator functionality.
    """

    def overload_func(arr0, arr1):
        """Handles cases where func_name receives an optional argument and forwards
        to the appropriate version of the real implementation"""
        args = [arr0, arr1]
        for i in range(2):
            if isinstance(args[i], types.optional):
                return unopt_argument(
                    f"bodo.libs.bodosql_array_kernels.{func_name}",
                    ["arr0", "arr1"],
                    i,
                )

        func_text = "def impl(arr0, arr1):\n"
        func_text += (
            f"  return bodo.libs.bodosql_array_kernels.{func_name}_util(arr0, arr1)"
        )
        loc_vars = {}
        exec(func_text, {"bodo": bodo}, loc_vars)

        return loc_vars["impl"]

    return overload_func


def create_comparison_operators_util_func_overload(func_name):  # pragma: no cover
    """Creates an overload function to support comparison operator functions
    with Snowflake SQL semantics. These SQL operators treat NULL as unknown, so if
    either input is null the output is null.

    Note: Several different types can be compared so we don't do any type checking.

    Returns:
        (function): a utility that returns an overload with the operator functionality.
    """

    def overload_func_util(arr0, arr1):
        arg_names = ["arr0", "arr1"]
        arg_types = [arr0, arr1]
        propagate_null = [True] * 2
        out_dtype = bodo.boolean_array
        if func_name == "equal":
            operator_str = "=="
        elif func_name == "not_equal":
            operator_str = "!="
        elif func_name == "less_than":
            operator_str = "<"
        elif func_name == "greater_than":
            operator_str = ">"
        elif func_name == "less_than_or_equal":
            operator_str = "<="
        else:
            operator_str = ">="

        # Always unbox in case of Timestamp to avoid issues
        scalar_text = f"res[i] = bodo.utils.conversion.unbox_if_tz_naive_timestamp(arg0) {operator_str} bodo.utils.conversion.unbox_if_tz_naive_timestamp(arg1)"
        return gen_vectorized(
            arg_names, arg_types, propagate_null, scalar_text, out_dtype
        )

    return overload_func_util


def _install_comparison_operators_overload():
    """Creates and installs the overloads for comparison operator
    functions."""
    for func, util, func_name in (
        (equal, equal_util, "equal"),
        (not_equal, not_equal_util, "not_equal"),
        (less_than, less_than_util, "less_than"),
        (greater_than, greater_than_util, "greater_than"),
        (less_than_or_equal, less_than_or_equal_util, "less_than_or_equal"),
        (greater_than_or_equal, greater_than_or_equal_util, "greater_than_or_equal"),
    ):
        func_overload_impl = create_comparison_operators_func_overload(func_name)
        overload(func)(func_overload_impl)
        util_overload_impl = create_comparison_operators_util_func_overload(func_name)
        overload(util)(util_overload_impl)


_install_comparison_operators_overload()