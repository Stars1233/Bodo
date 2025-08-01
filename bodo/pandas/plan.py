from __future__ import annotations

import sys
import time
import traceback
from contextlib import contextmanager

import pandas as pd
import pyarrow as pa
from pandas._libs import lib

import bodo
from bodo.pandas.utils import (
    BODO_NONE_DUMMY,
    arrow_to_empty_df,
    cpp_table_to_df,
    cpp_table_to_series,
    get_n_index_arrays,
    wrap_plan,
)


class LazyPlan:
    """Easiest mode to use DuckDB is to generate isolated queries and try to minimize
    node re-use issues due to the frequent use of unique_ptr.  This class should be
    used when constructing all plans and holds them lazily.  On demand, generate_duckdb
    can be used to convert to an isolated set of DuckDB objects for execution.
    """

    def __init__(self, plan_class, empty_data, *args):
        self.plan_class = plan_class
        self.args = args
        assert isinstance(empty_data, (pd.DataFrame, pd.Series)), (
            "LazyPlan: empty_data must be a DataFrame or Series"
        )
        self.is_series = isinstance(empty_data, pd.Series)
        self.empty_data = empty_data
        if self.is_series:
            # None name doesn't round-trip to dataframe correctly so we use a dummy name
            # that is replaced with None in wrap_plan
            name = BODO_NONE_DUMMY if empty_data.name is None else empty_data.name
            self.empty_data = empty_data.to_frame(name=name)

        self.pa_schema = pa.Schema.from_pandas(self.empty_data)

    def __str__(self):
        args = self.args

        # Avoid duplicated plan strings by omitting data_source.
        if isinstance(self, ColRefExpression):
            col_index = args[1]
            return f"ColRefExpression({col_index})"
        elif isinstance(self, PythonScalarFuncExpression):
            func_name, col_indices = args[1][0], args[2]
            return f"PythonScalarFuncExpression({func_name}, {col_indices})"

        out = f"{self.plan_class}: \n"
        args_str = ""
        for arg in args:
            if isinstance(arg, pd.DataFrame):
                args_str += f"{arg.columns.tolist()}\n"
            elif arg is not None:
                args_str += f"{arg}\n"

        out += "\n".join(
            f"  {arg_line}"
            for arg_line in args_str.split("\n")
            if not arg_line.isspace()
        )

        return out

    __repr__ = __str__

    def generate_duckdb(self, cache=None):
        from bodo.ext import plan_optimizer

        # Sometimes the same LazyPlan object is encountered twice during the same
        # query so  we use the cache dict to only convert it once.
        if cache is None:
            cache = {}
        # If previously converted then use the last result.
        # Don't cache expression nodes.
        # TODO - Try to eliminate caching altogether since it seems to cause
        # more problems than lack of caching.
        if not isinstance(self, Expression) and id(self) in cache:
            return cache[id(self)]

        def recursive_check(x, use_cache):
            """Recursively convert LazyPlans but return other types unmodified."""
            if isinstance(x, LazyPlan):
                return x.generate_duckdb(cache=cache if use_cache else None)
            elif isinstance(x, (tuple, list)):
                return type(x)(recursive_check(i, use_cache) for i in x)
            else:
                return x

        # NOTE: Caching is necessary to make sure source operators which have table
        # indexes and are reused in various nodes (e.g. expressions) are not re-created
        # with different table indexes.
        # Join however doesn't need this and cannot use caching since a sub-plan may
        # be reused across right and left sides (e.g. self-join) leading to unique_ptr
        # errors.
        use_cache = True
        if isinstance(self, (LogicalComparisonJoin, LogicalSetOperation)):
            use_cache = False

        # Convert any LazyPlan in the args.
        # We do this in reverse order because we expect the first arg to be
        # the source of the plan and for the node being created to take
        # ownership of that source.  If other args reference that
        # plan then if we process them after we have taken ownership then
        # we will get nullptr exceptions.  So, process the args that don't
        # claim ownership first (in the reverse direction) and finally
        # process the first arg which we expect will take ownership.
        args = [recursive_check(x, use_cache) for x in reversed(self.args)]
        args.reverse()

        # Create real duckdb class.
        ret = getattr(plan_optimizer, self.plan_class)(self.pa_schema, *args)
        # Add to cache so we don't convert it again.
        cache[id(self)] = ret
        return ret

    def replace_empty_data(self, empty_data):
        """Replace the empty_data of the plan with a new empty_data."""
        out = self.__class__(
            empty_data,
            *self.args,
        )
        out.is_series = self.is_series
        return out


