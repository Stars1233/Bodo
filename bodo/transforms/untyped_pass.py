# Copyright (C) 2019 Bodo Inc. All rights reserved.
"""
transforms the IR to remove features that Numba's type inference cannot support
such as non-uniform dictionary input of `pd.DataFrame({})`.
"""
import types as pytypes
import warnings
import itertools
import datetime
import pandas as pd
import numpy as np

import numba
from numba.core import ir, ir_utils, types

from numba.core.ir_utils import (
    mk_unique_var,
    find_topo_order,
    dprint_func_ir,
    remove_dead,
    remove_dels,
    replace_var_names,
    find_const,
    GuardException,
    compile_to_numba_ir,
    replace_arg_nodes,
    find_callname,
    guard,
    require,
    get_definition,
    build_definitions,
    replace_vars_stmt,
    find_build_sequence,
    compute_cfg_from_blocks,
)


import bodo
from bodo import config
import bodo.io
from bodo.io import h5
from bodo.utils.utils import is_call, is_expr
from bodo.libs.str_arr_ext import string_array_type
from bodo.libs.int_arr_ext import IntegerArrayType
from bodo.libs.bool_arr_ext import boolean_array
from bodo.hiframes.pd_index_ext import RangeIndexType
import bodo.ir
import bodo.ir.aggregate
import bodo.ir.join
import bodo.ir.sort
from bodo.ir import csv_ext
from bodo.ir import sql_ext
from bodo.ir import json_ext
from bodo.io.parquet_pio import ParquetHandler

from bodo.hiframes.pd_categorical_ext import PDCategoricalDtype, CategoricalArray
import bodo.hiframes.pd_dataframe_ext
from bodo.hiframes.pd_dataframe_ext import DataFrameType
from bodo.utils.transform import (
    update_locs,
    get_const_value,
    get_const_value_inner,
    update_node_list_definitions,
    compile_func_single_block,
    get_call_expr_arg,
    gen_const_tup,
    fix_struct_return,
)
from bodo.utils.typing import BodoError, BodoWarning, to_nullable_type


# dummy sentinel singleton to designate constant value not found for variable
class ConstNotFound:
    pass


CONST_NOT_FOUND = ConstNotFound()


