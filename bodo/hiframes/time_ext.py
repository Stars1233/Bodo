# Copyright (C) 2022 Bodo Inc. All rights reserved.
"""Numba extension support for time objects and their arrays.
"""
import operator

import llvmlite.binding as ll
import numba
import numpy as np
import pandas as pd
from llvmlite import ir as lir
from numba.core import cgutils, types
from numba.core.imputils import lower_builtin, lower_constant
from numba.extending import (
    NativeValue,
    box,
    intrinsic,
    lower_builtin,
    make_attribute_wrapper,
    models,
    overload,
    overload_attribute,
    overload_method,
    register_model,
    typeof_impl,
    unbox,
)
from numba.parfors.array_analysis import ArrayAnalysis

import bodo
from bodo.libs import hdatetime_ext
from bodo.utils.indexing import (
    array_getitem_bool_index,
    array_getitem_int_index,
    array_getitem_slice_index,
    array_setitem_bool_index,
    array_setitem_int_index,
    array_setitem_slice_index,
)
from bodo.utils.typing import (
    BodoError,
    is_iterable_type,
    is_list_like_index_type,
)

_nanos_per_micro = 1000
_nanos_per_milli = 1000 * _nanos_per_micro
_nanos_per_second = 1000 * _nanos_per_milli
_nanos_per_minute = 60 * _nanos_per_second
_nanos_per_hour = 60 * _nanos_per_minute


class Time:
    def __init__(
        self,
        hour=0,
        minute=0,
        second=0,
        millisecond=0,
        microsecond=0,
        nanosecond=0,
        precision=9,
    ):
        self.precision = precision
        assert all(
            np.issubdtype(type(val), np.integer) or pd.api.types.is_int64_dtype(val)
            for val in (hour, minute, second, millisecond, microsecond, nanosecond)
        ), "All time components must be integers"

        self.value = np.int64(
            hour * _nanos_per_hour
            + minute * _nanos_per_minute
            + second * _nanos_per_second
            + millisecond * _nanos_per_milli
            + microsecond * _nanos_per_micro
            + nanosecond
        )

    def __repr__(self):
        return (
            f"Time({self.hour}, {self.minute}, {self.second}, "
            f"{self.millisecond}, {self.microsecond}, {self.nanosecond}, "
            f"precision={self.precision})"
        )

    def __eq__(self, other):
        if not isinstance(other, Time):  # pragma: no cover
            return False
        return self.value == other.value and self.precision == other.precision

    def _check_can_compare(self, other):
        # raise an error if the precision is not the same to mimic sql time comparison
        if self.precision != other.precision:  # pragma: no cover
            raise BodoError(
                f"Cannot compare times with different precisions: {self} and {other}"
            )

    def __lt__(self, other):
        self._check_can_compare(other)
        return self.value < other.value

    def __le__(self, other):
        self._check_can_compare(other)
        return self.value <= other.value

    def __int__(self):
        """Return the value of the time as an integer in the given precision.
        Used for PyArrow compatibility.
        """
        if self.precision == 9:
            return self.value
        if self.precision == 6:
            return self.value // _nanos_per_micro
        if self.precision == 3:
            return self.value // _nanos_per_milli
        if self.precision == 0:
            return self.value // _nanos_per_second
        raise BodoError(f"Unsupported precision: {self.precision}")

    def __hash__(self):
        return hash((self.value, self.precision))

    @property
    def hour(self):
        return self.value // _nanos_per_hour

    @property
    def minute(self):
        return (self.value % _nanos_per_hour) // _nanos_per_minute

    @property
    def second(self):
        return (self.value % _nanos_per_minute) // _nanos_per_second

    @property
    def millisecond(self):
        return (self.value % _nanos_per_second) // _nanos_per_milli

    @property
    def microsecond(self):
        return (self.value % _nanos_per_milli) // _nanos_per_micro

    @property
    def nanosecond(self):
        return self.value % _nanos_per_micro