class LogicalOperator(LazyPlan):
    """Base class for all logical operators in the Bodo query plan."""

    def __init__(self, empty_data, *args):
        super().__init__(self.__class__.__name__, empty_data, *args)


class Expression(LazyPlan):
    """Base class for all expressions in the Bodo query plan,
    such as column references, function calls, and arithmetic operations.
    """

    def __init__(self, empty_data, *args):
        super().__init__(self.__class__.__name__, empty_data, *args)

    def update_func_expr_source(self, new_source_plan: LazyPlan, col_index_offset: int):
        """Update the source and column index of function expressions, which could be
        nested inside this expression."""
        new_args = [
            arg.update_func_expr_source(new_source_plan, col_index_offset)
            if isinstance(arg, Expression)
            else arg
            for arg in self.args
        ]
        out = self.__class__(
            self.empty_data,
            *new_args,
        )
        out.is_series = self.is_series
        return out

    def replace_source(self, new_source: LazyPlan):
        """Replace the source of the expression with a new source plan."""
        if self.source == new_source:
            return self


class LogicalProjection(LogicalOperator):
    """Logical operator for projecting columns and expressions."""

    def __init__(self, empty_data, source, exprs):
        self.source = source
        self.exprs = exprs
        super().__init__(empty_data, source, exprs)


class LogicalFilter(LogicalOperator):
    """Logical operator for filtering rows based on conditions."""

    pass


class LogicalAggregate(LogicalOperator):
    """Logical operator for aggregation operations."""

    pass


class LogicalDistinct(LogicalOperator):
    """Logical operator for distinct rows."""

    pass


class LogicalComparisonJoin(LogicalOperator):
    """Logical operator for comparison-based joins."""

    @property
    def left_plan(self):
        return self.args[0]

    @property
    def right_plan(self):
        return self.args[1]

    @property
    def join_type(self):
        return self.args[2]


class LogicalSetOperation(LogicalOperator):
    """Logical operator for set operations like union."""

    pass


class LogicalLimit(LogicalOperator):
    """Logical operator for limiting the number of rows (e.g. df.head())."""

    pass


class LogicalOrder(LogicalOperator):
    """Logical operator for sorting data."""

    pass


class LogicalGetParquetRead(LogicalOperator):
    """Logical operator for reading Parquet files."""

    pass


class LogicalGetPandasReadSeq(LogicalOperator):
    """Logical operator for sequential read of a Pandas DataFrame."""

    pass


class LogicalGetPandasReadParallel(LogicalOperator):
    """Logical operator for parallel read of a Pandas DataFrame.\
    """

    pass


class LogicalGetIcebergRead(LogicalOperator):
    """Logical operator for reading Apache Iceberg tables."""

    def __init__(
        self,
        empty_data,
        table_identifier,
        catalog_name,
        catalog_properties,
        row_filter,
        pyiceberg_schema,
        snapshot_id,
        table_len_estimate,
        *,
        arrow_schema,
    ):
        super().__init__(
            empty_data,
            table_identifier,
            catalog_name,
            catalog_properties,
            row_filter,
            pyiceberg_schema,
            snapshot_id,
            table_len_estimate,
        )
        # Iceberg needs schema metadata
        # TODO: avoid this to support operations like renaming columns
        self.pa_schema = arrow_schema


class LogicalParquetWrite(LogicalOperator):
    """Logical operator for writing data to Parquet files."""

    pass


class LogicalIcebergWrite(LogicalOperator):
    """Logical operator for writing data to Apache Iceberg tables."""

    pass


class LogicalS3VectorsWrite(LogicalOperator):
    """Logical operator for writing data to S3 Vectors."""

    def __init__(
        self,
        empty_data,
        source,
        vector_bucket_name,
        index_name,
        region,
    ):
        self.source = source
        self.vector_bucket_name = vector_bucket_name
        self.index_name = index_name
        self.region = region
        super().__init__(empty_data, source, vector_bucket_name, index_name, region)