class UntypedPass:
    """
    Transformations before typing to enable type inference.
    This pass transforms the IR to remove operations that cannot be handled in Numba's
    type inference due to complexity such as pd.read_csv().
    """

    def __init__(self, func_ir, typingctx, args, _locals, metadata, flags):
        self.func_ir = func_ir
        self.typingctx = typingctx
        self.args = args
        self.locals = _locals
        self.metadata = metadata
        self.flags = flags
        # TODO: remove this? update _max_label just in case?
        ir_utils._max_label = max(ir_utils._max_label, max(func_ir.blocks.keys()))

        self.arrow_tables = {}
        self.reverse_copies = {}
        self.pq_handler = ParquetHandler(
            func_ir, typingctx, args, _locals, self.reverse_copies
        )
        self.h5_handler = h5.H5_IO(
            self.func_ir, _locals, flags, args, self.reverse_copies
        )
        # save names of arguments and return values to catch invalid dist annotation
        self._arg_names = set()
        self._return_varnames = set()

    def run(self):
        # FIXME: see why this breaks test_kmeans
        # remove_dels(self.func_ir.blocks)
        dprint_func_ir(self.func_ir, "starting untyped pass")
        self._handle_metadata()
        blocks = self.func_ir.blocks
        # call build definition since rewrite pass doesn't update definitions
        # e.g. getitem to static_getitem in test_column_list_select2
        self.func_ir._definitions = build_definitions(blocks)
        # remove dead branches to avoid unnecessary typing issues
        remove_dead_branches(self.func_ir)
        # topo_order necessary since df vars need to be found before use
        topo_order = find_topo_order(blocks)
        work_list = list((l, blocks[l]) for l in reversed(topo_order))
        while work_list:
            label, block = work_list.pop()
            self._get_reverse_copies(blocks[label].body)
            new_body = []
            self._working_body = new_body
            for inst in block.body:
                out_nodes = [inst]

                if isinstance(inst, ir.Assign):
                    self.func_ir._definitions[inst.target.name].remove(inst.value)
                    out_nodes = self._run_assign(inst, label)
                elif isinstance(inst, ir.Return):
                    out_nodes = self._run_return(inst)

                assert isinstance(out_nodes, list)
                # TODO: fix scope/loc
                new_body.extend(out_nodes)
                update_node_list_definitions(out_nodes, self.func_ir)

            blocks[label].body = new_body

        self.func_ir.blocks = ir_utils.simplify_CFG(self.func_ir.blocks)
        # self.func_ir._definitions = build_definitions(blocks)
        # XXX: remove dead here fixes h5 slice issue
        # iterative remove dead to make sure all extra code (e.g. df vars) is removed
        # while remove_dead(blocks, self.func_ir.arg_names, self.func_ir):
        #     pass
        self.func_ir._definitions = build_definitions(blocks)
        # return {"A": 1, "B": 2.3} -> return struct((1, 2.3), ("A", "B"))
        fix_struct_return(self.func_ir)
        dprint_func_ir(self.func_ir, "after untyped pass")

        # raise a warning if a variable that is not an argument or return value has a
        # "distributed" annotation
        extra_vars = (
            self.metadata["distributed"] - self._return_varnames - self._arg_names
        )
        if extra_vars and bodo.get_rank() == 0:
            warnings.warn(
                BodoWarning(
                    "Only function arguments and return values can be specified as"
                    "distributed. Ignoring the flag for variables: {}.".format(
                        extra_vars
                    )
                )
            )
        return

    def _run_assign(self, assign, label):
        lhs = assign.target.name
        rhs = assign.value

        # save arg name to catch invalid dist annotations
        if isinstance(rhs, ir.Arg):
            self._arg_names.add(rhs.name)

        if isinstance(rhs, ir.Expr):
            if rhs.op == "call":
                return self._run_call(assign, label)

            # fix type for f['A'][:] dset reads
            if config._has_h5py and rhs.op in ("getitem", "static_getitem"):
                h5_nodes = self.h5_handler.handle_possible_h5_read(assign, lhs, rhs)
                if h5_nodes is not None:
                    return h5_nodes

            # HACK: delete pd.DataFrame({}) nodes to avoid typing errors
            # TODO: remove when dictionaries are implemented and typing works
            if rhs.op == "getattr":
                val_def = guard(get_definition, self.func_ir, rhs.value)
                if (
                    isinstance(val_def, ir.Global)
                    and isinstance(val_def.value, pytypes.ModuleType)
                    and val_def.value == pd
                    and rhs.attr in ("read_csv", "read_parquet", "read_json")
                ):
                    # put back the definition removed earlier but remove node
                    # enables function matching without node in IR
                    self.func_ir._definitions[lhs].append(rhs)
                    return []

            if rhs.op == "getattr":
                val_def = guard(get_definition, self.func_ir, rhs.value)
                if (
                    isinstance(val_def, ir.Global)
                    and isinstance(val_def.value, pytypes.ModuleType)
                    and val_def.value == np
                    and rhs.attr == "fromfile"
                ):
                    # put back the definition removed earlier but remove node
                    self.func_ir._definitions[lhs].append(rhs)
                    return []

            # HACK: delete pyarrow.parquet.read_table() to avoid typing errors
            if rhs.op == "getattr" and rhs.attr == "read_table":
                import pyarrow.parquet as pq

                val_def = guard(get_definition, self.func_ir, rhs.value)
                if isinstance(val_def, ir.Global) and val_def.value == pq:
                    # put back the definition removed earlier but remove node
                    self.func_ir._definitions[lhs].append(rhs)
                    return []

            if (
                rhs.op == "getattr"
                and rhs.value.name in self.arrow_tables
                and rhs.attr == "to_pandas"
            ):
                # put back the definition removed earlier but remove node
                self.func_ir._definitions[lhs].append(rhs)
                return []

            # replace datetime.date.today with an internal function since class methods
            # are not supported in Numba's typing
            if rhs.op == "getattr" and rhs.attr == "today":
                val_def = guard(get_definition, self.func_ir, rhs.value)
                if is_expr(val_def, "getattr") and val_def.attr == "date":
                    mod_def = guard(get_definition, self.func_ir, val_def.value)
                    if isinstance(mod_def, ir.Global) and mod_def.value == datetime:
                        return compile_func_single_block(
                            lambda: bodo.hiframes.datetime_date_ext.today_impl,
                            (),
                            assign.target,
                        )

            # replace datetime.date.fromordinal with an internal function since class methods
            # are not supported in Numba's typing
            if rhs.op == "getattr" and rhs.attr == "fromordinal":
                val_def = guard(get_definition, self.func_ir, rhs.value)
                if is_expr(val_def, "getattr") and val_def.attr == "date":
                    mod_def = guard(get_definition, self.func_ir, val_def.value)
                    if isinstance(mod_def, ir.Global) and mod_def.value == datetime:
                        return compile_func_single_block(
                            lambda: bodo.hiframes.datetime_date_ext.fromordinal_impl,
                            (),
                            assign.target,
                        )

            # replace datetime.datedatetime.now with an internal function since class methods
            # are not supported in Numba's typing
            if rhs.op == "getattr" and rhs.attr == "now":
                val_def = guard(get_definition, self.func_ir, rhs.value)
                if is_expr(val_def, "getattr") and val_def.attr == "datetime":
                    mod_def = guard(get_definition, self.func_ir, val_def.value)
                    if isinstance(mod_def, ir.Global) and mod_def.value == datetime:
                        return compile_func_single_block(
                            lambda: bodo.hiframes.datetime_datetime_ext.now_impl,
                            (),
                            assign.target,
                        )

            # replace datetime.datedatetime.strptime with an internal function since class methods
            # are not supported in Numba's typing
            if rhs.op == "getattr" and rhs.attr == "strptime":
                val_def = guard(get_definition, self.func_ir, rhs.value)
                if is_expr(val_def, "getattr") and val_def.attr == "datetime":
                    mod_def = guard(get_definition, self.func_ir, val_def.value)
                    if isinstance(mod_def, ir.Global) and mod_def.value == datetime:
                        return compile_func_single_block(
                            lambda: bodo.hiframes.datetime_datetime_ext.strptime_impl,
                            (),
                            assign.target,
                        )

            # replace itertools.chain.from_iterable with an internal function since
            #  class methods are not supported in Numba's typing
            if rhs.op == "getattr" and rhs.attr == "from_iterable":
                val_def = guard(get_definition, self.func_ir, rhs.value)
                if is_expr(val_def, "getattr") and val_def.attr == "chain":
                    mod_def = guard(get_definition, self.func_ir, val_def.value)
                    if isinstance(mod_def, ir.Global) and mod_def.value == itertools:
                        return compile_func_single_block(
                            lambda: bodo.utils.typing.from_iterable_impl,
                            (),
                            assign.target,
                        )

            if rhs.op == "make_function":
                # HACK make globals availabe for typing in series.map()
                rhs.globals = self.func_ir.func_id.func.__globals__

        # pass pivot values to df.pivot_table() calls using a meta
        # variable passed as argument. The meta variable's type
        # is set to MetaType with pivot values baked in.
        if lhs in self.flags.pivots:
            pivot_values = self.flags.pivots[lhs]
            # put back the definition removed earlier
            self.func_ir._definitions[lhs].append(rhs)
            pivot_call = guard(get_definition, self.func_ir, lhs)
            assert pivot_call is not None
            meta_var = ir.Var(assign.target.scope, mk_unique_var("pivot_meta"), rhs.loc)
            meta_assign = ir.Assign(ir.Const(0, rhs.loc), meta_var, rhs.loc)
            self._working_body.insert(0, meta_assign)
            pivot_call.kws = list(pivot_call.kws)
            pivot_call.kws.append(("_pivot_values", meta_var))
            self.locals[meta_var.name] = bodo.utils.typing.MetaType(pivot_values)

        # handle copies lhs = f
        if isinstance(rhs, ir.Var) and rhs.name in self.arrow_tables:
            self.arrow_tables[lhs] = self.arrow_tables[rhs.name]
            # enables function matching without node in IR
            self.func_ir._definitions[lhs].append(rhs)
            return []
        return [assign]

    def _run_call(self, assign, label):
        """handle calls and return new nodes if needed
        """
        lhs = assign.target
        rhs = assign.value

        func_name = ""
        func_mod = ""
        fdef = guard(find_callname, self.func_ir, rhs)
        if fdef is None:
            # could be make_function from list comprehension which is ok
            func_def = guard(get_definition, self.func_ir, rhs.func)
            if isinstance(func_def, ir.Expr) and func_def.op == "make_function":
                return [assign]
            # since typemap is not available in untyped pass, var.func() is not
            # recognized if var has multiple definitions (e.g. intraday
            # example). find_callname() assumes var could be a module, which
            # isn't an issue since we only match and transform 'drop' and
            # 'sort_values' here for variable in a safe way (TODO test more).
            if is_expr(func_def, "getattr"):
                func_mod = func_def.value
                func_name = func_def.attr
            # ignore objmode block calls
            elif isinstance(func_def, ir.Const) and isinstance(
                func_def.value, numba.core.dispatcher.ObjModeLiftedWith
            ):
                return [assign]
            else:
                warnings.warn("function call couldn't be found for initial analysis")
                return [assign]
        else:
            func_name, func_mod = fdef

        # handling pd.DataFrame() here since input can be constant dictionary
        if fdef == ("DataFrame", "pandas"):
            return self._handle_pd_DataFrame(assign, lhs, rhs, label)

        # handling pd.read_csv() here since input can have constants
        # like dictionaries for typing
        if fdef == ("read_csv", "pandas"):
            return self._handle_pd_read_csv(assign, lhs, rhs, label)

        # handling pd.read_sql() here since input can have constants
        # like dictionaries for typing
        if fdef == ("read_sql", "pandas"):
            return self._handle_pd_read_sql(assign, lhs, rhs, label)

        # handling pd.read_json() here since input can have constants
        # like dictionaries for typing
        if fdef == ("read_json", "pandas"):
            return self._handle_pd_read_json(assign, lhs, rhs, label)

        # handling pd.read_excel() here since typing info needs to be extracted
        if fdef == ("read_excel", "pandas"):
            return self._handle_pd_read_excel(assign, lhs, rhs, label)

        # match flatmap pd.Series(list(itertools.chain(*A))) and flatten
        if fdef == ("Series", "pandas"):
            return self._handle_pd_Series(assign, lhs, rhs)

        if fdef == ("read_table", "pyarrow.parquet"):
            return self._handle_pq_read_table(assign, lhs, rhs)

        if (
            func_name == "to_pandas"
            and isinstance(func_mod, ir.Var)
            and func_mod.name in self.arrow_tables
        ):
            return self._handle_pq_to_pandas(assign, lhs, rhs, func_mod)

        if fdef == ("read_parquet", "pandas"):
            return self._handle_pd_read_parquet(assign, lhs, rhs)

        if fdef == ("concat", "pandas"):
            return self._handle_concat(assign, lhs, rhs, label)

        if fdef == ("fromfile", "numpy"):
            return bodo.io.np_io._handle_np_fromfile(assign, lhs, rhs)

        if fdef == ("where", "numpy") and len(rhs.args) == 3:
            return self._handle_np_where(assign, lhs, rhs)

        return [assign]

    def _handle_np_where(self, assign, lhs, rhs):
        """replace np.where() calls with Bodo's version since Numba's typer assumes
        non-Array types like Series are scalars and produces wrong output type.
        """
        return compile_func_single_block(
            lambda c, x, y: bodo.hiframes.series_impl.where_impl(c, x, y), rhs.args, lhs
        )

    def _get_reverse_copies(self, body):
        for inst in body:
            if isinstance(inst, ir.Assign) and isinstance(inst.value, ir.Var):
                self.reverse_copies[inst.value.name] = inst.target.name
        return

    def _handle_pd_DataFrame(self, assign, lhs, rhs, label):
        """
        Enable typing for dictionary data arg to pd.DataFrame({'A': A}) call.
        Converts constant dictionary to tuple with sentinel if present.
        """
        nodes = [assign]
        kws = dict(rhs.kws)
        data_arg = get_call_expr_arg("pd.DataFrame", rhs.args, kws, 0, "data", "")
        index_arg = get_call_expr_arg("pd.DataFrame", rhs.args, kws, 1, "index", "")

        arg_def = guard(get_definition, self.func_ir, data_arg)

        if isinstance(arg_def, ir.Expr) and arg_def.op == "build_map":
            # check column names to be string
            col_names = tuple(
                guard(find_const, self.func_ir, t[0]) for t in arg_def.items
            )
            if not all(isinstance(c, str) for c in col_names):
                # TODO: support int column names?
                raise ValueError("DataFrame column names should be constant strings")

            # create tuple with sentinel
            sentinel_var = ir.Var(lhs.scope, mk_unique_var("sentinel"), lhs.loc)
            tup_var = ir.Var(lhs.scope, mk_unique_var("dict_tup"), lhs.loc)
            new_nodes = [
                ir.Assign(ir.Const("__bodo_tup", lhs.loc), sentinel_var, lhs.loc)
            ]
            tup_items = (
                [sentinel_var]
                + [t[0] for t in arg_def.items]
                + [t[1] for t in arg_def.items]
            )
            new_nodes.append(
                ir.Assign(ir.Expr.build_tuple(tup_items, lhs.loc), tup_var, lhs.loc)
            )

            # replace data arg with dict tuple
            if "data" in kws:
                kws["data"] = tup_var
                rhs.kws = list(kws.items())
            else:
                rhs.args[0] = tup_var

            nodes = new_nodes + nodes
            # HACK replace build_map to avoid inference errors
            # TODO: don't replace if used in other places
            arg_def.op = "build_list"
            arg_def.items = [v[0] for v in arg_def.items]

        # replace range() with pd.RangeIndex() for index argument
        arg_def = guard(get_definition, self.func_ir, index_arg)
        if is_call(arg_def) and guard(find_callname, self.func_ir, arg_def) == (
            "range",
            "builtins",
        ):
            # gen pd.RangeIndex() call
            def _call_range_index():
                return pd.RangeIndex()

            f_block = compile_to_numba_ir(
                _call_range_index, {"pd": pd}
            ).blocks.popitem()[1]
            new_nodes = f_block.body[:-2]
            new_nodes[-1].value.args = arg_def.args
            new_index = new_nodes[-1].target
            # replace index arg
            if "index" in kws:
                kws["index"] = new_index
                rhs.kws = list(kws.items())
            else:
                rhs.args[1] = new_index
            nodes = new_nodes + nodes

        return nodes

    def _handle_pd_read_sql(self, assign, lhs, rhs, label):
        """transform pd.read_sql calls"""
        # schema: pd.read_sql(sql, con, index_col=None,
        # coerce_float=True, params=None, parse_dates=None,
        # columns=None, chunksize=None
        kws = dict(rhs.kws)
        sql_var = get_call_expr_arg("read_sql", rhs.args, kws, 0, "sql")
        # The sql request has to be constant
        msg = (
            "pd.read_sql() requires 'sql' argument to be a constant string or an "
            "argument to the JIT function currently"
        )
        sql_const = get_const_value(sql_var, self.func_ir, msg, arg_types=self.args)
        con_var = get_call_expr_arg("read_sql", rhs.args, kws, 1, "con", "")
        msg = (
            "pd.read_sql() requires 'con' argument to be a constant string or an "
            "argument to the JIT function currently"
        )
        # the connection string has to be constant
        con_const = get_const_value(con_var, self.func_ir, msg, arg_types=self.args)
        index_col = self._get_const_arg(
            "read_sql", rhs.args, kws, 2, "index_col", default=-1
        )
        # coerce_float = self._get_const_arg(
        #     "read_sql", rhs.args, kws, 3, "coerce_float", default=True
        # )
        # params = self._get_const_arg("read_sql", rhs.args, kws, 4, "params", default=-1)
        # parse_dates = self._get_const_arg(
        #     "read_sql", rhs.args, kws, 5, "parse_dates", default=-1
        # )
        columns = self._get_const_arg(
            "read_sql", rhs.args, kws, 6, "columns", default=""
        )
        # chunksize = get_call_expr_arg("read_sql", rhs.args, kws, 7, "chunksize", -1)

        # SUPPORTED:
        # sql is supported since it is fundamental
        # con is supported since it is fundamental but only as a string
        # index_col is supported since setting the index is something useful.
        # UNSUPPORTED:
        # chunksize is unsupported because it is a different API and it makes for a different usage of pd.read_sql
        # columns   is unsupported because selecting columns could actually be done in SQL.
        # coerce_float is currently unsupported but it could be useful to support it.
        # params is currently unsupported because not needed for mysql but surely will be needed later.
        supported_args = ("sql", "con", "index_col", "parse_dates")

        unsupported_args = set(kws.keys()) - set(supported_args)
        if unsupported_args:
            raise ValueError(
                "read_sql() arguments {} not supported yet".format(unsupported_args)
            )

        # find df type
        df_type = _get_sql_df_type_from_db(sql_const, con_const)
        dtypes = df_type.data
        dtype_map = {c: dtypes[i] for i, c in enumerate(df_type.columns)}
        col_names = [c for c in df_type.columns]

        # date columns
        date_cols = []

        columns, data_arrs, out_types = self._get_read_file_col_info(
            dtype_map, date_cols, col_names, lhs
        )

        nodes = [
            sql_ext.SqlReader(
                sql_const, con_const, lhs.name, columns, data_arrs, out_types, lhs.loc,
            )
        ]

        columns = columns.copy()  # copy since modified below
        n_cols = len(columns)
        args = ["data{}".format(i) for i in range(n_cols)]
        data_args = args.copy()

        # one column is index
        if index_col != -1 and index_col != False:
            # convert column number to column name
            if isinstance(index_col, int):
                index_col = columns[index_col]
            index_ind = columns.index(index_col)
            index_arg = "bodo.utils.conversion.convert_to_index({})".format(
                data_args[index_ind]
            )
            columns.remove(index_col)
            data_args.remove(data_args[index_ind])
        else:
            # generate RangeIndex as default index
            assert len(data_args) > 0
            index_arg = "bodo.hiframes.pd_index_ext.init_range_index(0, len({}), 1, None)".format(
                data_args[0]
            )

        # Below we assume that the columns are strings
        col_var = gen_const_tup(columns)
        func_text = "def _init_df({}):\n".format(", ".join(args))
        func_text += "  return bodo.hiframes.pd_dataframe_ext.init_dataframe(({},), {}, {})\n".format(
            ", ".join(data_args), index_arg, col_var
        )
        loc_vars = {}
        exec(func_text, {}, loc_vars)
        _init_df = loc_vars["_init_df"]

        nodes += compile_func_single_block(_init_df, data_arrs, lhs)
        return nodes

    def _handle_pd_read_excel(self, assign, lhs, rhs, label):
        """add typing info to pd.read_excel() using extra argument '_bodo_df_type'
        This enables the overload implementation to just call Pandas
        """
        # schema (Pandas 1.0.3): io, sheet_name=0, header=0, names=None, index_col=None,
        # usecols=None, squeeze=False, dtype=None, engine=None, converters=None,
        # true_values=None, false_values=None, skiprows=None, nrows=None,
        # na_values=None, keep_default_na=True, verbose=False, parse_dates=False,
        # date_parser=None, thousands=None, comment=None, skipfooter=0,
        # convert_float=True, mangle_dupe_cols=True,
        kws = dict(rhs.kws)
        fname_var = get_call_expr_arg("read_excel", rhs.args, kws, 0, "io")
        sheet_name = self._get_const_arg(
            "read_excel", rhs.args, kws, 1, "sheet_name", 0, typ="str or int",
        )
        header = self._get_const_arg(
            "read_excel", rhs.args, kws, 2, "header", 0, typ="int",
        )
        col_names = self._get_const_arg("read_excel", rhs.args, kws, 3, "names", 0)
        # index_col = self._get_const_arg("read_excel", rhs.args, kws, 4, "index_col", -1)
        comment = self._get_const_arg("read_excel", rhs.args, kws, 20, "comment", "")
        date_cols = self._get_const_arg(
            "pd.read_excel", rhs.args, kws, 17, "parse_dates", [], typ="int or str"
        )
        dtype_var = get_call_expr_arg("read_excel", rhs.args, kws, 7, "dtype", "")
        skiprows = self._get_const_arg(
            "read_excel", rhs.args, kws, 12, "skiprows", 0, typ="int",
        )

        # replace "" placeholder with default None (can't use None in _get_const_arg)
        if comment == "":
            comment = None

        # TODO: support index_col
        # if index_col == -1:
        #     index_col = None

        # check unsupported arguments
        supported_args = (
            "io",
            "sheet_name",
            "header",
            "names",
            # "index_col",
            "comment",
            "dtype",
            "skiprows",
            "parse_dates",
        )
        unsupported_args = set(kws.keys()) - set(supported_args)
        if unsupported_args:
            raise BodoError(
                "read_excel() arguments {} not supported yet".format(unsupported_args)
            )

        if dtype_var != "" and col_names == 0 or dtype_var == "" and col_names != 0:
            raise BodoError(
                "pd.read_excel(): both 'dtype' and 'names' should be provided if either is provided"
            )

        # inference is necessary
        # TODO: refactor with read_csv
        if dtype_var == "":
            # infer column names and types from constant filename
            msg = (
                "pd.read_excel() requires explicit type "
                "annotation using 'dtype' if filename is not constant"
            )
            fname_const = get_const_value(
                fname_var, self.func_ir, msg, arg_types=self.args
            )

            df_type = _get_excel_df_type_from_file(
                fname_const, sheet_name, skiprows, header, comment, date_cols
            )

        else:
            dtype_map = guard(get_definition, self.func_ir, dtype_var)

            if (
                not isinstance(dtype_map, ir.Expr) or dtype_map.op != "build_map"
            ):  # pragma: no cover
                # try single type for all columns case
                dtype_map = self._get_const_dtype(dtype_var)
            else:
                new_dtype_map = {}
                for n_var, t_var in dtype_map.items:
                    # find constant column name
                    c = guard(find_const, self.func_ir, n_var)
                    if c is None:  # pragma: no cover
                        raise BodoError("dtype column names should be constant")
                    new_dtype_map[c] = self._get_const_dtype(t_var)

                # HACK replace build_map to avoid inference errors
                dtype_map.op = "build_list"
                dtype_map.items = [v[0] for v in dtype_map.items]
                dtype_map = new_dtype_map

            index = RangeIndexType(types.none)
            # TODO: support index_col
            # if index_col is not None:
            #     index_name = col_names[index_col]
            #     index = bodo.hiframes.pd_index_ext.array_typ_to_index(dtype_map[index_name], types.StringLiteral(index_name))
            #     col_names.remove(index_name)
            data_arrs = tuple(dtype_map[c] for c in col_names)
            df_type = DataFrameType(data_arrs, index, tuple(col_names), True)

        tp_var = ir.Var(lhs.scope, mk_unique_var("df_type_var"), rhs.loc)
        typ_assign = ir.Assign(ir.Const(df_type, rhs.loc), tp_var, rhs.loc)
        kws["_bodo_df_type"] = tp_var
        rhs.kws = list(kws.items())
        return [typ_assign, assign]

    def _handle_pd_read_csv(self, assign, lhs, rhs, label):
        """transform pd.read_csv(names=[A], dtype={'A': np.int32}) call
        """
        # schema: pd.read_csv(filepath_or_buffer, sep=',', delimiter=None,
        # header='infer', names=None, index_col=None, usecols=None,
        # squeeze=False, prefix=None, mangle_dupe_cols=True, dtype=None,
        # engine=None, converters=None, true_values=None, false_values=None,
        # skipinitialspace=False, skiprows=None, nrows=None, na_values=None,
        # keep_default_na=True, na_filter=True, verbose=False,
        # skip_blank_lines=True, parse_dates=False,
        # infer_datetime_format=False, keep_date_col=False, date_parser=None,
        # dayfirst=False, iterator=False, chunksize=None, compression='infer',
        # thousands=None, decimal=b'.', lineterminator=None, quotechar='"',
        # quoting=0, escapechar=None, comment=None, encoding=None,
        # dialect=None, tupleize_cols=None, error_bad_lines=True,
        # warn_bad_lines=True, skipfooter=0, doublequote=True,
        # delim_whitespace=False, low_memory=True, memory_map=False,
        # float_precision=None)
        kws = dict(rhs.kws)
        fname = get_call_expr_arg("read_csv", rhs.args, kws, 0, "filepath_or_buffer")
        sep = self._get_const_arg("read_csv", rhs.args, kws, 1, "sep", ",")
        sep = self._get_const_arg("read_csv", rhs.args, kws, 2, "delimiter", sep)
        header = self._get_const_arg("read_csv", rhs.args, kws, 3, "header", "infer")
        col_names = self._get_const_arg("read_csv", rhs.args, kws, 4, "names", 0)
        index_col = self._get_const_arg("read_csv", rhs.args, kws, 5, "index_col", -1)
        usecols = self._get_const_arg("read_csv", rhs.args, kws, 6, "usecols", "")
        dtype_var = get_call_expr_arg("read_csv", rhs.args, kws, 10, "dtype", "")
        skiprows = self._get_const_arg("read_csv", rhs.args, kws, 16, "skiprows", 0)
        date_cols = self._get_const_arg(
            "read_csv", rhs.args, kws, 23, "parse_dates", [], typ="int or str"
        )
        compression = self._get_const_arg(
            "read_csv", rhs.args, kws, 32, "compression", "infer"
        )

        # check unsupported arguments
        supported_args = (
            "filepath_or_buffer",
            "sep",
            "delimiter",
            "header",
            "names",
            "index_col",
            "usecols",
            "dtype",
            "skiprows",
            "parse_dates",
            "compression",
        )
        unsupported_args = set(kws.keys()) - set(supported_args)
        if unsupported_args:
            raise ValueError(
                "read_csv() arguments {} not supported yet".format(unsupported_args)
            )

        supported_compression_options = {"infer", "gzip", "bz2", None}
        if compression not in supported_compression_options:
            raise ValueError(
                "pd.read_json() compression = {} is not supported."
                " Supported options are {}".format(
                    compression, supported_compression_options
                )
            )

        # infer the column names: if no names
        # are passed the behavior is identical to ``header=0`` and column
        # names are inferred from the first line of the file, if column
        # names are passed explicitly then the behavior is identical to
        # ``header=None``
        if header == "infer":
            header = 0 if col_names == 0 else None
        elif header != 0 and header != None:
            raise ValueError(
                "pd.read_csv() header should be 'infer', 0, or None, not {}.".format(
                    header
                )
            )

        # if inference is required
        if dtype_var == "" or col_names == 0:
            # infer column names and types from constant filename
            msg = (
                "pd.read_csv() requires explicit type "
                "annotation using 'dtype' if filename is not constant"
            )
            fname_const = get_const_value(fname, self.func_ir, msg, arg_types=self.args)
            df_type = _get_csv_df_type_from_file(
                fname_const, sep, skiprows, header, compression
            )
            dtypes = df_type.data
            usecols = list(range(len(dtypes))) if usecols == "" else usecols
            # convert Pandas generated integer names if any
            cols = [str(df_type.columns[i]) for i in usecols]
            # overwrite column names like Pandas if explicitly provided
            if col_names != 0:
                cols[-len(col_names) :] = col_names
            col_names = cols
            dtype_map = {c: dtypes[usecols[i]] for i, c in enumerate(col_names)}

        usecols = list(range(len(col_names))) if usecols == "" else usecols

        # handle dtype arg if provided
        if dtype_var != "":
            dtype_map = guard(get_definition, self.func_ir, dtype_var)

            if (
                not isinstance(dtype_map, ir.Expr) or dtype_map.op != "build_map"
            ):  # pragma: no cover
                # try single type for all columns case
                dtype_map = self._get_const_dtype(dtype_var)
            else:
                new_dtype_map = {}
                for n_var, t_var in dtype_map.items:
                    # find constant column name
                    c = guard(find_const, self.func_ir, n_var)
                    if c is None:  # pragma: no cover
                        raise ValueError("dtype column names should be constant")
                    new_dtype_map[c] = self._get_const_dtype(t_var)

                # HACK replace build_map to avoid inference errors
                dtype_map.op = "build_list"
                dtype_map.items = [v[0] for v in dtype_map.items]
                dtype_map = new_dtype_map

        if col_names == 0:
            raise ValueError("pd.read_csv() names should be constant list")

        # TODO: support other args

        columns, data_arrs, out_types = self._get_read_file_col_info(
            dtype_map, date_cols, col_names, lhs
        )

        nodes = [
            csv_ext.CsvReader(
                fname,
                lhs.name,
                sep,
                columns,
                data_arrs,
                out_types,
                usecols,
                lhs.loc,
                header,
                compression,
                skiprows,
            )
        ]

        columns = columns.copy()  # copy since modified below
        n_cols = len(columns)
        args = ["data{}".format(i) for i in range(n_cols)]
        data_args = args.copy()

        # one column is index
        if index_col != -1 and index_col != False:
            # convert column number to column name
            if isinstance(index_col, int):
                index_col = columns[index_col]
            index_ind = columns.index(index_col)
            index_arg = "bodo.utils.conversion.convert_to_index({})".format(
                data_args[index_ind]
            )
            columns.remove(index_col)
            data_args.remove(data_args[index_ind])
        else:
            # generate RangeIndex as default index
            assert len(data_args) > 0
            index_arg = "bodo.hiframes.pd_index_ext.init_range_index(0, len({}), 1, None)".format(
                data_args[0]
            )

        # Below we assume that the columns are strings
        col_var = gen_const_tup(columns)
        func_text = "def _init_df({}):\n".format(", ".join(args))
        func_text += "  return bodo.hiframes.pd_dataframe_ext.init_dataframe(({},), {}, {})\n".format(
            ", ".join(data_args), index_arg, col_var
        )
        loc_vars = {}
        exec(func_text, {}, loc_vars)
        # print(func_text)
        _init_df = loc_vars["_init_df"]

        nodes += compile_func_single_block(_init_df, data_arrs, lhs)
        return nodes

    def _handle_pd_read_json(self, assign, lhs, rhs, label):
        """transform pd.read_json() call, 
        where default orient = 'records'

        schema: pandas.read_json(path_or_buf=None, orient=None, typ='frame', 
        dtype=None, convert_axes=None, convert_dates=True, 
        keep_default_dates=True, numpy=False, precise_float=False,
        date_unit=None, encoding=None, lines=False, chunksize=None,
        compression='infer')
        """
        # convert_dates required for date cols
        kws = dict(rhs.kws)
        fname = get_call_expr_arg("read_json", rhs.args, kws, 0, "path_or_buf")
        orient = self._get_const_arg("read_json", rhs.args, kws, 1, "orient", "records")
        frame_or_series = get_call_expr_arg(
            "read_json", rhs.args, kws, 3, "typ", "frame"
        )
        dtype_var = get_call_expr_arg("read_json", rhs.args, kws, 10, "dtype", "")
        # default value is True
        convert_dates = self._get_const_arg(
            "read_json", rhs.args, kws, 5, "convert_dates", True, typ="int or str"
        )
        date_cols = [] if convert_dates is True else convert_dates
        precise_float = self._get_const_arg(
            "read_json", rhs.args, kws, 8, "precise_float", False
        )
        lines = self._get_const_arg("read_json", rhs.args, kws, 11, "lines", True)
        compression = self._get_const_arg(
            "read_json", rhs.args, kws, 13, "compression", "infer"
        )

        # check unsupported arguments
        unsupported_args = {
            "convert_axes",
            "keep_default_dates",
            "numpy",
            "date_unit",
            "encoding",
            "chunksize",
        }

        passed_unsupported = unsupported_args.intersection(kws.keys())
        if len(passed_unsupported) > 0:
            if unsupported_args:
                raise ValueError(
                    "read_json() arguments {} not supported yet".format(
                        passed_unsupported
                    )
                )

        supported_compression_options = {"infer", "gzip", "bz2", None}
        if compression not in supported_compression_options:
            raise ValueError(
                "pd.read_json() compression = {} is not supported."
                " Supported options are {}".format(
                    compression, supported_compression_options
                )
            )

        if frame_or_series != "frame":
            raise ValueError(
                "pd.read_json() typ = {} is not supported."
                "Currently only supports orient = 'frame'".format(frame_or_series)
            )

        if orient != "records":
            raise ValueError(
                "pd.read_json() orient = {} is not supported."
                "Currently only supports orient = 'records'".format(orient)
            )

        if type(lines) != bool:
            raise ValueError(
                "pd.read_json() lines = {} is not supported."
                "lines must be of type bool.".format(lines)
            )

        col_names = []

        # infer column names and types from constant filenames if:
        # not explicitly passed with dtype
        # not reading from s3 & hdfs
        # not reading from directory
        msg = (
            "pd.read_json() requires explicit type "
            "annotation using 'dtype' if filename is not constant"
        )
        fname_const = get_const_value(fname, self.func_ir, msg, arg_types=self.args)
        import os

        if dtype_var == "":
            # can only read partial of the json file
            # when orient == 'records' && lines == True
            if not lines:
                raise ValueError(
                    "pd.read_json() requires explicit type annotation using 'dtype',"
                    " when lines != True"
                )
            # TODO: more error checking needed

            df_type = _get_json_df_type_from_file(
                fname_const, orient, convert_dates, precise_float, lines, compression
            )

            dtypes = df_type.data
            # convert Pandas generated integer names if any
            col_names = [str(df_type.columns[i]) for i in range(len(dtypes))]
            dtype_map = {c: dtypes[i] for i, c in enumerate(col_names)}
        else:  # handle dtype arg if provided
            dtype_map = guard(get_definition, self.func_ir, dtype_var)

            if (
                not isinstance(dtype_map, ir.Expr) or dtype_map.op != "build_map"
            ):  # pragma: no cover
                # try single type for all columns case
                dtype_map = self._get_const_dtype(dtype_var)
            else:
                new_dtype_map = {}
                for n_var, t_var in dtype_map.items:
                    # find constant column name
                    c = guard(find_const, self.func_ir, n_var)
                    if c is None:  # pragma: no cover
                        raise ValueError("dtype column names should be constant")
                    new_dtype_map[c] = self._get_const_dtype(t_var)

                # HACK replace build_map to avoid inference errors
                dtype_map.op = "build_list"
                dtype_map.items = [v[0] for v in dtype_map.items]
                dtype_map = new_dtype_map

        columns, data_arrs, out_types = self._get_read_file_col_info(
            dtype_map, date_cols, col_names, lhs
        )

        nodes = [
            json_ext.JsonReader(
                lhs.name,
                lhs.loc,
                data_arrs,
                out_types,
                fname,
                columns,
                orient,
                convert_dates,
                precise_float,
                lines,
                compression,
            )
        ]

        columns = columns.copy()  # copy since modified below
        n_cols = len(columns)
        args = ["data{}".format(i) for i in range(n_cols)]
        data_args = args.copy()

        # initialize range index
        assert len(data_args) > 0
        index_arg = "bodo.hiframes.pd_index_ext.init_range_index(0, len({}), 1, None)".format(
            data_args[0]
        )

        # Below we assume that the columns are strings
        col_var = gen_const_tup(columns)
        func_text = "def _init_df({}):\n".format(", ".join(args))
        func_text += "  return bodo.hiframes.pd_dataframe_ext.init_dataframe(({},), {}, {})\n".format(
            ", ".join(data_args), index_arg, col_var
        )
        loc_vars = {}
        exec(func_text, {}, loc_vars)
        # print(func_text)
        _init_df = loc_vars["_init_df"]

        nodes += compile_func_single_block(_init_df, data_arrs, lhs)
        return nodes

    def _get_read_file_col_info(self, dtype_map, date_cols, col_names, lhs):
        if isinstance(dtype_map, types.Type):
            typ = dtype_map
            data_arrs = [
                ir.Var(lhs.scope, mk_unique_var(cname), lhs.loc) for cname in col_names
            ]
            return col_names, data_arrs, [typ] * len(col_names)

        columns = []
        data_arrs = []
        out_types = []
        for i, (col_name, typ) in enumerate(dtype_map.items()):
            columns.append(col_name)
            # get array dtype
            if i in date_cols or col_name in date_cols:
                typ = types.Array(types.NPDatetime("ns"), 1, "C")
            out_types.append(typ)
            # output array variable
            data_arrs.append(ir.Var(lhs.scope, mk_unique_var(col_name), lhs.loc))

        return columns, data_arrs, out_types

    def _get_const_dtype(self, dtype_var):
        dtype_def = guard(get_definition, self.func_ir, dtype_var)
        if isinstance(dtype_def, ir.Const) and isinstance(dtype_def.value, str):
            typ_name = dtype_def.value
            if typ_name == "str":
                return string_array_type

            if typ_name.startswith("Int") or typ_name.startswith("UInt"):
                dtype = bodo.libs.int_arr_ext.typeof_pd_int_dtype(
                    pd.api.types.pandas_dtype(typ_name), None
                )
                return IntegerArrayType(dtype.dtype)

            # datetime64 case
            if typ_name == "datetime64[ns]":
                return types.Array(types.NPDatetime("ns"), 1, "C")

            typ_name = "int64" if typ_name == "int" else typ_name
            typ_name = "float64" if typ_name == "float" else typ_name
            typ_name = "bool_" if typ_name == "bool" else typ_name
            # XXX: bool with NA needs to be object, TODO: fix somehow? doc.
            typ_name = "bool_" if typ_name == "O" else typ_name

            if typ_name == "bool_":
                return boolean_array

            typ = getattr(types, typ_name)
            typ = types.Array(typ, 1, "C")
            return typ

        # str case
        if isinstance(dtype_def, ir.Global) and dtype_def.value == str:
            return string_array_type

        # cases that involve a function call like Int, Categorical, np.dtype()
        if isinstance(dtype_def, ir.Expr) and dtype_def.op == "call":
            fdef = guard(find_callname, self.func_ir, dtype_def)
            if (
                fdef is not None
                and len(fdef) == 2
                and fdef[1] == "pandas"
                and (fdef[0].startswith("Int") or fdef[0].startswith("UInt"))
            ):
                pd_dtype = getattr(pd, fdef[0])()
                dtype = bodo.libs.int_arr_ext.typeof_pd_int_dtype(pd_dtype, None)
                return IntegerArrayType(dtype.dtype)

            # np.dtype() case
            if fdef == ("dtype", "numpy"):
                assert len(dtype_def.args) == 1
                return self._get_const_dtype(dtype_def.args[0])

            if not fdef == ("CategoricalDtype", "pandas"):
                raise ValueError(
                    "pd.read_csv() invalid dtype "
                    "(built using a call but not Int or Categorical)"
                )
            err_msg = "categories should be constant list"
            cats = self._get_const_arg(
                "CategoricalDtype",
                dtype_def.args,
                dict(dtype_def.kws),
                0,
                "categories",
                err_msg=err_msg,
            )
            elem_typ = bodo.string_type if len(cats) == 0 else bodo.typeof(cats[0])
            # TODO: support other possible CategoricalDtype() calls
            typ = PDCategoricalDtype(cats, elem_typ, False)
            return CategoricalArray(typ)

        if not isinstance(dtype_def, ir.Expr) or dtype_def.op != "getattr":
            raise ValueError("pd.read_csv() invalid dtype")
        glob_def = guard(get_definition, self.func_ir, dtype_def.value)
        if not isinstance(glob_def, ir.Global) or glob_def.value != np:
            raise ValueError("pd.read_csv() invalid dtype")
        # TODO: extend to other types like string and date, check error
        typ_name = dtype_def.attr
        typ_name = "int64" if typ_name == "int" else typ_name
        typ_name = "float64" if typ_name == "float" else typ_name
        typ = getattr(types, typ_name)
        typ = types.Array(typ, 1, "C")
        return typ

    def _handle_pd_Series(self, assign, lhs, rhs):
        """transform pd.Series(A) call for flatmap case
        """
        kws = dict(rhs.kws)
        data = get_call_expr_arg("pd.Series", rhs.args, kws, 0, "data")

        # match flatmap pd.Series(list(itertools.chain(*A))) and flatten
        data_def = guard(get_definition, self.func_ir, data)
        if (
            is_call(data_def)
            and guard(find_callname, self.func_ir, data_def) == ("list", "builtins")
            and len(data_def.args) == 1
        ):
            data_def = guard(get_definition, self.func_ir, data_def.args[0])

        fdef = guard(find_callname, self.func_ir, data_def)
        if is_call(data_def) and fdef in (
            ("chain", "itertools"),
            ("from_iterable_impl", "bodo.utils.typing"),
        ):
            if fdef == ("chain", "itertools"):
                in_data = data_def.vararg
                data_def.vararg = None  # avoid typing error
            else:
                in_data = data_def.args[0]
            new_arr = ir.Var(in_data.scope, mk_unique_var("flat_arr"), in_data.loc)
            nodes = compile_func_single_block(
                lambda A: bodo.utils.conversion.flatten_array(
                    bodo.utils.conversion.coerce_to_array(A)
                ),
                (in_data,),
                new_arr,
            )
            # put the new array back to pd.Series call
            if len(rhs.args) > 0:
                rhs.args[0] = new_arr
            else:  # kw case
                # TODO: test
                kws["data"] = new_arr
                rhs.kws = list(kws.items())
            nodes.append(assign)
            return nodes

        # pd.Series() is handled in typed pass now
        return [assign]

    def _handle_pq_read_table(self, assign, lhs, rhs):
        if len(rhs.args) != 1:  # pragma: no cover
            raise ValueError("Invalid read_table() arguments")
        # put back the definition removed earlier but remove node
        self.func_ir._definitions[lhs.name].append(rhs)
        self.arrow_tables[lhs.name] = rhs.args[0]
        return []

    def _handle_pq_to_pandas(self, assign, lhs, rhs, t_var):
        return self._gen_parquet_read(self.arrow_tables[t_var.name], lhs)

    def _gen_parquet_read(self, fname, lhs, columns=None):
        # make sure pyarrow is available
        if not config._has_pyarrow:
            raise RuntimeError("pyarrow is required for Parquet support")

        columns, data_arrs, index_col, nodes = self.pq_handler.gen_parquet_read(
            fname, lhs, columns
        )
        n_cols = len(columns)
        args = ", ".join("data{}".format(i) for i in range(n_cols))
        data_args = ", ".join(
            "data{}".format(i)
            for i in range(n_cols)
            if (
                isinstance(index_col, dict)
                or index_col is None
                or i != columns.index(index_col)
            )
        )

        if index_col is None:
            assert n_cols > 0
            index_arg = (
                "bodo.hiframes.pd_index_ext.init_range_index(0, len(data0), 1, None)"
            )
        elif isinstance(index_col, dict):
            if index_col["name"] is None:
                index_col_name = None
            else:
                index_col_name = "'{}'".format(index_col["name"])
            index_arg = "bodo.hiframes.pd_index_ext.init_range_index({}, {}, {}, {})".format(
                index_col["start"], index_col["stop"], index_col["step"], index_col_name
            )
        else:
            # if the index_col is __index_level_0_, it means it has no name.
            # Thus we do not write the name instead of writing '__index_level_0_' as the name
            if "__index_level_" in index_col:
                index_arg = "bodo.utils.conversion.convert_to_index(data{}, None)".format(
                    columns.index(index_col)
                )
            else:
                index_arg = "bodo.utils.conversion.convert_to_index(data{}, '{}')".format(
                    columns.index(index_col), index_col
                )

        col_args = tuple(
            c
            for c in columns
            if (isinstance(index_col, dict) or index_col is None or c != index_col)
        )
        col_var = gen_const_tup(col_args)
        func_text = "def _init_df({}):\n".format(args)
        func_text += "  return bodo.hiframes.pd_dataframe_ext.init_dataframe(({},), {}, {})\n".format(
            data_args, index_arg, col_var
        )
        loc_vars = {}
        exec(func_text, {}, loc_vars)
        _init_df = loc_vars["_init_df"]
        nodes += compile_func_single_block(_init_df, data_arrs, lhs)
        return nodes

    def _handle_pd_read_parquet(self, assign, lhs, rhs):
        # get args and check values
        kws = dict(rhs.kws)
        fname = get_call_expr_arg("read_parquet", rhs.args, kws, 0, "path")
        engine = get_call_expr_arg("read_parquet", rhs.args, kws, 1, "engine", "auto")
        if engine not in ("auto", "pyarrow"):
            raise ValueError("read_parquet: only pyarrow engine supported")

        columns = self._get_const_arg("read_parquet", rhs.args, kws, 2, "columns", -1)
        if columns == -1:
            columns = None

        return self._gen_parquet_read(fname, lhs, columns)

    def _handle_concat(self, assign, lhs, rhs, label):
        # converting build_list to build_tuple before type inference to avoid
        # errors
        kws = dict(rhs.kws)
        objs_arg = get_call_expr_arg("concat", rhs.args, kws, 0, "objs")

        df_list = guard(get_definition, self.func_ir, objs_arg)
        if not isinstance(df_list, ir.Expr) or not (
            df_list.op in ["build_tuple", "build_list"]
        ):
            raise ValueError("pd.concat input should be constant list or tuple")

        # XXX convert build_list to build_tuple since Numba doesn't handle list of
        # arrays for np.concatenate()
        if df_list.op == "build_list":
            df_list.op = "build_tuple"

        if len(df_list.items) == 0:
            # copied error from pandas
            raise ValueError("No objects to concatenate")

        return [assign]

    def _get_const_arg(
        self, f_name, args, kws, arg_no, arg_name, default=None, err_msg=None, typ=None,
    ):
        """Get constant value for a function call argument. Raise error if the value is
        not constant.
        """
        typ = "str" if typ is None else typ
        arg = CONST_NOT_FOUND
        if err_msg is None:
            err_msg = ("{} requires '{}' argument as a constant {}").format(
                f_name, arg_name, typ
            )

        arg_var = get_call_expr_arg(f_name, args, kws, arg_no, arg_name, "")

        try:
            arg = get_const_value_inner(self.func_ir, arg_var, arg_types=self.args)
        except GuardException:
            # raise error if argument specified but not constant
            if arg_var != "":
                raise BodoError(err_msg)

        if arg is CONST_NOT_FOUND:
            if default is not None:
                return default
            raise BodoError(err_msg)
        return arg

    def _handle_metadata(self):
        """remove distributed input annotation from locals and add to metadata
        """
        if "distributed" not in self.metadata:
            # TODO: keep updated in variable renaming?
            self.metadata["distributed"] = self.flags.distributed.copy()

        if "distributed_block" not in self.metadata:
            self.metadata["distributed_block"] = self.flags.distributed_block.copy()

        if "threaded" not in self.metadata:
            self.metadata["threaded"] = self.flags.threaded.copy()

        if "is_return_distributed" not in self.metadata:
            self.metadata["is_return_distributed"] = False

        # handle old input flags
        # e.g. {"A:input": "distributed"} -> "A"
        dist_inputs = {
            var_name.split(":")[0]
            for (var_name, flag) in self.locals.items()
            if var_name.endswith(":input") and flag == "distributed"
        }

        thread_inputs = {
            var_name.split(":")[0]
            for (var_name, flag) in self.locals.items()
            if var_name.endswith(":input") and flag == "threaded"
        }

        # check inputs to be in actuall args
        for arg_name in dist_inputs | thread_inputs:
            if arg_name not in self.func_ir.arg_names:
                raise ValueError(
                    "distributed input {} not found in arguments".format(arg_name)
                )
            self.locals.pop(arg_name + ":input")

        self.metadata["distributed"] |= dist_inputs
        self.metadata["threaded"] |= thread_inputs

        # handle old return flags
        # e.g. {"A:return":"distributed"} -> "A"
        flagged_returns = {
            var_name.split(":")[0]: flag
            for (var_name, flag) in self.locals.items()
            if var_name.endswith(":return")
        }

        for v, flag in flagged_returns.items():
            if flag == "distributed":
                self.metadata["distributed"].add(v)
            elif flag == "threaded":
                self.metadata["threaded"].add(v)

            self.locals.pop(v + ":return")

        return

    def _run_return(self, ret_node):
        # TODO: handle distributed analysis, requires handling variable name
        # change in simplify() and replace_var_names()
        flagged_vars = (
            self.metadata["distributed"]
            | self.metadata["distributed_block"]
            | self.metadata["threaded"]
        )
        all_returns_distributed = self.flags.all_returns_distributed
        nodes = [ret_node]
        cast = guard(get_definition, self.func_ir, ret_node.value)
        assert cast is not None, "return cast not found"
        assert isinstance(cast, ir.Expr) and cast.op == "cast"
        scope = cast.value.scope
        loc = cast.loc
        # XXX: using split('.') since the variable might be renamed (e.g. A.2)
        ret_name = cast.value.name.split(".")[0]
        # save return name to catch invalid dist annotations
        self._return_varnames.add(ret_name)

        if ret_name in flagged_vars or all_returns_distributed:
            flag = (
                "distributed"
                if (
                    ret_name in self.metadata["distributed"]
                    or ret_name in self.metadata["distributed_block"]
                    or all_returns_distributed
                )
                else "threaded"
            )
            # save in metadata that the return value is distributed
            # TODO: refactor the tuple case below, which may need to be just deleted
            if flag == "distributed":
                self.metadata["is_return_distributed"] = True
            nodes = self._gen_replace_dist_return(cast.value, flag)
            new_arr = nodes[-1].target
            new_cast = ir.Expr.cast(new_arr, loc)
            new_out = ir.Var(scope, mk_unique_var(flag + "_return"), loc)
            nodes.append(ir.Assign(new_cast, new_out, loc))
            ret_node.value = new_out
            nodes.append(ret_node)
            return nodes

        cast_def = guard(get_definition, self.func_ir, cast.value)
        if (
            cast_def is not None
            and isinstance(cast_def, ir.Expr)
            and cast_def.op == "build_tuple"
        ):
            nodes = []
            new_var_list = []
            tup_varnames = []
            for v in cast_def.items:
                vname = v.name.split(".")[0]
                self._return_varnames.add(vname)
                tup_varnames.append(vname)
                if vname in flagged_vars or all_returns_distributed:
                    flag = (
                        "distributed"
                        if (
                            vname in self.metadata["distributed"]
                            or all_returns_distributed
                        )
                        else "threaded"
                    )
                    nodes += self._gen_replace_dist_return(v, flag)
                    new_var_list.append(nodes[-1].target)
                else:
                    new_var_list.append(v)
            # the return tuple is distributed if all its items are distributed
            if all(v in self.metadata["distributed"] for v in tup_varnames):
                self.metadata["is_return_distributed"] = True
            new_tuple_node = ir.Expr.build_tuple(new_var_list, loc)
            new_tuple_var = ir.Var(scope, mk_unique_var("dist_return_tp"), loc)
            nodes.append(ir.Assign(new_tuple_node, new_tuple_var, loc))
            new_cast = ir.Expr.cast(new_tuple_var, loc)
            new_out = ir.Var(scope, mk_unique_var("dist_return"), loc)
            nodes.append(ir.Assign(new_cast, new_out, loc))
            ret_node.value = new_out
            nodes.append(ret_node)

        return nodes

    def _gen_replace_dist_return(self, var, flag):
        if flag == "distributed":

            def f(_dist_arr):  # pragma: no cover
                dist_return = bodo.libs.distributed_api.dist_return(_dist_arr)

        elif flag == "threaded":

            def f(_threaded_arr):  # pragma: no cover
                _th_arr = bodo.libs.distributed_api.threaded_return(_threaded_arr)

        else:
            raise ValueError("Invalid return flag {}".format(flag))
        f_block = compile_to_numba_ir(f, {"bodo": bodo}).blocks.popitem()[1]
        replace_arg_nodes(f_block, [var])
        return f_block.body[:-3]  # remove none return