def time_from_str(time_str, precision=9):
    """Parse a time string into its components.
    `time_str` is passed in the formats:
    - 'hh:mm:ss'
    - 'hh:mm:ss.mmm'
    - 'hh:mm:SS.mmmμμμ'
    - 'hh:mm:SS.mmmμμμnnn'

    This function is used both by the native Python Time class as well as Bodo's
    compiled Time constructor.
    """
    hour = 0
    minute = 0
    second = 0
    millisecond = 0
    microsecond = 0
    nanosecond = 0
    hour = int(time_str[:2])
    assert time_str[2] == ":", "Invalid time string"
    minute = int(time_str[3:5])
    assert time_str[5] == ":", "Invalid time string"
    second = int(time_str[6:8])

    if len(time_str) > 8:
        assert time_str[8] == ".", "Invalid time string"
        millisecond = int(time_str[9:12])
        if len(time_str) > 12:
            microsecond = int(time_str[12:15])
            if len(time_str) > 15:
                nanosecond = int(time_str[15:18])

    return Time(
        hour,
        minute,
        second,
        millisecond,
        microsecond,
        nanosecond,
        precision=precision,
    )


@overload(time_from_str)
def overload_time_from_str(time_str, precision=9):
    """Overload time_from_str."""

    return time_from_str


ll.add_symbol("box_time_array", hdatetime_ext.box_time_array)
ll.add_symbol("unbox_time_array", hdatetime_ext.unbox_time_array)

# bodo.Time implementation that uses a single int to store hour/minute/second/microsecond/nanosecond
# The precision is saved in it's type
# Does not need refcounted object wrapping since it is immutable
class TimeType(types.Type):
    def __init__(self, precision):
        assert (
            isinstance(precision, int) and precision >= 0 and precision <= 9
        ), "precision must be an integer between 0 and 9"
        self.precision = precision
        super(TimeType, self).__init__(name=f"TimeType({precision})")
        self.bitwidth = 64  # needed for using IntegerModel


@typeof_impl.register(Time)
def typeof_time(val, c):
    return TimeType(val.precision)


@overload(Time)
def overload_time(
    hour=0, min=0, second=0, millisecond=0, microsecond=0, nanosecond=0, precision=9
):
    if (
        isinstance(hour, types.Integer)
        or isinstance(hour, types.IntegerLiteral)
        or hour == 0
    ):

        def impl(
            hour=0,
            min=0,
            second=0,
            millisecond=0,
            microsecond=0,
            nanosecond=0,
            precision=9,
        ):  # pragma: no cover
            return cast_int_to_time(
                _nanos_per_hour * hour
                + _nanos_per_minute * min
                + _nanos_per_second * second
                + _nanos_per_milli * millisecond
                + _nanos_per_micro * microsecond
                + nanosecond,
                precision,
            )

    else:
        raise TypeError(f"Invalid type for Time: {type(hour)}")

    return impl


register_model(TimeType)(models.IntegerModel)


@overload_attribute(TimeType, "hour")
def time_hour_attribute(val):  # pragma: no cover
    return lambda val: cast_time_to_int(val) // _nanos_per_hour


@overload_attribute(TimeType, "minute")
def time_minute_attribute(val):  # pragma: no cover
    return lambda val: cast_time_to_int(val) % _nanos_per_hour // _nanos_per_minute


@overload_attribute(TimeType, "second")
def time_second_attribute(val):  # pragma: no cover
    return lambda val: cast_time_to_int(val) % _nanos_per_minute // _nanos_per_second


@overload_attribute(TimeType, "millisecond")
def time_millisecond_attribute(val):  # pragma: no cover
    return lambda val: cast_time_to_int(val) % _nanos_per_second // _nanos_per_milli


@overload_attribute(TimeType, "microsecond")
def time_microsecond_attribute(val):  # pragma: no cover
    return lambda val: cast_time_to_int(val) % _nanos_per_milli // _nanos_per_micro


@overload_attribute(TimeType, "nanosecond")
def time_nanosecond_attribute(val):  # pragma: no cover
    return lambda val: cast_time_to_int(val) % _nanos_per_micro


