package com.bodosql.calcite.application.utils;

import static com.bodosql.calcite.application.utils.IsScalar.isScalar;

import com.bodosql.calcite.application.BodoSQLCodegenException;
import com.bodosql.calcite.application.PandasCodeGenVisitor;
import com.bodosql.calcite.catalog.SnowflakeCatalogImpl;
import com.bodosql.calcite.ir.Expr;
import com.bodosql.calcite.ir.Expr.IntegerLiteral;
import com.bodosql.calcite.ir.Expr.None;
import com.bodosql.calcite.ir.Expr.StringLiteral;
import com.bodosql.calcite.ir.Expr.Tuple;
import com.bodosql.calcite.ir.Module;
import com.bodosql.calcite.ir.Op;
import com.bodosql.calcite.ir.Variable;
import com.bodosql.calcite.schema.BodoSqlSchema;
import com.bodosql.calcite.schema.CatalogSchemaImpl;
import com.bodosql.calcite.table.BodoSqlTable;
import com.bodosql.calcite.table.CatalogTableImpl;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.apache.calcite.rel.core.AggregateCall;
import org.apache.calcite.rex.RexCall;
import org.apache.calcite.rex.RexLiteral;
import org.apache.calcite.rex.RexNode;
import org.apache.calcite.rex.RexOver;
import org.apache.calcite.sql.SqlKind;
import org.apache.calcite.sql.type.SqlTypeName;

/** Class filled with static utility functions. */
public class Utils {

  // Name of Dummy Colnames for Bodo Intermediate operations
  private static final String dummyColNameBase = "__bodo_dummy__";

  // two space indent
  private static final String bodoIndent = "  ";

  /** Function used to return the standard indent used within BodoSql */
  public static String getBodoIndent() {
    return bodoIndent;
  }

  /** Function used to add multiple indents to a string buffer all at once */
  public static void addIndent(StringBuilder funcText, int numIndents) {
    for (int i = 0; i < numIndents; i++) {
      funcText = funcText.append(bodoIndent);
    }
  }

  /**
   * Function to return the baseDummyColumnName. This should be extended with a counter if an
   * operation requires multiple dummy columns. NOTE: We assume dummy columns do not persist between
   * operations.
   *
   * @return dummyColNameBase
   */
  public static String getDummyColNameBase() {
    return dummyColNameBase;
  }

  /**
   * Function to enclose string in quotes
   *
   * @param unquotedString string to be enclosed
   * @return single quoted string
   */
  public static String makeQuoted(String unquotedString) {
    if (unquotedString.length() > 1
        && unquotedString.charAt(0) == '"'
        && unquotedString.charAt(unquotedString.length() - 1) == '"') {
      return unquotedString;
    }
    return '"' + unquotedString + '"';
  }

  /**
   * Function to convert a Java Hashmap of names into a Python dictionary for use in a
   * DataFrame.rename(columns) calls.
   */
  public static String renameColumns(HashMap<String, String> colMap) {
    StringBuilder dictStr = new StringBuilder();
    dictStr.append("{");
    // Generate a sorted version of the map so the same code is always
    // generated on all nodes
    TreeMap<String, String> sortedMap = new TreeMap<>(colMap);
    for (String prv : sortedMap.keySet()) {
      dictStr.append(makeQuoted(prv));
      dictStr.append(": ");
      dictStr.append(makeQuoted(colMap.get(prv)));
      dictStr.append(", ");
    }
    dictStr.append("}");
    return dictStr.toString();
  }

  /**
   * Escapes " so Python interprets String correctly.
   *
   * @param inputStr String possibly containing "
   * @return String with quotes properly escaped.
   */
  public static String escapePythonQuotes(String inputStr) {
    return inputStr.replaceAll("(?<!\\\\)\"", "\\\\\"");
  }

  public static void expectScalarArgument(RexNode argNode, String fnName, String argName) {
    if (!isScalar(argNode)) {
      throw new BodoSQLCodegenException(
          "Error: argument '" + argName + "' to function " + fnName + " must be a scalar.");
    }
  }

