# Copyright (C) 2022 Bodo Inc. All rights reserved.
"""
Tests various components of the SnowflakeCatalog type both inside and outside a
direct BodoSQLContext.
"""

import os
import time

import bodosql
import numpy as np
import pandas as pd
import pytest
from mpi4py import MPI

import bodo
from bodo.tests.utils import (
    check_func,
    create_snowflake_table,
    get_snowflake_connection_string,
)
from bodo.utils.typing import BodoError


@pytest.fixture(
    params=[
        bodosql.SnowflakeCatalog(
            "myusername",
            "mypassword",
            "myaccount",
            "mywarehouse",
            "mydatabase",
        ),
        bodosql.SnowflakeCatalog(
            "myusername",
            "mypassword",
            "myaccount",
            "mywarehouse",
            "mydatabase",
            {"role": "USERADMIN"},
        ),
    ]
)
def dummy_snowflake_catalogs(request):
    """
    List of table paths that should be suppported.
    None of these actually point to valid data
    """
    return request.param


def test_snowflake_catalog_lower_constant(dummy_snowflake_catalogs, memory_leak_check):
    """
    Test lowering a constant snowflake catalog.
    """

    def impl():
        return dummy_snowflake_catalogs

    check_func(impl, ())


def test_snowflake_catalog_boxing(dummy_snowflake_catalogs, memory_leak_check):
    """
    Test boxing and unboxing a table path type.
    """

    def impl(snowflake_catalog):
        return snowflake_catalog

    check_func(impl, (dummy_snowflake_catalogs,))


def test_snowflake_catalog_constructor(memory_leak_check):
    """
    Test using the table path constructor from JIT.
    """

    def impl1():
        return bodosql.SnowflakeCatalog(
            "myusername",
            "mypassword",
            "myaccount",
            "mywarehouse",
            "mydatabase",
        )

    def impl2():
        return bodosql.SnowflakeCatalog(
            "myusername",
            "mypassword",
            "myaccount",
            "mywarehouse",
            "mydatabase",
            # Explicitly pass None
            None,
        )

    def impl3():
        return bodosql.SnowflakeCatalog(
            "myusername",
            "mypassword",
            "myaccount",
            "mywarehouse",
            "mydatabase",
            {"role": "USERADMIN"},
        )

    def impl4():
        return bodosql.SnowflakeCatalog(
            "myusername",
            "mypassword",
            "myaccount",
            "mywarehouse",
            "mydatabase",
            # Try passing an empty dictionary
            {},
        )

    check_func(impl1, ())
    check_func(impl2, ())
    check_func(impl3, ())
    # Note: Empty dictionary passed via args or literal map not supported yet.
    # [BE-3455]
    # check_func(impl4, ())


@pytest.mark.skipif(
    "AGENT_NAME" not in os.environ,
    reason="requires Azure Pipelines",
)
def test_snowflake_catalog_read(memory_leak_check):
    def impl(bc):
        return bc.sql("SELECT r_name FROM TPCH_SF1.REGION ORDER BY r_name")

    bodo_connect_params = {"query_tag": "folder=folder1+ folder2&"}
    catalog = bodosql.SnowflakeCatalog(
        os.environ["SF_USER"],
        os.environ["SF_PASSWORD"],
        "bodopartner.us-east-1",
        "DEMO_WH",
        "SNOWFLAKE_SAMPLE_DATA",
        connection_params=bodo_connect_params,
    )
    bc = bodosql.BodoSQLContext(catalog=catalog)
    py_output = pd.read_sql(
        "Select r_name from REGION ORDER BY r_name",
        get_snowflake_connection_string("SNOWFLAKE_SAMPLE_DATA", "TPCH_SF1"),
    )
    check_func(impl, (bc,), py_output=py_output)