def _to_nanos_codegen(
    c, hour_ll, minute_ll, second_ll, millisecond_ll, microsecond_ll, nanosecond_ll
):
    """Generate code to convert time fields to nanos."""
    return c.builder.add(
        nanosecond_ll,
        c.builder.add(
            c.builder.mul(
                microsecond_ll, lir.Constant(lir.IntType(64), _nanos_per_micro)
            ),
            c.builder.add(
                c.builder.mul(
                    millisecond_ll, lir.Constant(lir.IntType(64), _nanos_per_milli)
                ),
                c.builder.add(
                    c.builder.mul(
                        second_ll, lir.Constant(lir.IntType(64), _nanos_per_second)
                    ),
                    c.builder.add(
                        c.builder.mul(
                            minute_ll, lir.Constant(lir.IntType(64), _nanos_per_minute)
                        ),
                        c.builder.mul(
                            hour_ll, lir.Constant(lir.IntType(64), _nanos_per_hour)
                        ),
                    ),
                ),
            ),
        ),
    )


def _from_nanos_codegen(c, val):
    """Generate code to convert nanos to time fields."""
    hour_obj = c.pyapi.long_from_longlong(
        c.builder.udiv(val, lir.Constant(lir.IntType(64), _nanos_per_hour))
    )
    minute_obj = c.pyapi.long_from_longlong(
        c.builder.udiv(
            c.builder.urem(val, lir.Constant(lir.IntType(64), _nanos_per_hour)),
            lir.Constant(lir.IntType(64), _nanos_per_minute),
        )
    )
    second_obj = c.pyapi.long_from_longlong(
        c.builder.udiv(
            c.builder.urem(val, lir.Constant(lir.IntType(64), _nanos_per_minute)),
            lir.Constant(lir.IntType(64), _nanos_per_second),
        )
    )
    millisecond_obj = c.pyapi.long_from_longlong(
        c.builder.udiv(
            c.builder.urem(val, lir.Constant(lir.IntType(64), _nanos_per_second)),
            lir.Constant(lir.IntType(64), _nanos_per_milli),
        )
    )
    microsecond_obj = c.pyapi.long_from_longlong(
        c.builder.udiv(
            c.builder.urem(val, lir.Constant(lir.IntType(64), _nanos_per_milli)),
            lir.Constant(lir.IntType(64), _nanos_per_micro),
        )
    )
    nanosecond_obj = c.pyapi.long_from_longlong(
        c.builder.urem(val, lir.Constant(lir.IntType(64), _nanos_per_micro))
    )

    return (
        hour_obj,
        minute_obj,
        second_obj,
        millisecond_obj,
        microsecond_obj,
        nanosecond_obj,
    )


@unbox(TimeType)
def unbox_time(typ, val, c):
    """Convert a time object to its Bodo representation as a nanoseconds integer."""

    hour_obj = c.pyapi.object_getattr_string(val, "hour")
    minute_obj = c.pyapi.object_getattr_string(val, "minute")
    second_obj = c.pyapi.object_getattr_string(val, "second")
    millisecond_obj = c.pyapi.object_getattr_string(val, "millisecond")
    microsecond_obj = c.pyapi.object_getattr_string(val, "microsecond")
    nanosecond_obj = c.pyapi.object_getattr_string(val, "nanosecond")

    hour_ll = c.pyapi.long_as_longlong(hour_obj)
    minute_ll = c.pyapi.long_as_longlong(minute_obj)
    second_ll = c.pyapi.long_as_longlong(second_obj)
    millisecond_ll = c.pyapi.long_as_longlong(millisecond_obj)
    microsecond_ll = c.pyapi.long_as_longlong(microsecond_obj)
    nanosecond_ll = c.pyapi.long_as_longlong(nanosecond_obj)

    nopython_time = _to_nanos_codegen(
        c, hour_ll, minute_ll, second_ll, millisecond_ll, microsecond_ll, nanosecond_ll
    )

    c.pyapi.decref(hour_obj)
    c.pyapi.decref(minute_obj)
    c.pyapi.decref(second_obj)
    c.pyapi.decref(millisecond_obj)
    c.pyapi.decref(microsecond_obj)
    c.pyapi.decref(nanosecond_obj)

    is_error = cgutils.is_not_null(c.builder, c.pyapi.err_occurred())
    return NativeValue(nopython_time, is_error=is_error)