  /**
   * Function to convert a SQL type to a matching Pandas type.
   *
   * @param typeName SQL Type.
   * @param outputScalar Should the output generate a type for converting scalars.
   * @return The pandas type
   */
  public static String sqlTypenameToPandasTypename(SqlTypeName typeName, boolean outputScalar) {
    String dtype;
    switch (typeName) {
      case BOOLEAN:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_bool";
        } else {
          dtype = makeQuoted("boolean");
        }
        break;
      case TINYINT:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_int8";
        } else {
          dtype = "pd.Int8Dtype()";
        }
        break;
      case SMALLINT:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_int16";
        } else {
          dtype = "pd.Int16Dtype()";
        }
        break;
      case INTEGER:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_int32";
        } else {
          dtype = "pd.Int32Dtype()";
        }
        break;
      case BIGINT:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_int64";
        } else {
          dtype = "pd.Int64Dtype()";
        }
        break;
      case FLOAT:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_float32";
        } else {
          dtype = "pd.Float32Dtype()";
        }
        break;
      case DOUBLE:
      case DECIMAL:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_float64";
        } else {
          dtype = "pd.Float64Dtype()";
        }
        break;
      case DATE:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_pd_to_date";
        } else {
          dtype = "bodo.datetime_date_type";
        }
        break;
      case TIMESTAMP:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_pd_to_datetime";
        } else {
          dtype = "np.dtype(\"datetime64[ns]\")";
        }
        break;
      case TIME:
        // TODO [BE-3649]: The precision needs to be handled here.
        throw new BodoSQLCodegenException(
            "Internal Error: Calcite Plan Produced an Unsupported TIME Type");
      case VARCHAR:
      case CHAR:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_str";
        } else {
          dtype = "str";
        }
        break;
      case VARBINARY:
      case BINARY:
        if (outputScalar) {
          dtype = "bodosql.libs.generated_lib.sql_null_checking_scalar_conv_str";
        } else {
          // TODO: FIXME?
          dtype = "bodo.bytes_type";
        }
        break;
      case INTERVAL_DAY_HOUR:
      case INTERVAL_DAY_MINUTE:
      case INTERVAL_DAY_SECOND:
      case INTERVAL_HOUR_MINUTE:
      case INTERVAL_HOUR_SECOND:
      case INTERVAL_MINUTE_SECOND:
      case INTERVAL_HOUR:
      case INTERVAL_MINUTE:
      case INTERVAL_SECOND:
      case INTERVAL_DAY:
        if (outputScalar) {
          // pd.to_timedelta(None) returns None in standard python, but not in Bodo
          // This should likely be in the engine itself, to match pandas behavior
          // BE-2882
          dtype = "pd.to_timedelta";
        } else {
          dtype = "np.dtype(\"timedelta64[ns]\")";
        }
        break;
      case INTERVAL_YEAR:
      case INTERVAL_MONTH:
      case INTERVAL_YEAR_MONTH:
        // May later refactor this code to create DateOffsets, for now
        // causes an error
      default:
        throw new BodoSQLCodegenException(
            "Internal Error: Calcite Plan Produced an Unsupported Type: " + typeName.getName());
    }
    return dtype;
  }

  /**
   * Calcite optimizes a large number of windowed aggregation functions into case statements, which
   * check if the window size is valid. This checks if the supplied node is one of those case
   * statements.
   *
   * <p>The rough location in which this occurs within calcite is here:
   * https://github.com/apache/calcite/blob/master/core/src/main/java/org/apache/calcite/sql2rel/SqlToRelConverter.java#L2081
   * I am still trying to find the exact location where this translation into case statements
   * occurs.
   *
   * @param node the case node to check
   * @return true if it is a wrapped windowed aggregation function, and False if it is not
   */
  public static boolean isWindowedAggFn(RexCall node) {
    // First, we expect exactly three operands in the case statement
    if (node.getOperands().size() != 3) {
      return false;
    }
    return isEmptyWindowCheck(node) || windowLen1Check(node);
  }

  /**
   * Calcite optimizes a large number of windowed aggregation functions into case statements, which
   * check if the window size is valid. This checks if the rexcall is a windowed aggregation
   * function checking that the size of the window is 0.
   *
   * @param node the rexCall on which to perform the check
   * @return Boolean determining if a rexcall is in fact a windowed aggregation with an empty window
   *     check
   */
  public static boolean isEmptyWindowCheck(RexCall node) {
    // For arg0 (when case), we expect a comparison to the size of the window
    boolean arg0IsWindowSizeComparison =
        node.getOperands().get(0) instanceof RexCall
            && ((RexCall) node.getOperands().get(0)).getOperator().getKind() == SqlKind.GREATER_THAN
            && ((RexCall) node.getOperands().get(0)).getOperands().get(0) instanceof RexOver;
    // For arg1 (then case), we expect a windowed aggregation function
    boolean arg1IsWindowed = node.getOperands().get(1) instanceof RexOver;
    // For the else case, we expect NULL
    boolean arg2Null = node.getOperands().get(2) instanceof RexLiteral;

    return arg0IsWindowSizeComparison && arg1IsWindowed && arg2Null;
  }

  /**
   * Calcite optimizes a large number of windowed aggregation functions into case statements, which
   * check if the window size is valid. This checks if the input rexcall is a windowed aggregation
   * function checking that the size of the window is 1.
   *
   * @param node the rexCall on which to perform the check
   * @return Boolean determining if a rexcall is in fact a windowed aggregation with a window size 1
   *     check
   */
  public static boolean windowLen1Check(RexCall node) {
    // For arg0 (when case), we expect a comparison to the size of the window
    boolean arg0IsWindowSizeComparison =
        node.getOperands().get(0) instanceof RexCall
            && ((RexCall) node.getOperands().get(0)).getOperator().getKind() == SqlKind.EQUALS
            && ((RexCall) node.getOperands().get(0)).getOperands().get(0) instanceof RexOver;
    // For arg1 (then case), we expect NULL
    boolean arg1IsWindowed = node.getOperands().get(1) instanceof RexLiteral;
    // For the else case, we expect a windowed aggregation function
    //    boolean arg2Null = node.getOperands().get(2) instanceof RexOver;

    return arg0IsWindowSizeComparison && arg1IsWindowed;
  }

  /**
   * Helper function, takes the existing column names and a hashset of columns to add, and returns a
   * new DataFrame, consisting of both the new and old columns. Generally used immediately before
   * generating code for CASE statements.
   *
   * @param inputVar The input DataFrame, to which we add the new columns.
   * @param colNames The Name of the columns already present in the inputVar in order of the column
   *     indices
   * @param colsToAddList The List of array variables that must be added to new DataFrame.
   * @param visitor The visitor for generating intermediate variables, especially globals.
   * @param builder The builder for appending generated code.
   * @return The variable with the output DataFrame.
   */
  public static Variable generateCombinedDf(
      Variable inputVar,
      List<String> colNames,
      List<String> colsToAddList,
      PandasCodeGenVisitor visitor,
      Module.Builder builder) {
    // TODO: Unify visitor and builder
    List<Expr.StringLiteral> names = new ArrayList<>();
    List<Expr.IntegerLiteral> keptIndices = new ArrayList<>();
    for (int i = 0; i < colNames.size(); i++) {
      Expr.StringLiteral colNameLiteral = new StringLiteral(colNames.get(i));
      names.add(colNameLiteral);
      keptIndices.add(new IntegerLiteral(i));
    }
    List<Expr> newColValues = new ArrayList<>();
    for (int j = 0; j < colsToAddList.size(); j++) {
      String newCol = colsToAddList.get(j);
      Expr.StringLiteral colNameLiteral = new StringLiteral(newCol);
      names.add(colNameLiteral);
      keptIndices.add(new IntegerLiteral(j + colNames.size()));
      newColValues.add(new Variable(newCol));
    }
    // Generate the data call
    Variable columnsVar = visitor.genGenericTempVar();
    Expr.Call getData =
        new Expr.Call("bodo.hiframes.pd_dataframe_ext.get_dataframe_all_data", List.of(inputVar));
    builder.add(new Op.Assign(columnsVar, getData));
    // Generate the table
    Variable tableVar = visitor.genTableVar();
    Expr.Tuple extraData = new Tuple(newColValues);
    Variable keptColsGlobal = visitor.lowerAsMetaType(new Expr.Tuple(keptIndices));
    Expr.IntegerLiteral originalNumCols = new Expr.IntegerLiteral(colNames.size());
    Expr.Call tableCall =
        new Expr.Call(
            "bodo.hiframes.table.logical_table_to_table",
            List.of(columnsVar, extraData, keptColsGlobal, originalNumCols));
    builder.add(new Op.Assign(tableVar, tableCall));
    // Generate the index
    Variable indexVar = visitor.genIndexVar();
    Expr.Len lenExpr = new Expr.Len(inputVar);
    Expr.Call indexCall =
        new Expr.Call(
            "bodo.hiframes.pd_index_ext.init_range_index",
            List.of(Expr.Companion.getZero(), lenExpr, Expr.Companion.getOne(), None.INSTANCE));
    builder.add(new Op.Assign(indexVar, indexCall));
    // Generate the DataFrame
    Variable dfVar = visitor.genDfVar();
    Expr.Tuple colNameTuple = new Expr.Tuple(names);
    Variable globalNamesVar = visitor.lowerAsColNamesMetaType(colNameTuple);

    // output dataframe is always in table format
    Expr tableTuple = new Expr.Tuple(List.of(tableVar));
    Expr.Call initDf =
        new Expr.Call(
            "bodo.hiframes.pd_dataframe_ext.init_dataframe",
            List.of(tableTuple, indexVar, globalNamesVar));
    builder.add(new Op.Assign(dfVar, initDf));
    return dfVar;
  }

  /**
   * Checks if a string is a legal name for a Python identifier
   *
   * @param name the string name that needs to be checked
   * @return Boolean for if the name matches the regex [A-Za-z_]\w*
   */
  public static boolean isValidPythonIdentifier(String name) {
    final Pattern p = Pattern.compile("[a-zA-Z_]\\w*");
    Matcher m = p.matcher(name);
    return m.matches();
  }

  public static String getInputColumn(
      List<String> inputColumnNames, AggregateCall a, List<Integer> keyCols) {
    if (a.getArgList().isEmpty()) {
      // count(*) case
      // count(*) is turned into to count() by Calcite
      // in this case, we can use any column for aggregation, since inputColumnNames should
      // always contain at least one group by column, we use the first column for the
      // aggregation, and manually set the fieldname to *. However, count(*) includes
      // NULL values (whereas count does not).
      assert !inputColumnNames.isEmpty();
      if (keyCols.size() > 0) {
        // Use the key the list is not empty.
        return inputColumnNames.get(keyCols.get(0));
      }
      return inputColumnNames.get(0);
    } else {
      return inputColumnNames.get(a.getArgList().get(0));
    }
  }

  /***
   * Searches the input expression for table references to oldTableName, and replaces them to reference the new Table.
   * Only used inside CASE when there are window functions (so a new dataframe has to be created).
   * For example, table1_1[i] -> tmp_case_df2_1[i]
   *
   * @param expr The expression to replace table references
   * @param oldVar The old variable, whose name the input expr uses for table references
   * @param newVar The new variable, whose name the output expr will use for table references
   * @return
   */
  public static String renameTableRef(String expr, Variable oldVar, Variable newVar) {
    // check word boundary with \b to reduce chance of name conflicts with oldTableName
    return expr.replaceAll("\\b" + Pattern.quote(oldVar.getName() + "_"), newVar.getName() + "_");
  }

  public static void assertWithErrMsg(boolean test, String msg) {
    if (!test) {
      throw new RuntimeException(msg);
    }
  }

  public static boolean isSnowflakeCatalogTable(BodoSqlTable table) {
    BodoSqlSchema schema = table.getSchema();
    if (table instanceof CatalogTableImpl && schema instanceof CatalogSchemaImpl) {
      CatalogSchemaImpl catalogSchema = (CatalogSchemaImpl) schema;
      return catalogSchema.getCatalog() instanceof SnowflakeCatalogImpl;
    }
    return false;
  }

  /**
   * Convert a list of strings to a list of string literals.
   *
   * @param arg The list of strings
   * @return A list of string literals
   */
  public static List<StringLiteral> stringsToStringLiterals(List<String> arg) {
    List<StringLiteral> output = new ArrayList<>(arg.size());
    for (int i = 0; i < arg.size(); i++) {
      output.add(new StringLiteral(arg.get(i)));
    }
    return output;
  }

  /**
   * Given a non-negative number stop, create a list of integer literals from [0, stop).
   *
   * @param stop The end of range.
   * @return A list of integer literals from [0, stop)
   */
  public static List<IntegerLiteral> integerLiteralArange(int stop) {
    List<IntegerLiteral> output = new ArrayList<>(stop);
    for (int i = 0; i < stop; i++) {
      output.add(new IntegerLiteral(i));
    }
    return output;
  }
}