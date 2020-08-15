# Copyright (C) 2019 Bodo Inc. All rights reserved.
"""IR node for the groupby, pivot and cross_tabulation"""
import operator
from collections import namedtuple, defaultdict
import ctypes
import types as pytypes
import numpy as np
import pandas as pd
import numba
from bodo.utils.typing import BodoError
from bodo.libs.decimal_arr_ext import DecimalArrayType, alloc_decimal_array
from numba.core import compiler, ir, ir_utils, types
from numba.core.ir_utils import (
    visit_vars_inner,
    replace_vars_inner,
    remove_dead,
    compile_to_numba_ir,
    replace_arg_nodes,
    replace_vars_stmt,
    find_callname,
    find_const,
    guard,
    mk_unique_var,
    find_topo_order,
    is_getitem,
    build_definitions,
    remove_dels,
    get_ir_of_code,
    get_definition,
    find_callname,
    get_name_var_table,
    replace_var_names,
)
from numba.parfors.parfor import wrap_parfor_blocks, unwrap_parfor_blocks, Parfor
from numba.core.analysis import compute_use_defs
from numba.core.typing import signature
from numba.core.typing.templates import infer_global, AbstractTemplate
from numba.extending import overload, lower_builtin
import bodo
from bodo.utils.utils import (
    is_call_assign,
    is_var_assign,
    is_assign,
    is_expr,
    debug_prints,
    alloc_arr_tup,
    empty_like_type,
    sanitize_varname,
)
from bodo.transforms import distributed_pass, distributed_analysis
from bodo.transforms.distributed_analysis import Distribution
from bodo.utils.utils import unliteral_all, incref
from bodo.libs.str_ext import string_type
from bodo.libs.int_arr_ext import IntegerArrayType, IntDtype
from bodo.libs.bool_arr_ext import BooleanArrayType
from bodo.utils.utils import build_set
from bodo.libs.array_item_arr_ext import ArrayItemArrayType, pre_alloc_array_item_array
from bodo.utils.typing import list_cumulative
from bodo.libs.str_arr_ext import (
    string_array_type,
    StringArrayType,
    pre_alloc_string_array,
    get_offset_ptr,
    get_data_ptr,
    get_utf8_size,
)
from bodo.hiframes.pd_series_ext import SeriesType
from bodo.hiframes import series_impl
from bodo.ir.join import write_send_buff
from bodo.libs.timsort import getitem_arr_tup, setitem_arr_tup
from bodo.utils.transform import get_call_expr_arg
from bodo.utils.shuffle import (
    getitem_arr_tup_single,
    val_to_tup,
    alltoallv_tup,
    finalize_shuffle_meta,
    update_shuffle_meta,
    alloc_pre_shuffle_metadata,
    _get_keys_tup,
    _get_data_tup,
)
from bodo.utils.typing import (
    is_overload_true,
    get_overload_const_func,
    get_overload_const_list,
    is_overload_constant_dict,
    get_overload_constant_dict,
    is_overload_constant_str,
    get_overload_const_str,
)
from bodo.libs.array import (
    array_to_info,
    arr_info_list_to_table,
    groupby_and_aggregate,
    compute_node_partition_by_hash,
    info_from_table,
    info_to_array,
    delete_table,
)


AggFuncTemplateStruct = namedtuple(
    "AggFuncTemplateStruct",
    ["var_typs", "init_func", "update_all_func", "combine_all_func", "eval_all_func"],
)

AggFuncStruct = namedtuple("AggFuncStruct", ["func", "ftype"])


supported_agg_funcs = [
    "sum",
    "count",
    "nunique",
    "median",
    "cumsum",
    "cumprod",
    "cummin",
    "cummax",
    "mean",
    "min",
    "max",
    "prod",
    "first",
    "last",
    "var",
    "std",
    "udf",
]


def get_agg_func(func_ir, func_name, rhs, series_type=None, typemap=None):
    """ Returns specification of functions used by a groupby operation. It will
        either return:
        - A single function (case of a single function applied to all groupby
          input columns). For example: df.groupby("A").sum()
        - A list (element i of the list corresponds to a function(s) to apply
          to input column i)
            - The list can contain functions and list of functions, meaning
              that for each input column, a single function or list of
              functions can be applied.
    """

    # FIXME: using float64 type as default to be compatible with old code
    # TODO: make groupby functions typed properly everywhere
    if series_type is None:
        series_type = SeriesType(types.float64)

    # Here we also set func.ncols_pre_shuffle and func.ncols_post_shuffle (see
    # below) for aggregation functions. These are the number of columns used
    # to compute the result of the function at runtime, before shuffle and
    # after shuffle, respectively. This is needed to generate code that invokes
    # udfs at runtime (see gen_update_cb, gen_combine_cb and gen_eval_cb),
    # to know which columns in the table received from C++ library correspond
    # to udfs and which to builtin functions

    if func_name == "var":
        func = _column_var_impl_linear
        func.ftype = func_name
        func.ncols_pre_shuffle = 3
        func.ncols_post_shuffle = 4
        return func
    if func_name == "std":
        func = _column_std_impl_linear
        func.ftype = func_name
        func.ncols_pre_shuffle = 3
        func.ncols_post_shuffle = 4
        return func
    if func_name in {"first", "last"}:
        # We don't have a function definition for first/last, and it is not needed
        # for the groupby C++ codepath, so we just use a dummy object.
        # Also NOTE: Series last and df.groupby.last() are different operations
        func = pytypes.SimpleNamespace()
        func.ftype = func_name
        func.ncols_pre_shuffle = 1
        func.ncols_post_shuffle = 1
        return func
    if func_name in supported_agg_funcs[:-5]:
        func = getattr(series_impl, "overload_series_" + func_name)(series_type)
        # HACK: use simple versions of sum/prod functions without extra arguments to
        # avoid errors in pivot
        # TODO: remove when pivot is moved to C++ code
        if func_name == "sum":
            val_zero = series_type.dtype(0)

            def func(S):  # pragma: no cover
                A = bodo.hiframes.pd_series_ext.get_series_data(S)
                numba.parfors.parfor.init_prange()
                s = val_zero
                for i in numba.parfors.parfor.internal_prange(len(A)):
                    val = val_zero
                    if not bodo.libs.array_kernels.isna(A, i):
                        val = A[i]
                    s += val
                return s

        if func_name == "prod":
            val_one = series_type.dtype(1)

            def func(S):  # pragma: no cover
                A = bodo.hiframes.pd_series_ext.get_series_data(S)
                numba.parfors.parfor.init_prange()
                s = val_one
                for i in numba.parfors.parfor.internal_prange(len(A)):
                    val = val_one
                    if not bodo.libs.array_kernels.isna(A, i):
                        val = A[i]
                    s *= val
                return s

        func.ftype = func_name
        func.ncols_pre_shuffle = 1
        func.ncols_post_shuffle = 1
        skipdropna = True
        if isinstance(rhs, ir.Expr):
            for erec in rhs.kws:
                if func_name in list_cumulative:
                    if erec[0] == "skipna":
                        skipdropna = guard(find_const, func_ir, erec[1])
                        if not isinstance(skipdropna, bool):
                            raise BodoError(
                                "For {} argument of skipna should be a boolean".format(
                                    func_name
                                )
                            )
                    else:
                        raise BodoError(
                            "argument to {} can only be skipna".format(func_name)
                        )
                if func_name == "nunique":
                    if erec[0] == "dropna":
                        skipdropna = guard(find_const, func_ir, erec[1])
                        if not isinstance(skipdropna, bool):
                            raise BodoError(
                                "argument of dropna to nunique should be a boolean"
                            )
                    else:
                        raise BodoError("argument to nunique can only be dropna")
        func.skipdropna = skipdropna
        return func

    # agg case
    assert func_name in ["agg", "aggregate"]

    # NOTE: assuming typemap is provided here
    # TODO: refactor old pivot code that doesn't provide typemap
    assert typemap is not None
    func_var = get_call_expr_arg(func_name, rhs.args, dict(rhs.kws), 0, "func")
    agg_func_typ = typemap[func_var.name]

    # multi-function const dict case
    if is_overload_constant_dict(agg_func_typ):
        funcs = []
        items = get_overload_constant_dict(agg_func_typ)
        # return a list, element i is function or list of functions to apply
        # to column i
        funcs = [
            get_agg_func_udf(func_ir, f_val, rhs, series_type, typemap)
            for f_val in items.values()
        ]
        return funcs

    # multi-function tuple case
    if isinstance(agg_func_typ, types.BaseTuple):
        funcs = []
        for t in agg_func_typ.types:
            if is_overload_constant_str(t):
                func_name = get_overload_const_str(t)
                funcs.append(
                    get_agg_func(func_ir, func_name, rhs, series_type, typemap)
                )
            else:
                assert typemap is not None, "typemap is required for agg UDF handling"
                func = _get_const_agg_func(t)
                func.ftype = "udf"
                funcs.append(func)
        # return a list containing one list of functions (applied to single
        # input column)
        return [funcs]

    # typemap should be available for UDF case
    assert typemap is not None, "typemap is required for agg UDF handling"
    func = _get_const_agg_func(typemap[rhs.args[0].name])
    func.ftype = "udf"
    return func


def get_agg_func_udf(func_ir, f_val, rhs, series_type, typemap):
    """get udf value for agg call
    """
    if isinstance(f_val, str):
        return get_agg_func(func_ir, f_val, rhs, series_type, typemap)
    if isinstance(f_val, (tuple, list)):
        return [get_agg_func_udf(func_ir, f, rhs, series_type, typemap) for f in f_val]
    else:
        assert is_expr(f_val, "make_function") or isinstance(
            f_val, (numba.core.registry.CPUDispatcher, types.Dispatcher)
        )
        assert typemap is not None, "typemap is required for agg UDF handling"
        func = _get_const_agg_func(f_val)
        func.ftype = "udf"
        return func


def _get_const_agg_func(func_typ):
    """get UDF function from its type. Wraps closures in functions.
    """
    agg_func = get_overload_const_func(func_typ)

    # convert agg_func to a function if it is a make_function object
    # TODO: more robust handling, maybe reuse Numba's inliner code if possible
    if is_expr(agg_func, "make_function"):

        def agg_func_wrapper(A):  # pragma: no cover
            return A

        agg_func_wrapper.__code__ = agg_func.code
        agg_func = agg_func_wrapper
        return agg_func

    return agg_func


# type(dtype) is called by np.full (used in agg_typer)
@infer_global(type)
class TypeDt64(AbstractTemplate):
    def generic(self, args, kws):
        assert not kws
        if len(args) == 1 and isinstance(
            args[0], (types.NPDatetime, types.NPTimedelta)
        ):
            classty = types.DType(args[0])
            return signature(classty, *args)


# combine function takes the reduce vars in reverse order of their user
@numba.njit(no_cpython_wrapper=True)
def _var_combine(ssqdm_a, mean_a, nobs_a, ssqdm_b, mean_b, nobs_b):  # pragma: no cover
    nobs = nobs_a + nobs_b
    mean_x = (nobs_a * mean_a + nobs_b * mean_b) / nobs
    delta = mean_b - mean_a
    M2 = ssqdm_a + ssqdm_b + delta * delta * nobs_a * nobs_b / nobs
    return M2, mean_x, nobs


# XXX: njit doesn't work when bodo.jit() is used for agg_func in hiframes
# @numba.njit
def __special_combine(*args):  # pragma: no cover
    return


@infer_global(__special_combine)
class SpecialCombineTyper(AbstractTemplate):
    def generic(self, args, kws):
        assert not kws
        return signature(types.void, *unliteral_all(args))


@lower_builtin(__special_combine, types.VarArg(types.Any))
def lower_special_combine(context, builder, sig, args):
    return context.get_dummy_value()


# https://en.wikipedia.org/wiki/Algorithms_for_calculating_variance
def _column_var_impl_linear(S):  # pragma: no cover
    A = bodo.hiframes.pd_series_ext.get_series_data(S)
    nobs = 0
    mean_x = 0.0
    ssqdm_x = 0.0
    N = len(A)
    for i in numba.parfors.parfor.internal_prange(N):
        bodo.ir.aggregate.__special_combine(
            ssqdm_x, mean_x, nobs, bodo.ir.aggregate._var_combine
        )
        val = A[i]
        if not np.isnan(val):
            nobs += 1
            delta = val - mean_x
            mean_x += delta / nobs
            # TODO: Pandas formula is better or Welford?
            # ssqdm_x += ((nobs - 1) * delta ** 2) / nobs
            delta2 = val - mean_x
            ssqdm_x += delta * delta2
    return bodo.hiframes.rolling.calc_var(2, nobs, mean_x, ssqdm_x)


# TODO: avoid code duplication
def _column_std_impl_linear(S):  # pragma: no cover
    A = bodo.hiframes.pd_series_ext.get_series_data(S)
    nobs = 0
    mean_x = 0.0
    ssqdm_x = 0.0
    N = len(A)
    for i in numba.parfors.parfor.internal_prange(N):
        bodo.ir.aggregate.__special_combine(
            ssqdm_x, mean_x, nobs, bodo.ir.aggregate._var_combine
        )
        val = A[i]
        if not np.isnan(val):
            nobs += 1
            delta = val - mean_x
            mean_x += delta / nobs
            # TODO: Pandas formula is better or Welford?
            # ssqdm_x += ((nobs - 1) * delta ** 2) / nobs
            delta2 = val - mean_x
            ssqdm_x += delta * delta2
    v = bodo.hiframes.rolling.calc_var(2, nobs, mean_x, ssqdm_x)
    return v ** 0.5


class Aggregate(ir.Stmt):
    def __init__(
        self,
        df_out,
        df_in,
        key_names,
        out_key_vars,
        df_out_vars,
        df_in_vars,
        key_arrs,
        agg_func,
        same_index,
        return_key,
        loc,
        pivot_arr=None,
        pivot_values=None,
        is_crosstab=False,
    ):
        # name of output dataframe (just for printing purposes)
        self.df_out = df_out
        # name of input dataframe (just for printing purposes)
        self.df_in = df_in
        # key name (for printing)
        self.key_names = key_names
        self.out_key_vars = out_key_vars

        self.df_out_vars = df_out_vars
        self.df_in_vars = df_in_vars
        self.key_arrs = key_arrs

        self.agg_func = agg_func
        self.same_index = same_index
        self.return_key = return_key
        self.loc = loc
        # pivot_table handling
        self.pivot_arr = pivot_arr
        self.pivot_values = pivot_values
        self.is_crosstab = is_crosstab

    def __repr__(self):  # pragma: no cover
        out_cols = ""
        for (c, v) in self.df_out_vars.items():
            out_cols += "'{}':{}, ".format(c, v.name)
        df_out_str = "{}{{{}}}".format(self.df_out, out_cols)
        in_cols = ""
        for (c, v) in self.df_in_vars.items():
            in_cols += "'{}':{}, ".format(c, v.name)
        df_in_str = "{}{{{}}}".format(self.df_in, in_cols)
        pivot = (
            "pivot {}:{}".format(self.pivot_arr.name, self.pivot_values)
            if self.pivot_arr is not None
            else ""
        )
        key_names = ",".join(self.key_names)
        key_arrnames = ",".join([v.name for v in self.key_arrs])
        return "aggregate: {} = {} [key: {}:{}] {}".format(
            df_out_str, df_in_str, key_names, key_arrnames, pivot
        )


