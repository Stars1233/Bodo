# Copyright (C) 2022 Bodo Inc. All rights reserved.
from datetime import date

import bodosql
import numpy as np
import pandas as pd
import pytest

import bodo
from bodo.tests.conftest import iceberg_database, iceberg_table_conn  # noqa
from bodo.tests.iceberg_database_helpers.utils import (
    create_iceberg_table,
    get_spark,
)
from bodo.tests.utils import check_func

# Skip this file until we merge the Iceberg branch
pytest.skip(
    allow_module_level=True,
    reason="Waiting for MERGE INTO support to fix the Calcite generated issue",
)

pytestmark = pytest.mark.iceberg

bodo_datatype_cols = {
    "int_col": pd.Series([np.int32(i) for i in range(10)], dtype=np.int32),
    "float_col": pd.Series([np.float32(i) for i in range(10)], dtype=np.float32),
    "str_col": pd.Series([str(i) for i in range(10)], dtype="string[pyarrow]"),
    "bool_col": pd.Series([bool(i % 2) for i in range(10)], dtype=bool),
    # "ts_col": pd.Series(
    #     [pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=i) for i in range(10)],
    # ),
    "non_ascii_col": pd.Series(
        [str(i) + "é" for i in range(10)], dtype="string[pyarrow]"
    ),
    "byte_col": pd.Series([bytes(i) for i in range(10)], dtype="bytes"),
    "long_col": pd.Series([np.int64(i) for i in range(10)], dtype=np.int64),
    "double_col": pd.Series([np.float64(i) for i in range(10)], dtype=np.float64),
    "date_col": pd.Series(
        [
            date(2018, 11, 12),
            date(2019, 11, 12),
            date(2018, 12, 12),
            date(2017, 11, 16),
            date(2020, 7, 11),
            date(2017, 11, 30),
            date(2016, 2, 3),
            date(2019, 11, 12),
            date(2018, 12, 20),
            date(2017, 12, 12),
        ]
    ),
}
bodo_datatype_expected_sql_types = {
    "int_col": "int",
    "float_col": "float",
    "str_col": "string",
    "bool_col": "boolean",
    # "ts_col": "timestamp",  # Spark writes timestamps with UTC timezone
    "non_ascii_col": "string",
    "byte_col": "binary",
    "long_col": "bigint",
    "double_col": "double",
    "date_col": "date",
}


def test_merge_into_bodo_datatypes_as_values(iceberg_database, iceberg_table_conn):
    """
    Test MERGE INTO with all Bodo datatypes as values.
    """

    # create table data
    target_table = pd.DataFrame(
        {
            "id": pd.Series([i for i in range(10)], dtype=np.int32),
        }
        | bodo_datatype_cols
    )

    source = target_table.copy()
    source.id = source.id + 10
    expected = target_table.append(source, ignore_index=True)

    # create query
    query = (
        "MERGE INTO target_table AS t USING source AS s "
        "ON t.id = s.id "
        "WHEN NOT MATCHED THEN "
        f"  INSERT ({', '.join(source.columns)}) "
        f"    values ({', '.join(['s.' + key for key in source.columns])})"
    )

    # create BodoSQL context
    spark = get_spark()
    db_schema, warehouse_loc = iceberg_database
    table_name = "target_table_merge_into_bodo_datatypes_as_values"
    sql_schema = [("id", "int", False)] + [
        (col, bodo_datatype_expected_sql_types[col], False)
        for col in bodo_datatype_cols.keys()
    ]
    if bodo.get_rank() == 0:
        create_iceberg_table(
            target_table,
            sql_schema,
            table_name,
            spark,
        )
    bodo.barrier()
    conn = iceberg_table_conn(table_name, db_schema, warehouse_loc)
    bc = bodosql.BodoSQLContext(
        {
            "target_table": bodosql.TablePath(
                table_name, "sql", conn_str=conn, db_schema=db_schema
            ),
            "source": source,
        }
    )

    # write target table
    bc.add_or_replace_view(table_name, target_table)

    # execute query
    def impl(bc, query):
        bc.sql(query)

        return bc.sql(f"SELECT * FROM target_table ORDER BY id")

    check_func(
        impl,
        (bc, query),
        py_output=expected,
        reset_index=True,
        sort_output=True,
        only_1DVar=True,
    )


@pytest.mark.parametrize("col_name", bodo_datatype_cols.keys())
def test_merge_into_bodo_datatypes_as_expr(
    col_name: str, iceberg_database, iceberg_table_conn
):
    """
    Test MERGE INTO with individual Bodo datatypes as join expression.
    """
    expr = bodo_datatype_cols[col_name]
    sql_type = bodo_datatype_expected_sql_types[col_name]

    # create table data
    target_table = pd.DataFrame(
        {
            "expr": expr[:7],
        }
    )

    source = pd.DataFrame(
        {
            "expr": expr[3:],
        }
    )

    if col_name == "bool_col":
        expected = target_table.copy()
    else:
        expected = target_table.merge(source, on="expr", how="outer")

    # create query
    query = (
        "MERGE INTO target_table AS t USING source AS s "
        "ON t.expr = s.expr "
        "WHEN NOT MATCHED THEN "
        "  INSERT (expr) values (s.expr)"
    )

    # create BodoSQL context
    spark = get_spark()
    db_schema, warehouse_loc = iceberg_database
    table_name = "target_table_merge_into_bodo_datatypes_as_exprs_" + col_name
    sql_schema = [("expr", sql_type, False)]
    if bodo.get_rank() == 0:
        create_iceberg_table(
            target_table,
            sql_schema,
            table_name,
            spark,
        )
    bodo.barrier()
    conn = iceberg_table_conn(table_name, db_schema, warehouse_loc)
    bc = bodosql.BodoSQLContext(
        {
            "target_table": bodosql.TablePath(
                table_name, "sql", conn_str=conn, db_schema=db_schema
            ),
            "source": source,
        }
    )

    # write target table
    bc.add_or_replace_view(table_name, target_table)

    # execute query
    def impl(bc, query):
        bc.sql(query)

        return bc.sql(f"SELECT * FROM target_table")

    check_func(
        impl,
        (bc, query),
        py_output=expected,
        reset_index=True,
        sort_output=True,
        only_1DVar=True,
    )