class ColRefExpression(Expression):
    """Expression representing a column reference in the query plan."""

    def __init__(self, empty_data, source, col_index):
        self.source = source
        self.col_index = col_index
        super().__init__(empty_data, source, col_index)

    def replace_source(self, new_source: LazyPlan):
        """Replace the source of the expression with a new source plan."""
        if self.source == new_source:
            return self

        # If the new source is a projection on the same source, we can just update the
        # column index
        if (
            isinstance(new_source, LogicalProjection)
            and new_source.source == self.source
        ):
            for i, expr in enumerate(new_source.exprs):
                if (
                    isinstance(expr, ColRefExpression)
                    and expr.col_index == self.col_index
                ):
                    # Found the same column in the new projection
                    out = ColRefExpression(self.empty_data, new_source, i)
                    out.is_series = self.is_series
                    return out

        # Cannot replace source, return None to indicate failure
        return None


class NullExpression(Expression):
    """Expression representing a null value in the query plan."""

    def __init__(self, empty_data, source, field_idx):
        # Source is kept only for frontend plan checking and not passed to backend.
        self.empty_data = empty_data
        self.source = source
        self.field_idx = field_idx
        super().__init__(empty_data, field_idx)

    def replace_source(self, new_source: LazyPlan):
        """Replace the source of the expression with a new source plan."""
        if self.source == new_source:
            return self

        # If the new source is a projection on the same source, we can just update the
        # source
        if (
            isinstance(new_source, LogicalProjection)
            and new_source.source == self.source
        ):
            out = NullExpression(self.empty_data, new_source, self.field_idx)
            out.is_series = self.is_series
            return out

        # Cannot replace source, return None to indicate failure
        return None


class ConstantExpression(Expression):
    """Expression representing a constant value in the query plan."""

    def __init__(self, empty_data, source, value):
        # Source is kept only for frontend plan checking and not passed to backend.
        self.empty_data = empty_data
        self.source = source
        self.value = value
        super().__init__(empty_data, value)

    def replace_source(self, new_source: LazyPlan):
        """Replace the source of the expression with a new source plan."""
        if self.source == new_source:
            return self

        # If the new source is a projection on the same source, we can just update the
        # source
        if (
            isinstance(new_source, LogicalProjection)
            and new_source.source == self.source
        ):
            out = ConstantExpression(self.empty_data, new_source, self.value)
            out.is_series = self.is_series
            return out

        # Cannot replace source, return None to indicate failure
        return None


class AggregateExpression(Expression):
    """Expression representing an aggregate function in the query plan."""

    @property
    def source(self):
        """Return the source of the aggregate expression."""
        return self.args[0]

    def replace_source(self, new_source: LazyPlan):
        # TODO: handle source replacement for aggregate expressions
        if self.source == new_source:
            return self


class PythonScalarFuncExpression(Expression):
    """Expression representing a Python scalar function call in the query plan."""

    @property
    def source(self):
        """Return the source of the expression."""
        return self.args[0]

    @property
    def func_args(self):
        """Return the arguments to the func."""
        return self.args[1]

    @property
    def input_column_indices(self):
        """Return the columns relevant to the expression."""
        return self.args[2]

    @property
    def is_cfunc(self):
        """Returns whether the scalar function is a cfunc."""
        return self.args[3]

    @property
    def has_state(self):
        """Returns whether the scalar function has separate init state."""
        return self.args[4]

    def update_func_expr_source(self, new_source_plan: LazyPlan, col_index_offset: int):
        """Update the source and column index of the function expression."""
        if self.source != new_source_plan:
            assert len(self.input_column_indices) == 1 + get_n_index_arrays(
                self.empty_data.index
            ), (
                "PythonScalarFuncExpression::update_func_expr_source: expected single input column"
            )
            # Previous input data column index
            in_col_ind = self.input_column_indices[0]
            n_source_cols = len(new_source_plan.empty_data.columns)
            # Add Index columns of the new source plan as input
            index_cols = tuple(
                range(
                    n_source_cols,
                    n_source_cols
                    + get_n_index_arrays(new_source_plan.empty_data.index),
                )
            )
            expr = PythonScalarFuncExpression(
                self.empty_data,
                new_source_plan,
                self.func_args,
                (in_col_ind + col_index_offset,) + index_cols,
                self.is_cfunc,
                self.has_state,
            )
            expr.is_series = self.is_series
            return expr
        return self

    def replace_source(self, new_source: LazyPlan):
        # TODO: handle source replacement for PythonScalarFuncExpression
        if self.source == new_source:
            return self