def aggregate_usedefs(aggregate_node, use_set=None, def_set=None):
    if use_set is None:
        use_set = set()
    if def_set is None:
        def_set = set()

    # key array and input columns are used
    use_set.update({v.name for v in aggregate_node.key_arrs})
    use_set.update({v.name for v in aggregate_node.df_in_vars.values()})

    if aggregate_node.pivot_arr is not None:
        use_set.add(aggregate_node.pivot_arr.name)

    # output columns are defined
    def_set.update({v.name for v in aggregate_node.df_out_vars.values()})

    # return key is defined
    if aggregate_node.out_key_vars is not None:
        def_set.update({v.name for v in aggregate_node.out_key_vars})

    return numba.core.analysis._use_defs_result(usemap=use_set, defmap=def_set)


numba.core.analysis.ir_extension_usedefs[Aggregate] = aggregate_usedefs


def remove_dead_aggregate(
    aggregate_node, lives_no_aliases, lives, arg_aliases, alias_map, func_ir, typemap
):

    dead_cols = []

    for col_name, col_var in aggregate_node.df_out_vars.items():
        if col_var.name not in lives:
            dead_cols.append(col_name)

    for cname in dead_cols:
        aggregate_node.df_out_vars.pop(cname)
        if aggregate_node.pivot_arr is None:
            # input/output column names don't match in multi-function case
            if cname in aggregate_node.df_in_vars:
                aggregate_node.df_in_vars.pop(cname)
        else:
            aggregate_node.pivot_values.remove(cname)

    out_key_vars = aggregate_node.out_key_vars
    if out_key_vars is not None and all(v.name not in lives for v in out_key_vars):
        aggregate_node.out_key_vars = None

    # TODO: test agg remove
    # remove empty aggregate node
    if len(aggregate_node.df_out_vars) == 0 and aggregate_node.out_key_vars is None:
        return None

    return aggregate_node


ir_utils.remove_dead_extensions[Aggregate] = remove_dead_aggregate


def get_copies_aggregate(aggregate_node, typemap):
    # aggregate doesn't generate copies, it just kills the output columns
    kill_set = set(v.name for v in aggregate_node.df_out_vars.values())
    if aggregate_node.out_key_vars is not None:
        kill_set.update({v.name for v in aggregate_node.out_key_vars})
    return set(), kill_set


ir_utils.copy_propagate_extensions[Aggregate] = get_copies_aggregate


def apply_copies_aggregate(
    aggregate_node, var_dict, name_var_table, typemap, calltypes, save_copies
):
    """apply copy propagate in aggregate node"""
    for i in range(len(aggregate_node.key_arrs)):
        aggregate_node.key_arrs[i] = replace_vars_inner(
            aggregate_node.key_arrs[i], var_dict
        )

    for col_name in list(aggregate_node.df_in_vars.keys()):
        aggregate_node.df_in_vars[col_name] = replace_vars_inner(
            aggregate_node.df_in_vars[col_name], var_dict
        )
    for col_name in list(aggregate_node.df_out_vars.keys()):
        aggregate_node.df_out_vars[col_name] = replace_vars_inner(
            aggregate_node.df_out_vars[col_name], var_dict
        )

    if aggregate_node.out_key_vars is not None:
        for i in range(len(aggregate_node.out_key_vars)):
            aggregate_node.out_key_vars[i] = replace_vars_inner(
                aggregate_node.out_key_vars[i], var_dict
            )

    if aggregate_node.pivot_arr is not None:
        aggregate_node.pivot_arr = replace_vars_inner(
            aggregate_node.pivot_arr, var_dict
        )

    return


ir_utils.apply_copy_propagate_extensions[Aggregate] = apply_copies_aggregate


def visit_vars_aggregate(aggregate_node, callback, cbdata):
    if debug_prints():  # pragma: no cover
        print("visiting aggregate vars for:", aggregate_node)
        print("cbdata: ", sorted(cbdata.items()))

    for i in range(len(aggregate_node.key_arrs)):
        aggregate_node.key_arrs[i] = visit_vars_inner(
            aggregate_node.key_arrs[i], callback, cbdata
        )

    for col_name in list(aggregate_node.df_in_vars.keys()):
        aggregate_node.df_in_vars[col_name] = visit_vars_inner(
            aggregate_node.df_in_vars[col_name], callback, cbdata
        )
    for col_name in list(aggregate_node.df_out_vars.keys()):
        aggregate_node.df_out_vars[col_name] = visit_vars_inner(
            aggregate_node.df_out_vars[col_name], callback, cbdata
        )

    if aggregate_node.out_key_vars is not None:
        for i in range(len(aggregate_node.out_key_vars)):
            aggregate_node.out_key_vars[i] = visit_vars_inner(
                aggregate_node.out_key_vars[i], callback, cbdata
            )

    if aggregate_node.pivot_arr is not None:
        aggregate_node.pivot_arr = visit_vars_inner(
            aggregate_node.pivot_arr, callback, cbdata
        )


# add call to visit aggregate variable
ir_utils.visit_vars_extensions[Aggregate] = visit_vars_aggregate


def aggregate_array_analysis(aggregate_node, equiv_set, typemap, array_analysis):
    # empty aggregate nodes should be deleted in remove dead
    assert (
        len(aggregate_node.df_in_vars) > 0
        or aggregate_node.out_key_vars is not None
        or aggregate_node.is_crosstab
    ), "empty aggregate in array analysis"

    # arrays of input df have same size in first dimension as key array
    all_shapes = []
    for key_arr in aggregate_node.key_arrs:
        col_shape = equiv_set.get_shape(key_arr)
        all_shapes.append(col_shape[0])

    if aggregate_node.pivot_arr is not None:
        col_shape = equiv_set.get_shape(aggregate_node.pivot_arr)
        all_shapes.append(col_shape[0])

    for col_var in aggregate_node.df_in_vars.values():
        col_shape = equiv_set.get_shape(col_var)
        all_shapes.append(col_shape[0])

    if len(all_shapes) > 1:
        equiv_set.insert_equiv(*all_shapes)

    # create correlations for output arrays
    # arrays of output df have same size in first dimension
    # gen size variable for an output column
    post = []
    all_shapes = []
    out_vars = list(aggregate_node.df_out_vars.values())
    if aggregate_node.out_key_vars is not None:
        out_vars.extend(aggregate_node.out_key_vars)

    for col_var in out_vars:
        typ = typemap[col_var.name]
        (shape, c_post) = array_analysis._gen_shape_call(
            equiv_set, col_var, typ.ndim, None
        )
        equiv_set.insert_equiv(col_var, shape)
        post.extend(c_post)
        all_shapes.append(shape[0])
        equiv_set.define(col_var, set())

    if len(all_shapes) > 1:
        equiv_set.insert_equiv(*all_shapes)

    return [], post


numba.parfors.array_analysis.array_analysis_extensions[
    Aggregate
] = aggregate_array_analysis


def aggregate_distributed_analysis(aggregate_node, array_dists):
    # input columns have same distribution
    in_dist = Distribution.OneD
    for col_var in aggregate_node.df_in_vars.values():
        in_dist = Distribution(min(in_dist.value, array_dists[col_var.name].value))

    # key arrays
    for key_arr in aggregate_node.key_arrs:
        in_dist = Distribution(min(in_dist.value, array_dists[key_arr.name].value))

    # pivot case
    if aggregate_node.pivot_arr is not None:
        in_dist = Distribution(
            min(in_dist.value, array_dists[aggregate_node.pivot_arr.name].value)
        )
        array_dists[aggregate_node.pivot_arr.name] = in_dist

    for col_var in aggregate_node.df_in_vars.values():
        array_dists[col_var.name] = in_dist
    for key_arr in aggregate_node.key_arrs:
        array_dists[key_arr.name] = in_dist

    # output columns have same distribution
    out_dist = Distribution.OneD_Var
    for col_var in aggregate_node.df_out_vars.values():
        # output dist might not be assigned yet
        if col_var.name in array_dists:
            out_dist = Distribution(
                min(out_dist.value, array_dists[col_var.name].value)
            )

    if aggregate_node.out_key_vars is not None:
        for col_var in aggregate_node.out_key_vars:
            if col_var.name in array_dists:
                out_dist = Distribution(
                    min(out_dist.value, array_dists[col_var.name].value)
                )

    # out dist should meet input dist (e.g. REP in causes REP out)
    out_dist = Distribution(min(out_dist.value, in_dist.value))
    for col_var in aggregate_node.df_out_vars.values():
        array_dists[col_var.name] = out_dist

    if aggregate_node.out_key_vars is not None:
        for cvar in aggregate_node.out_key_vars:
            array_dists[cvar.name] = out_dist

    # output can cause input REP
    if out_dist != Distribution.OneD_Var:
        for key_arr in aggregate_node.key_arrs:
            array_dists[key_arr.name] = out_dist
        # pivot case
        if aggregate_node.pivot_arr is not None:
            array_dists[aggregate_node.pivot_arr.name] = out_dist
        for col_var in aggregate_node.df_in_vars.values():
            array_dists[col_var.name] = out_dist

    return


distributed_analysis.distributed_analysis_extensions[
    Aggregate
] = aggregate_distributed_analysis


def build_agg_definitions(agg_node, definitions=None):
    if definitions is None:
        definitions = defaultdict(list)

    for col_var in agg_node.df_out_vars.values():
        definitions[col_var.name].append(agg_node)

    if agg_node.out_key_vars is not None:
        for cvar in agg_node.out_key_vars:
            definitions[cvar.name].append(agg_node)

    return definitions


ir_utils.build_defs_extensions[Aggregate] = build_agg_definitions


def __update_redvars():
    pass


@infer_global(__update_redvars)
class UpdateDummyTyper(AbstractTemplate):
    def generic(self, args, kws):
        assert not kws
        return signature(types.void, *args)


def __combine_redvars():
    pass


@infer_global(__combine_redvars)
class CombineDummyTyper(AbstractTemplate):
    def generic(self, args, kws):
        assert not kws
        return signature(types.void, *args)


def __eval_res():
    pass


@infer_global(__eval_res)
class EvalDummyTyper(AbstractTemplate):
    def generic(self, args, kws):
        assert not kws
        # takes the output array as first argument to know the output dtype
        return signature(args[0].dtype, *args)


def agg_distributed_run(
    agg_node, array_dists, typemap, calltypes, typingctx, targetctx
):
    parallel = False
    if array_dists is not None:
        parallel = True
        for v in (
            list(agg_node.df_in_vars.values())
            + list(agg_node.df_out_vars.values())
            + agg_node.key_arrs
        ):
            if (
                array_dists[v.name] != distributed_pass.Distribution.OneD
                and array_dists[v.name] != distributed_pass.Distribution.OneD_Var
            ):
                parallel = False
            # TODO: check supported types
            # if (typemap[v.name] != types.Array(types.intp, 1, 'C')
            #         and typemap[v.name] != types.Array(types.float64, 1, 'C')):
            #     raise ValueError(
            #         "Only int64 and float64 columns are currently supported in aggregate")
            # if (typemap[left_key_var.name] != types.Array(types.intp, 1, 'C')
            #     or typemap[right_key_var.name] != types.Array(types.intp, 1, 'C')):
            # raise ValueError("Only int64 keys are currently supported in aggregate")

    # TODO: rebalance if output distributions are 1D instead of 1D_Var

    # TODO: handle key column being part of output

    key_typs = tuple(typemap[v.name] for v in agg_node.key_arrs)
    # get column variables
    in_col_vars = [v for (n, v) in agg_node.df_in_vars.items()]
    out_col_vars = [v for (n, v) in agg_node.df_out_vars.items()]
    # get column types
    in_col_typs = tuple(typemap[v.name] for v in in_col_vars)
    out_col_typs = tuple(typemap[v.name] for v in out_col_vars)

    pivot_typ = (
        types.none if agg_node.pivot_arr is None else typemap[agg_node.pivot_arr.name]
    )
    arg_typs = tuple(key_typs + in_col_typs + (pivot_typ,))

    return_key = agg_node.return_key

    glbs = {"bodo": bodo, "np": np, "dt64_dtype": np.dtype("datetime64[ns]")}

    offload = agg_node.pivot_arr is None
    udf_func_struct = get_udf_func_struct(
        agg_node.agg_func,
        agg_node.same_index,
        in_col_typs,
        out_col_typs,
        typingctx,
        targetctx,
        pivot_typ,
        agg_node.pivot_values,
        agg_node.is_crosstab,
    )

    top_level_func = gen_top_level_agg_func(
        agg_node.key_names,
        key_typs,
        return_key,
        in_col_typs,
        out_col_typs,
        agg_node.df_in_vars.keys(),
        agg_node.df_out_vars.keys(),
        agg_node.agg_func,
        agg_node.same_index,
        parallel,
        offload,
        udf_func_struct,
    )
    glbs.update(
        {
            "pd": pd,
            "pre_alloc_string_array": pre_alloc_string_array,
            "pre_alloc_array_item_array": pre_alloc_array_item_array,
            "string_array_type": string_array_type,
            "alloc_decimal_array": alloc_decimal_array,
            "agg_seq_iter": agg_seq_iter,
            "parallel_agg": parallel_agg,
            "array_to_info": array_to_info,
            "arr_info_list_to_table": arr_info_list_to_table,
            "groupby_and_aggregate": groupby_and_aggregate,
            "compute_node_partition_by_hash": compute_node_partition_by_hash,
            "info_from_table": info_from_table,
            "info_to_array": info_to_array,
            "delete_table": delete_table,
        }
    )
    if udf_func_struct is not None:
        glbs.update(
            {
                "__update_redvars": udf_func_struct.update_all_func,
                "__init_func": udf_func_struct.init_func,
                "__combine_redvars": udf_func_struct.combine_all_func,
                "__eval_res": udf_func_struct.eval_all_func,
            }
        )

    f_block = compile_to_numba_ir(
        top_level_func, glbs, typingctx, arg_typs, typemap, calltypes
    ).blocks.popitem()[1]

    nodes = []
    if agg_node.pivot_arr is None:
        scope = agg_node.key_arrs[0].scope
        loc = agg_node.loc
        none_var = ir.Var(scope, mk_unique_var("dummy_none"), loc)
        typemap[none_var.name] = types.none
        nodes.append(ir.Assign(ir.Const(None, loc), none_var, loc))
        in_col_vars.append(none_var)
    else:
        in_col_vars.append(agg_node.pivot_arr)

    replace_arg_nodes(f_block, agg_node.key_arrs + in_col_vars)

    tuple_assign = f_block.body[-3]
    assert (
        is_assign(tuple_assign)
        and isinstance(tuple_assign.value, ir.Expr)
        and tuple_assign.value.op == "build_tuple"
    )
    nodes += f_block.body[:-3]

    out_vars = list(agg_node.df_out_vars.values())
    if agg_node.out_key_vars is not None:
        out_vars += agg_node.out_key_vars

    for i, var in enumerate(out_vars):
        out_var = tuple_assign.value.items[i]
        nodes.append(ir.Assign(out_var, var, var.loc))

    return nodes


