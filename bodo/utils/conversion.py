# Copyright (C) 2019 Bodo Inc. All rights reserved.
"""
Utility functions for conversion of data such as list to array.
Need to be inlined for better optimization.
"""
import pandas as pd
import numpy as np
import numba
from numba.core import types
from bodo.libs.decimal_arr_ext import Decimal128Type, DecimalArrayType
from numba.extending import overload
import bodo
from bodo.utils.typing import (
    is_overload_none,
    is_overload_true,
    BodoError,
    get_overload_const_str,
    is_overload_constant_str,
)


NS_DTYPE = np.dtype("M8[ns]")  # similar pandas/_libs/tslibs/conversion.pyx
TD_DTYPE = np.dtype("m8[ns]")


# TODO: use generated_jit with IR inlining
def coerce_to_ndarray(
    data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None
):  # pragma: no cover
    return data


@overload(coerce_to_ndarray, no_unliteral=True)
def overload_coerce_to_ndarray(
    data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None
):
    # TODO: other cases handled by this function in Pandas like scalar
    """
    Coerces data to ndarray. Data should be numeric.
    """
    from bodo.hiframes.pd_series_ext import SeriesType
    from bodo.hiframes.pd_index_ext import (
        RangeIndexType,
        NumericIndexType,
        DatetimeIndexType,
        TimedeltaIndexType,
    )

    # TODO: handle NAs?
    if isinstance(data, bodo.libs.int_arr_ext.IntegerArrayType):
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.int_arr_ext.get_int_arr_data(
            data
        )  # pragma: no cover

    if data == bodo.libs.bool_arr_ext.boolean_array:
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.bool_arr_ext.get_bool_arr_data(
            data
        )  # pragma: no cover

    if isinstance(data, types.Array):
        if not is_overload_none(use_nullable_array) and isinstance(
            data.dtype, (types.Boolean, types.Integer)
        ):
            if data.dtype == types.bool_:
                if data.layout != "C":
                    return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.bool_arr_ext.init_bool_array(
                        np.ascontiguousarray(data),
                        np.full((len(data) + 7) >> 3, 255, np.uint8),
                    )  # pragma: no cover
                else:
                    return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.bool_arr_ext.init_bool_array(
                        data, np.full((len(data) + 7) >> 3, 255, np.uint8)
                    )  # pragma: no cover
            else:  # Integer case
                if data.layout != "C":
                    return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.int_arr_ext.init_integer_array(
                        np.ascontiguousarray(data),
                        np.full((len(data) + 7) >> 3, 255, np.uint8),
                    )  # pragma: no cover
                else:
                    return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.int_arr_ext.init_integer_array(
                        data, np.full((len(data) + 7) >> 3, 255, np.uint8)
                    )  # pragma: no cover
        if data.layout != "C":
            return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: np.ascontiguousarray(
                data
            )  # pragma: no cover
        return (
            lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: data
        )  # pragma: no cover

    if isinstance(data, (types.List, types.UniTuple)):

        if not is_overload_none(use_nullable_array) and isinstance(
            data.dtype, (types.Boolean, types.Integer)
        ):
            if data.dtype == types.bool_:
                return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.bool_arr_ext.init_bool_array(
                    np.asarray(data), np.full((len(data) + 7) >> 3, 255, np.uint8)
                )  # pragma: no cover
            else:  # Integer case
                return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.int_arr_ext.init_integer_array(
                    np.asarray(data), np.full((len(data) + 7) >> 3, 255, np.uint8)
                )  # pragma: no cover
        # convert Timestamp() back to dt64
        if data.dtype == bodo.hiframes.pd_timestamp_ext.pandas_timestamp_type:

            def impl(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                vals = []
                for d in data:
                    vals.append(bodo.hiframes.pd_timestamp_ext.integer_to_dt64(d.value))
                return np.asarray(vals)

            return impl

        if isinstance(data.dtype, Decimal128Type):
            precision = data.dtype.precision
            scale = data.dtype.scale

            def impl(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                n = len(data)
                A = bodo.libs.decimal_arr_ext.alloc_decimal_array(n, precision, scale)
                for i, d in enumerate(data):
                    A._data[i] = bodo.libs.decimal_arr_ext.decimal128type_to_int128(d)
                    bodo.libs.int_arr_ext.set_bit_to_arr(A._null_bitmap, i, 1)

                return A

            return impl

        if data.dtype == bodo.hiframes.datetime_date_ext.datetime_date_type:

            def impl(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                n = len(data)
                A = bodo.hiframes.datetime_date_ext.alloc_datetime_date_array(n)
                for i, d in enumerate(data):
                    A[i] = d
                return A

            return impl

        if data.dtype == bodo.hiframes.datetime_timedelta_ext.datetime_timedelta_type:

            def impl(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                n = len(data)
                A = bodo.hiframes.datetime_timedelta_ext.alloc_datetime_timedelta_array(
                    n
                )
                for i, d in enumerate(data):
                    A[i] = d
                return A

            return impl

        if not is_overload_none(use_nullable_array) and data.dtype == types.bool_:
            return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.bool_arr_ext.init_bool_array(
                np.asarray(data), np.full((len(data) + 7) >> 3, 255, np.uint8)
            )
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: np.asarray(
            data
        )  # pragma: no cover

    if isinstance(data, SeriesType):
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.hiframes.pd_series_ext.get_series_data(
            data
        )  # pragma: no cover

    # index types
    if isinstance(data, (NumericIndexType, DatetimeIndexType, TimedeltaIndexType)):
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.hiframes.pd_index_ext.get_index_data(
            data
        )  # pragma: no cover

    if isinstance(data, RangeIndexType):
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: np.arange(
            data._start, data._stop, data._step
        )  # pragma: no cover

    # convert scalar to ndarray
    # TODO: make sure scalar is a Numpy dtype
    if not is_overload_none(scalar_to_arr_len):

        if isinstance(data, Decimal128Type):
            precision = data.precision
            scale = data.scale

            def impl_ts(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                n = scalar_to_arr_len
                A = bodo.libs.decimal_arr_ext.alloc_decimal_array(n, precision, scale)
                for i in numba.parfors.parfor.internal_prange(n):
                    A[i] = data
                return A

            return impl_ts

        if data == bodo.hiframes.datetime_timedelta_ext.datetime_timedelta_type:
            timedelta64_dtype = np.dtype("timedelta64[ns]")

            def impl_ts(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                n = scalar_to_arr_len
                A = np.empty(n, timedelta64_dtype)
                td64 = bodo.hiframes.pd_timestamp_ext.datetime_timedelta_to_timedelta64(
                    data
                )
                for i in numba.parfors.parfor.internal_prange(n):
                    A[i] = td64
                return A

            return impl_ts

        if data == bodo.hiframes.datetime_datetime_ext.datetime_datetime_type:
            dt64_dtype = np.dtype("datetime64[ns]")

            def impl_ts(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                n = scalar_to_arr_len
                A = np.empty(n, dt64_dtype)
                v = bodo.hiframes.pd_timestamp_ext.datetime_datetime_to_dt64(data)
                v_ret = bodo.hiframes.pd_timestamp_ext.integer_to_dt64(v)
                for i in numba.parfors.parfor.internal_prange(n):
                    A[i] = v_ret
                return A

            return impl_ts

        if data == bodo.hiframes.datetime_date_ext.datetime_date_type:

            def impl_ts(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                n = scalar_to_arr_len
                A = bodo.hiframes.datetime_date_ext.alloc_datetime_date_array(n)
                for i in numba.parfors.parfor.internal_prange(n):
                    A[i] = data
                return A

            return impl_ts

        # Timestamp values are stored as dt64 arrays
        if data == bodo.hiframes.pd_timestamp_ext.pandas_timestamp_type:
            dt64_dtype = np.dtype("datetime64[ns]")

            def impl_ts(
                data,
                error_on_nonarray=True,
                use_nullable_array=None,
                scalar_to_arr_len=None,
            ):  # pragma: no cover
                n = scalar_to_arr_len
                A = np.empty(scalar_to_arr_len, dt64_dtype)
                v = bodo.hiframes.pd_timestamp_ext.integer_to_dt64(data.value)
                for i in numba.parfors.parfor.internal_prange(n):
                    A[i] = v
                return A

            return impl_ts

        dtype = types.unliteral(data)

        def impl_num(
            data,
            error_on_nonarray=True,
            use_nullable_array=None,
            scalar_to_arr_len=None,
        ):  # pragma: no cover
            # TODO: parallelize np.full in PA
            # return np.full(scalar_to_arr_len, data)
            numba.parfors.parfor.init_prange()
            n = scalar_to_arr_len
            out_arr = np.empty(n, dtype)
            for i in numba.parfors.parfor.internal_prange(n):
                out_arr[i] = data
            return out_arr

        return impl_num

    if is_overload_true(error_on_nonarray):
        raise BodoError("cannot coerce {} to array".format(data))

    return (
        lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: data
    )  # pragma: no cover


# TODO: use generated_jit with IR inlining
def coerce_to_array(
    data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None
):  # pragma: no cover
    return data


@overload(coerce_to_array, no_unliteral=True)
def overload_coerce_to_array(
    data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None
):
    """
    convert data to Bodo arrays.
    use_nullable_array=True returns nullable boolean/int arrays instead of Numpy arrays.
    """
    # TODO: support other arrays like list(str), datetime.date ...
    from bodo.hiframes.pd_series_ext import is_str_series_typ
    from bodo.hiframes.pd_index_ext import StringIndexType

    # string series
    if is_str_series_typ(data):
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.hiframes.pd_series_ext.get_series_data(
            data
        )  # pragma: no cover

    if isinstance(data, StringIndexType):
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.hiframes.pd_index_ext.get_index_data(
            data
        )  # pragma: no cover

    # string array
    if data == bodo.string_array_type:
        return (
            lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: data
        )  # pragma: no cover

    # string list
    if isinstance(data, types.List) and data.dtype == bodo.string_type:
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.str_arr_ext.str_arr_from_sequence(
            data
        )  # pragma: no cover

    # string tuple
    if (
        isinstance(data, types.UniTuple)
        and isinstance(data.dtype, (types.UnicodeType, types.StringLiteral))
    ) or (
        isinstance(data, types.BaseTuple)
        and all(isinstance(t, types.StringLiteral) for t in data.types)
    ):
        return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.libs.str_arr_ext.str_arr_from_sequence(
            list(data)
        )  # pragma: no cover

    if data in (
        bodo.libs.bool_arr_ext.boolean_array,
        bodo.hiframes.datetime_date_ext.datetime_date_array_type,
        bodo.hiframes.datetime_timedelta_ext.datetime_timedelta_array_type,
        bodo.hiframes.split_impl.string_array_split_view_type,
    ) or isinstance(data, (bodo.libs.int_arr_ext.IntegerArrayType, DecimalArrayType)):
        return (
            lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: data
        )  # pragma: no cover

    # string scalars to array
    if not is_overload_none(scalar_to_arr_len) and isinstance(
        data, (types.UnicodeType, types.StringLiteral)
    ):

        def impl_str(
            data,
            error_on_nonarray=True,
            use_nullable_array=None,
            scalar_to_arr_len=None,
        ):  # pragma: no cover
            n = scalar_to_arr_len
            n_chars = n * bodo.libs.str_arr_ext.get_utf8_size(data)
            A = bodo.libs.str_arr_ext.pre_alloc_string_array(n, n_chars)
            for i in numba.parfors.parfor.internal_prange(n):
                A[i] = data
            return A

        return impl_str

    # assuming can be ndarray
    return lambda data, error_on_nonarray=True, use_nullable_array=None, scalar_to_arr_len=None: bodo.utils.conversion.coerce_to_ndarray(
        data, error_on_nonarray, use_nullable_array, scalar_to_arr_len
    )  # pragma: no cover


# TODO: use generated_jit with IR inlining
def fix_arr_dtype(data, new_dtype, copy=None):  # pragma: no cover
    return data


@overload(fix_arr_dtype, no_unliteral=True)
def overload_fix_arr_dtype(data, new_dtype, copy=None):
    """convert data to new_dtype, copy if copy parameter is not None
    """
    # TODO: support copy=True and copy=False when literals are passed reliably
    do_copy = not is_overload_none(copy)

    if is_overload_none(new_dtype):
        if do_copy:
            return lambda data, new_dtype, copy=None: data.copy()  # pragma: no cover
        return lambda data, new_dtype, copy=None: data  # pragma: no cover

    # convert to Categorical with predefined CategoricalDtype
    if isinstance(new_dtype, bodo.hiframes.pd_categorical_ext.PDCategoricalDtype):
        int_dtype = bodo.hiframes.pd_categorical_ext.get_categories_int_type(new_dtype)

        def impl_cat_dtype(data, new_dtype, copy=None):  # pragma: no cover
            n = len(data)
            numba.parfors.parfor.init_prange()
            label_dict = bodo.hiframes.pd_categorical_ext.get_label_dict_from_categories(
                new_dtype.categories
            )
            codes = np.empty(n, int_dtype)
            for i in numba.parfors.parfor.internal_prange(n):
                if bodo.libs.array_kernels.isna(data, i):
                    codes[i] = -1
                    continue
                val = data[i]
                if val not in label_dict:
                    codes[i] = -1
                    continue
                codes[i] = label_dict[val]
            A = bodo.hiframes.pd_categorical_ext.init_categorical_array(
                codes, new_dtype
            )
            return A

        return impl_cat_dtype

    if (
        is_overload_constant_str(new_dtype)
        and get_overload_const_str(new_dtype) == "category"
    ):
        # find categorical dtype from data first and reuse the explicit dtype impl
        def impl_category(data, new_dtype, copy=None):  # pragma: no cover
            # find categories in data
            cats = bodo.libs.array_kernels.unique(data)
            # make sure categories are replicated since dtype is replicated
            cats = bodo.allgatherv(cats, False)
            # sort categories to match Pandas
            cats = pd.Series(cats).sort_values().values
            cat_dtype = bodo.hiframes.pd_categorical_ext.init_cat_dtype(cats, False)
            return bodo.utils.conversion.fix_arr_dtype(data, cat_dtype, copy)

        return impl_category

    nb_dtype = bodo.utils.typing.parse_dtype(new_dtype)

    # nullable int array case
    if isinstance(nb_dtype, bodo.libs.int_arr_ext.IntDtype):
        _dtype = nb_dtype.dtype
        if isinstance(data.dtype, types.Float):

            def impl_float(data, new_dtype, copy=None):  # pragma: no cover
                n = len(data)
                numba.parfors.parfor.init_prange()
                B = bodo.libs.int_arr_ext.alloc_int_array(n, _dtype)
                for i in numba.parfors.parfor.internal_prange(n):
                    if bodo.libs.array_kernels.isna(data, i):
                        bodo.libs.array_kernels.setna(B, i)
                    else:
                        B[i] = int(data[i])
                        # no need for setting null bit since done by int arr's setitem
                return B

            return impl_float
        else:

            def impl(data, new_dtype, copy=None):  # pragma: no cover
                n = len(data)
                n_bytes = (n + 7) >> 3
                bitmap = np.empty(n_bytes, np.uint8)
                for i in numba.parfors.parfor.internal_prange(n):
                    # TODO: use simple set_bit
                    bodo.libs.int_arr_ext.set_bit_to_arr(bitmap, i, 1)
                return bodo.libs.int_arr_ext.init_integer_array(
                    data.astype(_dtype), bitmap
                )

            return impl

    # Array case
    if do_copy or data.dtype != nb_dtype:
        return lambda data, new_dtype, copy=None: data.astype(
            nb_dtype
        )  # pragma: no cover

    return lambda data, new_dtype, copy=None: data  # pragma: no cover


@numba.jit
def flatten_array(A):  # pragma: no cover
    flat_list = []
    n = len(A)
    for i in range(n):
        l = A[i]
        for s in l:
            flat_list.append(s)

    return bodo.utils.conversion.coerce_to_array(flat_list)


# TODO: use generated_jit with IR inlining
def parse_datetimes_from_strings(data):  # pragma: no cover
    return data


@overload(parse_datetimes_from_strings, no_unliteral=True)
def overload_parse_datetimes_from_strings(data):
    assert data == bodo.string_array_type

    def parse_impl(data):  # pragma: no cover
        numba.parfors.parfor.init_prange()
        n = len(data)
        S = np.empty(n, bodo.utils.conversion.NS_DTYPE)
        for i in numba.parfors.parfor.internal_prange(n):
            S[i] = bodo.hiframes.pd_timestamp_ext.parse_datetime_str(data[i])
        return S

    return parse_impl


# TODO: use generated_jit with IR inlining
def convert_to_dt64ns(data):  # pragma: no cover
    return data


@overload(convert_to_dt64ns, no_unliteral=True)
def overload_convert_to_dt64ns(data):
    """Converts data formats like int64 and arrays of strings to dt64ns
    """
    # see pd.core.arrays.datetimes.sequence_to_dt64ns for constructor types
    # TODO: support datetime.date, datetime.datetime
    # TODO: support dayfirst, yearfirst, tz
    if data == types.Array(types.int64, 1, "C"):
        return lambda data: data.view(bodo.utils.conversion.NS_DTYPE)

    if data == types.Array(types.NPDatetime("ns"), 1, "C"):
        return lambda data: data

    if data == bodo.string_array_type:
        return lambda data: bodo.utils.conversion.parse_datetimes_from_strings(data)

    raise TypeError("invalid data type {} for dt64 conversion".format(data))


# TODO: use generated_jit with IR inlining
def convert_to_td64ns(data):  # pragma: no cover
    return data


@overload(convert_to_td64ns, no_unliteral=True)
def overload_convert_to_td64ns(data):
    """Converts data formats like int64 to timedelta64ns
    """
    # TODO: array of strings
    # see pd.core.arrays.timedeltas.sequence_to_td64ns for constructor types
    # TODO: support datetime.timedelta
    if data == types.Array(types.int64, 1, "C"):
        return lambda data: data.view(bodo.utils.conversion.TD_DTYPE)

    if data == types.Array(types.NPTimedelta("ns"), 1, "C"):
        return lambda data: data

    if data == bodo.string_array_type:
        # TODO: support
        raise ValueError("conversion to timedelta from string not supported yet")

    raise TypeError("invalid data type {} for dt64 conversion".format(data))


def convert_to_index(data, name=None):  # pragma: no cover
    return data


@overload(convert_to_index, no_unliteral=True)
def overload_convert_to_index(data, name=None):
    """
    convert data to Index object if necessary.
    """
    from bodo.hiframes.pd_index_ext import (
        RangeIndexType,
        NumericIndexType,
        DatetimeIndexType,
        TimedeltaIndexType,
        StringIndexType,
    )

    # already Index
    if isinstance(
        data,
        (
            RangeIndexType,
            NumericIndexType,
            DatetimeIndexType,
            TimedeltaIndexType,
            StringIndexType,
            types.NoneType,
        ),
    ):
        return lambda data, name=None: data

    def impl(data, name=None):  # pragma: no cover
        data_arr = bodo.utils.conversion.coerce_to_array(data)
        return bodo.utils.conversion.index_from_array(data_arr, name)

    return impl


def force_convert_index(I1, I2):  # pragma: no cover
    return I2


@overload(force_convert_index, no_unliteral=True)
def overload_force_convert_index(I1, I2):
    """
    Convert I1 to type of I2, with possible loss of data. TODO: remove this
    """
    from bodo.hiframes.pd_index_ext import RangeIndexType

    if isinstance(I2, RangeIndexType):
        return lambda I1, I2: pd.RangeIndex(len(I1._data))

    return lambda I1, I2: I1


def index_from_array(data, name=None):  # pragma: no cover
    return data


@overload(index_from_array, no_unliteral=True)
def overload_index_from_array(data, name=None):
    """
    convert data array to Index object.
    """
    if data == bodo.string_array_type:
        return lambda data, name=None: bodo.hiframes.pd_index_ext.init_string_index(
            data, name
        )

    assert isinstance(data, (types.Array, bodo.libs.int_arr_ext.IntegerArrayType))

    if data.dtype == types.NPDatetime("ns"):
        return lambda data, name=None: pd.DatetimeIndex(data, name=name)

    if data.dtype == types.NPTimedelta("ns"):
        return lambda data, name=None: pd.TimedeltaIndex(data, name=name)

    if isinstance(data.dtype, types.Integer):
        if not data.dtype.signed:
            return lambda data, name=None: pd.UInt64Index(data, name=name)
        else:
            return lambda data, name=None: pd.Int64Index(data, name=name)

    if isinstance(data.dtype, types.Float):
        return lambda data, name=None: pd.Float64Index(data, name=name)

    # TODO: timedelta, period
    raise TypeError("invalid index type {}".format(data))


def index_to_array(data):  # pragma: no cover
    return data


@overload(index_to_array, no_unliteral=True)
def overload_index_to_array(I):
    """
    convert Index object to data array.
    """
    from bodo.hiframes.pd_index_ext import RangeIndexType

    if isinstance(I, RangeIndexType):
        return lambda I: np.arange(I._start, I._stop, I._step)

    # other indices have data
    return lambda I: bodo.hiframes.pd_index_ext.get_index_data(I)


def extract_name_if_none(data, name):  # pragma: no cover
    return name


@overload(extract_name_if_none, no_unliteral=True)
def overload_extract_name_if_none(data, name):
    """Extract name if `data` is has name (Series/Index) and `name` is None
    """
    from bodo.hiframes.pd_index_ext import (
        RangeIndexType,
        NumericIndexType,
        DatetimeIndexType,
        TimedeltaIndexType,
        PeriodIndexType,
    )
    from bodo.hiframes.pd_series_ext import SeriesType

    if not is_overload_none(name):
        return lambda data, name: name

    # Index type, TODO: other indices like Range?
    if isinstance(
        data, (NumericIndexType, DatetimeIndexType, TimedeltaIndexType, PeriodIndexType)
    ):
        return lambda data, name: bodo.hiframes.pd_index_ext.get_index_name(data)

    if isinstance(data, SeriesType):
        return lambda data, name: bodo.hiframes.pd_series_ext.get_series_name(data)

    return lambda data, name: name


def extract_index_if_none(data, index):  # pragma: no cover
    return index


@overload(extract_index_if_none, no_unliteral=True)
def overload_extract_index_if_none(data, index):
    """Extract index if `data` is Series and `index` is None
    """
    from bodo.hiframes.pd_series_ext import SeriesType

    if not is_overload_none(index):
        return lambda data, index: index

    if isinstance(data, SeriesType):
        return lambda data, index: bodo.hiframes.pd_series_ext.get_series_index(data)

    return lambda data, index: bodo.hiframes.pd_index_ext.init_range_index(
        0, len(data), 1, None
    )


def box_if_dt64(val):  # pragma: no cover
    return val


@overload(box_if_dt64, no_unliteral=True)
def overload_box_if_dt64(val):
    """If 'val' is dt64, box it to Timestamp otherwise just return 'val'
    """
    if val == types.NPDatetime("ns"):
        return lambda val: bodo.hiframes.pd_timestamp_ext.convert_datetime64_to_timestamp(
            np.int64(val)
        )

    if val == types.NPTimedelta("ns"):
        return lambda val: bodo.hiframes.pd_timestamp_ext.convert_numpy_timedelta64_to_datetime_timedelta(
            val
        )

    return lambda val: val


def unbox_if_timestamp(val):  # pragma: no cover
    return val


@overload(unbox_if_timestamp, no_unliteral=True)
def overload_unbox_if_timestamp(val):
    """If 'val' is Timestamp, "unbox" it to dt64 otherwise just return 'val'
    """
    if val == bodo.hiframes.pd_timestamp_ext.pandas_timestamp_type:
        return lambda val: bodo.hiframes.pd_timestamp_ext.integer_to_dt64(val.value)

    return lambda val: val


def get_array_if_series_or_index(data):  # pragma: no cover
    return data


@overload(get_array_if_series_or_index, no_unliteral=True)
def overload_get_array_if_series_or_index(data):
    from bodo.hiframes.pd_series_ext import SeriesType

    if isinstance(data, SeriesType):
        return lambda data: bodo.hiframes.pd_series_ext.get_series_data(data)

    if bodo.hiframes.pd_index_ext.is_pd_index_type(data):
        return lambda data: bodo.hiframes.pd_index_ext.get_index_data(data)

    return lambda data: data


def extract_index_array(A):  # pragma: no cover
    return np.arange(len(A))


@overload(extract_index_array, no_unliteral=True)
def overload_extract_index_array(A):
    """Returns an index array for Series or array.
    if Series, return it's index array. Otherwise, create an index array.
    """
    from bodo.hiframes.pd_series_ext import SeriesType

    if isinstance(A, SeriesType):

        def impl(A):  # pragma: no cover
            index = bodo.hiframes.pd_series_ext.get_series_index(A)
            index_arr = bodo.utils.conversion.coerce_to_array(index)
            return index_arr

        return impl

    return lambda A: np.arange(len(A))


def extract_index_array_tup(series_tup):  # pragma: no cover
    return tuple(extract_index_array(s) for s in series_tup)


@overload(extract_index_array_tup, no_unliteral=True)
def overload_extract_index_array_tup(series_tup):
    n_series = len(series_tup.types)
    func_text = "def f(series_tup):\n"
    res = ",".join(
        "bodo.utils.conversion.extract_index_array(series_tup[{}])".format(i)
        for i in range(n_series)
    )
    func_text += "  return ({}{})\n".format(res, "," if n_series == 1 else "")
    loc_vars = {}
    exec(func_text, {"bodo": bodo}, loc_vars)
    impl = loc_vars["f"]
    return impl


# return the NA value for array type (dtypes that support sentinel NA)
def get_NA_val_for_arr(arr):  # pragma: no cover
    return np.nan


@overload(get_NA_val_for_arr, no_unliteral=True)
def overload_get_NA_val_for_arr(arr):
    if isinstance(arr.dtype, (types.NPDatetime, types.NPTimedelta)):
        nat = arr.dtype("NaT")
        return lambda arr: nat  # pragma: no cover

    if isinstance(arr.dtype, types.Float):
        return lambda arr: np.nan  # pragma: no cover

    # TODO: other types?
    raise BodoError(
        "Array {} does not support sentinel NA".format(arr)
    )  # pragma: no cover


def ensure_contig_if_np(arr):  # pragma: no cover
    return np.ascontiguousarray(arr)


@overload(ensure_contig_if_np, no_unliteral=True)
def overload_ensure_contig_if_np(arr):
    """make sure array 'arr' is contiguous in memory if it is a numpy array.
    Other arrays are always contiguous.
    """
    if isinstance(arr, types.Array):
        return lambda arr: np.ascontiguousarray(arr)  # pragma: no cover

    return lambda arr: arr  # pragma: no cover


def struct_if_heter_dict(values, names):  # pragma: no cover
    return {k: v for k, v in zip(names, values)}


@overload(struct_if_heter_dict, no_unliteral=True)
def overload_struct_if_heter_dict(values, names):
    """returns a struct with fields names 'names' and data 'values' if value types are
    heterogeneous, otherwise a regular dict.
    """

    if not types.is_homogeneous(*values.types):
        return lambda values, names: bodo.libs.struct_arr_ext.init_struct(
            values, names
        )  # pragma: no cover

    n_fields = len(values.types)
    func_text = "def f(values, names):\n"
    res = ",".join(
        "'{}': values[{}]".format(get_overload_const_str(names.types[i]), i)
        for i in range(n_fields)
    )
    func_text += "  return {{{}}}\n".format(res)
    loc_vars = {}
    exec(func_text, {}, loc_vars)
    impl = loc_vars["f"]
    return impl


# def to_bool_array_if_np_bool(A):
#     return A


# @overload(to_bool_array_if_np_bool, no_unliteral=True)
# def overload_to_bool_array_if_np_bool(A):
#     """Returns a nullable BooleanArray if input is bool ndarray. Otherwise,
#     just returns the input.
#     """
#     if A == types.Array(types.bool_, 1, 'C'):
#         return lambda A: bodo.libs.bool_arr_ext.init_bool_array(
#                          A, np.full((len(A) + 7) >> 3, 255, np.uint8))

#     return lambda A: A