@pytest.mark.skipif(
    "AGENT_NAME" not in os.environ,
    reason="requires Azure Pipelines",
)
def test_snowflake_catalog_default(memory_leak_check):
    """
    Tests passing a default schema to SnowflakeCatalog.
    """

    def impl1(bc):
        # Load with snowflake or local default
        return bc.sql("SELECT r_name FROM REGION ORDER BY r_name")

    def impl2(bc):
        return bc.sql("SELECT r_name FROM LOCAL_REGION ORDER BY r_name")

    def impl3(bc):
        return bc.sql("SELECT r_name FROM __bodolocal__.REGION ORDER BY r_name")

    bodo_connect_params = {"schema": "TPCH_SF1"}
    catalog = bodosql.SnowflakeCatalog(
        os.environ["SF_USER"],
        os.environ["SF_PASSWORD"],
        "bodopartner.us-east-1",
        "DEMO_WH",
        "SNOWFLAKE_SAMPLE_DATA",
        connection_params=bodo_connect_params,
    )
    bc = bodosql.BodoSQLContext(catalog=catalog)
    py_output = pd.read_sql(
        "Select r_name from REGION ORDER BY r_name",
        get_snowflake_connection_string("SNOWFLAKE_SAMPLE_DATA", "TPCH_SF1"),
    )
    check_func(impl1, (bc,), py_output=py_output)

    # We use a different type for the local table to clearly tell if we
    # got the correct table.
    local_table = pd.DataFrame({"r_name": np.arange(100)})
    bc = bc.add_or_replace_view("LOCAL_REGION", local_table)
    # We should select the local table
    check_func(impl2, (bc,), py_output=local_table, reset_index=True)
    bc = bc.add_or_replace_view("REGION", local_table)
    # We two default conflicts we should get the snowflake option
    check_func(impl1, (bc,), py_output=py_output)
    # If we manually specify "__bodolocal__" we should get the local one.
    check_func(impl3, (bc,), py_output=local_table, reset_index=True)


@pytest.mark.skipif(
    "AGENT_NAME" not in os.environ,
    reason="requires Azure Pipelines",
)
def test_snowflake_catalog_read_tpch(memory_leak_check):
    tpch_query = """select
                      n_name,
                      sum(l_extendedprice * (1 - l_discount)) as revenue
                    from
                      tpch_sf1.customer,
                      tpch_sf1.orders,
                      tpch_sf1.lineitem,
                      tpch_sf1.supplier,
                      tpch_sf1.nation,
                      tpch_sf1.region
                    where
                      c_custkey = o_custkey
                      and l_orderkey = o_orderkey
                      and l_suppkey = s_suppkey
                      and c_nationkey = s_nationkey
                      and s_nationkey = n_nationkey
                      and n_regionkey = r_regionkey
                      and r_name = 'ASIA'
                      and o_orderdate >= '1994-01-01'
                      and o_orderdate < '1995-01-01'
                    group by
                      n_name
                    order by
                      revenue desc
    """

    def impl(bc):
        return bc.sql(tpch_query)

    catalog = bodosql.SnowflakeCatalog(
        os.environ["SF_USER"],
        os.environ["SF_PASSWORD"],
        "bodopartner.us-east-1",
        "DEMO_WH",
        "SNOWFLAKE_SAMPLE_DATA",
    )
    bc = bodosql.BodoSQLContext(catalog=catalog)
    py_output = pd.read_sql(
        tpch_query,
        get_snowflake_connection_string("SNOWFLAKE_SAMPLE_DATA", "TPCH_SF1"),
    )

    check_func(impl, (bc,), py_output=py_output, reset_index=True)


