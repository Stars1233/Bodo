package com.bodosql.calcite.application.utils;

import com.bodosql.calcite.application.BodoSQLCodegenException;
import com.bodosql.calcite.ir.Expr;
import org.apache.calcite.rel.type.RelDataType;
import org.apache.calcite.sql.type.SqlTypeName;
import org.apache.calcite.sql.type.TZAwareSqlType;

public class BodoArrayHelpers {

  /**
   * Takes a sql type, and a string length, and returns a string representing the appropriate
   * nullable array, allocated using bodo helper functions. Will upcast the type to the maximum bit
   * width.
   *
   * @param len String length expression
   * @param typ SqlType
   * @return A string representation of the allocated bodo array with the specified length
   */
  public static String sqlTypeToNullableBodoArray(String len, RelDataType typ) {
    // TODO: Use nullable information to optimize on if the output can contain NULLs.
    SqlTypeName typeName = typ.getSqlTypeName();
    switch (typeName) {
      case BOOLEAN:
        return String.format("bodo.libs.bool_arr_ext.alloc_bool_array(%s)", len);
      case TINYINT:
      case SMALLINT:
      case INTEGER:
      case BIGINT:
        return String.format("bodo.libs.int_arr_ext.alloc_int_array(%s, bodo.int64)", len);
      case FLOAT:
      case DOUBLE:
      case DECIMAL:
        return String.format("bodo.libs.float_arr_ext.alloc_float_array(%s, bodo.float64)", len);
      case DATE:
        return String.format("bodo.hiframes.datetime_date_ext.alloc_datetime_date_array(%s)", len);
      case TIMESTAMP:
        return String.format("np.empty(%s, dtype=\"datetime64[ns]\")", len);
      case TIMESTAMP_WITH_LOCAL_TIME_ZONE:
        // TZ-Aware timestamps contain tz info in the type.
        String tzStr = ((TZAwareSqlType) typ).getTZInfo().getZoneExpr().emit();
        return String.format(
            "bodo.libs.pd_datetime_arr_ext.alloc_pd_datetime_array(%s, %s)", len, tzStr);
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
        return String.format("np.empty(%s, dtype=\"timedelta64[ns]\")", len);
      case CHAR:
      case VARCHAR:
        return String.format("bodo.libs.str_arr_ext.pre_alloc_string_array(%s, -1)", len);
      case BINARY:
      case VARBINARY:
        return String.format("bodo.libs.str_arr_ext.pre_alloc_binary_array(%s, -1)", len);
      case TIME:
        int precision = typ.getPrecision();
        return String.format("bodo.hiframes.time_ext.alloc_time_array(%s, %d)", len, precision);
      default:
        throw new BodoSQLCodegenException(
            "Error, type: " + typ.toString() + " not supported for Window Aggregation functions");
    }
  }

  /**
   * Generate a String that matches the Bodo array type generated by the given type. This produces a
   * different array type depending on if the input is nullable.
   *
   * @param type SQL type for which we are generating the array.
   * @param strAsDict Should string types output a dictionary encoded array as opposed to a regular
   *     string array.
   * @return A string that can be provided to generate code for the corresponding array type. This
   *     will be lowered as a global when we JIT compile the code.
   */
  public static Expr sqlTypeToBodoArrayType(RelDataType type, boolean strAsDict) {
    boolean nullable = type.isNullable();
    // TODO: Create type exprs
    String typeName = "";
    switch (type.getSqlTypeName()) {
      case NULL:
        typeName = "bodo.null_array_type";
        break;
      case ARRAY:
        typeName =
            String.format(
                "bodo.ArrayItemArrayType(%s)",
                sqlTypeToBodoArrayType(type.getComponentType(), false).emit());
        break;
      case BOOLEAN:
        // TODO: Add nullable support in the type
        typeName = "bodo.boolean_array_type";
        break;
      case TINYINT:
        // TODO: Add signed vs unsigned support
        if (nullable) {
          typeName = "bodo.IntegerArrayType(bodo.int8)";
        } else {
          typeName = "numba.core.types.Array(bodo.int8, 1, 'C')";
        }
        break;
      case SMALLINT:
        // TODO: Add signed vs unsigned support
        if (nullable) {
          typeName = "bodo.IntegerArrayType(bodo.int16)";
        } else {
          typeName = "numba.core.types.Array(bodo.int16, 1, 'C')";
        }
        break;
      case INTEGER:
        // TODO: Add signed vs unsigned support
        if (nullable) {
          typeName = "bodo.IntegerArrayType(bodo.int32)";
        } else {
          typeName = "numba.core.types.Array(bodo.int32, 1, 'C')";
        }
        break;
      case BIGINT:
        // TODO: Add signed vs unsigned support
        if (nullable) {
          typeName = "bodo.IntegerArrayType(bodo.int64)";
        } else {
          typeName = "numba.core.types.Array(bodo.int64, 1, 'C')";
        }
        break;
      case FLOAT:
        if (nullable) {
          typeName = "bodo.FloatingArrayType(bodo.float32)";
        } else {
          typeName = "numba.core.types.Array(bodo.float32, 1, 'C')";
        }
        break;
      case DOUBLE:
      case DECIMAL:
        if (nullable) {
          typeName = "bodo.FloatingArrayType(bodo.float64)";
        } else {
          typeName = "numba.core.types.Array(bodo.float64, 1, 'C')";
        }
        break;
      case DATE:
        typeName = "bodo.datetime_date_array_type";
        break;
      case TIMESTAMP:
        // TODO: Add nullable support
        typeName = "numba.core.types.Array(bodo.datetime64ns, 1, 'C')";
        break;
      case TIMESTAMP_WITH_LOCAL_TIME_ZONE:
        // TODO: Add nullable support
        TZAwareSqlType tzAwareType = (TZAwareSqlType) type;
        typeName =
            String.format(
                "bodo.DatetimeArrayType(%s)", tzAwareType.getTZInfo().getZoneExpr().emit());
        break;
      case TIME:
        // TODO: Add nullable support
        // TODO: Add precision support once Bodo stores value differently based on precision
        typeName = "bodo.TimeArrayType(9)";
        break;
      case VARCHAR:
      case CHAR:
        // TODO: Add nullable support
        if (strAsDict) {
          typeName = "bodo.dict_str_arr_type";
        } else {
          typeName = "bodo.string_array_type";
        }
        break;
      case VARBINARY:
      case BINARY:
        // TODO: Add nullable support
        typeName = "bodo.binary_array_type";
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
        // TODO: Add nullable support
        typeName = "numba.core.types.Array(bodo.timedelta64ns, 1, 'C')";
        break;
      case INTERVAL_YEAR:
      case INTERVAL_MONTH:
      case INTERVAL_YEAR_MONTH:
        // May later refactor this code to create DateOffsets, for now
        // causes an error
      default:
        throw new BodoSQLCodegenException(
            "Internal Error: Calcite Plan Produced an Unsupported Type: "
                + type.getSqlTypeName().getName());
    }
    return new Expr.Raw(typeName);
  }
}