# Copyright (C) 2019 Bodo Inc. All rights reserved.
"""
Implements miscellaneous array kernels that are specific to BodoSQL
"""

import numba
from numba.core import types

import bodo
from bodo.libs.bodosql_array_kernel_utils import *
from bodo.utils.typing import raise_bodo_error


@numba.generated_jit(nopython=True)
def cond(arr, ifbranch, elsebranch):
    """Handles cases where IF receives optional arguments and forwards
    to the appropriate version of the real implementation"""
    args = [arr, ifbranch, elsebranch]
    for i in range(3):
        if isinstance(args[i], types.optional):  # pragma: no cover
            return unopt_argument(
                "bodo.libs.bodosql_array_kernels.cond",
                ["arr", "ifbranch", "elsebranch"],
                i,
            )

    def impl(arr, ifbranch, elsebranch):  # pragma: no cover
        return cond_util(arr, ifbranch, elsebranch)

    return impl


@numba.generated_jit(nopython=True)
def nullif(arr0, arr1):
    """Handles cases where NULLIF recieves optional arguments and forwards
    to args appropriate version of the real implementation"""
    args = [arr0, arr1]
    for i in range(2):
        if isinstance(args[i], types.optional):  # pragma: no cover
            return unopt_argument(
                "bodo.libs.bodosql_array_kernels.nullif", ["arr0", "arr1"], i
            )

    def impl(arr0, arr1):  # pragma: no cover
        return nullif_util(arr0, arr1)

    return impl


@numba.generated_jit(nopython=True)
def cond_util(arr, ifbranch, elsebranch):
    """A dedicated kernel for the SQL function IF which takes in 3 values:
    a boolean (or boolean column) and two values (or columns) with the same
    type and returns the first or second value depending on whether the boolean
    is true or false


    Args:
        arr (boolean array/series/scalar): the T/F values
        ifbranch (any array/series/scalar): the value(s) to return when true
        elsebranch (any array/series/scalar): the value(s) to return when false

    Returns:
        int series/scalar: the difference in months between the two dates
    """

    verify_boolean_arg(arr, "cond", "arr")

    # Both branches cannot be scalar nulls if the output is an array
    # (causes a typing ambiguity)
    if (
        bodo.utils.utils.is_array_typ(arr, True)
        and ifbranch == bodo.none
        and elsebranch == bodo.none
    ):
        raise_bodo_error("Both branches of IF() cannot be scalar NULL")

    arg_names = ["arr", "ifbranch", "elsebranch"]
    arg_types = [arr, ifbranch, elsebranch]
    propagate_null = [False] * 3
    # If the conditional is an array, add a null check (null = False)
    if bodo.utils.utils.is_array_typ(arr, True):
        scalar_text = "if (not bodo.libs.array_kernels.isna(arr, i)) and arg0:\n"
    # If the conditional is a non-null scalar, case on its truthiness
    elif arr != bodo.none:
        scalar_text = "if arg0:\n"
    # Skip the ifbranch if the conditional is a scalar None (since we know that
    # the condition is always false)
    else:
        scalar_text = ""
    if arr != bodo.none:
        # If the ifbranch is an array, add a null check
        if bodo.utils.utils.is_array_typ(ifbranch, True):
            scalar_text += "   if bodo.libs.array_kernels.isna(ifbranch, i):\n"
            scalar_text += "      bodo.libs.array_kernels.setna(res, i)\n"
            scalar_text += "   else:\n"
            scalar_text += "      res[i] = arg1\n"
        # If the ifbranch is a scalar null, just set to null
        elif ifbranch == bodo.none:
            scalar_text += "   bodo.libs.array_kernels.setna(res, i)\n"
        # If the ifbranch is a non-null scalar, then no null check is required
        else:
            scalar_text += "   res[i] = arg1\n"
        scalar_text += "else:\n"
    # If the elsebranch is an array, add a null check
    if bodo.utils.utils.is_array_typ(elsebranch, True):
        scalar_text += "   if bodo.libs.array_kernels.isna(elsebranch, i):\n"
        scalar_text += "      bodo.libs.array_kernels.setna(res, i)\n"
        scalar_text += "   else:\n"
        scalar_text += "      res[i] = arg2\n"
    # If the elsebranch is a scalar null, just set to null
    elif elsebranch == bodo.none:
        scalar_text += "   bodo.libs.array_kernels.setna(res, i)\n"
    # If the elsebranch is a non-null scalar, then no null check is required
    else:
        scalar_text += "   res[i] = arg2\n"

    # Get the common dtype from the two branches
    out_dtype = get_common_broadcasted_type([ifbranch, elsebranch], "IF")

    return gen_vectorized(
        arg_names,
        arg_types,
        propagate_null,
        scalar_text,
        out_dtype,
    )


@numba.generated_jit(nopython=True)
def nullif_util(arr0, arr1):
    """A dedicated kernel for the SQL function NULLIF which takes in two
    scalars (or columns), which returns NULL if the two values are equal, and
    arg0 otherwise.


    Args:
        arg0 (array/series/scalar): The 0-th argument. This value is returned if
            the two arguments are equal.
        arg1 (array/series/scalar): The 1st argument.

    Returns:
        string series/scalar: the string/column of formatted numbers
    """

    arg_names = ["arr0", "arr1"]
    arg_types = [arr0, arr1]
    # If the first argument is NULL, the output is always NULL
    propagate_null = [True, False]
    # NA check needs to come first here, otherwise the equalify check misbehaves

    if arr1 == bodo.none:
        scalar_text = "res[i] = arg0\n"
    elif bodo.utils.utils.is_array_typ(arr1, True):
        scalar_text = "if bodo.libs.array_kernels.isna(arr1, i) or arg0 != arg1:\n"
        scalar_text += "   res[i] = arg0\n"
        scalar_text += "else:\n"
        scalar_text += "   bodo.libs.array_kernels.setna(res, i)"
    else:
        scalar_text = "if arg0 != arg1:\n"
        scalar_text += "   res[i] = arg0\n"
        scalar_text += "else:\n"
        scalar_text += "   bodo.libs.array_kernels.setna(res, i)"

    out_dtype = get_common_broadcasted_type([arr0, arr1], "NULLIF")

    return gen_vectorized(arg_names, arg_types, propagate_null, scalar_text, out_dtype)