def remove_dead_branches(func_ir):
    """
    Remove branches that have a compile-time constant as condition.
    Similar to dead_branch_prune() of Numba, but dead_branch_prune() only focuses on
    binary expressions in conditions, not simple constants like global values.
    """
    for block in func_ir.blocks.values():
        assert len(block.body) > 0
        last_stmt = block.body[-1]
        if isinstance(last_stmt, ir.Branch):
            # handle const bool() calls like bool(False)
            cond = last_stmt.cond
            cond_def = guard(get_definition, func_ir, cond)
            if (
                guard(find_callname, func_ir, cond_def) == ("bool", "numpy")
                and guard(find_const, func_ir, cond_def.args[0]) is not None
            ):
                cond = cond_def.args[0]
            try:
                cond_val = find_const(func_ir, cond)
                target_label = last_stmt.truebr if cond_val else last_stmt.falsebr
                block.body[-1] = ir.Jump(target_label, last_stmt.loc)
            except GuardException:
                pass

    # Remove dead blocks using CFG
    cfg = compute_cfg_from_blocks(func_ir.blocks)
    for dead in cfg.dead_nodes():
        del func_ir.blocks[dead]


def _get_json_df_type_from_file(
    fname_const, orient, convert_dates, precise_float, lines, compression
):
    """get dataframe type for read_json() using file path constant.
    Only rank 0 looks at the file to infer df type, then broadcasts.
    """
    from mpi4py import MPI

    comm = MPI.COMM_WORLD

    df_type = None
    if bodo.get_rank() == 0:
        from bodo.io.fs_io import find_file_name_or_handler

        rows_to_read = 20  # TODO: tune this
        is_handler, file_name_or_handler, f_size, _ = find_file_name_or_handler(
            fname_const, "json"
        )
        if is_handler and compression == "infer":
            # pandas can't infer compression without filename, we need to do it
            if fname_const.endswith(".gz"):
                compression = "gzip"
            elif fname_const.endswith(".bz2"):
                compression = "bz2"
            else:
                compression = None
        try:
            # Ideally, we should use chunksize to read the number of rows desired
            # instead of manually reading the number of rows into a buffer.
            # There is a issue for it in pandas:
            # https://github.com/pandas-dev/pandas/issues/27135
            # read() is used instead of readline() because
            # Pyarrow's hdfs open() returns stream (NativeFile) that does not
            # readline() implemented, but seems like they are working on it:
            # https://issues.apache.org/jira/browse/ARROW-7584

            file_name_or_buff = file_name_or_handler

            # TODO read only `rows_to_read` of compressed files
            if is_handler and compression is None:
                read_chunk_size = 500  # max number of bytes read at a time
                rows_read = 0  # rows seen
                size_read = 0  # number of bytes seen
                buff = ""  # bytes read
                # keep reading until we read at least rows_to_read number of rows
                while size_read < f_size and rows_read < rows_to_read:
                    read_size = min(read_chunk_size, f_size - size_read)
                    tmp_buff = file_name_or_handler.read(read_size).decode("utf-8")
                    rows_read += tmp_buff.count("\n")
                    buff += tmp_buff
                    size_read += read_size
                file_name_or_buff = buff

            df = pd.read_json(
                file_name_or_buff,
                orient=orient,
                convert_dates=convert_dates,
                precise_float=precise_float,
                lines=lines,
                compression=compression,
                # chunksize=rows_to_read,
            )
        finally:
            if is_handler:
                file_name_or_handler.close()

        # TODO: categorical, etc.
        df_type = numba.typeof(df)
        # always convert to nullable type since initial rows of a column could be all
        # int for example, but later rows could have NAs
        df_type = to_nullable_type(df_type)

    df_type = comm.bcast(df_type)
    return df_type