class BinaryExpression(Expression):
    """Base class for binary expressions in the query plan, such as arithmetic and
    comparison operations.
    """

    def __init__(self, empty_data, lhs, rhs, op):
        self.empty_data = empty_data
        self.lhs = lhs
        self.rhs = rhs
        self.op = op
        super().__init__(empty_data, lhs, rhs, op)

    @property
    def source(self):
        """Return the source of the binary expression."""
        return self.lhs.source if isinstance(self.lhs, Expression) else self.rhs.source

    def replace_source(self, new_source: LazyPlan):
        """Replace the source of the expression with a new source plan."""
        new_lhs = (
            self.lhs.replace_source(new_source)
            if isinstance(self.lhs, Expression)
            else self.lhs
        )
        new_rhs = (
            self.rhs.replace_source(new_source)
            if isinstance(self.rhs, Expression)
            else self.rhs
        )

        if (new_lhs is None and self.lhs is not None) or (
            new_rhs is None and self.rhs is not None
        ):
            return None

        out = self.__class__(self.empty_data, new_lhs, new_rhs, self.op)
        out.is_series = self.is_series
        return out


class ComparisonOpExpression(BinaryExpression):
    """Expression representing a comparison operation in the query plan."""

    pass


class ConjunctionOpExpression(BinaryExpression):
    """Expression representing a conjunction (AND) operation in the query plan."""

    pass


class UnaryOpExpression(Expression):
    """Expression representing a unary operation (e.g. negation) in the query plan."""

    def __init__(self, empty_data, source_expr, op):
        self.empty_data = empty_data
        self.source_expr = source_expr
        self.op = op
        super().__init__(empty_data, source_expr, op)

    @property
    def source(self):
        """Return the source of the unary expression."""
        return self.source_expr.source

    def replace_source(self, new_source: LazyPlan):
        """Replace the source of the expression with a new source plan."""
        new_source_expr = self.source_expr.replace_source(new_source)
        if new_source_expr is None:
            return None

        out = UnaryOpExpression(self.empty_data, new_source_expr, self.op)
        out.is_series = self.is_series
        return out


class ArithOpExpression(BinaryExpression):
    """Expression representing an arithmetic operation (e.g. addition, subtraction)
    in the query plan.
    """

    pass


total_init_lazy = 0
total_execute_plan = 0


def execute_plan(plan: LazyPlan):
    """Execute a dataframe plan using Bodo's execution engine.

    Args:
        plan (LazyPlan): query plan to execute

    Returns:
        pd.DataFrame: output data
    """
    import bodo

    PlanExecutionCounter.increment()

    def _exec_plan(plan):
        import bodo
        from bodo.ext import plan_optimizer

        if bodo.libs.distributed_api.get_rank() == 0:
            start_time = time.perf_counter()
        duckdb_plan = plan.generate_duckdb()
        if bodo.dataframe_library_profile and bodo.libs.distributed_api.get_rank() == 0:
            print("profile_time gen", time.perf_counter() - start_time)

        if (
            bodo.dataframe_library_dump_plans
            and bodo.libs.distributed_api.get_rank() == 0
        ):
            print("Unoptimized plan")
            print(duckdb_plan.toString())

        # Print the plan before optimization
        if bodo.tracing_level >= 2 and bodo.libs.distributed_api.get_rank() == 0:
            pre_optimize_graphviz = duckdb_plan.toGraphviz()
            with open("pre_optimize" + str(id(plan)) + ".dot", "w") as f:
                print(pre_optimize_graphviz, file=f)

        if bodo.libs.distributed_api.get_rank() == 0:
            start_time = time.perf_counter()
        optimized_plan = plan_optimizer.py_optimize_plan(duckdb_plan)
        if bodo.dataframe_library_profile and bodo.libs.distributed_api.get_rank() == 0:
            print("profile_time opt", time.perf_counter() - start_time)

        if (
            bodo.dataframe_library_dump_plans
            and bodo.libs.distributed_api.get_rank() == 0
        ):
            print("Optimized plan")
            print(optimized_plan.toString())

        # Print the plan after optimization
        if bodo.tracing_level >= 2 and bodo.libs.distributed_api.get_rank() == 0:
            post_optimize_graphviz = optimized_plan.toGraphviz()
            with open("post_optimize" + str(id(plan)) + ".dot", "w") as f:
                print(post_optimize_graphviz, file=f)

        output_func = cpp_table_to_series if plan.is_series else cpp_table_to_df
        if bodo.libs.distributed_api.get_rank() == 0:
            start_time = time.perf_counter()
        ret = plan_optimizer.py_execute_plan(
            optimized_plan, output_func, duckdb_plan.out_schema
        )
        if bodo.dataframe_library_profile and bodo.libs.distributed_api.get_rank() == 0:
            print("profile_time execute", time.perf_counter() - start_time)
        return ret

    if bodo.dataframe_library_run_parallel:
        import bodo.spawn.spawner

        start_time = time.perf_counter()
        # Initialize LazyPlanDistributedArg objects that may need scattering data
        # to workers before execution.
        for a in plan.args:
            _init_lazy_distributed_arg(a)
        init_time = time.perf_counter() - start_time
        global total_init_lazy
        total_init_lazy += init_time
        if bodo.dataframe_library_profile:
            print("profile_time _init_lazy_distributed_arg", init_time)

        if bodo.dataframe_library_dump_plans:
            # Sometimes when an execution is triggered it isn't expected that
            # an execution should happen at that point.  This traceback is
            # useful to identify what is triggering the execution as it may be
            # a bug or the usage of some Pandas API that calls a function that
            # triggers execution.  This traceback can help fix the bug or
            # select a different Pandas API or an internal Pandas function that
            # bypasses the issue.
            traceback.print_stack(file=sys.stdout)
            print("")  # Print on new line during tests.

        start_time = time.perf_counter()
        ret = bodo.spawn.spawner.submit_func_to_workers(_exec_plan, [], plan)
        exec_time = time.perf_counter() - start_time
        global total_execute_plan
        total_execute_plan += exec_time
        if bodo.dataframe_library_profile:
            print("profile_time total_execute_plan", exec_time)
        return ret

    return _exec_plan(plan)


