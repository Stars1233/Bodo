package com.bodosql.calcite.table;

import com.bodosql.calcite.adapter.pandas.StreamingOptions;
import com.bodosql.calcite.catalog.BodoSQLCatalog;
import com.bodosql.calcite.catalog.SnowflakeCatalogImpl;
import com.bodosql.calcite.ir.Expr;
import com.bodosql.calcite.ir.Variable;
import com.google.common.base.Suppliers;
import com.google.common.collect.ImmutableList;
import java.util.*;
import java.util.function.Supplier;
import javax.annotation.Nullable;
import org.apache.calcite.plan.RelOptTable;
import org.apache.calcite.rel.RelNode;
import org.apache.calcite.rel.type.RelDataType;
import org.apache.calcite.rel.type.RelDataTypeField;
import org.apache.calcite.schema.Statistic;
import org.apache.calcite.schema.Table;
import org.apache.calcite.schema.TranslatableTable;
import org.apache.calcite.sql.type.BodoTZInfo;
import org.apache.calcite.sql.type.TZAwareSqlType;
import org.jetbrains.annotations.NotNull;

/**
 *
 *
 * <h1>Stores a table with its corresponding columns</h1>
 *
 * @author bodo
 */
public class CatalogTable extends BodoSqlTable implements TranslatableTable {
  // Hold the statistics for this table.
  private final Statistic statistic = new StatisticImpl();
  // The catalog that holds this table's origin.
  @NotNull private final BodoSQLCatalog catalog;

  /**
   * See the design described on Confluence:
   * https://bodo.atlassian.net/wiki/spaces/BodoSQL/pages/1130299393/Java+Table+and+Schema+Typing#Table
   */

  /**
   * This constructor is used to fill in all the values for the table.
   *
   * @param name the name of the table that is being created
   * @param schemaPath A list of schemas names that must be traversed from the root to reach this
   *     table.
   * @param columns list of columns to be added to the table.
   * @param catalog The catalog used to submit remote requests.
   */
  public CatalogTable(
      @NotNull String name,
      @NotNull ImmutableList<String> schemaPath,
      @NotNull List<BodoSQLColumn> columns,
      @NotNull BodoSQLCatalog catalog) {
    super(name, schemaPath, columns);
    this.catalog = catalog;
  }

  /**
   * Return the fully qualified name. This should be of the form
   * "DATABASE_NAME"."SCHEMA_NAME"."TABLE_NAME"
   *
   * @return
   */
  public String getQualifiedName() {
    ImmutableList.Builder<String> quotedPath = new ImmutableList.Builder<>();
    for (String elem : getFullPath()) {
      quotedPath.add(String.format(Locale.ROOT, "\"%s\"", elem));
    }

    return String.join(".", quotedPath.build());
  }

  /** Interface to get the catalog for creating RelNodes. */
  public BodoSQLCatalog getCatalog() {
    return catalog;
  }

  /**
   * Can BodoSQL write to this table. By default this is true but in the future this may be extended
   * to look at the permissions given in the catalog.
   *
   * @return Can BodoSQL write to this table.
   */
  @Override
  public boolean isWriteable() {
    // TODO: Update with the ability to check permissions from the schema/catalog
    return true;
  }

  /**
   * This is used to facilitate the indirection required for getting the correct casing.
   *
   * <p>Calcite needs to pretend that the case is lowercase for the purposes of expanding the star
   * for selects and also to fit in with the pandas convention.
   *
   * <p>At the same time, Calcite needs to know the original name of the columns for SQL generation.
   *
   * <p>Until we have conventions in place and have overridden the default behavior of star (which
   * uses the real names instead of normalized lowercase names), we need to have this little hack.
   *
   * @param name column index.
   * @return the column name.
   */
  public String getPreservedColumnName(String name) {
    for (BodoSQLColumn column : columns) {
      if (column.getColumnName().equals(name)) {
        // We found the original column so return
        // the write name as that's the original.
        return column.getWriteColumnName();
      }
    }
    // Just return the original name.
    return name;
  }

  /**
   * Generate the code needed to write the given variable to storage. This table type generates code
   * common to all tables in the catalog.
   *
   * @param varName Name of the variable to write.
   * @return The generated code to write the table.
   */
  @Override
  public Expr generateWriteCode(Variable varName) {
    return catalog.generateAppendWriteCode(varName, getFullPath());
  }

  /**
   * Generate the code needed to write the given variable to storage.
   *
   * @param varName Name of the variable to write.
   * @param extraArgs Extra arguments to pass to the Python API. They are assume to be escaped by
   *     the calling function and are of the form "key1=value1, ..., keyN=valueN".
   * @return The generated code to write the table.
   */
  public Variable generateWriteCode(Variable varName, String extraArgs) {
    throw new UnsupportedOperationException("Catalog APIs do not support additional arguments");
  }