def _get_excel_df_type_from_file(
    fname_const, sheet_name, skiprows, header, comment, date_cols
):
    """get dataframe type for read_excel() using file path constant.
    Only rank 0 looks at the file to infer df type, then broadcasts.
    """
    from mpi4py import MPI

    comm = MPI.COMM_WORLD

    df_type = None
    if bodo.get_rank() == 0:
        rows_to_read = 100  # TODO: tune this
        df = pd.read_excel(
            fname_const,
            sheet_name=sheet_name,
            nrows=rows_to_read,
            skiprows=skiprows,
            header=header,
            # index_col=index_col,
            comment=comment,
            parse_dates=date_cols,
        )
        df_type = numba.typeof(df)
        # always convert to nullable type since initial rows of a column could be all
        # int for example, but later rows could have NAs
        df_type = to_nullable_type(df_type)

    df_type = comm.bcast(df_type)
    return df_type


def _get_sql_df_type_from_db(sql_const, con_const):
    """access the database to find df type for read_sql() output.
    Only rank zero accesses the database, then broadcasts.
    """
    from mpi4py import MPI

    comm = MPI.COMM_WORLD

    df_type = None
    if bodo.get_rank() == 0:
        rows_to_read = 100  # TODO: tune this
        sql_call = f"select * from ({sql_const}) x LIMIT {rows_to_read}"
        df = pd.read_sql(sql_call, con_const)
        df_type = numba.typeof(df)
        # always convert to nullable type since initial rows of a column could be all
        # int for example, but later rows could have NAs
        df_type = to_nullable_type(df_type)

    df_type = comm.bcast(df_type)
    return df_type


