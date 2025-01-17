package com.bodosql.calcite.schema;

import com.bodosql.calcite.table.BodoSqlTable;
import com.google.common.collect.ImmutableList;
import java.util.HashMap;
import java.util.Set;
import org.apache.calcite.schema.Table;

public class LocalSchema extends BodoSqlSchema {
  /**
   * Definition of a Schema that is used for local tables that are not part of any catalog. These
   * include in memory DataFrames and the table path API.
   *
   * <p>See the design described on Confluence:
   * https://bodo.atlassian.net/wiki/spaces/BodoSQL/pages/1130299393/Java+Table+and+Schema+Typing#Schema
   */
  private HashMap<String, Table> tables;

  /**
   * Full constructor to allow specifying depth. This is used for constructing a root schema.
   *
   * @param name Name of the schema.
   * @param depth The depth to the root schema.
   */
  public LocalSchema(String name, int depth) {
    super(name, depth, ImmutableList.of());
    this.tables = new HashMap<>();
  }

  /**
   * Default constructor for actual
   *
   * @param name
   */
  public LocalSchema(String name) {
    this(name, 1);
  }

  /**
   * Returns a table with a given name. This table must be registered in the schemas tables using
   * addTable.
   *
   * @param name Name of the table.
   * @return A table object.
   */
  @Override
  public Table getTable(String name) {
    if (tables.containsKey(name)) {
      return tables.get(name);
    } else {
      throw new RuntimeException(
          String.format("Table %s not found in Schema %s.", name, this.getName()));
    }
  }

  /**
   * Adds a table to the given schema.
   *
   * @param table Table to add.
   */
  public void addTable(BodoSqlTable table) {
    tables.put(table.getName(), table);
  }

  /**
   * Remove a table from the local schema. Note this is a NO-OP if there is no table registered with
   * the given name.
   *
   * @param tableName Name of the table to remove.
   */
  public void removeTable(String tableName) {
    if (tables.containsKey(tableName)) {
      tables.remove(tableName);
    }
  }

  /**
   * Returns all table names registered in this schema. Each table must be registered using
   * addTable.
   *
   * @return Set of all registered table names.
   */
  @Override
  public Set<String> getTableNames() {
    return this.tables.keySet();
  }
}