def _init_lazy_distributed_arg(arg, visited_plans=None):
    """Initialize the LazyPlanDistributedArg objects for the given plan argument that
    may need scattering data to workers before execution.
    Has to be called right before plan execution since the dataframe state
    may change (distributed to collected) and the result ID may not be valid anymore.
    """
    if visited_plans is None:
        # Keep track of visited LazyPlans to prevent extra checks.
        visited_plans = set()

    if isinstance(arg, LazyPlan):
        if id(arg) in visited_plans:
            return
        visited_plans.add(id(arg))
        for a in arg.args:
            _init_lazy_distributed_arg(a, visited_plans=visited_plans)
    elif isinstance(arg, (tuple, list)):
        for a in arg:
            _init_lazy_distributed_arg(a, visited_plans=visited_plans)
    elif isinstance(arg, LazyPlanDistributedArg):
        arg.init()


def get_plan_cardinality(plan: LazyPlan):
    """See if we can statically know the cardinality of the result of the plan.

    Args:
        plan (LazyPlan): query plan to get cardinality of.

    Returns:
        int (if cardinality is known) or None (if not known)
    """

    duckdb_plan = plan.generate_duckdb()
    return duckdb_plan.getCardinality()


def getPlanStatistics(plan: LazyPlan):
    """Get statistics for a plan pre and post optimization.

    Args:
        plan (LazyPlan): query plan to get statistics for

    Returns:
        Number of nodes in the tree before and after optimization.
    """
    from bodo.ext import plan_optimizer

    duckdb_plan = plan.generate_duckdb()
    preOptNum = plan_optimizer.count_nodes(duckdb_plan)
    optimized_plan = plan_optimizer.py_optimize_plan(duckdb_plan)
    postOptNum = plan_optimizer.count_nodes(optimized_plan)
    return preOptNum, postOptNum


def get_proj_expr_single(proj: LazyPlan):
    """Get the single expression from a LogicalProjection node."""
    assert is_single_projection(proj), "Expected single projection"
    return proj.exprs[0]


def get_single_proj_source_if_present(proj: LazyPlan):
    """Get the single expression from a LogicalProjection node."""
    if is_single_projection(proj):
        return proj.source
    else:
        if not proj.is_series:
            raise Exception("Got a non-Series in get_single_proj_source_if_present")
        return proj