distributed_pass.distributed_run_extensions[Aggregate] = agg_distributed_run


@numba.njit(no_cpython_wrapper=True, cache=True)
def par_agg_get_shuffle_meta(
    key_arrs, node_arr, data_redvar_dummy, init_vals
):  # pragma: no cover
    # alloc shuffle meta
    n_pes = bodo.libs.distributed_api.get_size()
    pre_shuffle_meta = alloc_pre_shuffle_metadata(
        key_arrs, data_redvar_dummy, n_pes, False
    )
    node_ids = np.empty(len(key_arrs[0]), np.int32)

    # calc send/recv counts
    key_set = get_key_set(key_arrs)
    for i in range(len(key_arrs[0])):
        val = getitem_arr_tup_single(key_arrs, i)
        if val not in key_set:
            # key_set.add(val)
            key_set[val] = 0
            node_id = node_arr[i]
            node_ids[i] = node_id
            # data isn't computed here yet so pass empty tuple
            update_shuffle_meta(pre_shuffle_meta, node_id, i, key_arrs, (), False)

    shuffle_meta = finalize_shuffle_meta(
        key_arrs, data_redvar_dummy, pre_shuffle_meta, n_pes, False, init_vals
    )
    return shuffle_meta, node_ids


@numba.njit(no_cpython_wrapper=True)
def parallel_agg(
    key_arrs,
    node_arr,
    data_redvar_dummy,
    out_dummy_tup,
    data_in,
    init_vals,
    __update_redvars,
    __combine_redvars,
    __eval_res,
    return_key,
    pivot_arr,
):  # pragma: no cover

    shuffle_meta, node_ids = par_agg_get_shuffle_meta(
        key_arrs, node_arr, data_redvar_dummy, init_vals
    )

    agg_parallel_local_iter(
        key_arrs,
        data_in,
        shuffle_meta,
        data_redvar_dummy,
        __update_redvars,
        pivot_arr,
        node_ids,
    )

    recvs = alltoallv_tup(key_arrs + data_redvar_dummy, shuffle_meta, key_arrs)
    key_arrs = _get_keys_tup(recvs, key_arrs)
    reduce_recvs = _get_data_tup(recvs, key_arrs)
    out_arrs = agg_parallel_combine_iter(
        key_arrs,
        reduce_recvs,
        out_dummy_tup,
        init_vals,
        __combine_redvars,
        __eval_res,
        return_key,
        data_in,
        pivot_arr,
    )
    return out_arrs


@numba.njit(no_cpython_wrapper=True)
def agg_parallel_local_iter(
    key_arrs,
    data_in,
    shuffle_meta,
    data_redvar_dummy,
    __update_redvars,
    pivot_arr,
    node_ids,
):  # pragma: no cover
    # _init_val_0 = np.int64(0)
    # redvar_0_arr = np.full(n_uniq_keys, _init_val_0, np.int64)
    # _init_val_1 = np.int64(0)
    # redvar_1_arr = np.full(n_uniq_keys, _init_val_1, np.int64)
    # out_key = np.empty(n_uniq_keys, np.float64)

    n_pes = bodo.libs.distributed_api.get_size()
    key_write_map = get_key_dict(key_arrs)

    redvar_arrs = get_shuffle_data_send_buffs(shuffle_meta, key_arrs, data_redvar_dummy)

    for i in range(len(key_arrs[0])):
        # val = key_arrs[0][i]
        val = getitem_arr_tup_single(key_arrs, i)
        if val not in key_write_map:
            node_id = node_ids[i]
            w_ind = write_send_buff(shuffle_meta, node_id, i, key_arrs, ())
            shuffle_meta.tmp_offset[node_id] += 1
            key_write_map[val] = w_ind
        else:
            w_ind = key_write_map[val]
        __update_redvars(redvar_arrs, data_in, w_ind, i, pivot_arr)
        # redvar_arrs[0][w_ind], redvar_arrs[1][w_ind] = __update_redvars(redvar_arrs[0][w_ind], redvar_arrs[1][w_ind], data_in[0][i])
    return


@numba.njit(no_cpython_wrapper=True)
def agg_parallel_combine_iter(
    key_arrs,
    reduce_recvs,
    out_dummy_tup,
    init_vals,
    __combine_redvars,
    __eval_res,
    return_key,
    data_in,
    pivot_arr,
):  # pragma: no cover
    key_set = _build_set_tup(key_arrs)
    n_uniq_keys = len(key_set)
    out_arrs = alloc_agg_output(n_uniq_keys, out_dummy_tup, key_set, return_key)
    # out_arrs = alloc_arr_tup(n_uniq_keys, out_dummy_tup)
    local_redvars = alloc_arr_tup(n_uniq_keys, reduce_recvs, init_vals)

    # key_write_map = get_key_dict(key_arrs[0])
    key_write_map = get_key_dict(key_arrs)
    curr_write_ind = 0
    for i in range(len(key_arrs[0])):
        # k = key_arrs[0][i]
        k = getitem_arr_tup_single(key_arrs, i)
        if k not in key_write_map:
            w_ind = curr_write_ind
            curr_write_ind += 1
            key_write_map[k] = w_ind
            if return_key:
                _set_out_keys(out_arrs, w_ind, key_arrs, i, k)
                # setitem_array_with_str(out_arrs[-1], w_ind, k)
                # out_arrs[-1][w_ind] = k
        else:
            w_ind = key_write_map[k]
        __combine_redvars(local_redvars, reduce_recvs, w_ind, i, pivot_arr)
    for j in range(n_uniq_keys):
        __eval_res(local_redvars, out_arrs, j)

    return out_arrs


@numba.njit(no_cpython_wrapper=True)
def agg_seq_iter(
    key_arrs,
    redvar_dummy_tup,
    out_dummy_tup,
    data_in,
    init_vals,
    __update_redvars,
    __eval_res,
    return_key,
    pivot_arr,
):  # pragma: no cover
    key_set = _build_set_tup(key_arrs)
    n_uniq_keys = len(key_set)
    out_arrs = alloc_agg_output(n_uniq_keys, out_dummy_tup, key_set, return_key)
    # out_arrs = alloc_arr_tup(n_uniq_keys, out_dummy_tup)
    local_redvars = alloc_arr_tup(n_uniq_keys, redvar_dummy_tup, init_vals)

    key_write_map = get_key_dict(key_arrs)
    curr_write_ind = 0
    for i in range(len(key_arrs[0])):
        # k = key_arrs[0][i]
        k = getitem_arr_tup_single(key_arrs, i)
        if k not in key_write_map:
            w_ind = curr_write_ind
            curr_write_ind += 1
            key_write_map[k] = w_ind
            if return_key:
                _set_out_keys(out_arrs, w_ind, key_arrs, i, k)
                # setitem_array_with_str(out_arrs[-1], w_ind, k)
                # out_arrs[-1][w_ind] = k
        else:
            w_ind = key_write_map[k]
        __update_redvars(local_redvars, data_in, w_ind, i, pivot_arr)
    for j in range(n_uniq_keys):
        __eval_res(local_redvars, out_arrs, j)

    return out_arrs


def get_shuffle_data_send_buffs(sh, karrs, data):  # pragma: no cover
    return ()


@overload(get_shuffle_data_send_buffs, no_unliteral=True)
def get_shuffle_data_send_buffs_overload(meta, key_arrs, data):
    n_keys = len(key_arrs.types)
    count = len(data.types)

    func_text = "def send_buff_impl(meta, key_arrs, data):\n"
    func_text += "  return ({}{})\n".format(
        ",".join(
            ["meta.send_buff_tup[{}]".format(i) for i in range(n_keys, count + n_keys)]
        ),
        "," if count == 1 else "",
    )  # single value needs comma to become tuple

    loc_vars = {}
    exec(func_text, {}, loc_vars)
    send_buff_impl = loc_vars["send_buff_impl"]
    return send_buff_impl


def get_key_dict(arr):  # pragma: no cover
    return dict()


@overload(get_key_dict, no_unliteral=True)
def get_key_dict_overload(arr):
    """returns dictionary and possibly a byte_vec for multi-key case
    """
    # get byte_vec dict for multi-key case
    if isinstance(arr, types.BaseTuple) and len(arr.types) != 1:
        key_typ = types.Tuple([a.dtype for a in arr.types])

        def _impl(arr):  # pragma: no cover
            return numba.typed.Dict.empty(key_type=key_typ, value_type=types.int64)

        return _impl

    # regular scalar keys
    dtype = arr.types[0].dtype
    return lambda arr: numba.typed.Dict.empty(dtype, types.int64)


def _set_out_keys(out_arrs, w_ind, key_arrs, i, k):  # pragma: no cover
    setitem_array_with_str(out_arrs[-1], w_ind, k)


@overload(_set_out_keys, no_unliteral=True)
def _set_out_keys_overload(out_arrs, w_ind, key_arrs, i, k):
    if isinstance(key_arrs, types.BaseTuple):
        n_keys = len(key_arrs.types)
        n_outs = len(out_arrs.types)
        key_start = n_outs - n_keys

        func_text = "def set_keys_impl(out_arrs, w_ind, key_arrs, i, k):\n"
        for i in range(n_keys):
            func_text += "  setitem_array_with_str(out_arrs[{}], w_ind, key_arrs[{}][i])\n".format(
                key_start + i, i
            )

        loc_vars = {}
        exec(func_text, {"setitem_array_with_str": setitem_array_with_str}, loc_vars)
        set_keys_impl = loc_vars["set_keys_impl"]
        return set_keys_impl

    return _set_out_keys


def get_numba_set(dtype):
    pass


@infer_global(get_numba_set)
class GetNumbaSetTyper(AbstractTemplate):
    def generic(self, args, kws):
        assert not kws
        assert len(args) == 1
        arr = args[0]
        dtype = (
            types.Tuple([t.dtype for t in arr.types])
            if isinstance(arr, types.BaseTuple)
            else arr.dtype
        )
        if isinstance(arr, types.BaseTuple) and len(arr.types) == 1:
            dtype = arr.types[0].dtype
        return signature(types.Set(dtype), *args)


@lower_builtin(get_numba_set, types.Any)
def lower_get_numba_set(context, builder, sig, args):
    return numba.cpython.setobj.set_empty_constructor(context, builder, sig, args)


def get_key_set(arr):  # pragma: no cover
    # return set()
    return dict()


@overload(get_key_set, no_unliteral=True)
def get_key_set_overload(arr):

    # XXX using dict instead of set due to refcount issue
    # return lambda arr: get_numba_set(arr)
    dtype = (
        types.Tuple([t.dtype for t in arr.types])
        if isinstance(arr, types.BaseTuple)
        else arr.dtype
    )
    if isinstance(arr, types.BaseTuple) and len(arr.types) == 1:
        dtype = arr.types[0].dtype

    return lambda arr: numba.typed.Dict.empty(dtype, types.int64)

    # HACK below can cause crashes in case of zero-length arrays
    # if isinstance(arr, types.BaseTuple):
    #     def get_set_tup(arr):
    #         s = set()
    #         v = getitem_arr_tup_single(arr, 0)
    #         s.add(v)
    #         s.remove(v)
    #         return s
    #     return get_set_tup

    # # hack to return set with specified type
    # def get_set(arr):
    #     s = set()
    #     s.add(arr[0])
    #     s.remove(arr[0])
    #     return s

    # return get_set


def alloc_agg_output(
    n_uniq_keys, out_dummy_tup, key_set, return_key
):  # pragma: no cover
    return out_dummy_tup


@overload(alloc_agg_output, no_unliteral=True)
def alloc_agg_output_overload(n_uniq_keys, out_dummy_tup, key_set, return_key):

    # return key is either True or None
    if is_overload_true(return_key) or return_key == types.boolean:
        # TODO: handle pivot_table/crosstab with return key
        # dtype = key_set.dtype
        dtype = key_set.key_type
        key_types = list(dtype.types) if isinstance(dtype, types.BaseTuple) else [dtype]
        n_keys = len(key_types)
        n_data = out_dummy_tup.count - n_keys

        func_text = (
            "def out_alloc_f(n_uniq_keys, out_dummy_tup, key_set, return_key):\n"
        )
        for i in range(n_data):
            func_text += "  c_{} = empty_like_type(n_uniq_keys, out_dummy_tup[{}])\n".format(
                i, i
            )

        if string_type in key_types:
            # TODO: handle unicode length
            func_text += "  num_total_chars = num_total_chars_set(key_set)\n"

        for i, key_typ in enumerate(key_types):
            if key_typ == string_type:
                func_text += "  out_key_{0} = pre_alloc_string_array(n_uniq_keys, num_total_chars[{0}])\n".format(
                    i
                )
            else:
                func_text += "  out_key_{} = np.empty(n_uniq_keys, np.{})\n".format(
                    i, key_typ
                )

        func_text += "  return ({}{}{},)\n".format(
            ", ".join(["c_{}".format(i) for i in range(n_data)]),
            "," if n_data != 0 else "",
            ", ".join(["out_key_{}".format(i) for i in range(n_keys)]),
        )

        loc_vars = {}
        exec(
            func_text,
            {
                "empty_like_type": empty_like_type,
                "np": np,
                "pre_alloc_string_array": pre_alloc_string_array,
                "num_total_chars_set": num_total_chars_set,
            },
            loc_vars,
        )
        alloc_impl = loc_vars["out_alloc_f"]
        return alloc_impl

    assert return_key == types.none

    def no_key_out_alloc(n_uniq_keys, out_dummy_tup, key_set, return_key):
        return alloc_arr_tup(n_uniq_keys, out_dummy_tup)

    return no_key_out_alloc