  /**
   * Generate the streaming code needed to initialize a writer for the given variable.
   *
   * @return The generated streaming code to write the table.
   */
  public Expr generateStreamingWriteInitCode(Expr.IntegerLiteral operatorID) {
    return catalog.generateStreamingAppendWriteInitCode(operatorID, getFullPath());
  }

  public Expr generateStreamingWriteAppendCode(
      Variable stateVarName,
      Variable dfVarName,
      Variable colNamesGlobal,
      Variable isLastVarName,
      Variable iterVarName,
      Expr columnPrecisions) {
    return catalog.generateStreamingWriteAppendCode(
        stateVarName, dfVarName, colNamesGlobal, isLastVarName, iterVarName, columnPrecisions);
  }

  /**
   * Return the location from which the table is generated. The return value is always entirely
   * capitalized.
   *
   * @return The source DB location.
   */
  @Override
  public String getDBType() {
    return catalog.getDBType().toUpperCase();
  }

  /**
   * Generate the code needed to read the table. This table type generates code common to all tables
   * in the catalog.
   *
   * @param useStreaming Should we generate code to read the table as streaming (currently only
   *     supported for snowflake tables)
   * @param streamingOptions Streaming-related options including batch size
   * @return The generated code to read the table.
   */
  @Override
  public Expr generateReadCode(boolean useStreaming, StreamingOptions streamingOptions) {
    return catalog.generateReadCode(getFullPath(), useStreaming, streamingOptions);
  }

  /**
   * Generate the code needed to read the table. This function is called by specialized IO
   * implementations that require passing 1 or more additional arguments.
   *
   * @param extraArgs: Extra arguments to pass to the Python API. They are assume to be escaped by
   *     the calling function and are of the form "key1=value1, ..., keyN=valueN".
   * @return The generated code to read the table.
   */
  @Override
  public Expr generateReadCode(String extraArgs) {
    throw new UnsupportedOperationException("Catalog APIs do not support additional arguments");
  }

  @Override
  public Expr generateReadCastCode(Variable varName) {
    // Snowflake catalog uses _bodo_read_date_as_dt64=True to convert date columns to datetime64
    // without astype() calls in the IR which cause issues for limit pushdown.
    // see BE-4238
    return varName;
  }

  /**
   * Generates the code necessary to submit the remote query to the catalog DB. This is not
   * supported for local tables.
   *
   * @param query Query to submit.
   * @return The generated code.
   */
  @Override
  public Expr generateRemoteQuery(String query) {
    return catalog.generateRemoteQuery(query);
  }

  @Override
  public Table extend(List<RelDataTypeField> extensionFields) {
    String name = this.getName();
    List<BodoSQLColumn> extendedColumns = new ArrayList<>();
    extendedColumns.addAll(this.columns);
    for (int i = 0; i < extensionFields.size(); i++) {
      RelDataTypeField curField = extensionFields.get(0);
      String fieldName = curField.getName();
      RelDataType colType = curField.getType();
      BodoSQLColumn.BodoSQLColumnDataType newColType =
          BodoSQLColumn.BodoSQLColumnDataType.fromSqlType(colType);
      // getTZInfo() returns null if the type is not TZAware Timestamp
      BodoTZInfo tzInfo = TZAwareSqlType.getTZInfo(colType);
      BodoSQLColumn newCol = new BodoSQLColumnImpl(fieldName, newColType, false, tzInfo);
      extendedColumns.add(newCol);
    }
    return new CatalogTable(name, getParentFullPath(), extendedColumns, this.catalog);
  }

  /**
   * Returns if calling `generateReadCode()` for a table will result in an IO operation in the Bodo
   * generated code.
   *
   * @return Does the table require IO?
   */
  @Override
  public boolean readRequiresIO() {
    return true;
  }

  @Override
  public Statistic getStatistic() {
    return statistic;
  }

  @Override
  public RelNode toRel(RelOptTable.ToRelContext toRelContext, RelOptTable relOptTable) {
    throw new UnsupportedOperationException(
        "toRel() must be implemented by specific catalog table implementations");
  }

  private class StatisticImpl implements Statistic {
    private final Supplier<Double> rowCount = Suppliers.memoize(this::estimateRowCount);

    /**
     * Retrieves the estimated row count for this table. This value is memoized.
     *
     * @return estimated row count for this table.
     */
    @Override
    public @Nullable Double getRowCount() {
      return rowCount.get();
    }

    /**
     * Retrieves the estimated row count for this table. It performs a query every time this is
     * invoked.
     *
     * @return estimated row count for this table.
     */
    private @Nullable Double estimateRowCount() {
      SnowflakeCatalogImpl catalog = (SnowflakeCatalogImpl) CatalogTable.this.catalog;
      return catalog.estimateRowCount(getFullPath());
    }
  }
}