@lower_constant(TimeType)
def lower_constant_time(context, builder, ty, pyval):  # pragma: no cover
    """Convert a constant Python time object to its Bodo representation as a nanoseconds integer."""
    hour_ll = context.get_constant(types.int64, pyval.hour)
    minute_ll = context.get_constant(types.int64, pyval.minute)
    second_ll = context.get_constant(types.int64, pyval.second)
    millisecond_ll = context.get_constant(types.int64, pyval.millisecond)
    microsecond_ll = context.get_constant(types.int64, pyval.microsecond)
    nanosecond_ll = context.get_constant(types.int64, pyval.nanosecond)

    nopython_time = _to_nanos_codegen(
        context,
        hour_ll,
        minute_ll,
        second_ll,
        millisecond_ll,
        microsecond_ll,
        nanosecond_ll,
    )

    return nopython_time


@box(TimeType)
def box_time(typ, val, c):
    """Convert a time Bodo representation as a nanoseconds integer to a Python time object."""
    (
        hour_obj,
        minute_obj,
        second_obj,
        millisecond_obj,
        microsecond_obj,
        nanosecond_obj,
    ) = _from_nanos_codegen(c, val)

    dt_obj = c.pyapi.unserialize(c.pyapi.serialize_object(Time))
    res = c.pyapi.call_function_objargs(
        dt_obj,
        (
            hour_obj,
            minute_obj,
            second_obj,
            millisecond_obj,
            microsecond_obj,
            nanosecond_obj,
            c.pyapi.long_from_longlong(lir.Constant(lir.IntType(64), typ.precision)),
        ),
    )
    c.pyapi.decref(hour_obj)
    c.pyapi.decref(minute_obj)
    c.pyapi.decref(second_obj)
    c.pyapi.decref(millisecond_obj)
    c.pyapi.decref(microsecond_obj)
    c.pyapi.decref(nanosecond_obj)
    c.pyapi.decref(dt_obj)
    return res


@lower_builtin(
    Time,
    types.int64,
    types.int64,
    types.int64,
    types.int64,
    types.int64,
    types.int64,
)
def impl_ctor_time(context, builder, sig, args):  # pragma: no cover
    """Constructor for a Time created within Bodo code."""
    hour_ll, minute_ll, second_ll, millisecond_ll, microsecond_ll, nanosecond_ll = args
    nopython_time = _to_nanos_codegen(
        context,
        hour_ll,
        minute_ll,
        second_ll,
        millisecond_ll,
        microsecond_ll,
        nanosecond_ll,
    )
    return nopython_time


@intrinsic
def cast_int_to_time(typingctx, val, precision):
    """Cast int value to Time"""
    assert types.unliteral(val) == types.int64, "val must be int64"
    assert isinstance(
        precision, types.IntegerLiteral
    ), "precision must be an integer literal"

    def codegen(context, builder, signature, args):
        return args[0]

    return TimeType(precision.literal_value)(types.int64, types.int64), codegen


@intrinsic
def cast_time_to_int(typingctx, val):
    """Cast Time value to int"""
    assert isinstance(val, TimeType), "val must be Time"

    def codegen(context, builder, signature, args):
        return args[0]

    return types.int64(val), codegen


##################### Array of Time objects ##########################


class TimeArrayType(types.ArrayCompatible):
    def __init__(self, precision):
        assert (
            isinstance(precision, int) and precision >= 0 and precision <= 9
        ), "precision must be an integer between 0 and 9"
        self.precision = precision
        super(TimeArrayType, self).__init__(name=f"TimeArrayType({precision})")

    @property
    def as_array(self):
        return types.Array(types.undefined, 1, "C")

    @property
    def dtype(self):
        return TimeType(self.precision)

    def copy(self):
        return TimeArrayType(self.precision)


data_type = types.Array(types.int64, 1, "C")
nulls_type = types.Array(types.uint8, 1, "C")

