# Copyright (C) 2023 Bodo Inc. All rights reserved.
"""Support for streaming groupby (a.k.a. vectorized groupby).
This file is mostly wrappers for C++ implementations.
"""
from functools import cached_property
from typing import List

import llvmlite.binding as ll
import numba
import numpy as np
from llvmlite import ir as lir
from numba.core import cgutils, types
from numba.extending import intrinsic, models, register_model

import bodo
from bodo.ext import stream_groupby_cpp
from bodo.libs.array import (
    cpp_table_to_py_table,
    delete_table,
    py_data_to_cpp_table,
)
from bodo.libs.array import table_type as cpp_table_type
from bodo.utils.typing import MetaType, is_overload_none, unwrap_typeref
from bodo.utils.utils import numba_to_c_array_type, numba_to_c_type

ll.add_symbol(
    "groupby_state_init_py_entry", stream_groupby_cpp.groupby_state_init_py_entry
)
ll.add_symbol(
    "groupby_build_consume_batch_py_entry",
    stream_groupby_cpp.groupby_build_consume_batch_py_entry,
)

ll.add_symbol(
    "groupby_produce_output_batch_py_entry",
    stream_groupby_cpp.groupby_produce_output_batch_py_entry,
)
ll.add_symbol("delete_groupby_state", stream_groupby_cpp.delete_groupby_state)