# TODO: fix BaseContext.get_function() used in is_true()
# @overload(bool, no_unliteral=True)
# def bool_none_overload(v_t):
#     if v_t == types.none:
#         return lambda a: False


@infer_global(bool)
class BoolNoneTyper(AbstractTemplate):
    def generic(self, args, kws):
        assert not kws
        assert len(args) == 1
        val_t = args[0]
        if val_t == types.none:
            return signature(types.boolean, *args)


@lower_builtin(bool, types.none)
def lower_column_mean_impl(context, builder, sig, args):
    res = context.compile_internal(builder, lambda a: False, sig, args)
    return res  # impl_ret_untracked(context, builder, sig.return_type, res)


def setitem_array_with_str(arr, i, v):  # pragma: no cover
    return


@overload(setitem_array_with_str)
def setitem_array_with_str_overload(arr, i, val):
    if arr == string_array_type:

        def setitem_str_arr(arr, i, val):  # pragma: no cover
            arr[i] = val

        return setitem_str_arr

    # return_key == False case where val could be string resulting in typing
    # issue, no need to set
    if val == string_type:
        return lambda arr, i, val: None

    def setitem_impl(arr, i, val):  # pragma: no cover
        arr[i] = val

    return setitem_impl


def _gen_dummy_alloc(t):
    """generate dummy allocation text for type `t`, used for creating dummy arrays that
    just pass data type to functions.
    """
    # TODO: support other types
    if t == string_array_type:
        return "pre_alloc_string_array(1, 1)"
    else:
        return "np.empty(1, {})".format(_get_np_dtype(t.dtype))


def _get_np_dtype(t):
    if t == types.NPDatetime("ns"):
        return "dt64_dtype"
    return "np.{}".format(t)


def gen_update_cb(
    udf_func_struct,
    allfuncs,
    n_keys,
    data_in_typs_,
    out_data_typs,
    do_combine,
    func_idx_to_in_col,
):
    """
    Generates a Python function (to be compiled into a numba cfunc) which
    does the "update" step of an agg operation. The code is for a specific
    groupby.agg(). The update step performs the initial local aggregation.
    """
    red_var_typs = udf_func_struct.var_typs
    n_red_vars = len(red_var_typs)

    func_text = "def update_local(in_table, out_table, row_to_group):\n"

    # get redvars data types
    func_text += "    data_redvar_dummy = ({}{})\n".format(
        ",".join(["np.empty(1, {})".format(_get_np_dtype(t)) for t in red_var_typs]),
        "," if len(red_var_typs) == 1 else "",
    )

    # calculate the offsets of redvars of udfs in the table received from C++.
    # Note that the table can contain a mix of columns from udfs and builtins
    col_offset = n_keys  # keys are the first columns in the table, skip them
    in_col_offsets = []
    redvar_offsets = []  # offsets of redvars in the table received from C++
    data_in_typs = []
    if do_combine:
        # the groupby will do a combine after update and shuffle. This means
        # the table we are receiving is pre_shuffle
        for i, f in enumerate(allfuncs):
            if f.ftype != "udf":
                col_offset += f.ncols_pre_shuffle
            else:
                redvar_offsets += list(range(col_offset, col_offset + f.n_redvars))
                col_offset += f.n_redvars
                data_in_typs.append(data_in_typs_[func_idx_to_in_col[i]])
                in_col_offsets.append(func_idx_to_in_col[i] + n_keys)
    else:
        # a combine won't be done in this case (which means either a shuffle
        # was done before update, or no shuffle is necessary, so the table
        # we are getting is post_shuffle table
        for i, f in enumerate(allfuncs):
            if f.ftype != "udf":
                col_offset += f.ncols_post_shuffle
            else:
                # udfs in post_shuffle table have one column for output plus
                # redvars columns
                redvar_offsets += list(
                    range(col_offset + 1, col_offset + 1 + f.n_redvars)
                )
                col_offset += f.n_redvars + 1
                data_in_typs.append(data_in_typs_[func_idx_to_in_col[i]])
                in_col_offsets.append(func_idx_to_in_col[i] + n_keys)
    assert len(redvar_offsets) == n_red_vars

    # get input data types
    n_data_cols = len(data_in_typs)
    data_in_dummy_text = []
    for t in data_in_typs:
        data_in_dummy_text.append(_gen_dummy_alloc(t))
    func_text += "    data_in_dummy = ({}{})\n".format(
        ",".join(data_in_dummy_text), "," if len(data_in_typs) == 1 else ""
    )

    func_text += "\n    # initialize redvar cols\n"
    func_text += "    init_vals = __init_func()\n"
    for i in range(n_red_vars):
        func_text += "    redvar_arr_{} = info_to_array(info_from_table(out_table, {}), data_redvar_dummy[{}])\n".format(
            i, redvar_offsets[i], i
        )
        # incref needed so that arrays aren't deleted after this function exits
        func_text += "    incref(redvar_arr_{})\n".format(i)
        func_text += "    redvar_arr_{}.fill(init_vals[{}])\n".format(i, i)
    func_text += "    redvars = ({}{})\n".format(
        ",".join(["redvar_arr_{}".format(i) for i in range(n_red_vars)]),
        "," if n_red_vars == 1 else "",
    )

    func_text += "\n"
    for i in range(n_data_cols):
        func_text += "    data_in_{} = info_to_array(info_from_table(in_table, {}), data_in_dummy[{}])\n".format(
            i, in_col_offsets[i], i
        )
        # incref needed so that arrays aren't deleted after this function exits
        func_text += "    incref(data_in_{})\n".format(i)
    func_text += "    data_in = ({}{})\n".format(
        ",".join(["data_in_{}".format(i) for i in range(n_data_cols)]),
        "," if n_data_cols == 1 else "",
    )

    func_text += "\n"
    func_text += "    for i in range(len(data_in_0)):\n"
    func_text += "        w_ind = row_to_group[i]\n"
    func_text += "        if w_ind != -1:\n"
    func_text += (
        "            __update_redvars(redvars, data_in, w_ind, i, pivot_arr=None)\n"
    )

    loc_vars = {}
    exec(
        func_text,
        {
            "np": np,
            "info_to_array": info_to_array,
            "info_from_table": info_from_table,
            "incref": incref,
            "pre_alloc_string_array": pre_alloc_string_array,
            "__init_func": udf_func_struct.init_func,
            "__update_redvars": udf_func_struct.update_all_func,
        },
        loc_vars,
    )
    return loc_vars["update_local"]


def gen_combine_cb(udf_func_struct, allfuncs, n_keys, out_data_typs):
    """
    Generates a Python function (to be compiled into a numba cfunc) which
    does the "combine" step of an agg operation. The code is for a specific
    groupby.agg(). The combine step combines the received aggregated data from
    other processes.
    """
    red_var_typs = udf_func_struct.var_typs
    n_red_vars = len(red_var_typs)

    func_text = "def combine(in_table, out_table, row_to_group):\n"

    # get redvars data types
    func_text += "    data_redvar_dummy = ({}{})\n".format(
        ",".join(["np.empty(1, {})".format(_get_np_dtype(t)) for t in red_var_typs]),
        "," if len(red_var_typs) == 1 else "",
    )

    # calculate the offsets of redvars of udfs in the tables received from C++.
    # Note that the tables can contain a mix of columns from udfs and builtins.
    # The input table is the pre_shuffle table right after shuffling (so has
    # the same specs as pre_shuffle). post_shuffle is the output table from
    # combine operation
    col_offset_in = n_keys
    col_offset_out = n_keys
    redvar_offsets_in = []  # offsets of udf redvars in the table received from C++
    redvar_offsets_out = []  # offsets of udf redvars in the table received from C++
    for f in allfuncs:
        if f.ftype != "udf":
            col_offset_in += f.ncols_pre_shuffle
            col_offset_out += f.ncols_post_shuffle
        else:
            redvar_offsets_in += list(range(col_offset_in, col_offset_in + f.n_redvars))
            # udfs in post_shuffle table have one column for output plus
            # redvars columns
            redvar_offsets_out += list(
                range(col_offset_out + 1, col_offset_out + 1 + f.n_redvars)
            )
            col_offset_in += f.n_redvars
            col_offset_out += 1 + f.n_redvars
    assert len(redvar_offsets_in) == n_red_vars

    func_text += "\n    # initialize redvar cols\n"
    func_text += "    init_vals = __init_func()\n"
    for i in range(n_red_vars):
        func_text += "    redvar_arr_{} = info_to_array(info_from_table(out_table, {}), data_redvar_dummy[{}])\n".format(
            i, redvar_offsets_out[i], i
        )
        # incref needed so that arrays aren't deleted after this function exits
        func_text += "    incref(redvar_arr_{})\n".format(i)
        func_text += "    redvar_arr_{}.fill(init_vals[{}])\n".format(i, i)
    func_text += "    redvars = ({}{})\n".format(
        ",".join(["redvar_arr_{}".format(i) for i in range(n_red_vars)]),
        "," if n_red_vars == 1 else "",
    )

    func_text += "\n"
    for i in range(n_red_vars):
        func_text += "    recv_redvar_arr_{} = info_to_array(info_from_table(in_table, {}), data_redvar_dummy[{}])\n".format(
            i, redvar_offsets_in[i], i
        )
        # incref needed so that arrays aren't deleted after this function exits
        func_text += "    incref(recv_redvar_arr_{})\n".format(i)
    func_text += "    recv_redvars = ({}{})\n".format(
        ",".join(["recv_redvar_arr_{}".format(i) for i in range(n_red_vars)]),
        "," if n_red_vars == 1 else "",
    )

    func_text += "\n"
    if n_red_vars:  # if there is a parfor
        func_text += "    for i in range(len(recv_redvar_arr_0)):\n"
        func_text += "        w_ind = row_to_group[i]\n"
        func_text += "        __combine_redvars(redvars, recv_redvars, w_ind, i, pivot_arr=None)\n"

    loc_vars = {}
    exec(
        func_text,
        {
            "np": np,
            "info_to_array": info_to_array,
            "info_from_table": info_from_table,
            "incref": incref,
            "__init_func": udf_func_struct.init_func,
            "__combine_redvars": udf_func_struct.combine_all_func,
        },
        loc_vars,
    )
    return loc_vars["combine"]


def gen_eval_cb(udf_func_struct, allfuncs, n_keys, out_data_typs_):
    """
    Generates a Python function (to be compiled into a numba cfunc) which
    does the "eval" step of an agg operation. The code is for a specific
    groupby.agg(). The eval step writes the final result to the output columns
    for each group.
    """
    red_var_typs = udf_func_struct.var_typs
    n_red_vars = len(red_var_typs)

    # calculate the offsets of redvars and output columns of udfs in the table
    # received from C++. Note that the table can contain a mix of columns from
    # udfs and builtins
    col_offset = n_keys
    redvar_offsets = []  # offsets of redvars in the table received from C++
    data_out_offsets = []  # offsets of data col in the table received from C++
    out_data_typs = []
    for i, f in enumerate(allfuncs):
        if f.ftype != "udf":
            col_offset += f.ncols_post_shuffle
        else:
            # udfs in post_shuffle table have one column for output plus
            # redvars columns
            data_out_offsets.append(col_offset)
            redvar_offsets += list(range(col_offset + 1, col_offset + 1 + f.n_redvars))
            col_offset += 1 + f.n_redvars
            out_data_typs.append(out_data_typs_[i])
    assert len(redvar_offsets) == n_red_vars
    n_data_cols = len(out_data_typs)

    func_text = "def eval(table):\n"
    func_text += "    data_redvar_dummy = ({}{})\n".format(
        ",".join(["np.empty(1, {})".format(_get_np_dtype(t)) for t in red_var_typs]),
        "," if len(red_var_typs) == 1 else "",
    )

    func_text += "    out_data_dummy = ({}{})\n".format(
        ",".join(
            ["np.empty(1, {})".format(_get_np_dtype(t.dtype)) for t in out_data_typs]
        ),
        "," if len(out_data_typs) == 1 else "",
    )

    for i in range(n_red_vars):
        func_text += "    redvar_arr_{} = info_to_array(info_from_table(table, {}), data_redvar_dummy[{}])\n".format(
            i, redvar_offsets[i], i
        )
        # incref needed so that arrays aren't deleted after this function exits
        func_text += "    incref(redvar_arr_{})\n".format(i)
    func_text += "    redvars = ({}{})\n".format(
        ",".join(["redvar_arr_{}".format(i) for i in range(n_red_vars)]),
        "," if n_red_vars == 1 else "",
    )

    func_text += "\n"
    for i in range(n_data_cols):
        func_text += "    data_out_{} = info_to_array(info_from_table(table, {}), out_data_dummy[{}])\n".format(
            i, data_out_offsets[i], i
        )
        # incref needed so that arrays aren't deleted after this function exits
        func_text += "    incref(data_out_{})\n".format(i)
    func_text += "    data_out = ({}{})\n".format(
        ",".join(["data_out_{}".format(i) for i in range(n_data_cols)]),
        "," if n_data_cols == 1 else "",
    )

    func_text += "\n"
    func_text += "    for i in range(len(data_out_0)):\n"
    func_text += "        __eval_res(redvars, data_out, i)\n"

    loc_vars = {}
    exec(
        func_text,
        {
            "np": np,
            "info_to_array": info_to_array,
            "info_from_table": info_from_table,
            "incref": incref,
            "__eval_res": udf_func_struct.eval_all_func,
        },
        loc_vars,
    )
    return loc_vars["eval"]


def gen_allfuncs(agg_func, nb_col):
    if not isinstance(agg_func, list):
        agg_func_work = [agg_func] * nb_col
    else:
        agg_func_work = agg_func
    allfuncs = []
    for f_val in agg_func_work:
        if isinstance(f_val, list):
            allfuncs += f_val
        else:
            allfuncs += [f_val]
    return allfuncs