# Time array has only an array integers to store data
@register_model(TimeArrayType)
class TimeArrayModel(models.StructModel):
    def __init__(self, dmm, fe_type):
        members = [
            ("data", data_type),
            ("null_bitmap", nulls_type),
        ]
        models.StructModel.__init__(self, dmm, fe_type, members)


make_attribute_wrapper(TimeArrayType, "data", "_data")
make_attribute_wrapper(TimeArrayType, "null_bitmap", "_null_bitmap")


@overload_method(TimeArrayType, "copy", no_unliteral=True)
def overload_time_arr_copy(A):
    """Copy a TimeArrayType by copying the underlying data and null bitmap"""
    return lambda A: bodo.hiframes.time_ext.init_time_array(
        A._data.copy(),
        A._null_bitmap.copy(),
    )  # pragma: no cover


@overload_attribute(TimeArrayType, "dtype")
def overload_time_arr_dtype(A):
    """Return the dtype of the TimeArrayType, which is a numpy object because
    the time is stored in a numpy array
    """
    return lambda A: np.object_  # pragma: no cover


@unbox(TimeArrayType)
def unbox_time_array(typ, val, c):
    """Unbox a numpy array of time objects to a TimeArrayType"""
    n = bodo.utils.utils.object_length(c, val)
    arr_typ = types.Array(types.intp, 1, "C")
    data_arr = bodo.utils.utils._empty_nd_impl(c.context, c.builder, arr_typ, [n])
    n_bitmask_bytes = c.builder.udiv(
        c.builder.add(n, lir.Constant(lir.IntType(64), 7)),
        lir.Constant(lir.IntType(64), 8),
    )
    bitmap_arr = bodo.utils.utils._empty_nd_impl(
        c.context, c.builder, types.Array(types.uint8, 1, "C"), [n_bitmask_bytes]
    )

    # function signature of unbox_time_array
    fnty = lir.FunctionType(
        lir.VoidType(),
        [
            lir.IntType(8).as_pointer(),
            lir.IntType(64),
            lir.IntType(64).as_pointer(),
            lir.IntType(8).as_pointer(),
        ],
    )
    fn = cgutils.get_or_insert_function(c.builder.module, fnty, name="unbox_time_array")
    c.builder.call(fn, [val, n, data_arr.data, bitmap_arr.data])

    out_dt_time_arr = cgutils.create_struct_proxy(typ)(c.context, c.builder)
    out_dt_time_arr.data = data_arr._getvalue()
    out_dt_time_arr.null_bitmap = bitmap_arr._getvalue()

    is_error = cgutils.is_not_null(c.builder, c.pyapi.err_occurred())
    return NativeValue(out_dt_time_arr._getvalue(), is_error=is_error)


@box(TimeArrayType)
def box_time_array(typ, val, c):
    """Box a TimeArrayType to a numpy array of time objects"""
    in_arr = cgutils.create_struct_proxy(typ)(c.context, c.builder, val)

    data_arr = c.context.make_array(types.Array(types.int64, 1, "C"))(
        c.context, c.builder, in_arr.data
    )
    bitmap_arr_data = c.context.make_array(types.Array(types.uint8, 1, "C"))(
        c.context, c.builder, in_arr.null_bitmap
    ).data

    n = c.builder.extract_value(data_arr.shape, 0)

    fnty = lir.FunctionType(
        c.pyapi.pyobj,
        [
            lir.IntType(64),
            lir.IntType(64).as_pointer(),
            lir.IntType(8).as_pointer(),
            lir.IntType(8),
        ],
    )
    fn_get = cgutils.get_or_insert_function(
        c.builder.module, fnty, name="box_time_array"
    )
    obj_arr = c.builder.call(
        fn_get,
        [
            n,
            data_arr.data,
            bitmap_arr_data,
            lir.Constant(lir.IntType(8), typ.precision),
        ],
    )

    c.context.nrt.decref(c.builder, typ, val)
    return obj_arr


