# Copyright (C) 2024 Bodo Inc. All rights reserved.
"""
Python classes/functionality exposed for writing puffin files via
the Bodo Iceberg connector.
"""

from dataclasses import dataclass

import pandas as pd

from bodo_iceberg_connector.catalog_conn import (
    normalize_loc,
    parse_conn_str,
)
from bodo_iceberg_connector.py4j_support import (
    get_java_table_handler,
)


@dataclass
class BlobMetadata:
    """
    Python equivalent of BlobMetadata in Java. This is used for passing
    information to the Java connector via JSON.
    """

    type: str
    sourceSnapshotId: int
    sourceSnapshotSequenceNumber: int
    fields: list[int]
    properties: dict[str, str]


@dataclass
class StatisticsFile:
    """
    Python equivalent of the StatisticsFile interface in Java.
    This is used for passing information to the Java connector via JSON.
    """

    snapshotId: int
    path: str
    fileSizeInBytes: int
    fileFooterSizeInBytes: int
    blobMetadata: list[BlobMetadata]

    @staticmethod
    def empty():
        return StatisticsFile(-1, "", -1, -1, [])


def table_columns_have_theta_sketches(
    conn_str: str, db_name: str, table_name: str
) -> pd.array:
    """
    Determine which columns in a given table have theta sketches. The returned
    result is a boolean array where the ith element is True if the ith column
    has a theta sketch, and False otherwise. The indices are based on current
    column locations in the table schema, not field id.

    Args:
        conn_str (str): The iceberg connection string.
        db_name (str): The table's database name.
        table_name (str): The table name.

    Returns:
        pd.array[bool]: A boolean array where the ith element is True if the ith
        column has a theta sketch, and False otherwise.
    """
    catalog_type, _ = parse_conn_str(conn_str)
    handler = get_java_table_handler(conn_str, catalog_type, db_name, table_name)
    hasSketches = handler.tableColumnsHaveThetaSketches()
    return pd.array(hasSketches, dtype="boolean")


def get_old_statistics_file_path(
    txn_id: int, conn_str: str, db_name: str, table_name: str
):
    """
    Get the old puffin file path from the connector. We know that the puffin file
    must exist because of previous checks.
    """
    catalog_type, _ = parse_conn_str(conn_str)
    handler = get_java_table_handler(conn_str, catalog_type, db_name, table_name)
    return normalize_loc(handler.getStatisticsFilePath(txn_id))