def gen_top_level_agg_func(
    key_names,
    key_types,
    return_key,
    in_col_typs,
    out_col_typs,
    in_col_names,
    out_col_names,
    agg_func,
    same_index,
    parallel,
    offload,
    udf_func_struct,
):
    """create the top level aggregation function by generating text
    """
    # If we output the index then we need to remove it from the list of variables.
    if same_index:
        in_col_typs = in_col_typs[0:-1]
        in_col_names = list(in_col_names)[0:-1]
    out_typs = [t.dtype for t in out_col_typs]

    # arg names
    key_names = tuple(sanitize_varname(c) for c in key_names)
    in_names = tuple("in_{}".format(sanitize_varname(c)) for c in in_col_names)
    out_names = []
    for c in out_col_names:
        if not isinstance(c, (tuple, list)):
            if c.startswith("<lambda"):
                # remove brackets from "<lambda>" to avoid syntax errors
                out_names.append("out_" + c[1:-1])
            else:
                out_names.append("out_" + c)
        else:
            # convert multi-level names into something that won't give syntax
            # errors
            # TODO lambdas inside tuple (depends on a TODO in pd_groupby_ext.py)
            out_names.append("out_{}".format("__".join(v for v in c)))
    out_names = tuple(sanitize_varname(c) for c in out_names)
    key_args = ", ".join("key_{}".format(sanitize_varname(c)) for c in key_names)

    in_args = ", ".join(in_names)
    if in_args != "":
        in_args = ", " + in_args

    # pass None instead of False to enable static specialization in
    # alloc_agg_output()
    return_key_p = "True" if return_key else "None"

    if not offload:
        red_var_typs = udf_func_struct.var_typs

        func_text = "def agg_top({}{}, pivot_arr):\n".format(key_args, in_args)
        func_text += "    data_redvar_dummy = ({}{})\n".format(
            ",".join(
                ["np.empty(1, {})".format(_get_np_dtype(t)) for t in red_var_typs]
            ),
            "," if len(red_var_typs) == 1 else "",
        )
        func_text += "    out_dummy_tup = ({}{}{})\n".format(
            ",".join(["np.empty(1, {})".format(_get_np_dtype(t)) for t in out_typs]),
            "," if len(out_typs) != 0 else "",
            "{},".format(key_args) if return_key else "",
        )
        func_text += "    data_in = ({}{})\n".format(
            ",".join(in_names), "," if len(in_names) == 1 else ""
        )
        func_text += "    init_vals = __init_func()\n"

        out_keys = tuple("out_key_{}".format(sanitize_varname(c)) for c in key_names)
        out_tup = ", ".join(out_names + out_keys if return_key else out_names)

        if parallel:
            func_text += "    i_arr_tab = arr_info_list_to_table([{}])\n".format(
                ",".join(
                    "array_to_info(key_{})".format(sanitize_varname(c))
                    for c in key_names
                )
            )
            func_text += "    n_pes = bodo.libs.distributed_api.get_size()\n"
            func_text += "    o_arr_tab = compute_node_partition_by_hash(i_arr_tab, {}, n_pes)\n".format(
                len(key_names)
            )
            func_text += "    data_dummy = np.empty(1, np.int32)\n"
            func_text += "    node_arr = info_to_array(info_from_table(o_arr_tab, 0), data_dummy)\n"
            func_text += "    delete_table(o_arr_tab)\n"
            func_text += "    delete_table(i_arr_tab)\n"
            func_text += (
                "    ({},) = parallel_agg(({},), node_arr, data_redvar_dummy, "
                "out_dummy_tup, data_in, init_vals, __update_redvars, "
                "__combine_redvars, __eval_res, {}, pivot_arr)\n"
            ).format(out_tup, key_args, return_key_p)
        else:
            func_text += (
                "    ({},) = agg_seq_iter(({},), data_redvar_dummy, "
                "out_dummy_tup, data_in, init_vals, __update_redvars, "
                "__eval_res, {}, pivot_arr)\n"
            ).format(out_tup, key_args, return_key_p)

        func_text += "    return ({},)\n".format(out_tup)
    else:
        all_arrs = tuple("key_{}".format(c) for c in key_names) + in_names
        n_keys = len(key_names)
        # If we put the index as argument, then it is the last argument of the
        # function.
        func_text = "def agg_top({}{}{}, pivot_arr):\n".format(
            key_args, in_args, ", index_arg" if same_index else ""
        )

        # convert arrays to table
        func_text += "    info_list = [{}{}]\n".format(
            ", ".join("array_to_info({})".format(a) for a in all_arrs),
            ", array_to_info(index_arg)" if same_index else "",
        )
        func_text += "    table = arr_info_list_to_table(info_list)\n"

        for i in range(len(out_names)):
            out_name = out_names[i] + "_dummy"
            if isinstance(out_col_typs[i], IntegerArrayType):
                int_typ_name = IntDtype(out_typs[i]).name
                assert int_typ_name.endswith("Dtype()")
                int_typ_name = int_typ_name[:-7]  # remove trailing "Dtype()"
                func_text += '    {} = pd.Series([1], dtype="{}").values\n'.format(
                    out_name, int_typ_name
                )
            elif isinstance(out_col_typs[i], BooleanArrayType):
                func_text += "    {} = bodo.libs.bool_arr_ext.init_bool_array(np.empty(0, np.bool_), np.empty(0, np.uint8))\n".format(
                    out_name
                )
            elif isinstance(out_col_typs[i], StringArrayType):
                func_text += "    {} = pre_alloc_string_array(1,1)\n".format(out_name)
            elif out_col_typs[i] == ArrayItemArrayType(string_array_type):
                func_text += "    {} = pre_alloc_array_item_array(1, (1, 1), string_array_type)\n".format(
                    out_name
                )
            elif isinstance(out_col_typs[i], DecimalArrayType):
                scale = out_col_typs[i].scale
                precision = out_col_typs[i].precision
                func_text += "    {} = alloc_decimal_array(1, {}, {})\n".format(
                    out_name, precision, scale
                )
            else:
                func_text += "    {} = np.empty(1, {})\n".format(
                    out_name, _get_np_dtype(out_typs[i])
                )

        # do_combine indicates whether GroupbyPipeline in C++ will need to do
        # `void combine()` operation or not
        do_combine = parallel
        # flat list of aggregation functions, one for each (input_col, func)
        # combination, each combination results in one output column
        allfuncs = []
        # index of first function (in allfuncs) of input col i
        func_offsets = []
        # map index of function i in allfuncs to its input col
        func_idx_to_in_col = []
        # number of redvars for each udf function
        udf_ncols = []
        if not isinstance(agg_func, list):
            agg_func = [agg_func] * len(in_col_typs)
        skipdropna = False
        num_cum_funcs = 0
        for in_col_idx, f_val in enumerate(agg_func):
            # for each input column, a list of functions can be applied to it
            func_offsets.append(len(allfuncs))
            if not isinstance(f_val, list):
                funcs = [f_val]
            else:
                funcs = f_val
            for func in funcs:
                if func.ftype in {"median", "nunique"}:
                    # these operations require shuffle at the beginning, so a
                    # local aggregation followed by combine is not necessary
                    do_combine = False
                if func.ftype in list_cumulative:
                    num_cum_funcs += 1
                if hasattr(func, "skipdropna"):
                    skipdropna = func.skipdropna
                allfuncs.append(func)
                func_idx_to_in_col.append(in_col_idx)
                if func.ftype == "udf":
                    udf_ncols.append(func.n_redvars)
        func_offsets.append(len(allfuncs))
        assert len(out_names) == len(allfuncs)
        if num_cum_funcs > 0:
            assert num_cum_funcs == len(
                allfuncs
            ), "Cannot mix cumulative operations with other aggregation functions"
            do_combine = False  # same as median and nunique

        if udf_func_struct is not None:
            # there are user-defined functions

            # generate update, combine and eval functions for the user-defined
            # functions and compile them to numba cfuncs, to be called from C++
            c_sig = types.void(
                types.voidptr, types.voidptr, types.CPointer(types.int64)
            )
            cpp_cb_update = numba.cfunc(c_sig, nopython=True)(
                gen_update_cb(
                    udf_func_struct,
                    allfuncs,
                    n_keys,
                    in_col_typs,
                    out_col_typs,
                    do_combine,
                    func_idx_to_in_col,
                )
            )
            cpp_cb_combine = numba.cfunc(c_sig, nopython=True)(
                gen_combine_cb(udf_func_struct, allfuncs, n_keys, out_col_typs)
            )
            cpp_cb_eval = numba.cfunc("void(voidptr)", nopython=True)(
                gen_eval_cb(udf_func_struct, allfuncs, n_keys, out_col_typs)
            )

            cpp_cb_update_addr = cpp_cb_update.address
            cpp_cb_combine_addr = cpp_cb_combine.address
            cpp_cb_eval_addr = cpp_cb_eval.address

            # generate a dummy (empty) table with correct type info for
            # output columns and reduction variables corresponding to udfs,
            # so that C++ library can allocate arrays
            udf_names_dummy = []
            redvar_offset = 0
            i = 0
            for out_name, f in zip(out_names, allfuncs):
                if f.ftype == "udf":
                    udf_names_dummy.append(out_name + "_dummy")
                    for j in range(redvar_offset, redvar_offset + udf_ncols[i]):
                        udf_names_dummy.append("data_redvar_dummy_" + str(j))
                    redvar_offset += udf_ncols[i]
                    i += 1

            red_var_typs = udf_func_struct.var_typs
            for i, t in enumerate(red_var_typs):
                func_text += "    data_redvar_dummy_{} = np.empty(1, {})\n".format(
                    i, _get_np_dtype(t)
                )

            func_text += "    out_info_list_dummy = [{}]\n".format(
                ", ".join("array_to_info({})".format(a) for a in udf_names_dummy)
            )
            func_text += (
                "    udf_table_dummy = arr_info_list_to_table(out_info_list_dummy)\n"
            )

        else:
            # if there are no udfs we don't need udf table, so just create
            # an empty one-column table
            func_text += "    udf_table_dummy = arr_info_list_to_table([array_to_info(np.empty(1))])\n"
            cpp_cb_update_addr = 0
            cpp_cb_combine_addr = 0
            cpp_cb_eval_addr = 0

        func_text += "    ftypes = np.array({}, dtype=np.int32)\n".format(
            str([supported_agg_funcs.index(f.ftype) for f in allfuncs])
        )
        func_text += "    func_offsets = np.array({}, dtype=np.int32)\n".format(
            str(func_offsets)
        )
        if len(udf_ncols) > 0:
            func_text += "    udf_ncols = np.array({}, dtype=np.int32)\n".format(
                str(udf_ncols)
            )
        else:
            func_text += "    udf_ncols = np.array([0], np.int32)\n"  # dummy
        # call C++ groupby
        # We pass the logical arguments to the function (skipdropna, return_key, same_index, ...)
        func_text += (
            "    out_table = groupby_and_aggregate(table, {},"
            " ftypes.ctypes.data, func_offsets.ctypes.data, udf_ncols.ctypes.data, {}, {}, {}, {}, {}, {}, {}, udf_table_dummy)\n".format(
                n_keys,
                parallel,
                skipdropna,
                return_key,
                same_index,
                cpp_cb_update_addr,
                cpp_cb_combine_addr,
                cpp_cb_eval_addr,
            )
        )

        key_names = ["key_" + name for name in key_names]
        idx = 0
        if return_key:
            for i, key_name in enumerate(key_names):
                func_text += "    {} = info_to_array(info_from_table(out_table, {}), {})\n".format(
                    key_name, idx, key_name
                )
                idx += 1
        for i in range(len(out_names)):
            out_name = out_names[i]
            func_text += "    {} = info_to_array(info_from_table(out_table, {}), {})\n".format(
                out_name, idx, out_name + "_dummy"
            )
            idx += 1
        # The index as last argument in output as well.
        if same_index:
            func_text += "    out_index_arg = info_to_array(info_from_table(out_table, {}), index_arg)\n".format(
                idx
            )
            idx += 1
        # clean up
        func_text += "    delete_table(table)\n"
        func_text += "    delete_table(out_table)\n"

        ret_names = out_names
        if return_key:
            ret_names += tuple(key_names)
        func_text += "    return ({},{})\n".format(
            ", ".join(ret_names), " out_index_arg," if same_index else ""
        )

    loc_vars = {}
    exec(func_text, {}, loc_vars)
    agg_top = loc_vars["agg_top"]
    return agg_top


def gen_top_level_transform_func(key_names, in_col_names, out_col_names, parallel):
    """create the top level transformation function by generating text
    """

    # arg names
    in_names = tuple("in_{}".format(c) for c in in_col_names)
    out_names = tuple("out_{}".format(c) for c in out_col_names)
    key_names = tuple("key_{}".format(sanitize_varname(c)) for c in key_names)
    key_args = ", ".join(key_names)

    in_args = ", ".join(in_names)
    if in_args != "":
        in_args = ", " + in_args

    func_text = "def agg_top({}{}, pivot_arr):\n".format(key_args, in_args)
    func_text += "    data_in = ({}{})\n".format(
        ",".join(in_names), "," if len(in_names) == 1 else ""
    )
    func_text += "    key_in = ({}{})\n".format(
        ",".join(key_names), "," if len(key_names) == 1 else ""
    )

    out_tup = ", ".join(out_names)

    # cumsum ignores the index and returns a Series with values in the same
    # order as original column. Therefore, we need to shuffle the data back.
    if parallel:
        func_text += "    i_arr_tab = arr_info_list_to_table([{}])\n".format(
            ",".join("array_to_info({})".format(c) for c in key_names)
        )
        func_text += "    n_pes = bodo.libs.distributed_api.get_size()\n"
        func_text += "    o_arr_tab = compute_node_partition_by_hash(i_arr_tab, {}, n_pes)\n".format(
            len(key_names)
        )
        func_text += "    data_dummy = np.empty(1, np.int32)\n"
        func_text += (
            "    node_arr = info_to_array(info_from_table(o_arr_tab, 0), data_dummy)\n"
        )
        func_text += "    delete_table(o_arr_tab)\n"
        func_text += "    delete_table(i_arr_tab)\n"
        func_text += "    key_in, data_in, orig_indices, shuffle_meta = bodo.utils.shuffle.shuffle_with_index(key_in, node_arr, data_in)\n".format()

    func_text += "    ({},) = group_cumsum(key_in, data_in)\n".format(out_tup)

    if parallel:
        func_text += "    ({0},) = bodo.utils.shuffle.reverse_shuffle(({0},), orig_indices, shuffle_meta)\n".format(
            out_tup
        )

    func_text += "    return ({},)\n".format(out_tup)

    loc_vars = {}
    exec(func_text, {}, loc_vars)
    agg_top = loc_vars["agg_top"]
    return agg_top