@intrinsic
def init_time_array(typingctx, data, nulls, precision):
    """Create a TimeArrayType with provided data values."""
    assert data == types.Array(types.int64, 1, "C"), "data must be an array of int64"
    assert nulls == types.Array(types.uint8, 1, "C"), "nulls must be an array of uint8"
    assert isinstance(
        precision, types.IntegerLiteral
    ), "precision must be an integer literal"

    def codegen(context, builder, signature, args):
        (data_val, bitmap_val, _) = args
        # create arr struct and store values
        dt_time_arr = cgutils.create_struct_proxy(signature.return_type)(
            context, builder
        )
        dt_time_arr.data = data_val
        dt_time_arr.null_bitmap = bitmap_val

        # increase refcount of stored values
        context.nrt.incref(builder, signature.args[0], data_val)
        context.nrt.incref(builder, signature.args[1], bitmap_val)

        return dt_time_arr._getvalue()

    sig = TimeArrayType(precision.literal_value)(data, nulls, precision)
    return sig, codegen


@lower_constant(TimeArrayType)
def lower_constant_time_arr(context, builder, typ, pyval):  # pragma: no cover
    """Lower a constant TimeArrayType to a numpy array of time objects"""
    n = len(pyval)
    data_arr = np.full(n, 0, np.int64)
    nulls_arr = np.empty((n + 7) >> 3, np.uint8)

    for i, s in enumerate(pyval):
        is_na = pd.isna(s)
        bodo.libs.int_arr_ext.set_bit_to_arr(nulls_arr, i, int(not is_na))
        if not is_na:
            data_arr[i] = (
                s.hour * _nanos_per_hour
                + s.minute * _nanos_per_minute
                + s.second * _nanos_per_second
                + s.millisecond * _nanos_per_milli
                + s.microsecond * _nanos_per_micro
                + s.nanosecond
            )

    data_const_arr = context.get_constant_generic(builder, data_type, data_arr)
    nulls_const_arr = context.get_constant_generic(builder, nulls_type, nulls_arr)

    # create time arr struct
    return lir.Constant.literal_struct([data_const_arr, nulls_const_arr])


@numba.njit(no_cpython_wrapper=True)
def alloc_time_array(n, precision):  # pragma: no cover
    """Allocate a TimeArrayType with n elements"""
    data_arr = np.empty(n, dtype=np.int64)
    nulls = np.empty((n + 7) >> 3, dtype=np.uint8)
    return init_time_array(data_arr, nulls, precision)


def alloc_time_array_equiv(self, scope, equiv_set, loc, args, kws):
    """Array analysis function for alloc_time_array() passed to Numba's array
    analysis extension. Assigns output array's size as equivalent to the input size
    variable.
    """
    assert len(args) == 2 and not kws, "alloc_time_array() takes two arguments"
    return ArrayAnalysis.AnalyzeResult(shape=args[0], pre=[])


ArrayAnalysis._analyze_op_call_bodo_hiframes_time_ext_alloc_time_array = (
    alloc_time_array_equiv
)


@overload(operator.getitem, no_unliteral=True)
def time_arr_getitem(A, ind):  # pragma: no cover
    """Overload getitem for TimeArrayType"""
    if not isinstance(A, TimeArrayType):
        return
    precision = A.precision

    if isinstance(types.unliteral(ind), types.Integer):
        return lambda A, ind: cast_int_to_time(
            A._data[ind], precision
        )  # pragma: no cover

    # bool arr indexing
    if is_list_like_index_type(ind) and ind.dtype == types.bool_:

        def impl_bool(A, ind):  # pragma: no cover
            new_data, new_mask = array_getitem_bool_index(A, ind)
            return init_time_array(new_data, new_mask, precision)

        return impl_bool

    # int arr indexing
    if is_list_like_index_type(ind) and isinstance(ind.dtype, types.Integer):

        def impl(A, ind):  # pragma: no cover
            new_data, new_mask = array_getitem_int_index(A, ind)
            return init_time_array(new_data, new_mask, precision)

        return impl

    # slice case
    if isinstance(ind, types.SliceType):

        def impl_slice(A, ind):  # pragma: no cover
            new_data, new_mask = array_getitem_slice_index(A, ind)
            return init_time_array(new_data, new_mask, precision)

        return impl_slice

    # This should be the only TimeArray implementation.
    # We only expect to reach this case if more idx options are added.
    raise BodoError(
        f"getitem for TimeArray with indexing type {ind} not supported."
    )  # pragma: no cover