def is_single_projection(proj: LazyPlan):
    """Return True if plan is a projection with a single expression"""
    return isinstance(proj, LogicalProjection) and len(proj.exprs) == (
        get_n_index_arrays(proj.empty_data.index) + 1
    )


def is_single_colref_projection(proj: LazyPlan):
    """Return True if plan is a projection with a single expression that is a column reference"""
    return is_single_projection(proj) and isinstance(proj.exprs[0], ColRefExpression)


def is_colref_projection(proj: LazyPlan):
    """Return True if plan is a projection with all expressions being column references"""
    return isinstance(proj, LogicalProjection) and all(
        isinstance(expr, ColRefExpression) for expr in proj.exprs
    )


def make_col_ref_exprs(key_indices, src_plan):
    """Create column reference expressions for the given key indices for the input
    source plan.
    """

    exprs = []
    for k in key_indices:
        # Using Arrow schema instead of zero_size_self.iloc to handle Index
        # columns correctly.
        empty_data = arrow_to_empty_df(pa.schema([src_plan.pa_schema[k]]))
        p = ColRefExpression(empty_data, src_plan, k)
        exprs.append(p)

    return exprs


class LazyPlanDistributedArg:
    """
    Class to hold the arguments for a LazyPlan that are distributed on the workers.
    """

    def __init__(self, df: pd.DataFrame | pd.Series):
        self.df = df
        self.mgr = None
        self.res_id = None

    def init(self):
        """Initialize to make sure the result ID is set in preparation for pickling
        result ID to workers for execution.
        Should be called right before execution of the plan since the dataframe state
        may change (distributed to collected) and the result ID may not be valid
        anymore.
        """
        if getattr(self.df._mgr, "_md_result_id", None) is not None:
            # The dataframe is already distributed so we can use the existing result ID
            self.res_id = self.df._mgr._md_result_id
        elif self.mgr is not None:
            # We scattered a DataFrame already and own a manager to reuse
            self.res_id = self.mgr._md_result_id
        else:
            # The dataframe is not distributed yet so we need to scatter it
            # and create a new result ID.
            mgr = bodo.spawn.spawner.get_spawner().scatter_data(self.df)
            self.res_id = mgr._md_result_id
            self.mgr = mgr

    def __reduce__(self):
        """
        This method is used to serialize the object for distribution.
        We can't send the manager to the workers without triggering collection
        so we just send the result ID instead.
        """
        assert self.res_id is not None, (
            "LazyPlanDistributedArg: result ID is not set, call init() first"
        )
        return (str, (self.res_id,))


def count_plan(self):
    # See if we can get the cardinality statically.
    static_cardinality = get_plan_cardinality(self._plan)
    if static_cardinality is not None:
        return static_cardinality

    # Can't be known statically so create count plan on top of
    # existing plan.
    count_star_schema = pd.Series(dtype="uint64", name="count_star")
    aggregate_plan = LogicalAggregate(
        count_star_schema,
        self._plan,
        [],
        [
            AggregateExpression(
                count_star_schema,
                self._plan,
                "count_star",
                # Adding column 0 as input to avoid deleting all input by the optimizer
                # TODO: avoid materializing the input column
                [0],
                False,  # dropna
            )
        ],
    )
    projection_plan = LogicalProjection(
        count_star_schema,
        aggregate_plan,
        make_col_ref_exprs([0], aggregate_plan),
    )

    data = execute_plan(projection_plan)
    return data[0]


def _get_df_python_func_plan(df_plan, empty_data, func, args, kwargs, is_method=True):
    """Create plan for calling some function or method on a DataFrame. Creates a
    PythonScalarFuncExpression with provided arguments and a LogicalProjection.
    """
    df_len = len(df_plan.empty_data.columns)
    udf_arg = PythonScalarFuncExpression(
        empty_data,
        df_plan,
        (
            func,
            False,  # is_series
            is_method,
            args,
            kwargs,
        ),
        tuple(range(df_len + get_n_index_arrays(df_plan.empty_data.index))),
        False,  # is_cfunc
        False,  # has_state
    )

    # Select Index columns explicitly for output
    index_col_refs = tuple(
        make_col_ref_exprs(
            range(df_len, df_len + get_n_index_arrays(df_plan.empty_data.index)),
            df_plan,
        )
    )
    plan = LogicalProjection(
        empty_data,
        df_plan,
        (udf_arg,) + index_col_refs,
    )
    return wrap_plan(plan=plan)