def compile_to_optimized_ir(func, arg_typs, typingctx):
    # TODO: reuse Numba's compiler pipelines
    # XXX are outside function's globals needed?
    code = func.code if hasattr(func, "code") else func.__code__
    closure = func.closure if hasattr(func, "closure") else func.__closure__
    f_ir = get_ir_of_code(func.__globals__, code)
    replace_closures(f_ir, closure, code)

    # replace len(arr) calls (i.e. size of group) with a sentinel function that will be
    # replaced with a simple loop in series pass
    for block in f_ir.blocks.values():
        for stmt in block.body:
            if (
                is_call_assign(stmt)
                and find_callname(f_ir, stmt.value) == ("len", "builtins")
                and stmt.value.args[0].name == f_ir.arg_names[0]
            ):
                len_global = get_definition(f_ir, stmt.value.func)
                len_global.name = "dummy_agg_count"
                len_global.value = dummy_agg_count

    # rename all variables to avoid conflict (init and eval nodes)
    var_table = get_name_var_table(f_ir.blocks)
    new_var_dict = {}
    for name, _ in var_table.items():
        new_var_dict[name] = mk_unique_var(name)
    replace_var_names(f_ir.blocks, new_var_dict)
    f_ir._definitions = build_definitions(f_ir.blocks)

    assert f_ir.arg_count == 1, "agg function should have one input"
    # construct default flags similar to numba.core.compiler
    flags = numba.core.compiler.Flags()
    flags.set("nrt")
    untyped_pass = bodo.transforms.untyped_pass.UntypedPass(
        f_ir, typingctx, arg_typs, {}, {}, flags
    )
    untyped_pass.run()
    f_ir._definitions = build_definitions(f_ir.blocks)
    typemap, return_type, calltypes = numba.core.typed_passes.type_inference_stage(
        typingctx, f_ir, arg_typs, None
    )

    options = numba.core.cpu.ParallelOptions(True)
    targetctx = numba.core.cpu.CPUContext(typingctx)

    DummyPipeline = namedtuple(
        "DummyPipeline",
        [
            "typingctx",
            "targetctx",
            "args",
            "func_ir",
            "typemap",
            "return_type",
            "calltypes",
            "type_annotation",
            "locals",
            "flags",
            "pipeline",
        ],
    )
    TypeAnnotation = namedtuple("TypeAnnotation", ["typemap", "calltypes"])
    ta = TypeAnnotation(typemap, calltypes)
    # The new Numba 0.50 inliner requires the pipline state itselft to be a member of
    # the pipeline state. To emulate it using a namedtuple (which is immutable), we
    # create a pipline first with the required data and add it to another one.
    pm = DummyPipeline(
        typingctx,
        targetctx,
        None,
        f_ir,
        typemap,
        return_type,
        calltypes,
        ta,
        {},
        flags,
        None,
    )
    untyped_pipeline = numba.core.compiler.DefaultPassBuilder.define_untyped_pipeline(
        pm
    )
    pm = DummyPipeline(
        typingctx,
        targetctx,
        None,
        f_ir,
        typemap,
        return_type,
        calltypes,
        ta,
        {},
        flags,
        untyped_pipeline,
    )
    # run overload inliner to inline Series implementations such as Series.max()
    inline_overload_pass = numba.core.typed_passes.InlineOverloads()
    inline_overload_pass.run_pass(pm)

    series_pass = bodo.transforms.series_pass.SeriesPass(
        f_ir, typingctx, typemap, calltypes, {}, False
    )
    series_pass.run()
    # change the input type to UDF from Series to Array since Bodo passes Arrays to UDFs
    # Series functions should be handled by SeriesPass and there should be only
    # `get_series_data` Series function left to remove
    for block in f_ir.blocks.values():
        for stmt in block.body:
            if (
                is_assign(stmt)
                and isinstance(stmt.value, (ir.Arg, ir.Var))
                and isinstance(typemap[stmt.target.name], SeriesType)
            ):
                typ = typemap.pop(stmt.target.name)
                typemap[stmt.target.name] = typ.data
            if is_call_assign(stmt) and find_callname(f_ir, stmt.value) == (
                "get_series_data",
                "bodo.hiframes.pd_series_ext",
            ):
                f_ir._definitions[stmt.target.name].remove(stmt.value)
                stmt.value = stmt.value.args[0]
                f_ir._definitions[stmt.target.name].append(stmt.value)
            # remove isna() calls since NA cannot be handled in UDFs yet
            # TODO: support NA in UDFs
            if is_call_assign(stmt) and find_callname(f_ir, stmt.value) == (
                "isna",
                "bodo.libs.array_kernels",
            ):
                f_ir._definitions[stmt.target.name].remove(stmt.value)
                stmt.value = ir.Const(False, stmt.loc)
                f_ir._definitions[stmt.target.name].append(stmt.value)

    bodo.transforms.untyped_pass.remove_dead_branches(f_ir)
    preparfor_pass = numba.parfors.parfor.PreParforPass(
        f_ir, typemap, calltypes, typingctx, options
    )
    preparfor_pass.run()
    f_ir._definitions = build_definitions(f_ir.blocks)
    state = numba.core.compiler.StateDict()
    state.func_ir = f_ir
    state.typemap = typemap
    state.calltypes = calltypes
    state.typingctx = typingctx
    state.targetctx = targetctx
    state.return_type = return_type
    numba.core.rewrites.rewrite_registry.apply("after-inference", state)
    parfor_pass = numba.parfors.parfor.ParforPass(
        f_ir, typemap, calltypes, return_type, typingctx, options, flags
    )
    parfor_pass.run()
    remove_dels(f_ir.blocks)
    # make sure eval nodes are after the parfor for easier extraction
    # TODO: extract an eval func more robustly
    numba.parfors.parfor.maximize_fusion(f_ir, f_ir.blocks, typemap, False)
    return f_ir, pm


def replace_closures(f_ir, closure, code):
    """replace closure variables similar to inline_closure_call
    """
    if closure:
        closure = f_ir.get_definition(closure)
        if isinstance(closure, tuple):
            cellget = ctypes.pythonapi.PyCell_Get
            cellget.restype = ctypes.py_object
            cellget.argtypes = (ctypes.py_object,)
            items = tuple(cellget(x) for x in closure)
        else:
            assert isinstance(closure, ir.Expr) and closure.op == "build_tuple"
            items = closure.items
        assert len(code.co_freevars) == len(items)
        numba.core.inline_closurecall._replace_freevars(f_ir.blocks, items)


def get_udf_func_struct(
    agg_func,
    same_index,
    in_col_types,
    out_col_typs,
    typingctx,
    targetctx,
    pivot_typ,
    pivot_values,
    is_crosstab,
):
    """find initialization, update, combine and final evaluation code of the
    aggregation function. Currently assuming that the function is single block
    and has one parfor.
    """
    all_reduce_vars = []
    all_vartypes = []
    all_init_nodes = []
    all_eval_funcs = []
    all_update_funcs = []
    all_combine_funcs = []
    typemap = {}
    calltypes = {}
    # offsets of reduce vars
    curr_offset = 0
    redvar_offsets = [0]
    # If same_index is selected the index is put as last argument.
    # So, it needs to be removed from the list of input columns
    if same_index:
        in_col_types = in_col_types[0:-1]

    if is_crosstab and len(in_col_types) == 0:
        # use dummy int input type for crosstab since doesn't have input
        in_col_types = [types.Array(types.intp, 1, "C")]

    typ_and_func = [(t, agg_func) for t in in_col_types]
    if isinstance(agg_func, list):
        # tuple function or constant dict case
        typ_and_func = []
        for in_typ, f_val in zip(in_col_types, agg_func):
            if isinstance(f_val, list):
                # multiple functions are applied to this input column
                for func in f_val:
                    typ_and_func.append((in_typ, func))
            else:
                # a single function is applied to this input column
                typ_and_func.append((in_typ, f_val))

    udf_found = False

    for in_col_typ, func in typ_and_func:
        if pivot_values is None and not is_crosstab and func.ftype != "udf":
            # don't generate code for non-udf functions
            continue

        udf_found = True
        in_series_typ = SeriesType(in_col_typ.dtype, in_col_typ, None, string_type)
        f_ir, pm = compile_to_optimized_ir(func, (in_series_typ,), typingctx)

        f_ir._definitions = build_definitions(f_ir.blocks)
        # TODO: support multiple top-level blocks
        assert len(f_ir.blocks) == 1 and 0 in f_ir.blocks, (
            "only simple functions" " with one block supported for aggregation"
        )
        block = f_ir.blocks[0]

        # find and ignore arg and size/shape nodes for input arr
        block_body, arr_var = _rm_arg_agg_block(block, pm.typemap)

        parfor_ind = -1
        for i, stmt in enumerate(block_body):
            if isinstance(stmt, numba.parfors.parfor.Parfor):
                assert parfor_ind == -1, "only one parfor for aggregation function"
                parfor_ind = i

        # some UDFs could have no parfors (e.g. lambda x: 1)
        parfor = None
        if parfor_ind != -1:
            parfor = block_body[parfor_ind]
            remove_dels(parfor.loop_body)
            remove_dels({0: parfor.init_block})

        init_nodes = []
        if parfor:
            init_nodes = block_body[:parfor_ind] + parfor.init_block.body

        eval_nodes = block_body[parfor_ind + 1 :]

        redvars = []
        var_to_redvar = {}
        if parfor:
            redvars, var_to_redvar = get_parfor_reductions(
                parfor, parfor.params, pm.calltypes
            )

        func.ncols_pre_shuffle = len(redvars)
        func.ncols_post_shuffle = len(redvars) + 1  # one for output after eval
        func.n_redvars = len(redvars)

        # find reduce variables given their names
        reduce_vars = [0] * len(redvars)
        for stmt in init_nodes:
            if is_assign(stmt) and stmt.target.name in redvars:
                ind = redvars.index(stmt.target.name)
                reduce_vars[ind] = stmt.target
        var_types = [pm.typemap[v] for v in redvars]

        combine_func = gen_combine_func(
            f_ir,
            parfor,
            redvars,
            var_to_redvar,
            var_types,
            arr_var,
            pm,
            typingctx,
            targetctx,
        )

        init_nodes = _mv_read_only_init_vars(init_nodes, parfor, eval_nodes)

        # XXX: update mutates parfor body
        update_func = gen_update_func(
            parfor,
            redvars,
            var_to_redvar,
            var_types,
            arr_var,
            in_col_typ,
            pm,
            typingctx,
            targetctx,
        )

        eval_func = gen_eval_func(
            f_ir, eval_nodes, reduce_vars, var_types, pm, typingctx, targetctx
        )

        all_reduce_vars += reduce_vars
        all_vartypes += var_types
        all_init_nodes += init_nodes
        all_eval_funcs.append(eval_func)
        typemap.update(pm.typemap)
        calltypes.update(pm.calltypes)
        all_update_funcs.append(update_func)
        all_combine_funcs.append(combine_func)
        curr_offset += len(redvars)
        redvar_offsets.append(curr_offset)

    if not udf_found:
        # no user-defined functions found for groupby.agg()
        return None

    all_vartypes = (
        all_vartypes * len(pivot_values) if pivot_values is not None else all_vartypes
    )
    all_reduce_vars = (
        all_reduce_vars * len(pivot_values)
        if pivot_values is not None
        else all_reduce_vars
    )

    init_func = gen_init_func(
        all_init_nodes, all_reduce_vars, all_vartypes, typingctx, targetctx
    )
    update_all_func = gen_all_update_func(
        all_update_funcs,
        all_vartypes,
        in_col_types,
        redvar_offsets,
        typingctx,
        targetctx,
        pivot_typ,
        pivot_values,
        is_crosstab,
    )
    combine_all_func = gen_all_combine_func(
        all_combine_funcs,
        all_vartypes,
        redvar_offsets,
        typingctx,
        targetctx,
        pivot_typ,
        pivot_values,
    )
    eval_all_func = gen_all_eval_func(
        all_eval_funcs,
        all_vartypes,
        redvar_offsets,
        out_col_typs,
        typingctx,
        targetctx,
        pivot_values,
    )

    return AggFuncTemplateStruct(
        all_vartypes, init_func, update_all_func, combine_all_func, eval_all_func
    )


def _mv_read_only_init_vars(init_nodes, parfor, eval_nodes):
    """move stmts that are only used in the parfor body to the beginning of
    parfor body. For example, in test_agg_seq_str, B='aa' should be moved.
    """
    if not parfor:
        return init_nodes

    # get parfor body usedefs
    use_defs = compute_use_defs(parfor.loop_body)
    parfor_uses = set()
    for s in use_defs.usemap.values():
        parfor_uses |= s
    parfor_defs = set()
    for s in use_defs.defmap.values():
        parfor_defs |= s

    # get uses of eval nodes
    dummy_block = ir.Block(ir.Scope(None, parfor.loc), parfor.loc)
    dummy_block.body = eval_nodes
    e_use_defs = compute_use_defs({0: dummy_block})
    e_uses = e_use_defs.usemap[0]

    # find stmts that are only used in parfor body
    i_uses = set()  # variables used later in init nodes
    new_init_nodes = []
    const_nodes = []
    for stmt in reversed(init_nodes):
        stmt_uses = {v.name for v in stmt.list_vars()}
        if is_assign(stmt):
            v = stmt.target.name
            stmt_uses.remove(v)
            # v is only used in parfor body
            if (
                v in parfor_uses
                and v not in i_uses
                and v not in e_uses
                and v not in parfor_defs
            ):
                const_nodes.append(stmt)
                i_uses |= stmt_uses
                continue
        i_uses |= stmt_uses
        new_init_nodes.append(stmt)

    const_nodes.reverse()
    new_init_nodes.reverse()

    first_body_label = min(parfor.loop_body.keys())
    first_block = parfor.loop_body[first_body_label]
    first_block.body = const_nodes + first_block.body
    return new_init_nodes


def gen_init_func(init_nodes, reduce_vars, var_types, typingctx, targetctx):

    # parallelaccelerator adds functions that check the size of input array
    # these calls need to be removed
    _checker_calls = (
        numba.parfors.parfor.max_checker,
        numba.parfors.parfor.min_checker,
        numba.parfors.parfor.argmax_checker,
        numba.parfors.parfor.argmin_checker,
    )
    checker_vars = set()
    cleaned_init_nodes = []
    for stmt in init_nodes:
        if (
            is_assign(stmt)
            and isinstance(stmt.value, ir.Global)
            and isinstance(stmt.value.value, pytypes.FunctionType)
            and stmt.value.value in _checker_calls
        ):
            checker_vars.add(stmt.target.name)
        elif is_call_assign(stmt) and stmt.value.func.name in checker_vars:
            pass  # remove call
        else:
            cleaned_init_nodes.append(stmt)

    init_nodes = cleaned_init_nodes

    return_typ = types.Tuple(var_types)

    dummy_f = lambda: None
    f_ir = compile_to_numba_ir(dummy_f, {})
    block = list(f_ir.blocks.values())[0]
    loc = block.loc

    # return initialized reduce vars as tuple
    tup_var = ir.Var(block.scope, mk_unique_var("init_tup"), loc)
    tup_assign = ir.Assign(ir.Expr.build_tuple(reduce_vars, loc), tup_var, loc)
    block.body = block.body[-2:]
    block.body = init_nodes + [tup_assign] + block.body
    block.body[-2].value.value = tup_var

    # compile implementation to binary (Dispatcher)
    init_all_func = compiler.compile_ir(
        typingctx, targetctx, f_ir, (), return_typ, compiler.DEFAULT_FLAGS, {}
    )

    imp_dis = numba.core.registry.dispatcher_registry["cpu"](dummy_f)
    imp_dis.add_overload(init_all_func)
    return imp_dis