class GroupbyStateType(types.Type):
    """Type for C++ GroupbyState pointer"""

    def __init__(
        self,
        key_inds,
        ftypes,
        f_in_offsets,
        f_in_cols,
        build_table_type=types.unknown,
    ):
        self.key_inds = key_inds
        self.ftypes = ftypes
        self.f_in_offsets = f_in_offsets
        self.f_in_cols = f_in_cols
        self.build_table_type = build_table_type
        super().__init__(
            f"GroupbyStateType(key_inds={key_inds}, ftypes={ftypes}, f_in_offsets={f_in_offsets}, f_in_cols={f_in_cols}, build_table={build_table_type})"
        )

    @property
    def key(self):
        return (
            self.key_inds,
            self.ftypes,
            self.f_in_offsets,
            self.f_in_cols,
            self.build_table_type,
        )

    @property
    def n_keys(self):
        return len(self.key_inds)

    @cached_property
    def key_types(self) -> List[types.ArrayCompatible]:
        """Generate the list of array types that should be used for the
        keys to groupby.

        Returns:
            List[types.ArrayCompatible]: The list of array types used
            by the keys.
        """
        build_table_type = self.build_table_type
        if build_table_type == types.unknown:
            # Typing transformations haven't fully finished yet.
            return []

        build_key_inds = self.key_inds
        arr_types = []
        num_keys = len(build_key_inds)

        for i in range(num_keys):
            build_key_index = build_key_inds[i]
            build_arr_type = self.build_table_type.arr_types[build_key_index]
            arr_types.append(build_arr_type)

        return arr_types

    @staticmethod
    def _derive_input_type(
        key_types, key_indices, table_type
    ) -> List[types.ArrayCompatible]:
        """Generate the input table type based on the given key types, key
        indices, and table type.

        Args:
            key_types (List[types.ArrayCompatible]): The list of key types in order.
            key_indices (N Tuple(int)): The indices of the key columns
            table_type (TableType): The input table type.

        Returns:
            List[types.ArrayCompatible]: The list of array types for the input table (in order).
        """
        types = key_types.copy()
        idx_set = set(key_indices)
        # Append the data columns
        for i in range(len(table_type.arr_types)):
            if i not in idx_set:
                types.append(table_type.arr_types[i])
        return types

    @cached_property
    def build_reordered_arr_types(self) -> List[types.ArrayCompatible]:
        """
        Get the list of array types for the actual input to the C++ build table.
        This is different from the build_table_type because the input to the C++
        will reorder keys to the front.

        Returns:
            List[types.ArrayCompatible]: The list of array types for the build table.
        """
        if self.build_table_type == types.unknown:
            return []

        key_types = self.key_types
        key_indices = self.key_inds
        table = self.build_table_type
        return self._derive_input_type(key_types, key_indices, table)

    @staticmethod
    def _derive_c_types(arr_types: List[types.ArrayCompatible]) -> np.ndarray:
        """Generate the CType Enum types for each array in the
        C++ build table via the indices.

        Args:
            arr_types (List[types.ArrayCompatible]): The array types to use.

        Returns:
            List(int): List with the integer values of each CTypeEnum value.
        """
        return np.array(
            [numba_to_c_type(arr_type.dtype) for arr_type in arr_types],
            dtype=np.int8,
        )

    @property
    def build_arr_ctypes(self) -> np.ndarray:
        """
        Fetch the CTypes used for each array in the build table.

        Note: We must use build_reordered_arr_types to account for reordering.

        Returns:
            List(int): The ctypes for each array in the build table. Note
                that C++ wants the actual integer but these are the values derived from
                CTypeEnum.
        """
        return self._derive_c_types(self.build_reordered_arr_types)

    @staticmethod
    def _derive_c_array_types(arr_types: List[types.ArrayCompatible]) -> np.ndarray:
        """Generate the CArrayTypeEnum Enum types for each array in the
        C++ build table via the indices.

        Args:
            arr_types (List[types.ArrayCompatible]): The array types to use.

        Returns:
            List(int): List with the integer values of each CTypeEnum value.
        """
        return np.array(
            [numba_to_c_array_type(arr_type) for arr_type in arr_types],
            dtype=np.int8,
        )

    @property
    def build_arr_array_types(self) -> np.ndarray:
        """
        Fetch the CArrayTypeEnum used for each array in the build table.

        Note: We must use build_reordered_arr_types to account for reordering.


        Returns:
            List(int): The CArrayTypeEnum for each array in the build table. Note
                that C++ wants the actual integer but these are the values derived from
                CArrayTypeEnum.
        """
        return self._derive_c_array_types(self.build_reordered_arr_types)

    @property
    def num_build_input_arrs(self) -> int:
        """
        Determine the actual number of build arrays in the input.

        Note: We use build_reordered_arr_types in case the same column
        is used as a key in multiple comparisons.

        Return (int): The number of build arrays
        """
        return len(self.build_reordered_arr_types)

    @staticmethod
    def _derive_cpp_indices(key_indices, num_cols):
        """Generate the indices used for the C++ table from the
        given Python table.

        Args:
            key_indices (N Tuple(int)): The indices of the key columns
            num_cols (int): The number of total columns in the array.

        Returns:
            N Tuple(int): Tuple giving the order of the output indices
        """
        total_idxs = []
        for key_idx in key_indices:
            total_idxs.append(key_idx)

        idx_set = set(key_indices)
        for i in range(num_cols):
            if i not in idx_set:
                total_idxs.append(i)
        return tuple(total_idxs)

    @cached_property
    def build_indices(self):
        if self.build_table_type == types.unknown:
            return ()
        else:
            return self._derive_cpp_indices(
                self.key_inds, len(self.build_table_type.arr_types)
            )

    @cached_property
    def out_table_type(self):
        # TODO[BSE-578]: get proper output type when not same as input
        return self.build_table_type


register_model(GroupbyStateType)(models.OpaqueModel)