@overload(operator.setitem, no_unliteral=True)
def time_arr_setitem(A, idx, val):  # pragma: no cover
    """Overload setitem for TimeArrayType"""
    if not isinstance(A, TimeArrayType):
        return

    if val == types.none or isinstance(val, types.optional):  # pragma: no cover
        # None/Optional goes through a separate step.
        return

    typ_err_msg = f"setitem for TimeArray with indexing type {idx} received an incorrect 'value' type {val}."

    # scalar case
    if isinstance(idx, types.Integer):

        if isinstance(types.unliteral(val), TimeType):

            def impl(A, idx, val):  # pragma: no cover
                A._data[idx] = cast_time_to_int(val)
                bodo.libs.int_arr_ext.set_bit_to_arr(A._null_bitmap, idx, 1)

            # Covered by test_series_iat_setitem , test_series_iloc_setitem_int , test_series_setitem_int
            return impl

        else:
            raise BodoError(typ_err_msg)

    if not (
        (is_iterable_type(val) and isinstance(val.dtype, TimeType))
        or isinstance(types.unliteral(val), TimeType)
    ):
        raise BodoError(typ_err_msg)

    # array of integers
    if is_list_like_index_type(idx) and isinstance(idx.dtype, types.Integer):

        if isinstance(types.unliteral(val), TimeType):
            return lambda A, idx, val: array_setitem_int_index(
                A, idx, cast_time_to_int(val)
            )  # pragma: no cover

        def impl_arr_ind(A, idx, val):  # pragma: no cover
            array_setitem_int_index(A, idx, val)

        # covered by test_series_iloc_setitem_list_int
        return impl_arr_ind

    # bool array
    if is_list_like_index_type(idx) and idx.dtype == types.bool_:

        if isinstance(types.unliteral(val), TimeType):
            return lambda A, idx, val: array_setitem_bool_index(
                A, idx, cast_time_to_int(val)
            )  # pragma: no cover

        def impl_bool_ind_mask(A, idx, val):  # pragma: no cover
            array_setitem_bool_index(A, idx, val)

        return impl_bool_ind_mask

    # slice case
    if isinstance(idx, types.SliceType):

        if isinstance(types.unliteral(val), TimeType):
            return lambda A, idx, val: array_setitem_slice_index(
                A, idx, cast_time_to_int(val)
            )  # pragma: no cover

        def impl_slice_mask(A, idx, val):  # pragma: no cover
            array_setitem_slice_index(A, idx, val)

        # covered by test_series_setitem_slice
        return impl_slice_mask

    # This should be the only TimeArray implementation.
    # We only expect to reach this case if more idx options are added.
    raise BodoError(
        f"setitem for TimeArray with indexing type {idx} not supported."
    )  # pragma: no cover


@overload(len, no_unliteral=True)
def overload_len_time_arr(A):
    """Overload len for TimeArrayType by returning the length of the underlying
    data array
    """
    if isinstance(A, TimeArrayType):
        return lambda A: len(A._data)  # pragma: no cover


@overload_attribute(TimeArrayType, "shape")
def overload_time_arr_shape(A):
    """Overload shape for TimeArrayType by returning the shape of the underlying
    data array
    """
    return lambda A: (len(A._data),)  # pragma: no cover


@overload_attribute(TimeArrayType, "nbytes")
def time_arr_nbytes_overload(A):
    """Overload nbytes for TimeArrayType"""
    return lambda A: A._data.nbytes + A._null_bitmap.nbytes  # pragma: no cover


def create_cmp_op_overload(op):
    """create overload function for comparison operators with Time type."""

    def overload_time_cmp(lhs, rhs):
        if isinstance(lhs, TimeType) and isinstance(rhs, TimeType):

            def impl(lhs, rhs):  # pragma: no cover
                x = cast_time_to_int(lhs)
                y = cast_time_to_int(rhs)
                return op(0 if x == y else 1 if x > y else -1, 0)

            return impl

    return overload_time_cmp