def gen_all_update_func(
    update_funcs,
    reduce_var_types,
    in_col_types,
    redvar_offsets,
    typingctx,
    targetctx,
    pivot_typ,
    pivot_values,
    is_crosstab,
):

    out_num_cols = len(update_funcs)
    in_num_cols = len(in_col_types)
    if pivot_values is not None:
        assert in_num_cols == 1

    # redvar_arrs[0][w_ind], redvar_arrs[1][w_ind] = __update_redvars(
    #              redvar_arrs[0][w_ind], redvar_arrs[1][w_ind], data_in[0][i])

    func_text = "def update_all_f(redvar_arrs, data_in, w_ind, i, pivot_arr):\n"
    if pivot_values is not None:
        num_redvars = redvar_offsets[in_num_cols]
        func_text += "  pv = pivot_arr[i]\n"
        for j, pv in enumerate(pivot_values):
            el = "el" if j != 0 else ""
            func_text += "  {}if pv == '{}':\n".format(el, pv)  # TODO: non-string pivot
            init_offset = num_redvars * j
            redvar_access = ", ".join(
                [
                    "redvar_arrs[{}][w_ind]".format(i)
                    for i in range(
                        init_offset + redvar_offsets[0], init_offset + redvar_offsets[1]
                    )
                ]
            )
            data_access = "data_in[0][i]"
            if is_crosstab:  # TODO: crosstab with values arg
                data_access = "0"
            func_text += "    {} = update_vars_0({}, {})\n".format(
                redvar_access, redvar_access, data_access
            )
    else:
        for j in range(out_num_cols):
            redvar_access = ", ".join(
                [
                    "redvar_arrs[{}][w_ind]".format(i)
                    for i in range(redvar_offsets[j], redvar_offsets[j + 1])
                ]
            )
            if redvar_access:  # if there is a parfor
                func_text += "  {} = update_vars_{}({},  data_in[{}][i])\n".format(
                    redvar_access, j, redvar_access, 0 if in_num_cols == 1 else j
                )
    func_text += "  return\n"

    glbs = {}
    for i, f in enumerate(update_funcs):
        glbs["update_vars_{}".format(i)] = f
    loc_vars = {}
    exec(func_text, glbs, loc_vars)
    update_all_f = loc_vars["update_all_f"]
    return numba.njit(no_cpython_wrapper=True)(update_all_f)


def gen_all_combine_func(
    combine_funcs,
    reduce_var_types,
    redvar_offsets,
    typingctx,
    targetctx,
    pivot_typ,
    pivot_values,
):

    reduce_arrs_tup_typ = types.Tuple(
        [types.Array(t, 1, "C") for t in reduce_var_types]
    )
    arg_typs = (
        reduce_arrs_tup_typ,
        reduce_arrs_tup_typ,
        types.intp,
        types.intp,
        pivot_typ,
    )

    num_cols = len(redvar_offsets) - 1
    num_redvars = redvar_offsets[num_cols]

    #       redvar_0_arr[w_ind], redvar_1_arr[w_ind] = __combine_redvars_0(
    #             redvar_0_arr[w_ind], redvar_1_arr[w_ind], in_c0[i], in_c1[i])
    #       redvar_2_arr[w_ind], redvar_3_arr[w_ind] = __combine_redvars_1(
    #             redvar_2_arr[w_ind], redvar_3_arr[w_ind], in_c2[i], in_c3[i])

    func_text = "def combine_all_f(redvar_arrs, recv_arrs, w_ind, i, pivot_arr):\n"

    if pivot_values is not None:
        assert num_cols == 1
        for k in range(len(pivot_values)):
            init_offset = num_redvars * k
            redvar_access = ", ".join(
                [
                    "redvar_arrs[{}][w_ind]".format(i)
                    for i in range(
                        init_offset + redvar_offsets[0], init_offset + redvar_offsets[1]
                    )
                ]
            )
            recv_access = ", ".join(
                [
                    "recv_arrs[{}][i]".format(i)
                    for i in range(
                        init_offset + redvar_offsets[0], init_offset + redvar_offsets[1]
                    )
                ]
            )
            func_text += "  {} = combine_vars_0({}, {})\n".format(
                redvar_access, redvar_access, recv_access
            )
    else:
        for j in range(num_cols):
            redvar_access = ", ".join(
                [
                    "redvar_arrs[{}][w_ind]".format(i)
                    for i in range(redvar_offsets[j], redvar_offsets[j + 1])
                ]
            )
            recv_access = ", ".join(
                [
                    "recv_arrs[{}][i]".format(i)
                    for i in range(redvar_offsets[j], redvar_offsets[j + 1])
                ]
            )
            if recv_access:  # if there is a parfor
                func_text += "  {} = combine_vars_{}({}, {})\n".format(
                    redvar_access, j, redvar_access, recv_access
                )
    func_text += "  return\n"
    glbs = {}
    for i, f in enumerate(combine_funcs):
        glbs["combine_vars_{}".format(i)] = f
    loc_vars = {}
    exec(func_text, glbs, loc_vars)
    combine_all_f = loc_vars["combine_all_f"]

    f_ir = compile_to_numba_ir(combine_all_f, glbs)

    # compile implementation to binary (Dispatcher)
    combine_all_func = compiler.compile_ir(
        typingctx, targetctx, f_ir, arg_typs, types.none, compiler.DEFAULT_FLAGS, {}
    )

    imp_dis = numba.core.registry.dispatcher_registry["cpu"](combine_all_f)
    imp_dis.add_overload(combine_all_func)
    return imp_dis


def gen_all_eval_func(
    eval_funcs,
    reduce_var_types,
    redvar_offsets,
    out_col_typs,
    typingctx,
    targetctx,
    pivot_values,
):

    reduce_arrs_tup_typ = types.Tuple(
        [types.Array(t, 1, "C") for t in reduce_var_types]
    )
    out_col_typs = types.Tuple(out_col_typs)

    num_cols = len(redvar_offsets) - 1

    #       out_c0[j] = __eval_res_0(redvar_0_arr[j], redvar_1_arr[j])
    #       out_c1[j] = __eval_res_1(redvar_2_arr[j], redvar_3_arr[j])

    num_redvars = redvar_offsets[num_cols]

    func_text = "def eval_all_f(redvar_arrs, out_arrs, j):\n"

    if pivot_values is not None:
        assert num_cols == 1
        for j in range(len(pivot_values)):
            init_offset = num_redvars * j
            redvar_access = ", ".join(
                [
                    "redvar_arrs[{}][j]".format(i)
                    for i in range(
                        init_offset + redvar_offsets[0], init_offset + redvar_offsets[1]
                    )
                ]
            )
            func_text += "  out_arrs[{}][j] = eval_vars_0({})\n".format(
                j, redvar_access
            )
    else:
        for j in range(num_cols):
            redvar_access = ", ".join(
                [
                    "redvar_arrs[{}][j]".format(i)
                    for i in range(redvar_offsets[j], redvar_offsets[j + 1])
                ]
            )
            func_text += "  out_arrs[{}][j] = eval_vars_{}({})\n".format(
                j, j, redvar_access
            )
    func_text += "  return\n"
    glbs = {}
    for i, f in enumerate(eval_funcs):
        glbs["eval_vars_{}".format(i)] = f
    loc_vars = {}
    exec(func_text, glbs, loc_vars)
    eval_all_f = loc_vars["eval_all_f"]
    return numba.njit(no_cpython_wrapper=True)(eval_all_f)


def gen_eval_func(f_ir, eval_nodes, reduce_vars, var_types, pm, typingctx, targetctx):

    # eval func takes reduce vars and produces final result
    num_red_vars = len(var_types)
    in_names = ["in{}".format(i) for i in range(num_red_vars)]
    return_typ = types.unliteral(pm.typemap[eval_nodes[-1].value.name])

    # TODO: non-numeric return
    zero = return_typ(0)
    func_text = "def agg_eval({}):\n return _zero\n".format(", ".join(in_names))

    loc_vars = {}
    exec(func_text, {"_zero": zero}, loc_vars)
    agg_eval = loc_vars["agg_eval"]

    arg_typs = tuple(var_types)
    f_ir = compile_to_numba_ir(
        agg_eval,
        # TODO: add outside globals
        {"numba": numba, "bodo": bodo, "np": np, "_zero": zero},
        typingctx,
        arg_typs,
        pm.typemap,
        pm.calltypes,
    )

    # TODO: support multi block eval funcs
    block = list(f_ir.blocks.values())[0]

    # assign inputs to reduce vars used in computation
    assign_nodes = []
    for i, v in enumerate(reduce_vars):
        assign_nodes.append(ir.Assign(block.body[i].target, v, v.loc))
    block.body = block.body[:num_red_vars] + assign_nodes + eval_nodes

    # compile implementation to binary (Dispatcher)
    eval_func = compiler.compile_ir(
        typingctx, targetctx, f_ir, arg_typs, return_typ, compiler.DEFAULT_FLAGS, {}
    )

    imp_dis = numba.core.registry.dispatcher_registry["cpu"](agg_eval)
    imp_dis.add_overload(eval_func)
    return imp_dis


def gen_combine_func(
    f_ir, parfor, redvars, var_to_redvar, var_types, arr_var, pm, typingctx, targetctx
):
    if not parfor:
        return numba.njit(lambda: ())

    num_red_vars = len(redvars)
    redvar_in_names = ["v{}".format(i) for i in range(num_red_vars)]
    in_names = ["in{}".format(i) for i in range(num_red_vars)]

    func_text = "def agg_combine({}):\n".format(", ".join(redvar_in_names + in_names))

    blocks = wrap_parfor_blocks(parfor)
    topo_order = find_topo_order(blocks)
    topo_order = topo_order[1:]  # ignore init block
    unwrap_parfor_blocks(parfor)

    special_combines = {}
    ignore_redvar_inds = []

    for label in topo_order:
        bl = parfor.loop_body[label]
        for stmt in bl.body:
            if is_call_assign(stmt) and (
                guard(find_callname, f_ir, stmt.value)
                == ("__special_combine", "bodo.ir.aggregate")
            ):
                args = stmt.value.args
                l_argnames = []
                r_argnames = []
                for v in args[:-1]:
                    ind = redvars.index(v.name)
                    ignore_redvar_inds.append(ind)
                    l_argnames.append("v{}".format(ind))
                    r_argnames.append("in{}".format(ind))
                comb_name = "__special_combine__{}".format(len(special_combines))
                func_text += "    ({},) = {}({})\n".format(
                    ", ".join(l_argnames), comb_name, ", ".join(l_argnames + r_argnames)
                )
                dummy_call = ir.Expr.call(args[-1], [], (), bl.loc)
                sp_func = guard(find_callname, f_ir, dummy_call)
                # XXX: only var supported for now
                # TODO: support general functions
                assert sp_func == ("_var_combine", "bodo.ir.aggregate")
                sp_func = bodo.ir.aggregate._var_combine
                special_combines[comb_name] = sp_func

            # reduction variables
            if is_assign(stmt) and stmt.target.name in redvars:
                red_var = stmt.target.name
                ind = redvars.index(red_var)
                if ind in ignore_redvar_inds:
                    continue
                if len(f_ir._definitions[red_var]) == 2:
                    # 0 is the actual func since init_block is traversed later
                    # in parfor.py:3039, TODO: make this detection more robust
                    # XXX trying both since init_prange doesn't work for min
                    var_def = f_ir._definitions[red_var][0]
                    func_text += _match_reduce_def(var_def, f_ir, ind)
                    var_def = f_ir._definitions[red_var][1]
                    func_text += _match_reduce_def(var_def, f_ir, ind)

    func_text += "    return {}".format(
        ", ".join(["v{}".format(i) for i in range(num_red_vars)])
    )
    loc_vars = {}
    exec(func_text, {}, loc_vars)
    agg_combine = loc_vars["agg_combine"]

    # reduction variable types for new input and existing values
    arg_typs = tuple(2 * var_types)

    glbs = {"numba": numba, "bodo": bodo, "np": np}
    glbs.update(special_combines)
    f_ir = compile_to_numba_ir(
        agg_combine,
        glbs,  # TODO: add outside globals
        typingctx,
        arg_typs,
        pm.typemap,
        pm.calltypes,
    )

    block = list(f_ir.blocks.values())[0]

    return_typ = pm.typemap[block.body[-1].value.name]
    # compile implementation to binary (Dispatcher)
    combine_func = compiler.compile_ir(
        typingctx, targetctx, f_ir, arg_typs, return_typ, compiler.DEFAULT_FLAGS, {}
    )

    imp_dis = numba.core.registry.dispatcher_registry["cpu"](agg_combine)
    imp_dis.add_overload(combine_func)
    return imp_dis


def _match_reduce_def(var_def, f_ir, ind):
    func_text = ""
    while isinstance(var_def, ir.Var):
        var_def = guard(get_definition, f_ir, var_def)
    # TODO: support other reductions
    if (
        isinstance(var_def, ir.Expr)
        and var_def.op == "inplace_binop"
        and var_def.fn in ("+=", operator.iadd)
    ):
        func_text = "    v{} += in{}\n".format(ind, ind)
    if isinstance(var_def, ir.Expr) and var_def.op == "call":
        fdef = guard(find_callname, f_ir, var_def)
        if fdef == ("min", "builtins"):
            func_text = "    v{} = min(v{}, in{})\n".format(ind, ind, ind)
        if fdef == ("max", "builtins"):
            func_text = "    v{} = max(v{}, in{})\n".format(ind, ind, ind)
    return func_text


