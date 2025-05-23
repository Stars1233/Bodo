from bodo.tests.iceberg_database_helpers import (
    bank_accounts_table,
    english_dictionary_table,
    file_subset_deleted_rows_table,
    file_subset_empty_files_table,
    file_subset_partial_file_table,
    filter_pushdown_test_table,
    large_delete_table,
    mock_holiday_table,
    mock_news_table,
    part_sort_table,
    partitions_dropped_dt_table,
    partitions_dt_table,
    partitions_general_table,
    schema_evolution_eg_table,
    shakespeare_table,
    suspicious_sums_table,
)
from bodo.tests.iceberg_database_helpers.partition_schema_evolution_tables import (
    PARTITION_SCHEMA_EVOLUTION_TABLE_NAME_MAP,
    create_partition_schema_evolution_tables,
)
from bodo.tests.iceberg_database_helpers.partition_tables import (
    PARTITION_TABLE_NAME_MAP,
    create_partition_tables,
)
from bodo.tests.iceberg_database_helpers.schema_evolution_tables import (
    SCHEMA_EVOLUTION_TABLE_NAME_MAP,
    create_schema_evolution_tables,
)
from bodo.tests.iceberg_database_helpers.simple_tables import (
    TABLE_MAP as SIMPLE_TABLE_NAME_MAP,
)
from bodo.tests.iceberg_database_helpers.simple_tables import (
    create_simple_tables,
)
from bodo.tests.iceberg_database_helpers.sort_tables import (
    SORT_TABLE_NAME_MAP,
    create_sort_tables,
)
from bodo.tests.iceberg_database_helpers.utils import DATABASE_NAME, get_spark

table_mods = [
    english_dictionary_table,
    shakespeare_table,
    mock_news_table,
    mock_holiday_table,
    bank_accounts_table,
    suspicious_sums_table,
    filter_pushdown_test_table,
    partitions_dt_table,
    part_sort_table,
    schema_evolution_eg_table,
    # These are not used in any of the tests at this time.
    # These should be added back when they are.
    file_subset_deleted_rows_table,
    file_subset_empty_files_table,
    file_subset_partial_file_table,
    large_delete_table,
    partitions_dropped_dt_table,
    partitions_general_table,
]


def create_tables(tables, spark=None):
    if spark is None:
        spark = get_spark()

    create_simple_tables(tables, spark)
    create_partition_tables(tables, spark)
    create_sort_tables(tables, spark)
    create_schema_evolution_tables(tables, spark)
    create_partition_schema_evolution_tables(tables, spark)

    for table_mod in table_mods:
        if table_mod.TABLE_NAME in tables:
            table_mod.create_table(spark=spark)

    return DATABASE_NAME


if __name__ == "__main__":
    create_tables(
        list(SIMPLE_TABLE_NAME_MAP.keys())
        + list(PARTITION_TABLE_NAME_MAP.keys())
        + list(SORT_TABLE_NAME_MAP.keys())
        + list(SCHEMA_EVOLUTION_TABLE_NAME_MAP.keys())
        + list(PARTITION_SCHEMA_EVOLUTION_TABLE_NAME_MAP.keys())
        + [table_mod.TABLE_NAME for table_mod in table_mods]
    )
