package com.bodosql.calcite.application.BodoSQLCodeGen;

import static com.bodosql.calcite.application.Utils.Utils.checkNotNullColumns;
import static com.bodosql.calcite.application.Utils.Utils.checkNullColumns;

import com.bodosql.calcite.application.BodoSQLCodegenException;
import java.util.HashSet;
import org.apache.calcite.sql.SqlOperator;

/**
 * Class that returns the generated code for Postfix Operators after all inputs have been visited.
 */
public class PostfixOpCodeGen {
  /**
   * Function that return the necessary generated code for a Postfix Operator call.
   *
   * @param arg The arg expr.
   * @param postfixOp The postfix operator.
   * @param inputVar Name of dataframe from which InputRefs select Columns
   * @param nullSet The nullset used by IS_NULL and IS_NOT_NULL.
   * @param outputScalar Should the output generate scalar code.
   * @return The code generated that matches the Postfix Operator call.
   */
  public static String generatePostfixOpCode(
      String arg,
      SqlOperator postfixOp,
      String inputVar,
      HashSet<String> nullSet,
      boolean outputScalar) {
    StringBuilder codeBuilder = new StringBuilder();
    switch (postfixOp.getKind()) {
      case IS_NULL:
        if (outputScalar) {
          if (nullSet.size() > 0) {
            codeBuilder.append(checkNullColumns(inputVar, nullSet));
          } else {
            codeBuilder.append("pd.isna(").append(arg).append(")");
          }
        } else {
          codeBuilder.append("(").append(arg).append(".isna())");
        }
        break;
      case IS_NOT_NULL:
        if (outputScalar) {
          if (nullSet.size() > 0) {
            codeBuilder.append(checkNotNullColumns(inputVar, nullSet));
          } else {
            codeBuilder.append("pd.notna(").append(arg).append(")");
          }
        } else {
          codeBuilder.append("(").append(arg).append(".notna())");
        }
        break;
        // IS_NOT FALSE != IS_TRUE and visa vera for null case.
        // (NULL IS NOT FALSE) == TRUE, but (NULL IS TRUE) == False

        // TODO: ~ on a column containing none does NOT work.
      case IS_NOT_FALSE:
        if (outputScalar) {
          codeBuilder.append("(not (").append(arg).append("is False))");
        } else {
          codeBuilder.append(arg).append(".fillna(True)");
        }
        break;
      case IS_NOT_TRUE:
        if (outputScalar) {
          codeBuilder.append("(not (").append(arg).append("is True))");
        } else {
          codeBuilder.append("(~").append(arg).append(".fillna(False))");
        }
        break;
      case IS_TRUE:
        if (outputScalar) {
          codeBuilder.append("(").append(arg).append("is True)");
        } else {
          codeBuilder.append(arg).append(".fillna(False)");
        }
        break;
      case IS_FALSE:
        if (outputScalar) {
          codeBuilder.append("(").append(arg).append("is False)");
        } else {
          codeBuilder.append("(~").append(arg).append(".fillna(True))");
        }
        break;
      default:
        throw new BodoSQLCodegenException(
            "Internal Error: Calcite Plan Produced an Unsupported Postfix Operator");
    }

    return codeBuilder.toString();
  }

  /**
   * Function that returns the generated name for a Postfix Operator call.
   *
   * @param name The name for the arg.
   * @param postfixOp The postfix operator.
   * @return The name generated that matches Postfix Operator call.
   */
  public static String generatePostfixOpName(String name, SqlOperator postfixOp) {
    StringBuilder nameBuilder = new StringBuilder();
    switch (postfixOp.getKind()) {
      case IS_NULL:
        nameBuilder.append("IS_NULL(").append(name).append(")");
        break;
      case IS_NOT_NULL:
        nameBuilder.append("IS_NOT_NULL(").append(name).append(")");
        break;
      case IS_NOT_FALSE:
        nameBuilder.append("IS_NOT_FALSE(").append(name).append(")");
        break;
      case IS_TRUE:
        nameBuilder.append("IS_TRUE(").append(name).append(")");
        break;
      case IS_FALSE:
        nameBuilder.append("IS_FALSE(").append(name).append(")");
        break;
      case IS_NOT_TRUE:
        nameBuilder.append("IS_NOT_TRUE(").append(name).append(")");
        break;
      default:
        throw new BodoSQLCodegenException(
            "Internal Error: Calcite Plan Produced an Unsupported Postfix Operator");
    }
    return nameBuilder.toString();
  }
}