# Copyright (C) 2023 Bodo Inc. All rights reserved.
"""Array implementation for null array type. This is an array that contains
all null values and can be cast to any other array type.
"""
from llvmlite import ir as lir
from numba.core import cgutils, types
from numba.extending import (
    intrinsic,
    make_attribute_wrapper,
    models,
    overload,
    overload_attribute,
    overload_method,
    register_model,
)
from numba.parfors.array_analysis import ArrayAnalysis

import bodo
from bodo.utils.typing import (
    dtype_to_array_type,
    is_scalar_type,
    to_nullable_type,
    unwrap_typeref,
)


class NullDType(types.Type):
    """
    Type that can be used to represent a null value
    that can be cast to any type.
    """

    def __init__(self):
        super(NullDType, self).__init__(name="NullType()")


null_dtype = NullDType()

# The null dtype is just used to represent a null value in typing
register_model(NullDType)(models.OpaqueModel)


class NullArrayType(types.IterableType, types.ArrayCompatible):
    def __init__(self):
        super(NullArrayType, self).__init__(name="NullArrayType()")

    @property
    def as_array(self):
        return types.Array(types.undefined, 1, "C")

    @property
    def dtype(self):
        return null_dtype

    def copy(self):
        return NullArrayType()

    @property
    def iterator_type(self):
        return bodo.utils.typing.BodoArrayIterator(self)


null_array_type = NullArrayType()


# store the length of the array as the struct since all values are null
@register_model(NullArrayType)
class NullArrayModel(models.StructModel):
    def __init__(self, dmm, fe_type):
        members = [
            ("length", types.int64),
            # Keep an extra field that is always 1 so we can determine
            # if the struct is null or not. We use context.get_constant_null
            # inside ensure_column_unboxed and this will become all 0s.
            # https://github.com/Bodo-inc/Bodo/blob/3108eb47a7a79861739b1ae3a4939c1525ef16ae/bodo/hiframes/table.py#L1195
            # https://github.com/numba/numba/blob/135d15047c5237f751d4b81347effe2a3704288b/numba/core/base.py#L522
            # https://github.com/numba/llvmlite/blob/dffe582d6080494ba8e39689d09aacde1952214c/llvmlite/ir/values.py#L457
            # https://github.com/numba/llvmlite/blob/dffe582d6080494ba8e39689d09aacde1952214c/llvmlite/ir/types.py#L545
            ("not_empty", types.boolean),
        ]
        models.StructModel.__init__(self, dmm, fe_type, members)


make_attribute_wrapper(NullArrayType, "length", "_length")


@intrinsic
def init_null_array(typingctx, length_t):
    """Create a null array with the provided length."""
    assert types.unliteral(length_t) == types.int64, "length must be an int64"

    def codegen(context, builder, signature, args):
        (length,) = args
        # create null_arr struct and store values
        null_arr = cgutils.create_struct_proxy(signature.return_type)(context, builder)
        null_arr.length = length
        null_arr.not_empty = lir.Constant(lir.IntType(1), 1)
        return null_arr._getvalue()

    sig = null_array_type(types.int64)
    return sig, codegen


def init_null_array_equiv(self, scope, equiv_set, loc, args, kws):
    """
    Array analysis for init_null_array. The shape is just the first argument.
    """
    assert len(args) == 1 and not kws
    var = args[0]
    return ArrayAnalysis.AnalyzeResult(shape=var, pre=[])


ArrayAnalysis._analyze_op_call_bodo_libs_null_arr_ext_init_null_array = (
    init_null_array_equiv
)


@overload(len, no_unliteral=True)
def overload_null_arr_len(A):
    if A == null_array_type:
        return lambda A: A._length  # pragma: no cover


@overload_attribute(NullArrayType, "shape")
def overload_null_arr_shape(A):
    return lambda A: (A._length,)  # pragma: no cover


@overload_attribute(NullArrayType, "ndim")
def overload_null_arr_ndim(A):
    return lambda A: 1  # pragma: no cover


@overload_attribute(NullArrayType, "nbytes")
def overload_null_nbytes(A):
    # A null array always takes exactly 8 bytes
    return lambda A: 8  # pragma: no cover


@overload_method(NullArrayType, "copy")
def overload_null_copy(A):
    # Just return the same array since this array is immutable
    return lambda A: A  # pragma: no cover


@overload_method(NullArrayType, "astype", no_unliteral=True)
def overload_null_astype(A, dtype, copy=True):
    # Note we ignore the copy argument since this array
    # always requires a copy.
    new_dtype = unwrap_typeref(dtype)
    if bodo.utils.utils.is_array_typ(new_dtype, False):
        # Some internal types (e.g. Dictionary encode arrays)
        # must be passed as array types and not dtypes.
        nb_dtype = new_dtype
    else:
        nb_dtype = bodo.utils.typing.parse_dtype(new_dtype)
    if isinstance(
        nb_dtype,
        (bodo.libs.int_arr_ext.IntDtype, bodo.libs.float_arr_ext.FloatDtype),
    ):
        dtype = nb_dtype.dtype
    else:
        dtype = nb_dtype
    if is_scalar_type(dtype):
        dtype = dtype_to_array_type(dtype)
    _arr_typ = to_nullable_type(dtype)
    if _arr_typ == null_array_type:
        return lambda A, dtype, copy=True: A  # pragma: no cover
    else:

        def impl(A, dtype, copy=True):  # pragma: no cover
            return bodo.libs.array_kernels.gen_na_array(A._length, _arr_typ, True)

        return impl