def is_col_ref(expr):
    return isinstance(expr, ColRefExpression)


def is_scalar_func(expr):
    return isinstance(expr, PythonScalarFuncExpression)


def is_arith_expr(expr):
    return isinstance(expr, ArithOpExpression)


def match_binop_expr_source_plans(lhs, rhs):
    """Match the source plans of two binary expressions if possible.
    Returns (None, None) if sources cannot be matched.
    """
    if not (isinstance(lhs, Expression) and isinstance(rhs, Expression)):
        # No matching necessary
        return lhs, rhs

    new_lhs = lhs.replace_source(rhs.source)
    if new_lhs is not None:
        return new_lhs, rhs

    new_rhs = rhs.replace_source(lhs.source)
    if new_rhs is not None:
        return lhs, new_rhs

    return None, None


def maybe_make_list(obj):
    """If non-iterable input, turn into singleton list"""
    if obj is None:
        return []
    elif not isinstance(obj, (tuple, list)):
        return [obj]
    elif not isinstance(obj, list):
        return list(obj)
    return obj


def reset_index(self, drop, level, name=None, names=None):
    """Index resetter used by BodoSeries and BodoDataFrame."""
    is_series = isinstance(self, bodo.pandas.BodoSeries)
    assert is_series or isinstance(self, bodo.pandas.BodoDataFrame), (
        "reset_index() should take in either a BodoSeries or a BodoDataFrame."
    )

    index = self._plan.empty_data.index
    levelset = set(maybe_make_list(level))
    new_index = pd.RangeIndex(0) if not level else index.droplevel(level)

    col_names = []
    if names is None:
        if isinstance(index, pd.RangeIndex):
            col_names = "index"
        elif isinstance(index, pd.MultiIndex):
            for i in range(len(index.names)):
                col_name = index.names[i]
                if not level or (i in levelset or col_name in levelset):
                    col_names.append(col_name if col_name is not None else f"level_{i}")
        elif isinstance(index, pd.Index):
            col_names.append(index.name if index.name is not None else "index")
        else:
            raise TypeError(f"Invalid index type: {type(index)}")
    else:
        col_names = maybe_make_list(names) if not is_series else names

    n_cols = 1 if is_series else len(self._plan.empty_data.columns)
    index_size = get_n_index_arrays(index)
    index_cols, remaining_cols = [], []

    data_cols = make_col_ref_exprs(range(n_cols), self._plan)
    empty_data = None

    # Series.reset_index with drop=True does not require extra columns or renaming.
    if is_series and drop:
        empty_data = pd.Series(
            dtype=self.dtype,
            name=self.name,
            index=new_index,
        )
    else:
        empty_data = self._plan.empty_data.copy()
        empty_data.index = new_index

        # Series.reset_index supports `name` field to enable renaming of the Series data column.
        if is_series:
            old_col_name = empty_data.columns[0]
            new_col_name = (
                name
                if name is not lib.no_default
                else (self.name if self.name is not None else "0")
            )
            empty_data = empty_data.rename(columns={old_col_name: new_col_name})

        # If drop=False, append index column names to the front of the resulting Dataframe.
        if not drop:
            drop_index = range(n_cols, n_cols + index_size)
            preserve_index = []
            if level:
                drop_index = []
                for i, name in enumerate(index.names):
                    if name in levelset or i in levelset:
                        drop_index.append(n_cols + i)
                    else:
                        preserve_index.append(n_cols + i)

            index_cols = make_col_ref_exprs(drop_index, self._plan)
            for i in range(len(drop_index)):
                empty_data.insert(i, col_names[i], index_cols[i].empty_data)
            remaining_cols = make_col_ref_exprs(preserve_index, self._plan)

    new_plan = LogicalProjection(
        empty_data,
        self._plan,
        index_cols + data_cols + remaining_cols,
    )

    return wrap_plan(new_plan)


class PlanExecutionCounter:
    count = 0

    @classmethod
    def increment(cls):
        cls.count += 1

    @classmethod
    def reset(cls):
        cls.count = 0

    @classmethod
    def get(cls):
        return cls.count


@contextmanager
def assert_executed_plan_count(n: int):
    start = PlanExecutionCounter.get()
    yield
    end = PlanExecutionCounter.get()
    assert end - start == n, f"Expected {n} plan executions, but got {end - start}"