def gen_update_func(
    parfor,
    redvars,
    var_to_redvar,
    var_types,
    arr_var,
    in_col_typ,
    pm,
    typingctx,
    targetctx,
):
    if not parfor:
        return numba.njit(lambda A: ())

    num_red_vars = len(redvars)
    var_types = [pm.typemap[v] for v in redvars]

    num_in_vars = 1

    # create input value variable for each reduction variable
    in_vars = []
    for i in range(num_in_vars):
        in_var = ir.Var(arr_var.scope, "$input{}".format(i), arr_var.loc)
        in_vars.append(in_var)

    # replace X[i] with input value
    index_var = parfor.loop_nests[0].index_variable
    red_ir_vars = [0] * num_red_vars
    for bl in parfor.loop_body.values():
        new_body = []
        for stmt in bl.body:
            # remove extra index assignment i = parfor_index for isna(A, i)
            if is_var_assign(stmt) and stmt.value.name == index_var.name:
                continue
            if is_getitem(stmt) and stmt.value.value.name == arr_var.name:
                stmt.value = in_vars[0]
            # XXX replace bodo.libs.array_kernels.isna(A, i) for now
            # TODO: handle actual NA
            # for test_agg_seq_count_str test
            if (
                is_call_assign(stmt)
                and guard(find_callname, pm.func_ir, stmt.value)
                == ("isna", "bodo.libs.array_kernels")
                and stmt.value.args[0].name == arr_var.name
            ):
                stmt.value = ir.Const(False, stmt.target.loc)
            # store reduction variables
            if is_assign(stmt) and stmt.target.name in redvars:
                ind = redvars.index(stmt.target.name)
                red_ir_vars[ind] = stmt.target
            new_body.append(stmt)
        bl.body = new_body

    redvar_in_names = ["v{}".format(i) for i in range(num_red_vars)]
    in_names = ["in{}".format(i) for i in range(num_in_vars)]

    func_text = "def agg_update({}):\n".format(", ".join(redvar_in_names + in_names))
    func_text += "    __update_redvars()\n"
    func_text += "    return {}".format(
        ", ".join(["v{}".format(i) for i in range(num_red_vars)])
    )

    loc_vars = {}
    exec(func_text, {}, loc_vars)
    agg_update = loc_vars["agg_update"]

    # XXX input column type can be different than reduction variable type
    arg_typs = tuple(var_types + [in_col_typ.dtype] * num_in_vars)

    f_ir = compile_to_numba_ir(
        agg_update,
        # TODO: add outside globals
        {"__update_redvars": __update_redvars},
        typingctx,
        arg_typs,
        pm.typemap,
        pm.calltypes,
    )

    f_ir._definitions = build_definitions(f_ir.blocks)

    body = f_ir.blocks.popitem()[1].body
    return_typ = pm.typemap[body[-1].value.name]

    blocks = wrap_parfor_blocks(parfor)
    topo_order = find_topo_order(blocks)
    topo_order = topo_order[1:]  # ignore init block
    unwrap_parfor_blocks(parfor)

    f_ir.blocks = parfor.loop_body
    first_block = f_ir.blocks[topo_order[0]]
    last_block = f_ir.blocks[topo_order[-1]]

    # arg assigns
    initial_assigns = body[: (num_red_vars + num_in_vars)]
    if num_red_vars > 1:
        # return nodes: build_tuple, cast, return
        return_nodes = body[-3:]
        assert (
            is_assign(return_nodes[0])
            and isinstance(return_nodes[0].value, ir.Expr)
            and return_nodes[0].value.op == "build_tuple"
        )
    else:
        # return nodes: cast, return
        return_nodes = body[-2:]

    # assign input reduce vars
    # redvar_i = v_i
    for i in range(num_red_vars):
        arg_var = body[i].target
        node = ir.Assign(arg_var, red_ir_vars[i], arg_var.loc)
        initial_assigns.append(node)

    # assign input value vars
    # redvar_in_i = in_i
    for i in range(num_red_vars, num_red_vars + num_in_vars):
        arg_var = body[i].target
        node = ir.Assign(arg_var, in_vars[i - num_red_vars], arg_var.loc)
        initial_assigns.append(node)

    first_block.body = initial_assigns + first_block.body

    # assign ouput reduce vars
    # v_i = red_var_i
    after_assigns = []
    for i in range(num_red_vars):
        arg_var = body[i].target
        node = ir.Assign(red_ir_vars[i], arg_var, arg_var.loc)
        after_assigns.append(node)

    last_block.body += after_assigns + return_nodes

    # TODO: simplify f_ir
    # compile implementation to binary (Dispatcher)
    agg_impl_func = compiler.compile_ir(
        typingctx, targetctx, f_ir, arg_typs, return_typ, compiler.DEFAULT_FLAGS, {}
    )

    imp_dis = numba.core.registry.dispatcher_registry["cpu"](agg_update)
    imp_dis.add_overload(agg_impl_func)
    return imp_dis


def _rm_arg_agg_block(block, typemap):
    block_body = []
    arr_var = None
    for i, stmt in enumerate(block.body):
        if is_assign(stmt) and isinstance(stmt.value, ir.Arg):
            arr_var = stmt.target
            arr_typ = typemap[arr_var.name]
            # array analysis generates shape only for ArrayCompatible types
            if not isinstance(arr_typ, types.ArrayCompatible):
                block_body += block.body[i + 1 :]
                break
            # XXX assuming shape/size nodes are right after arg
            shape_nd = block.body[i + 1]
            assert (
                is_assign(shape_nd)
                and isinstance(shape_nd.value, ir.Expr)
                and shape_nd.value.op == "getattr"
                and shape_nd.value.attr == "shape"
                and shape_nd.value.value.name == arr_var.name
            )
            shape_vr = shape_nd.target
            size_nd = block.body[i + 2]
            assert (
                is_assign(size_nd)
                and isinstance(size_nd.value, ir.Expr)
                and size_nd.value.op == "static_getitem"
                and size_nd.value.value.name == shape_vr.name
            )
            # ignore size/shape vars
            block_body += block.body[i + 3 :]
            break
        block_body.append(stmt)

    return block_body, arr_var


# adapted from numba/parfor.py
def get_parfor_reductions(
    parfor,
    parfor_params,
    calltypes,
    reduce_varnames=None,
    param_uses=None,
    var_to_param=None,
):
    """find variables that are updated using their previous values and an array
    item accessed with parfor index, e.g. s = s+A[i]
    """
    if reduce_varnames is None:
        reduce_varnames = []

    # for each param variable, find what other variables are used to update it
    # also, keep the related nodes
    if param_uses is None:
        param_uses = defaultdict(list)
    if var_to_param is None:
        var_to_param = {}

    blocks = wrap_parfor_blocks(parfor)
    topo_order = find_topo_order(blocks)
    topo_order = topo_order[1:]  # ignore init block
    unwrap_parfor_blocks(parfor)

    for label in reversed(topo_order):
        for stmt in reversed(parfor.loop_body[label].body):
            if isinstance(stmt, ir.Assign) and (
                stmt.target.name in parfor_params or stmt.target.name in var_to_param
            ):
                lhs = stmt.target.name
                rhs = stmt.value
                cur_param = lhs if lhs in parfor_params else var_to_param[lhs]
                used_vars = []
                if isinstance(rhs, ir.Var):
                    used_vars = [rhs.name]
                elif isinstance(rhs, ir.Expr):
                    used_vars = [v.name for v in stmt.value.list_vars()]
                param_uses[cur_param].extend(used_vars)
                for v in used_vars:
                    var_to_param[v] = cur_param
            if isinstance(stmt, Parfor):
                # recursive parfors can have reductions like test_prange8
                get_parfor_reductions(
                    stmt,
                    parfor_params,
                    calltypes,
                    reduce_varnames,
                    param_uses,
                    var_to_param,
                )

    for param, used_vars in param_uses.items():
        # a parameter is a reduction variable if its value is used to update it
        # check reduce_varnames since recursive parfors might have processed
        # param already
        if param in used_vars and param not in reduce_varnames:
            reduce_varnames.append(param)

    return reduce_varnames, var_to_param


def _build_set_tup(arr_tup):  # pragma: no cover
    return build_set(arr_tup[0])


@overload(_build_set_tup, jit_options={"cache": True}, no_unliteral=True)
def _build_set_tup_overload(arr_tup):
    # TODO: support string in tuple set
    if isinstance(arr_tup, types.BaseTuple) and len(arr_tup.types) != 1:

        def _impl(arr_tup):  # pragma: no cover
            n = len(arr_tup[0])
            s = dict()
            for i in range(n):
                val = getitem_arr_tup(arr_tup, i)
                s[val] = 0
            return s

        return _impl
    return _build_set_tup


def num_total_chars_set(s):  # pragma: no cover
    return (0,)


@overload(num_total_chars_set, no_unliteral=True)
def num_total_chars_set_overload(s):
    # XXX assuming dict for set workaround
    dtype = s.key_type
    key_typs = dtype.types if isinstance(dtype, types.BaseTuple) else [dtype]

    count = len(key_typs)
    func_text = "def f(s):\n"
    for i in range(count):
        func_text += "  n_{} = 0\n".format(i)
    if any(t == string_type for t in key_typs):
        func_text += "  for v in s:\n"
        for i in range(count):
            if key_typs[i] == string_type:
                func_text += "    n_{} += get_utf8_size(v{})\n".format(
                    i, "[{}]".format(i) if isinstance(dtype, types.BaseTuple) else ""
                )
    func_text += "  return ({},)\n".format(
        ", ".join("n_{}".format(i) for i in range(count))
    )

    loc_vars = {}
    exec(func_text, {"bodo": bodo, "get_utf8_size": get_utf8_size}, loc_vars)
    impl = loc_vars["f"]
    return impl


###############  transform functions like cumsum  ###########################


# TODO: cumprod etc.
# adapted from Pandas group_cumsum()
@numba.njit(no_cpython_wrapper=True)
def group_cumsum(key_arrs, data):  # pragma: no cover
    n = len(key_arrs[0])
    out = alloc_arr_tup(n, data)
    if n == 0:
        return out

    acc_map = dict()
    # extra assign to help with type inference
    zero = get_zero_tup(data)
    acc_map[getitem_arr_tup_single(key_arrs, 0)] = zero
    # TODO: multiple outputs

    for i in range(n):
        if isna_tup(key_arrs, i):
            # group_cumsum stores -1 for int arrays in location of NAs
            bodo.libs.array_kernels.setna_tup(out, i, -1)
            continue
        k = getitem_arr_tup_single(key_arrs, i)
        val = getitem_arr_tup_single(data, i)
        # replace NAs with zero for acc calculation
        val = set_nan_zero_tup(data, i, val)
        if k in acc_map:
            acc = acc_map[k]
        else:
            acc = zero
        acc = add_tup(acc, val)
        acc_map[k] = acc
        setitem_arr_tup(out, i, acc)
        # set NA in out if data is NA
        setitem_arr_tup_na_match(out, data, i)

    return out


def get_zero_tup(arr_tup):  # pragma: no cover
    zeros = []
    for in_arr in arr_tup:
        zeros.append(in_arr.dtype(0))
    return tuple(zeros)


@overload(get_zero_tup, no_unliteral=True)
def get_zero_tup_overload(data):
    """get a tuple of zeros matching the data types of data (tuple of arrays)
    tuple of single array returns a single value
    """
    count = data.count
    if count == 1:
        zero = data.types[0].dtype(0)
        return lambda data: zero

    zeros = {"z{}".format(i): data.types[i].dtype(0) for i in range(count)}

    func_text = "def f(data):\n"
    func_text += "  return {}\n".format(
        ", ".join("z{}".format(i) for i in range(count))
    )

    loc_vars = {}
    exec(func_text, zeros, loc_vars)
    impl = loc_vars["f"]
    return impl


def add_tup(val1_tup, val2_tup):
    out = []
    for v1, v2 in zip(val1_tup, val2_tup):
        out.append(v1 + v2)
    return tuple(out)


@overload(add_tup, no_unliteral=True)
def add_tup_overload(val1_tup, val2_tup):
    """add two tuples element-wise
    """
    if not isinstance(val1_tup, types.BaseTuple):
        return lambda val1_tup, val2_tup: val1_tup + val2_tup

    count = val1_tup.count
    func_text = "def f(val1_tup, val2_tup):\n"
    func_text += "  return {}\n".format(
        ", ".join("val1_tup[{0}] + val2_tup[{0}]".format(i) for i in range(count))
    )

    loc_vars = {}
    exec(func_text, {}, loc_vars)
    impl = loc_vars["f"]
    return impl


def isna_tup(arr_tup, ind):  # pragma: no cover
    for arr in arr_tup:
        if np.isnan(arr[ind]):
            return True
    return False


@overload(isna_tup, no_unliteral=True)
def isna_tup_overload(arr_tup, ind):
    """return True if any array value is NA
    """
    if not isinstance(arr_tup, types.BaseTuple):
        return lambda arr_tup, ind: bodo.libs.array_kernels.isna(arr_tup, ind)

    count = arr_tup.count
    func_text = "def f(arr_tup, ind):\n"
    func_text += "  return {}\n".format(
        " or ".join(
            "bodo.libs.array_kernels.isna(arr_tup[{}], ind)".format(i)
            for i in range(count)
        )
    )

    loc_vars = {}
    exec(func_text, {"bodo": bodo}, loc_vars)
    impl = loc_vars["f"]
    return impl


def set_nan_zero_tup(arr_tup, ind, val):  # pragma: no cover
    return tuple((0.0 if np.isnan(arr[ind]) else val[ind]) for arr in arr_tup)


@overload(set_nan_zero_tup, no_unliteral=True)
def set_nan_zero_tup_overload(arr_tup, ind, val):
    """replace NAs with zero in val and return new val
    """
    if not isinstance(val, types.BaseTuple):
        zero = val(0)
        return (
            lambda arr_tup, ind, val: zero
            if bodo.libs.array_kernels.isna(arr_tup[0], ind)
            else val
        )

    count = arr_tup.count
    func_text = "def f(arr_tup, ind, val):\n"
    func_text += "  return {}\n".format(
        ", ".join(
            "0 if bodo.libs.array_kernels.isna(arr_tup[{0}], ind) else val[{0}]".format(
                i
            )
            for i in range(count)
        )
    )

    loc_vars = {}
    exec(func_text, {"bodo": bodo}, loc_vars)
    impl = loc_vars["f"]
    return impl


def setitem_arr_tup_na_match(arr_tup1, arr_tup2, ind):  # pragma: no cover
    pass


@overload(setitem_arr_tup_na_match, no_unliteral=True)
def setitem_arr_tup_na_match_overload(arr_tup1, arr_tup2, ind):
    """set NA in arr_tup1[ind] if arr_tup2[2] is NA
    """

    count = arr_tup1.count
    func_text = "def f(arr_tup1, arr_tup2, ind):\n"
    for i in range(count):
        func_text += "  if bodo.libs.array_kernels.isna(arr_tup2[{}], ind):\n".format(i)
        func_text += "    setna(arr_tup1[{}], ind)\n".format(i)

    loc_vars = {}
    exec(func_text, {"bodo": bodo, "setna": bodo.libs.array_kernels.setna}, loc_vars)
    impl = loc_vars["f"]
    return impl


# sentinel function for the use of len (length of group) in agg UDFs, which will be
# replaced with a dummy loop in series pass
@numba.extending.register_jitable
def dummy_agg_count(A):  # pragma: no cover
    return len(A)