@pytest.mark.skipif(
    "AGENT_NAME" not in os.environ,
    reason="requires Azure Pipelines",
)
def test_delete_simple(memory_leak_check):
    """
    Tests a simple delete clause inside of Snowflake.
    """
    comm = MPI.COMM_WORLD

    schema = "PUBLIC"
    db = "TEST_DB"
    new_df = pd.DataFrame(
        {
            "A": [1, 2, 3] * 10,
            "B": np.arange(30),
        }
    )

    def impl1(query, bc):
        df = bc.sql(query)
        # Use the column name to confirm we use a standard name.
        return df["ROWCOUNT"].iloc[0]

    def impl2(query, bc):
        # Verify the delete is still performed even if the output
        # is unused
        bc.sql(query)
        return 10

    with create_snowflake_table(
        new_df, "bodosql_delete_test", db, schema
    ) as table_name:
        catalog = bodosql.SnowflakeCatalog(
            os.environ["SF_USER"],
            os.environ["SF_PASSWORD"],
            "bodopartner.us-east-1",
            "DEMO_WH",
            db,
        )
        bc = bodosql.BodoSQLContext(catalog=catalog)
        num_rows_to_delete = 10

        query1 = f"DELETE FROM {schema}.{table_name} WHERE A = 1"

        query2 = f"DELETE FROM {schema}.{table_name} WHERE A = 2"

        # We run only 1 distribution because DELETE has side effects
        check_func(impl1, (query1, bc), only_1D=True, py_output=num_rows_to_delete)
        check_func(impl2, (query2, bc), only_1D=True, py_output=num_rows_to_delete)
        output_df = None
        # Load the table on rank 0 to verify the drop. We wait a few
        # seconds in case there could be a delay
        time.sleep(3)
        if bodo.get_rank() == 0:
            conn_str = get_snowflake_connection_string(db, schema)
            output_df = pd.read_sql(f"select * from {table_name}", conn_str)
            # Convert output to match the input.
            output_df.columns = [colname.upper() for colname in output_df.columns]
        output_df = comm.bcast(output_df)
        result_df = new_df[new_df.A == 3]
        # Data doesn't have a defined ordering so sort.
        output_df.sort_values(by=["A", "B"])
        result_df.sort_values(by=["A", "B"])
        # Drop the index
        output_df = output_df.reset_index(drop=True)
        result_df = result_df.reset_index(drop=True)
        pd.testing.assert_frame_equal(output_df, result_df)


def test_delete_named_param():
    """
    Tests submitting a delete query with a named param. Since we just
    push the query into Snowflake without any changes, this query
    should just raise a reasonable error.
    """
    comm = MPI.COMM_WORLD

    schema = "PUBLIC"
    db = "TEST_DB"
    new_df = pd.DataFrame(
        {
            "A": [1, 2, 3] * 10,
            "B": np.arange(30),
        }
    )

    @bodo.jit
    def impl(query, bc, pyvar):
        return bc.sql(query, {"pyvar": pyvar})

    with create_snowflake_table(
        new_df, "bodosql_dont_delete_test_param", db, schema
    ) as table_name:
        catalog = bodosql.SnowflakeCatalog(
            os.environ["SF_USER"],
            os.environ["SF_PASSWORD"],
            "bodopartner.us-east-1",
            "DEMO_WH",
            db,
        )
        bc = bodosql.BodoSQLContext(catalog=catalog)
        # Snowflake cannot support out named_params
        query = f"DELETE FROM {schema}.{table_name} WHERE A = @pyvar"
        with pytest.raises(
            BodoError,
            match="Please verify that all of your Delete query syntax is supported inside of Snowflake",
        ):
            impl(query, bc, 1)


def test_delete_bodosql_syntax():
    """
    Tests submitting a delete query with SQL that does not match
    Snowflake syntax. Since we just push the query into Snowflake
    without any changes, this query should just raise a reasonable error.
    """
    comm = MPI.COMM_WORLD

    schema = "PUBLIC"
    db = "TEST_DB"
    new_df = pd.DataFrame(
        {
            "A": [1, 2, 3] * 10,
            "B": np.arange(30),
        }
    )

    @bodo.jit
    def impl(query, bc):
        return bc.sql(query)

    with create_snowflake_table(
        new_df, "bodosql_dont_delete_test", db, schema
    ) as table_name:
        catalog = bodosql.SnowflakeCatalog(
            os.environ["SF_USER"],
            os.environ["SF_PASSWORD"],
            "bodopartner.us-east-1",
            "DEMO_WH",
            db,
        )
        bc = bodosql.BodoSQLContext(catalog=catalog)
        # Snowflake doesn't have a CEILING function
        query = f"DELETE FROM {schema}.{table_name} WHERE A = CEILING(1.5)"
        with pytest.raises(
            BodoError,
            match="Please verify that all of your Delete query syntax is supported inside of Snowflake",
        ):
            impl(query, bc)