@intrinsic
def _init_groupby_state(
    typingctx,
    build_arr_dtypes,
    build_arr_array_types,
    n_build_arrs,
    ftypes_t,
    f_in_offsets_t,
    f_in_cols_t,
    n_funcs_t,
    output_state_type,
    parallel_t,
):
    """Initialize C++ GroupbyState pointer

    Args:
        build_arr_dtypes (int8*): pointer to array of ints representing array dtypes
                                   (as provided by numba_to_c_type)
        build_arr_array_types (int8*): pointer to array of ints representing array types
                                    (as provided by numba_to_c_array_type)
        n_build_arrs (int32): number of build columns
        output_state_type (TypeRef[GroupbyStateType]): The output type for the state
                                                    that should be generated.
    """
    output_type = unwrap_typeref(output_state_type)

    def codegen(context, builder, sig, args):
        (
            build_arr_dtypes,
            build_arr_array_types,
            n_build_arrs,
            ftypes,
            f_in_offsets,
            f_in_cols,
            n_funcs,
            _,
            parallel,
        ) = args
        n_keys = context.get_constant(types.uint64, output_type.n_keys)
        fnty = lir.FunctionType(
            lir.IntType(8).as_pointer(),
            [
                lir.IntType(8).as_pointer(),
                lir.IntType(8).as_pointer(),
                lir.IntType(32),
                lir.IntType(32).as_pointer(),
                lir.IntType(32).as_pointer(),
                lir.IntType(32).as_pointer(),
                lir.IntType(32),
                lir.IntType(64),
                lir.IntType(1),
            ],
        )
        fn_tp = cgutils.get_or_insert_function(
            builder.module, fnty, name="groupby_state_init_py_entry"
        )
        input_args = (
            build_arr_dtypes,
            build_arr_array_types,
            n_build_arrs,
            ftypes,
            f_in_offsets,
            f_in_cols,
            n_funcs,
            n_keys,
            parallel,
        )
        ret = builder.call(fn_tp, input_args)
        bodo.utils.utils.inlined_check_and_propagate_cpp_exception(context, builder)
        return ret

    sig = output_type(
        types.voidptr,
        types.voidptr,
        types.int32,
        types.CPointer(types.int32),
        types.CPointer(types.int32),
        types.CPointer(types.int32),
        types.int32,
        output_state_type,
        parallel_t,
    )
    return sig, codegen


@numba.generated_jit(nopython=True, no_cpython_wrapper=True)
def init_groupby_state(
    key_inds,
    ftypes,
    f_in_offsets,
    f_in_cols,
    expected_state_type=None,
    parallel=False,
):
    expected_state_type = unwrap_typeref(expected_state_type)
    if is_overload_none(expected_state_type):
        key_inds = unwrap_typeref(key_inds).meta
        ftypes = unwrap_typeref(ftypes).meta
        f_in_offsets = unwrap_typeref(f_in_offsets).meta
        f_in_cols = unwrap_typeref(f_in_cols).meta
        output_type = GroupbyStateType(key_inds, ftypes, f_in_offsets, f_in_cols)
    else:
        output_type = expected_state_type

    build_arr_dtypes = output_type.build_arr_ctypes
    build_arr_array_types = output_type.build_arr_array_types
    n_build_arrs = output_type.num_build_input_arrs

    ftypes_arr = np.array(output_type.ftypes, np.int32)
    f_in_offsets_arr = np.array(output_type.f_in_offsets, np.int32)
    f_in_cols_arr = np.array(output_type.f_in_cols, np.int32)
    n_funcs = len(output_type.ftypes)

    def impl(
        key_inds,
        ftypes,
        f_in_offsets,
        f_in_cols,
        expected_state_type=None,
        parallel=False,
    ):  # pragma: no cover
        return _init_groupby_state(
            build_arr_dtypes.ctypes,
            build_arr_array_types.ctypes,
            n_build_arrs,
            ftypes_arr.ctypes,
            f_in_offsets_arr.ctypes,
            f_in_cols_arr.ctypes,
            n_funcs,
            output_type,
            parallel,
        )

    return impl


@intrinsic
def _groupby_build_consume_batch(
    typingctx,
    groupby_state,
    cpp_table,
    is_last,
):
    def codegen(context, builder, sig, args):
        fnty = lir.FunctionType(
            lir.VoidType(),
            [
                lir.IntType(8).as_pointer(),
                lir.IntType(8).as_pointer(),
                lir.IntType(1),
            ],
        )
        fn_tp = cgutils.get_or_insert_function(
            builder.module, fnty, name="groupby_build_consume_batch_py_entry"
        )
        builder.call(fn_tp, args)
        bodo.utils.utils.inlined_check_and_propagate_cpp_exception(context, builder)

    sig = types.void(groupby_state, cpp_table, is_last)
    return sig, codegen


