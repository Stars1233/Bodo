# Copyright (C) 2022 Bodo Inc. All rights reserved.

import io
import os

import bodosql
import pandas as pd
import pytest

from bodo.tests.iceberg_database_helpers.utils import (
    DATABASE_NAME,
    create_iceberg_table,
    get_spark,
)
from bodo.tests.user_logging_utils import (
    check_logger_msg,
    create_string_io_logger,
    set_logging_stream,
)
from bodo.tests.utils import check_func
from bodo.utils.typing import BodoError

pytest.skip(allow_module_level=True, reason="Waiting for MERGE INTO support")


@pytest.fixture(scope="module")
def table_name():
    return "test_merge_into_iceberg_parity"


def _create_and_init_table(table_name, types, df, source=None):
    spark = get_spark()
    warehouse_loc = os.path.abspath(os.getcwd())
    db_schema = DATABASE_NAME
    create_iceberg_table(
        df,
        types,
        table_name,
        spark,
    )
    conn = f"iceberg://{warehouse_loc}"

    return bodosql.BodoSQLContext(
        {
            **{
                table_name: bodosql.TablePath(
                    table_name, conn_str=conn, db_schema=db_schema
                )
            },
            **({"source": source} if source else {}),
        }
    )


def test_merge_into_empty_target_insert_all_non_matching_rows(
    table_name, memory_leak_check
):
    """
    Parity for iceberg's testMergeIntoEmptyTargetInsertAllNonMatchingRows.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(),
        pd.DataFrame({"id": [1, 2, 3], "dep": ["emp-id-1", "emp-id-2", "emp-id-3"]}),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN NOT MATCHED THEN "
            "  INSERT *",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame(
        {"id": [1, 2, 3], "dep": ["emp-id-1", "emp-id-2", "emp-id-3"]}
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_into_empty_target_insert_only_matching_rows(
    table_name, memory_leak_check
):
    """
    Parity for iceberg's testMergeIntoEmptyTargetInsertOnlyMatchingRows.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(),
        pd.DataFrame({"id": [1, 2, 3], "dep": ["emp-id-1", "emp-id-2", "emp-id-3"]}),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN NOT MATCHED AND (s.id >=2) THEN "
            "  INSERT *",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [2, 3], "dep": ["emp-id-2", "emp-id-3"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_into_non_existing_table(table_name, memory_leak_check):
    """
    Tests that merging into a non existing table works throws a reasonable error
    """

    bc = bodosql.BodoSQLContext(
        {
            "source": pd.DataFrame(
                {"id": [1, 2, 3], "dep": ["emp-id-1", "emp-id-2", "emp-id-3"]}
            )
        }
    )

    def impl(bc):

        bc.sql(
            f"MERGE INTO I_DO_NOT_EXIST AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN NOT MATCHED THEN "
            "  INSERT *",
        )

    with pytest.raises(BodoError, "TODO!"):
        impl(bc)


def test_merge_with_only_update_clause(table_name, memory_leak_check):
    """
    Parity for iceberg's testMergeWithOnlyUpdateClause.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        ('{ "id": 1, "dep": "emp-id-one" }\n' '{ "id": 6, "dep": "emp-id-six" }'),
        pd.DataFrame(
            {"id": [1, 6], "dep": ["emp-id-one", "emp-id-six"]},
        ),
        pd.DataFrame(
            {"id": [2, 1, 6], "dep": ["emp-id-2", "emp-id-1", "emp-id-6"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 1 THEN "
            "  UPDATE SET *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 6], "dep": ["emp-id-1", "emp-id-six"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_only_delete_clause(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithOnlyDeleteClause.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [1, 6], "dep": ["emp-id-one", "emp-id-6"]},
        ),
        pd.DataFrame(
            {"id": [2, 1, 6], "dep": ["emp-id-2", "emp-id-1", "emp-id-6"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 6 THEN "
            "  DELETE"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1], "dep": ["emp-id-one"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_all_clauses(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithAllClauses.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [1, 6], "dep": ["emp-id-one", "emp-id-6"]},
        ),
        pd.DataFrame(
            {"id": [2, 1, 6], "dep": ["emp-id-2", "emp-id-1", "emp-id-6"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 1 THEN "
            "  UPDATE SET * "
            "WHEN MATCHED AND t.id = 6 THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND s.id = 2 THEN "
            "  INSERT *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 2], "dep": ["emp-id-1", "emp-id-2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_all_causes_with_explicit_column_specification(
    table_name, memory_leak_check
):
    """
    Parity for Iceberg's testMergeWithAllClausesWithExplicitColumnSpecification.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [1, 6], "dep": ["emp-id-one", "emp-id-6"]},
        ),
        pd.DataFrame(
            {"id": [2, 1, 6], "dep": ["emp-id-2", "emp-id-1", "emp-id-6"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 1 THEN "
            "  UPDATE SET t.id = s.id, t.dep = s.dep "
            "WHEN MATCHED AND t.id = 6 THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND s.id = 2 THEN "
            "  INSERT (id, dep) VALUES (s.id, s.dep)"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 2], "dep": ["emp-id-1", "emp-id-2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_source_cte(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithSourceCTE.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [2, 6], "dep": ["emp-id-two", "emp-id-6"]},
        ),
        pd.DataFrame(
            {"id": [2, 1, 5], "dep": ["emp-id-3", "emp-id-2", "emp-id-6"]},
        ),
    )

    def impl(bc):
        bc.sql(
            "WITH cte1 AS (SELECT id + 1 AS id, dep FROM source) "
            f"MERGE INTO {table_name} AS t USING cte1 AS s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 2 THEN "
            "  UPDATE SET * "
            "WHEN MATCHED AND t.id = 6 THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND s.id = 3 THEN "
            "  INSERT *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [2, 3], "dep": ["emp-id-2", "emp-id-3"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_source_from_set_ops(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithSourceFromSetOps.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [1, 6], "dep": ["emp-id-one", "emp-id-6"]},
        ),
        pd.DataFrame(
            {"id": [2, 1, 6], "dep": ["emp-id-2", "emp-id-1", "emp-id-6"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING (%s) AS s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 1 THEN "
            "  UPDATE SET * "
            "WHEN MATCHED AND t.id = 6 THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND s.id = 2 THEN "
            "  INSERT *",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 2], "dep": ["emp-id-1", "emp-id-2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_multiple_updates_for_target_row(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithMultipleUpdatesForTargetRow.
    """

    derivedSource = (
        "SELECT * FROM source WHERE id = 2 "
        "UNION ALL "
        "SELECT * FROM source WHERE id = 1 OR id = 6"
    )

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [1, 6], "dep": ["emp-id-one", "emp-id-6"]},
        ),
        pd.DataFrame(
            {"id": [2, 1, 6], "dep": ["emp-id-2", "emp-id-1", "emp-id-6"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING {derivedSource} AS s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 1 THEN "
            "  UPDATE SET * "
            "WHEN MATCHED AND t.id = 6 THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND s.id = 2 THEN "
            "  INSERT *",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 2], "dep": ["emp-id-1", "emp-id-2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_unconditional_delete(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithUnconditionalDelete.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [1, 6], "dep": ["emp-id-one", "emp-id-6"]},
        ),
        pd.DataFrame(
            {
                "id": [1, 1, 2, 6],
                "dep": ["emp-id-1", "emp-id-1", "emp-id-2", "emp-id-6"],
            },
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN MATCHED THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND s.id = 2 THEN "
            "  INSERT *",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [2], "dep": ["emp-id-2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_single_conditional_delete(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithSingleConditionalDelete.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [1, 6], "dep": ["emp-id-one", "emp-id-6"]},
        ),
        pd.DataFrame(
            {
                "id": [1, 1, 2, 6],
                "dep": ["emp-id-1", "emp-id-1", "emp-id-2", "emp-id-6"],
            },
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} AS t USING source AS s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 1 THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND s.id = 2 THEN "
            "  INSERT *",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 6], "dep": ["emp-id-one", "emp-id-6"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_self_merge(table_name, memory_leak_check):
    """
    Parity for Iceberg's testSelfMerge.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": [1, 2], "v": ["v1", "v2"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING {table_name} s "
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id = 1 THEN "
            "  UPDATE SET v = 'x' "
            "WHEN NOT MATCHED THEN "
            "  INSERT *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 2], "v": ["x", "v2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_source_as_self_subquery(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithSourceAsSelfSubquery.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": [1, 2], "v": ["v1", "v2"]},
        ),
        pd.DataFrame(
            {"value": [1, None]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING (SELECT id AS value FROM {table_name} r JOIN source ON r.id = source.value) s "
            "ON t.id == s.value "
            "WHEN MATCHED AND t.id = 1 THEN "
            "  UPDATE SET v = 'x' "
            "WHEN NOT MATCHED THEN "
            "  INSERT (v, id) VALUES ('invalid', -1) "
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 2], "dep": ["x", "v2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_extra_columns_in_source(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithExtraColumnsInSource.
    (slightly modified, changed column names in source vs dest
    to make sure logger msg is meaningful)
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": [1, 2], "v": ["v1", "v2"]},
        ),
        pd.DataFrame(
            {
                "id": [1, 3, 4],
                "extra_col": [-1, -1, -1],
                "source_v": ["v1_1", "v3", "v4"],
            },
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id "
            "WHEN MATCHED THEN "
            "  UPDATE SET v = source.source_v "
            "WHEN NOT MATCHED THEN "
            "  INSERT (v, id) VALUES (source.source_v, source.id)",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    stream = io.StringIO()
    logger = create_string_io_logger(stream)
    with set_logging_stream(logger, 1):

        # Check the columns were pruned on the source table
        check_logger_msg(stream, "Columns loaded ['id', 'source_v']")

    expected_rows = pd.DataFrame({"id": [1, 2, 3, 4], "v": ["v1_1", "v2", "v3", "v4"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_nulls_in_target_and_source(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithNullsInTargetAndSource.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": [None, 2], "v": ["v1", "v2"]},
        ),
        pd.DataFrame(
            {"id": [None, 4], "v": ["v1_1", "v4"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id "
            "WHEN MATCHED THEN "
            "  UPDATE SET v = source.v "
            "WHEN NOT MATCHED THEN "
            "  INSERT (v, id) VALUES (source.v, source.id)",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY v")

    expected_rows = pd.DataFrame(
        {"id": [None, None, 2, 4], "v": ["v1", "v1_1", "v2", "v4"]}
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_null_safe_equals(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithNullSafeEquals.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": [None, 2], "v": ["v1", "v2"]},
        ),
        pd.DataFrame(
            {"id": [None, 4], "v": ["v1_1", "v4"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id <=> source.id "
            "WHEN MATCHED THEN "
            "  UPDATE SET v = source.v "
            "WHEN NOT MATCHED THEN "
            "  INSERT (v, id) VALUES (source.v, source.id)",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY v")

    expected_rows = pd.DataFrame({"id": [None, 2, 4], "v": ["v1_1", "v2", "v4"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_null_condition(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithNullCondition.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": [None, 2], "v": ["v1", "v2"]},
        ),
        pd.DataFrame(
            {"id": [None, 2], "v": ["v1_1", "v2_2"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id AND NULL "
            "WHEN MATCHED THEN "
            "  UPDATE SET v = source.v "
            "WHEN NOT MATCHED THEN "
            "  INSERT (v, id) VALUES (source.v, source.id)"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY v")

    expected_rows = pd.DataFrame(
        {"id": [None, None, 2, 2], "v": ["v1", "v1_1", "v2", "v2_2"]}
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_null_action_conditions(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithNullActionConditions.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": [None, 2], "v": ["v1", "v2"]},
        ),
        pd.DataFrame(
            {"id": [1, 2, 3], "v": ["v1_1", "v2_2", "v3_3"]},
        ),
    )

    def impl1(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id "
            "WHEN MATCHED AND source.id = 1 AND NULL THEN "
            "  UPDATE SET v = source.v "
            "WHEN MATCHED AND source.v = 'v1_1' AND NULL THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND source.id = 3 AND NULL THEN "
            "  INSERT (v, id) VALUES (source.v, source.id)"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY v")

    def impl2(bc):

        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id "
            "WHEN MATCHED AND source.id = 1 AND NULL THEN "
            "  UPDATE SET v = source.v "
            "WHEN MATCHED AND source.v = 'v1_1' THEN "
            "  DELETE "
            "WHEN NOT MATCHED AND source.id = 3 AND NULL THEN "
            "  INSERT (v, id) VALUES (source.v, source.id)"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY v")

    expected_rows = pd.DataFrame({"id": [1, 2], "v": ["v1", "v2"]}), pd.DataFrame(
        {"id": [2], "v": ["v2"]}
    )
    check_func(
        impl1,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows[0],
        reset_index=True,
        sort_output=True,
    )
    check_func(
        impl2,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows[1],
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_multiple_matching_actions(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithMultipleMatchingActions.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": [1, 2], "v": ["v1", "v2"]},
        ),
        pd.DataFrame(
            {"id": [1, 2], "v": ["v1_1", "v2_2"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id "
            "WHEN MATCHED AND source.id = 1 AND NULL THEN "
            "  UPDATE SET v = source.v "
            "WHEN MATCHED AND source.v = 'v1_1' AND NULL THEN "
            "  DELETE "
            "WHEN NOT MATCHED THEN "
            "  INSERT (v, id) VALUES (source.v, source.id)"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY v")

    expected_rows = pd.DataFrame({"id": [1, 2], "v": ["v1", "v2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_insert_only(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeInsertOnly.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "string", False), ("v", "string", False)],
        pd.DataFrame(
            {"id": ["a", "b"], "v": ["v1", "v2"]},
        ),
        pd.DataFrame(
            {
                "id": ["a", "a", "c", "d", "d"],
                "v": ["v1_1", "v1_2", "v3", "v4_1", "v4_2"],
            },
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id "
            "WHEN NOT MATCHED THEN "
            "  INSERT *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame(
        {"id": ["a", "b", "c", "d", "d"], "v": ["v1", "v2", "v3", "v4_1", "v4_2"]}
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_insert_only_with_condition(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeInsertOnlyWithCondition.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("v", "int", False)],
        pd.DataFrame(
            {"id": [1], "v": [1]},
        ),
        pd.DataFrame(
            {"id": [1, 2, 2], "v": [11, 21, 22], "is_new": [True, True, False]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.id "
            "WHEN NOT MATCHED AND is_new = TRUE THEN "
            "  INSERT (v, id) VALUES (s.v + 100, s.id)",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame({"id": [1, 2], "v": [1, 121]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_aligns_update_and_insert_actions(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeAlignsUpdateAndInsertActions.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("a", "int", False), ("b", "string", False)],
        pd.DataFrame(
            {"id": [1], "a": [2], "b": ["str"]},
        ),
        pd.DataFrame(
            {"id": [1, 2], "c1": [-2, -20], "c2": ["new_str_1", "new_str_1"]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id "
            "WHEN MATCHED THEN "
            "  UPDATE SET b = c2, a = c1, t.id = source.id "
            "WHEN NOT MATCHED THEN "
            "  INSERT (b, a, id) VALUES (c2, c1, id)",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_rows = pd.DataFrame(
        {"id": [1, 2], "a": [-2, -20], "b": ["new_str_1", "new_str_2"]}
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_rows,
        reset_index=True,
        sort_output=True,
    )


def test_merge_mixed_case_aligns_update_and_insert_actions(
    table_name, memory_leak_check
):
    """
    Parity for Iceberg's testMergeMixedCaseAlignsUpdateAndInsertActions.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("a", "int", False), ("b", "int", False)],
        pd.DataFrame(
            {"id": [1], "a": [2], "b": ["str"]},
        ),
        pd.DataFrame(
            {"id": [1, 2], "c1": [-2, -20], "c2": ["new_str_1", "new_str_1"]},
        ),
    )

    def impl1(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.iD == source.Id "
            "WHEN MATCHED THEN "
            "  UPDATE SET B = c2, A = c1, t.Id = source.id "
            "WHEN NOT MATCHED THEN "
            "  INSERT (b, a, iD) VALUES (c2, c1, id)",
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    def impl2(bc):
        return bc.sql(f"SELECT * FROM {table_name} WHERE id = 1 ORDER BY id")

    def impl3(bc):
        return bc.sql(f"SELECT * FROM {table_name} WHERE b = 'new_str_2' ORDER BY id")

    expected_1 = pd.DataFrame(
        {"id": [1, 2], "a": [-2, -20], "b": ["new_str_1", "new_str_2"]}
    )
    expected_2 = pd.DataFrame({"id": [1], "a": [-2], "b": ["new_str_1"]})
    expected_3 = pd.DataFrame({"id": [2], "a": [-20], "b": ["new_str_2"]})

    check_func(
        impl1,
        (bc,),
        only_1DVar=True,
        py_output=expected_1,
        reset_index=True,
        sort_output=True,
    )
    check_func(
        impl2,
        (bc,),
        only_1DVar=True,
        py_output=expected_2,
        reset_index=True,
        sort_output=True,
    )
    check_func(
        impl3,
        (bc,),
        only_1DVar=True,
        py_output=expected_3,
        reset_index=True,
        sort_output=True,
    )


def test_merge_multiple_match_ordering(table_name, memory_leak_check):
    """
    Tests that rows perform the first matching condition
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("a", "int", False), ("b", "int", False)],
        pd.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "a": [2, 4, 6, 8, 10],
                "b": ["str1", "str2", "str3", "str4", "str5"],
            },
        ),
        pd.DataFrame(
            {
                "id": [1, 2, 3, 4, 5, -1, -2, -3, -4, -5],
                "c1": [-2, -4, -6, -8, -10, -2, -4, -6, -8, -10],
                "c2": [
                    "new_str_1",
                    "new_str_2",
                    "new_str_3",
                    "new_str_4",
                    "new_str_5",
                    "new_str_1",
                    "new_str_2",
                    "new_str_3",
                    "new_str_4",
                    "new_str_5",
                ],
            },
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s"
            "ON t.id == s.id "
            "WHEN MATCHED AND t.id > 3 THEN "
            "  UPDATE SET t.a = s.c1"
            "WHEN MATCHED AND t.id < 3 THEN "
            "  UPDATE SET t.a = s.c1 + 1"
            "WHEN MATCHED AND t.id = 4 THEN "
            "  UPDATE SET t.a = -1000"
            "WHEN MATCHED THEN "
            "  DELETE"
            "WHEN NOT MATCHED AND s.id > -3 THEN "
            "  INSERT (id, a, s) VALUES (id, c1, c2)"
            "WHEN NOT MATCHED AND s.id < -3 THEN "
            "  INSERT (id, a, s) VALUES (id, c1, 'I am < -3!')"
            "WHEN NOT MATCHED AND s.id = -3 THEN "
            "  INSERT (id, a, s) VALUES (id, c1, 'I am here!')"
            "WHEN NOT MATCHED THEN "
            "  INSERT (id, a, s) VALUES (id, c1, 'I should never reach here!')"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame(
        {
            "id": [
                1,
                2,
                4,
                5,
                -1,
                -2,
                -3,
                -4,
                -5,
            ],
            "a": [-1, -3, -5, -8, -10, -2, -4, -6, -8, -10],
            "b": [
                "str1",
                "str2",
                "str4",
                "str5",
                "new_str_1",
                "new_str_2",
                "I am here!",
                "I am < -3!",
                "I am < -3!",
            ],
        }
    ).sort_values(by="id")
    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_inferred_casts(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithInferredCasts.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("s", "string", False)],
        ('{ "id": 1, "s": "value"}'),
        pd.DataFrame({"id": [1], "s": ["value"]}),
        pd.DataFrame({"id": [1], "c1": [-2]}),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source "
            "ON t.id == source.id "
            "WHEN MATCHED THEN "
            "  UPDATE SET t.s = source.c1"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_1 = pd.DataFrame({"id": [1], "s": ["-2"]})

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_1,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_multiple_not_matched_actions(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeWithMultipleNotMatchedActions.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame({"id": [0], "dep": ["emp-id-0"]}),
        pd.DataFrame({"id": [1, 2, 3], "dep": ["emp-id-1", "emp-id-2", "emp-id-3"]}),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source AS s "
            "ON t.id == s.id "
            "WHEN NOT MATCHED AND s.id = 1 THEN "
            "  INSERT (dep, id) VALUES (s.dep, -1)"
            "WHEN NOT MATCHED THEN "
            "  INSERT *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_1 = pd.DataFrame(
        {"id": [-1, 0, 2, 3], "s": ["emp-id-1", "emp-id-0", "emp-id-2", "emp-id-3"]}
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_1,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_multiple_conditional_not_matched_actions(
    table_name, memory_leak_check
):
    """
    Parity for Iceberg's testMergeWithMultipleConditionalNotMatchedActions.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame({"id": [0], "dep": ["emp-id-0"]}),
        pd.DataFrame({"id": [1, 2, 3], "dep": ["emp-id-1", "emp-id-2", "emp-id-3"]}),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source AS s "
            "ON t.id == s.id "
            "WHEN NOT MATCHED AND s.id = 1 THEN "
            "  INSERT (dep, id) VALUES (s.dep, -1)"
            "WHEN NOT MATCHED AND s.id = 2 THEN "
            "  INSERT *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_1 = pd.DataFrame(
        {"id": [-1, 0, 2], "s": ["emp-id-1", "emp-id-0", "emp-id-2"]}
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_1,
        reset_index=True,
        sort_output=True,
    )


def test_merge_resolves_columns_by_name(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeResolvesColumnsByName.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("badge", "int", False), ("dep", "string", False)],
        pd.DataFrame(
            {"id": [1, 6], "badge": [1000, 6000], "dep": ["emp-id-0", "emp-id-6"]}
        ),
        pd.DataFrame(
            {
                "badge": [1001, 6006, 7007],
                "id": [1, 6, 7],
                "dep": ["emp-id-1", "emp-id-6", "emp-id-7"],
            }
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source as s"
            "ON t.id == s.id "
            "WHEN MATCHED THEN "
            "  UPDATE SET * "
            "WHEN NOT MATCHED THEN "
            "  INSERT * "
        )

        return bc.sql(f"SELECT id, badge, dep FROM {table_name} ORDER BY id")

    expected_1 = pd.DataFrame(
        {
            "id": [1, 6, 7],
            "badge": [1001, 6006, 7007],
            "s": ["emp-id-1", "emp-id-6", "emp-id-7"],
        }
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_1,
        reset_index=True,
        sort_output=True,
    )


def test_merge_should_resolve_when_there_are_no_unresolved_expressions_or_columns(
    table_name, memory_leak_check
):
    """
    Parity for Iceberg's testMergeShouldResolveWhenThereAreNoUnresolvedExpressionsOrColumns.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("dep", "string", False)],
        pd.DataFrame(),
        pd.DataFrame({"id": [1, 2, 3], "dep": ["emp-id-1", "emp-id-2", "emp-id-3"]}),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source AS s "
            "ON 1 != 1 "
            "WHEN MATCHED THEN "
            "  UPDATE SET * "
            "WHEN NOT MATCHED THEN "
            "  INSERT *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_1 = pd.DataFrame(
        {"id": [1, 2, 3], "s": ["emp-id-1", "emp-id-2", "emp-id-3"]}
    )

    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_1,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_non_existing_columns(
    table_name,
):
    """
    Partial parity for Iceberg's testMergeWithNonExistingColumns.
    (Does not check for issues with struct fields due to our lack of struct support)
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("c", "date", False)],
        pd.DataFrame(),
        pd.DataFrame({"c1": [1, 2, 3], "c2": [4, 5, 6]}),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN MATCHED THEN "
            "  UPDATE SET t.invalid_col = s.c2"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    with pytest.raises(BodoError, "TODO!"):
        out1 = impl(bc)


def test_merge_with_invalid_columns_in_insert(
    table_name,
):
    """
    Partial parity for Iceberg's testMergeWithInvalidColumnsInInsert.
    (Does not check for issues with struct fields due to our lack of struct support)
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("c", "date", False)],
        pd.DataFrame(),
        pd.DataFrame({"c1": [-100], "c2": [-200]}),
    )

    def init_bc():
        return bc

    def test_dup_insert_cols(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN NOT MATCHED THEN "
            "  INSERT (id, id) VALUES (s.c1, null)"
        )

    def test_must_provide_all_dest_cols(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN NOT MATCHED THEN " + "  INSERT (id) VALUES (s.c1)",
        )

    bc = init_bc()

    with pytest.raises(BodoError, "TODO!"):
        test_dup_insert_cols(bc)

    with pytest.raises(BodoError, "TODO!"):
        test_must_provide_all_dest_cols(bc)


def test_merge_with_conflicting_updates(
    table_name,
):
    """
    Partial parity for Iceberg's testMergeWithConflictingUpdates.
    (Does not check for issues with struct fields due to our lack of struct support)
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("c", "int", False)],
        pd.DataFrame(),
        pd.DataFrame(
            {
                "c1": [-100],
                "c2": [-200],
                "c3": ["hello world"],
            }
        ),
    )

    def impl(bc):
        return bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN MATCHED THEN "
            "  UPDATE SET t.id = 1, t.c = 2, t.id = 2",
        )

    with pytest.raises(BodoError, "TODO!"):
        impl(bc)


def test_merge_with_invalid_assignments(
    table_name,
):
    """
    Partial parity for Iceberg's testMergeWithInvalidAssignments.
    (Does not check for issues with struct fields due to our lack of struct support)
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("s", "int", False)],
        pd.DataFrame(
            {"c1": [1], "s": [2]},
        ),
        pd.DataFrame(
            {"c1": [1], "c2": [2]},
        ),
    )

    def test_set_null(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN MATCHED THEN "
            "  UPDATE SET t.id = NULL"
        )

    def test_set_wrong_typ(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN MATCHED THEN "
            "  UPDATE SET t.s = s.c2"
        )

    with pytest.raises(BodoError, "TODO!"):
        test_set_null(bc)
    with pytest.raises(BodoError, "TODO!"):
        test_set_wrong_typ(bc)


def gen_agg_subquery_bc():
    # We use the same source/dest for all of the agg/subquery tests,
    # It's in a helper fn to avoid code duplication
    return _create_and_init_table(
        table_name,
        [("id", "int", False), ("c", "int", False)],
        pd.DataFrame(
            {"id": [1], "c": [1]},
        ),
        pd.DataFrame(
            {"c1": [1, 2], "c2": [-1, -2]},
        ),
    )


@pytest.mark.skip("Not currently supported: https://bodo.atlassian.net/browse/BE-3716")
def test_merge_with_aggregate_expressions_in_join(table_name, memory_leak_check):
    """
    Partial parity for Iceberg's testMergeWithAggregateExpressions.
    (Changed due to our lack of struct support, and the possibility to support this in the future)

    We may support this in the future.
    """

    bc = gen_agg_subquery_bc()

    def test_agg_in_join(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 AND max(t.id) == 1 "
            "WHEN MATCHED THEN "
            "  UPDATE SET t.c = -1"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame({"id": [1], "c": [-1]})
    check_func(
        test_agg_in_join,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


@pytest.mark.skip("Not currently supported: https://bodo.atlassian.net/browse/BE-3716")
def test_merge_with_aggregate_expressions_in_update_cond(table_name, memory_leak_check):
    """
    Partial parity for Iceberg's testMergeWithAggregateExpressions.
    (Changed due to our lack of struct support, and the possibility to support this in the future)

    We may support this in the future.
    """

    bc = gen_agg_subquery_bc()

    def test_agg_in_update_cond(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN MATCHED AND sum(t.id) < 1 THEN "
            "  UPDATE SET t.c = -1"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame({"id": [1], "c": [1]})
    check_func(
        test_agg_in_update_cond,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


@pytest.mark.skip("Not currently supported: https://bodo.atlassian.net/browse/BE-3716")
def test_merge_with_aggregate_expressions_in_delete_cond(table_name, memory_leak_check):
    """
    Partial parity for Iceberg's testMergeWithAggregateExpressions.
    (Changed due to our lack of struct support, and the possibility to support this in the future)

    We may support this in the future.
    """
    bc = gen_agg_subquery_bc()

    def test_agg_in_delete_cond(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN MATCHED AND sum(t.id) = 1 THEN "
            "  DELETE"
        )
        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame({"id": [], "c": []})
    check_func(
        test_agg_in_delete_cond,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


@pytest.mark.skip("Not currently supported: https://bodo.atlassian.net/browse/BE-3716")
def test_merge_with_aggregate_expressions_in_update_cond(table_name, memory_leak_check):
    """
    Partial parity for Iceberg's testMergeWithAggregateExpressions.
    (Changed due to our lack of struct support, and the possibility to support this in the future)

    We may support this in the future.
    """
    bc = gen_agg_subquery_bc()

    def test_agg_in_update_cond(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN NOT MATCHED AND sum(c1) < 1 THEN "
            "  INSERT (id, c) VALUES (1, null)"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame({"id": [1], "c": [1]})
    check_func(
        test_agg_in_update_cond,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


@pytest.mark.skip("Not currently supported: https://bodo.atlassian.net/browse/BE-3716")
def test_merge_with_subqueries_in_join_condition(table_name, memory_leak_check):
    """
    Partial parity for Iceberg's testMergeWithSubqueriesInConditions.
    (Changed due to our lack of struct support, and the possibility to support this in the future)

    We may support this in the future.
    """
    bc = gen_agg_subquery_bc()

    def test_agg_in_join(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 AND t.id < (SELECT max(c2) FROM source)"
            "WHEN MATCHED THEN "
            "  UPDATE SET t.c = -1"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame({"id": [1], "c": [1]})
    check_func(
        test_agg_in_join,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


@pytest.mark.skip("Not currently supported: https://bodo.atlassian.net/browse/BE-3716")
def test_merge_with_subqueries_in_update_condition(table_name, memory_leak_check):
    """
    Partial parity for Iceberg's testMergeWithSubqueriesInConditions.
    (Changed due to our lack of struct support, and the possibility to support this in the future)

    We may support this in the future.
    """
    bc = gen_agg_subquery_bc()

    def test_agg_in_update_cond(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN MATCHED AND t.id > (SELECT max(c2) FROM source) THEN "
            "  UPDATE SET t.c = -1"
        )
        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame({"id": [1], "c": [-1]})
    check_func(
        test_agg_in_update_cond,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


@pytest.mark.skip("Not currently supported: https://bodo.atlassian.net/browse/BE-3716")
def test_merge_with_subqueries_in_delete_condition(table_name, memory_leak_check):
    """
    Partial parity for Iceberg's testMergeWithSubqueriesInConditions.
    (Changed due to our lack of struct support, and the possibility to support this in the future)

    We may support this in the future.
    """
    bc = gen_agg_subquery_bc()

    def test_agg_in_delete_cond(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN MATCHED AND t.id < (SELECT max(c2) FROM source) THEN "
            "  DELETE"
        )
        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame({"id": [1], "c": [1]})
    check_func(
        test_agg_in_delete_cond,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


@pytest.mark.skip("Not currently supported: https://bodo.atlassian.net/browse/BE-3716")
def test_merge_with_subqueries_in_insert_condition(table_name, memory_leak_check):
    """
    Partial parity for Iceberg's testMergeWithSubqueriesInConditions.
    (Changed due to our lack of struct support, and the possibility to support this in the future)

    We may support this in the future.
    """
    bc = gen_agg_subquery_bc()

    def test_agg_in_insert_cond(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.c1 "
            "WHEN NOT MATCHED AND t.id NOT IN (SELECT c2 FROM source) THEN "
            "  INSERT (id, c) VALUES (1, null)"
        )
        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected = pd.DataFrame({"id": [1], "c": [1]})
    check_func(
        test_agg_in_insert_cond,
        (bc,),
        only_1DVar=True,
        py_output=expected,
        reset_index=True,
        sort_output=True,
    )


def test_merge_with_target_columns_in_insert_conditions(table_name):
    """
    Parity for Iceberg's testMergeWithTargetColumnsInInsertConditions.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False), ("c2", "int", False)],
        pd.DataFrame(),
        pd.DataFrame({"id": [1], "dep": [11]}),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s "
            "ON t.id == s.id "
            "WHEN NOT MATCHED AND c2 = 1 THEN "
            "  INSERT (id, c2) VALUES (s.id, null)"
        )

    with pytest.raises(BodoError, "TODO"):
        impl(bc)


def test_merge_empty_table(table_name, memory_leak_check):
    """
    Parity for Iceberg's testMergeEmptyTable.
    """

    bc = _create_and_init_table(
        table_name,
        [("id", "int", False)],
        pd.DataFrame(),
        pd.DataFrame(
            {"id": [0, 1, 2, 3, 4]},
        ),
    )

    def impl(bc):
        bc.sql(
            f"MERGE INTO {table_name} t USING source s ON t.id = s.id "
            "WHEN MATCHED THEN UPDATE SET *"
            "WHEN NOT MATCHED THEN INSERT *"
        )

        return bc.sql(f"SELECT * FROM {table_name} ORDER BY id")

    expected_1 = pd.DataFrame({"id": [0, 1, 2, 3, 4]})
    check_func(
        impl,
        (bc,),
        only_1DVar=True,
        py_output=expected_1,
        reset_index=True,
        sort_output=True,
    )