def _get_csv_df_type_from_file(fname_const, sep, skiprows, header, compression):
    """get dataframe type for read_csv() using file path constant or raise error if not
    possible (e.g. file doesn't exist).
    If fname_const points to a directory, find a non-empty csv file from
    the directory. 
    For posix, pass the file name directly to pandas. For s3 & hdfs, open the 
    file reader, and pass it to pandas.
    Only rank 0 looks at the file to infer df type, then broadcasts.
    """
    from mpi4py import MPI

    comm = MPI.COMM_WORLD

    # dataframe type or Exception raised trying to find the type
    df_type_or_e = None
    if bodo.get_rank() == 0:
        from bodo.io.fs_io import find_file_name_or_handler

        is_handler = None
        try:
            is_handler, file_name_or_handler, _, _ = find_file_name_or_handler(
                fname_const, "csv"
            )

            if is_handler and compression == "infer":
                # pandas can't infer compression without filename, we need to do it
                if fname_const.endswith(".gz"):
                    compression = "gzip"
                elif fname_const.endswith(".bz2"):
                    compression = "bz2"
                else:
                    compression = None

            rows_to_read = 100  # TODO: tune this
            df = pd.read_csv(
                file_name_or_handler,
                sep=sep,
                nrows=rows_to_read,
                skiprows=skiprows,
                header=header,
                compression=compression,
            )

            # TODO: categorical, etc.
            df_type_or_e = numba.typeof(df)
            # always convert to nullable type since initial rows of a column could be all
            # int for example, but later rows could have NAs
            df_type_or_e = to_nullable_type(df_type_or_e)
        except Exception as e:
            df_type_or_e = e
        finally:
            if is_handler:
                file_name_or_handler.close()

    df_type_or_e = comm.bcast(df_type_or_e)

    # raise error on all processors if found (not just rank 0 which would cause hangs)
    if isinstance(df_type_or_e, Exception):
        raise df_type_or_e

    return df_type_or_e


def _check_type(val, typ):
    """check whether "val" is of type "typ", or any type in "typ" if "typ" is a list
    """
    if isinstance(typ, list):
        return any(isinstance(val, t) for t in typ)
    return isinstance(val, typ)