@numba.generated_jit(nopython=True, no_cpython_wrapper=True)
def groupby_build_consume_batch(groupby_state, table, is_last):
    """Consume a build table batch in streaming groupby (insert into hash table and
    update running values)

    Args:
        groupby_state (GroupbyState): C++ GroupbyState pointer
        table (table_type): build table batch
        is_last (bool): is last batch
    """
    in_col_inds = MetaType(groupby_state.build_indices)
    n_table_cols = groupby_state.num_build_input_arrs

    def impl(groupby_state, table, is_last):  # pragma: no cover
        cpp_table = py_data_to_cpp_table(table, (), in_col_inds, n_table_cols)
        _groupby_build_consume_batch(groupby_state, cpp_table, is_last)

    return impl


@intrinsic
def _groupby_produce_output_batch(
    typingctx,
    groupby_state,
):
    def codegen(context, builder, sig, args):
        out_is_last = cgutils.alloca_once(builder, lir.IntType(1))
        fnty = lir.FunctionType(
            lir.IntType(8).as_pointer(),
            [lir.IntType(8).as_pointer(), lir.IntType(1).as_pointer()],
        )
        fn_tp = cgutils.get_or_insert_function(
            builder.module, fnty, name="groupby_produce_output_batch_py_entry"
        )
        func_args = [
            args[0],
            out_is_last,
        ]
        table_ret = builder.call(fn_tp, func_args)
        bodo.utils.utils.inlined_check_and_propagate_cpp_exception(context, builder)
        items = [table_ret, builder.load(out_is_last)]
        return context.make_tuple(builder, sig.return_type, items)

    ret_type = types.Tuple([cpp_table_type, types.bool_])
    sig = ret_type(
        groupby_state,
    )
    return sig, codegen


@numba.generated_jit(nopython=True, no_cpython_wrapper=True)
def groupby_produce_output_batch(groupby_state):
    """Produce output batches of groupby operation

    Args:
        groupby_state (GroupbyState): C++ GroupbyState pointer

    Returns:
        table_type: output table batch
    """
    out_table_type = groupby_state.out_table_type

    if out_table_type == types.unknown:
        out_cols_arr = np.array([], dtype=np.int64)
    else:
        output_cols = []
        key_map = {idx: i for i, idx in enumerate(groupby_state.key_inds)}
        data_offset = groupby_state.n_keys
        num_cols = len(out_table_type.arr_types)

        for i in range(num_cols):
            if i in key_map:
                physical_index = key_map[i]
            else:
                physical_index = data_offset
                data_offset += 1

            output_cols.append(physical_index)

        out_cols_arr = np.array(output_cols, dtype=np.int64)

    def impl(
        groupby_state,
    ):  # pragma: no cover
        out_cpp_table, out_is_last = _groupby_produce_output_batch(groupby_state)
        out_table = cpp_table_to_py_table(
            out_cpp_table, out_cols_arr, out_table_type, 0
        )
        delete_table(out_cpp_table)
        return out_table, out_is_last

    return impl


@intrinsic
def delete_groupby_state(
    typingctx,
    groupby_state,
):
    def codegen(context, builder, sig, args):
        fnty = lir.FunctionType(
            lir.VoidType(),
            [
                lir.IntType(8).as_pointer(),
            ],
        )
        fn_tp = cgutils.get_or_insert_function(
            builder.module, fnty, name="delete_groupby_state"
        )
        builder.call(fn_tp, args)
        bodo.utils.utils.inlined_check_and_propagate_cpp_exception(context, builder)

    sig = types.void(groupby_state)
    return sig, codegen