package com.bodosql.calcite.prepare

import com.bodosql.calcite.application.BodoSQLCodegenException
import com.bodosql.calcite.application.operatorTables.ArrayOperatorTable
import com.bodosql.calcite.application.operatorTables.CastingOperatorTable
import com.bodosql.calcite.application.operatorTables.CondOperatorTable
import com.bodosql.calcite.application.operatorTables.DatetimeFnUtils
import com.bodosql.calcite.application.operatorTables.DatetimeOperatorTable
import com.bodosql.calcite.application.operatorTables.NumericOperatorTable
import com.bodosql.calcite.application.operatorTables.StringOperatorTable
import com.bodosql.calcite.sql.func.SqlBodoOperatorTable
import com.bodosql.calcite.sql.func.SqlLikeQuantifyOperator
import org.apache.calcite.rex.RexCall
import org.apache.calcite.rex.RexNode
import org.apache.calcite.rex.RexUtil
import org.apache.calcite.sql.SqlCall
import org.apache.calcite.sql.SqlKind
import org.apache.calcite.sql.SqlNodeList
import org.apache.calcite.sql.`fun`.SqlLibraryOperators
import org.apache.calcite.sql.`fun`.SqlStdOperatorTable
import org.apache.calcite.sql.type.SqlTypeFamily
import org.apache.calcite.sql.type.SqlTypeName
import org.apache.calcite.sql.type.VariantSqlType
import org.apache.calcite.sql2rel.SqlRexContext
import org.apache.calcite.sql2rel.SqlRexConvertlet
import org.apache.calcite.sql2rel.StandardConvertletTable
import org.apache.calcite.sql2rel.StandardConvertletTableConfig

/**
 * Custom convertlet table for Bodo code generation. Handles custom functions
 * along with overriding the standard behavior for certain functions that can
 * be handled natively.
 */
class BodoConvertletTable(
    config: StandardConvertletTableConfig,
) : StandardConvertletTable(config) {
    init {
        /**
         * The default Item implementation has this convertlet, so we also use this convertlet for our
         * extended implementation.
         *
         * The `convertItem` convertlet only makes changes if we're indexing into "ROW" objects
         * A "ROW" object is different from an array. It seems to be some sort of a struct type,
         * I assume from indexing into some scalar subquery? I'm not sure. We don't seem to be
         * using this functionality, but it seems more likely to cause a problem if exclude this.
         * TODO: investigate and confirm how the convertItem convertlet works
         */
        registerOp(ArrayOperatorTable.ARRAY_MAP_GET, this::convertItem)
        addAlias(ArrayOperatorTable.ARRAY_MAP_GET, ArrayOperatorTable.ARRAY_MAP_GET_BRACKET)
        addAlias(CastingOperatorTable.TO_NUMERIC, CastingOperatorTable.TO_NUMBER)
        addAlias(CastingOperatorTable.TO_DECIMAL, CastingOperatorTable.TO_NUMBER)
        addAlias(CastingOperatorTable.TRY_TO_NUMERIC, CastingOperatorTable.TRY_TO_NUMBER)
        addAlias(CastingOperatorTable.TRY_TO_DECIMAL, CastingOperatorTable.TRY_TO_NUMBER)
        addAlias(CastingOperatorTable.DATE, CastingOperatorTable.TO_DATE)
        addAlias(CastingOperatorTable.TIME, CastingOperatorTable.TO_TIME)
        addAlias(CastingOperatorTable.TO_CHAR, CastingOperatorTable.TO_VARCHAR)
        addAlias(DatetimeOperatorTable.DATEFROMPARTS, DatetimeOperatorTable.DATE_FROM_PARTS)
        addAlias(DatetimeOperatorTable.TIMEFROMPARTS, DatetimeOperatorTable.TIME_FROM_PARTS)
        addAlias(DatetimeOperatorTable.TIMESTAMPFROMPARTS, DatetimeOperatorTable.TIMESTAMP_FROM_PARTS)
        addAlias(DatetimeOperatorTable.TIMESTAMPNTZFROMPARTS, DatetimeOperatorTable.TIMESTAMP_NTZ_FROM_PARTS)
        addAlias(DatetimeOperatorTable.TIMESTAMPLTZFROMPARTS, DatetimeOperatorTable.TIMESTAMP_LTZ_FROM_PARTS)
        addAlias(DatetimeOperatorTable.TIMESTAMPTZFROMPARTS, DatetimeOperatorTable.TIMESTAMP_TZ_FROM_PARTS)
        addAlias(StringOperatorTable.LEN, StringOperatorTable.LENGTH)
        addAlias(StringOperatorTable.SHA2_HEX, StringOperatorTable.SHA2)
        addAlias(StringOperatorTable.MD5_HEX, StringOperatorTable.MD5)
        addAlias(CondOperatorTable.IF_FUNC, CondOperatorTable.IFF_FUNC)
        addAlias(CondOperatorTable.NVL, SqlStdOperatorTable.COALESCE)
        addAlias(CondOperatorTable.IFNULL_FUNC, SqlStdOperatorTable.COALESCE)
        addAlias(StringOperatorTable.CHR, StringOperatorTable.CHAR)
        addAlias(StringOperatorTable.ORD, SqlStdOperatorTable.ASCII)
        addAlias(StringOperatorTable.LCASE, SqlStdOperatorTable.LOWER)
        addAlias(StringOperatorTable.UCASE, SqlStdOperatorTable.UPPER)
        addAlias(DatetimeOperatorTable.SYSDATE, DatetimeOperatorTable.UTC_TIMESTAMP)
        addAlias(SqlStdOperatorTable.LOCALTIME, SqlStdOperatorTable.CURRENT_TIME)
        addAlias(DatetimeOperatorTable.CURDATE, SqlStdOperatorTable.CURRENT_DATE)
        addAlias(SqlBodoOperatorTable.CURRENT_TIMESTAMP, DatetimeOperatorTable.GETDATE)
        addAlias(DatetimeOperatorTable.NOW, DatetimeOperatorTable.GETDATE)
        addAlias(SqlBodoOperatorTable.LOCALTIMESTAMP, DatetimeOperatorTable.GETDATE)
        addAlias(DatetimeOperatorTable.SYSTIMESTAMP, DatetimeOperatorTable.GETDATE)
        addAlias(DatetimeOperatorTable.DATE_ADD, DatetimeOperatorTable.DATEADD)
        addAlias(DatetimeOperatorTable.TIMEADD, DatetimeOperatorTable.DATEADD)
        addAlias(SqlBodoOperatorTable.TIMESTAMP_ADD, DatetimeOperatorTable.DATEADD)
        addAlias(DatetimeOperatorTable.TIMEDIFF, DatetimeOperatorTable.DATEDIFF)
        addAlias(SqlBodoOperatorTable.TIMESTAMP_DIFF, DatetimeOperatorTable.DATEDIFF)
        addAlias(SqlStdOperatorTable.POSITION, StringOperatorTable.CHARINDEX)
        addAlias(StringOperatorTable.SUBSTR, SqlStdOperatorTable.SUBSTRING)
        addAlias(StringOperatorTable.MID, SqlStdOperatorTable.SUBSTRING)
        addAlias(SqlLibraryOperators.RLIKE, StringOperatorTable.REGEXP_LIKE)
        addAlias(StringOperatorTable.RLIKE, StringOperatorTable.REGEXP_LIKE)
        addAlias(SqlBodoOperatorTable.NULL_EQUALS, SqlStdOperatorTable.IS_NOT_DISTINCT_FROM)
        // TODO: Restore when semi-structured implementations are fixed.
//        addAlias(CondOperatorTable.EQUAL_NULL, SqlStdOperatorTable.IS_NOT_DISTINCT_FROM)
        registerOp(StringOperatorTable.LENGTH, this::simpleConversion)
        registerOp(SqlStdOperatorTable.PLUS, this::convertPlus)
        registerOp(SqlStdOperatorTable.MINUS, this::convertMinus)
    }

    constructor() : this(StandardConvertletTableConfig(true, true))

    /**
     * Convert an operator from Sql To Rex directly. This is used in case our convertlet
     * conflicts with Calcite.
     */
    private fun simpleConversion(
        cx: SqlRexContext,
        call: SqlCall,
    ): RexNode {
        val operands = call.operandList.map { op -> cx.convertExpression(op) }
        return cx.rexBuilder.makeCall(call.operator, operands)
    }

    /**
     * Convert a Sql Datetime Plus. This is used to expand Calcite functionality.
     */
    private fun convertPlus(
        cx: SqlRexContext,
        call: SqlCall,
    ): RexNode {
        val rex = convertCall(cx, call)
        return when (rex.type.sqlTypeName) {
            SqlTypeName.DATE, SqlTypeName.TIME, SqlTypeName.TIMESTAMP -> {
                // Use special "+" operator for datetime + interval.
                // Re-order operands, if necessary, so that interval is second.
                val rexBuilder = cx.rexBuilder
                var operands = (rex as RexCall).getOperands()
                if (operands.size == 2) {
                    when (operands[0].type.sqlTypeName) {
                        SqlTypeName.DATE, SqlTypeName.TIME, SqlTypeName.TIMESTAMP, SqlTypeName.TIMESTAMP_WITH_LOCAL_TIME_ZONE -> {
                            if (listOf(
                                    SqlTypeName.TINYINT,
                                    SqlTypeName.SMALLINT,
                                    SqlTypeName.INTEGER,
                                    SqlTypeName.BIGINT,
                                ).contains(operands[1].type.sqlTypeName)
                            ) {
                                val newOperands =
                                    listOf(
                                        rexBuilder.makeLiteral(
                                            DatetimeFnUtils.DateTimePart.DAY,
                                            cx.typeFactory.createSqlType(SqlTypeName.SYMBOL),
                                        ),
                                        operands[1],
                                        operands[0],
                                    )
                                rexBuilder.makeCall(
                                    rex.getType(),
                                    DatetimeOperatorTable.DATEADD,
                                    newOperands,
                                )
                            } else {
                                rexBuilder.makeCall(
                                    rex.getType(),
                                    SqlStdOperatorTable.DATETIME_PLUS,
                                    operands,
                                )
                            }
                        }
                        SqlTypeName.INTERVAL_YEAR, SqlTypeName.INTERVAL_YEAR_MONTH, SqlTypeName.INTERVAL_MONTH,
                        SqlTypeName.INTERVAL_DAY, SqlTypeName.INTERVAL_DAY_HOUR, SqlTypeName.INTERVAL_DAY_MINUTE,
                        SqlTypeName.INTERVAL_DAY_SECOND, SqlTypeName.INTERVAL_HOUR, SqlTypeName.INTERVAL_HOUR_MINUTE,
                        SqlTypeName.INTERVAL_HOUR_SECOND, SqlTypeName.INTERVAL_MINUTE, SqlTypeName.INTERVAL_MINUTE_SECOND,
                        SqlTypeName.INTERVAL_SECOND,
                        -> {
                            val newOperands = listOf(operands[1], operands[0])
                            rexBuilder.makeCall(
                                rex.getType(),
                                SqlStdOperatorTable.DATETIME_PLUS,
                                newOperands,
                            )
                        }
                        SqlTypeName.TINYINT, SqlTypeName.SMALLINT, SqlTypeName.INTEGER, SqlTypeName.BIGINT -> {
                            val newOperands =
                                listOf(
                                    rexBuilder.makeLiteral(
                                        DatetimeFnUtils.DateTimePart.DAY,
                                        cx.typeFactory.createSqlType(SqlTypeName.SYMBOL),
                                    ),
                                    operands[0],
                                    operands[1],
                                )
                            rexBuilder.makeCall(
                                rex.getType(),
                                DatetimeOperatorTable.DATEADD,
                                newOperands,
                            )
                        }
                        else -> {
                            rex
                        }
                    }
                } else {
                    rex
                }
            } else -> rex
        }
    }

    /**
     * Convert a Sql Datetime Minus. This is used to expand Calcite functionality.
     */
    private fun convertMinus(
        cx: SqlRexContext,
        call: SqlCall,
    ): RexNode {
        val e = convertCall(cx, call) as RexCall
        val operands = e.getOperands()
        return when (operands[0].type.sqlTypeName) {
            SqlTypeName.DATE, SqlTypeName.TIME, SqlTypeName.TIMESTAMP -> {
                val rexBuilder = cx.rexBuilder
                when (operands[1].type.sqlTypeName) {
                    SqlTypeName.TINYINT, SqlTypeName.SMALLINT, SqlTypeName.INTEGER, SqlTypeName.BIGINT -> {
                        val newOperands =
                            listOf(
                                rexBuilder.makeLiteral(DatetimeFnUtils.DateTimePart.DAY, cx.typeFactory.createSqlType(SqlTypeName.SYMBOL)),
                                // Reuse add by negating the integer input.
                                rexBuilder.makeCall(operands[1].type, SqlStdOperatorTable.UNARY_MINUS, listOf(operands[1])),
                                operands[0],
                            )
                        rexBuilder.makeCall(
                            e.getType(),
                            DatetimeOperatorTable.DATEADD,
                            newOperands,
                        )
                    } else -> {
                        // TODO: Determine if we need/want to support MINUS_DATE. This syntax
                        // doesn't seem to be supported by Snowflake.
                        e
                    }
                }
            }
            else -> e
        }
    }

    override fun get(call: SqlCall): SqlRexConvertlet? =
        when (call.kind) {
            // LEAST and GREATEST default to expanding into case statements
            // in the standard convertlet table. We natively support these
            // operations so avoid converting them to another pattern.
            SqlKind.LEAST, SqlKind.GREATEST -> AliasConverter
            SqlKind.LIKE ->
                if (call.operator is SqlLikeQuantifyOperator) {
                    LikeQuantifyConverter
                } else {
                    super.get(call)
                }
            SqlKind.OTHER_FUNCTION -> {
                if (CONVERSION_FUNCTIONS.contains(call.operator.name) || TRY_CONVERSION_FUNCTIONS.contains(call.operator.name)) {
                    ConversionFunctionConverter
                } else {
                    when (call.operator.name) {
                        "TRUNC" -> DateTruncConverter
                        "NULLIFZERO" -> NullIfZeroConverter
                        "DIV0NULL" -> DivZeroNullConverter
                        else -> super.get(call)
                    }
                }
            }
            else -> super.get(call)
        }

    private object DateTruncConverter : SqlRexConvertlet {
        override fun convertCall(
            cx: SqlRexContext,
            call: SqlCall,
        ): RexNode {
            val operands = call.operandList.map { op -> cx.convertExpression(op) }

            return when (cx.validator.getValidatedNodeType(call).family) {
                SqlTypeFamily.NUMERIC -> cx.rexBuilder.makeCall(SqlStdOperatorTable.TRUNCATE, operands)
                else -> cx.rexBuilder.makeCall(DatetimeOperatorTable.DATE_TRUNC, operands)
            }
        }
    }

    private object NullIfZeroConverter : SqlRexConvertlet {
        override fun convertCall(
            cx: SqlRexContext,
            call: SqlCall,
        ): RexNode {
            // Convert NULLIFZERO(a) into IFF(a = 0, NULL, a)
            val originalOperands = call.operandList.map { op -> cx.convertExpression(op) }
            val arg0 = originalOperands[0]

            val inputIsVariant = arg0.type is VariantSqlType
            val literalZero =
                if (inputIsVariant) {
                    // Construct a TO_VARIANT(0::INTEGER) to compare the input with
                    val type = cx.typeFactory.createSqlType(SqlTypeName.INTEGER)
                    val zero = cx.rexBuilder.makeZeroLiteral(type)
                    cx.rexBuilder.makeCall(CastingOperatorTable.TO_VARIANT, listOf(zero))
                } else {
                    // Create a 0 using the input's type
                    cx.rexBuilder.makeZeroLiteral(arg0.type)
                }

            val equalityCheck = cx.rexBuilder.makeCall(SqlStdOperatorTable.EQUALS, listOf(arg0, literalZero))
            // NULL returned in the "then" branch
            val nullValue = cx.rexBuilder.makeNullLiteral(arg0.type)
            return cx.rexBuilder.makeCall(CondOperatorTable.IFF_FUNC, listOf(equalityCheck, nullValue, arg0))
        }
    }

    private object DivZeroNullConverter : SqlRexConvertlet {
        override fun convertCall(
            cx: SqlRexContext,
            call: SqlCall,
        ): RexNode {
            // Convert DIV0NULL(x, y) into DIV0(x, COALESCE(y, 0))
            val originalOperands = call.operandList.map { op -> cx.convertExpression(op) }
            val arg0 = originalOperands[0]
            val arg1 = originalOperands[1]

            val literalZero = cx.rexBuilder.makeZeroLiteral(arg1.type)

            val coalesce = cx.rexBuilder.makeCall(SqlStdOperatorTable.COALESCE, listOf(arg1, literalZero))
            return cx.rexBuilder.makeCall(NumericOperatorTable.DIV0, listOf(arg0, coalesce))
        }
    }

    private object AliasConverter : SqlRexConvertlet {
        override fun convertCall(
            cx: SqlRexContext,
            call: SqlCall,
        ): RexNode {
            val operands = call.operandList.map { op -> cx.convertExpression(op) }
            return cx.rexBuilder.makeCall(call.operator, operands)
        }
    }

    /**
     * Handles the conversion of LIKE ANY/ALL operators to the RexNode equivalent.
     *
     * At the moment, this convertlet only handles sets such as LIKE ANY ('%a%', '%b%')
     * and converts that to an equivalent AND/OR expression with a comparison
     * against each part of the set.
     *
     * It does not handle subqueries which are valid in this context. Subqueries
     * would require further support from the planner and decorrelator to support
     * and can't be done with a simple expansion transformation.
     */
    private object LikeQuantifyConverter : SqlRexConvertlet {
        override fun convertCall(
            cx: SqlRexContext,
            call: SqlCall,
        ): RexNode {
            assert(call.operandCount() >= 2)

            val op = call.operator as SqlLikeQuantifyOperator
            val likeOp =
                if (op.caseSensitive) {
                    // Case insensitive is not standard SQL so it's in the library operators.
                    SqlStdOperatorTable.LIKE
                } else {
                    SqlLibraryOperators.ILIKE
                }

            val arg0 = cx.convertExpression(call.operandList[0])
            val arg2 = call.operandList.getOrNull(2)?.let { sqlNode -> cx.convertExpression(sqlNode) }
            val arg1 =
                when (val arg1 = call.operandList[1]) {
                    is SqlNodeList ->
                        arg1.list.map { n ->
                            val expr = cx.convertExpression(n)
                            if (arg2 != null) {
                                cx.rexBuilder.makeCall(likeOp, arg0, expr, arg2)
                            } else {
                                cx.rexBuilder.makeCall(likeOp, arg0, expr)
                            }
                        }
                    else -> throw BodoSQLCodegenException("Unsupported argument to $op: $arg1")
                }

            return when (op.comparisonKind) {
                SqlKind.SOME -> RexUtil.composeDisjunction(cx.rexBuilder, arg1)
                SqlKind.ALL -> RexUtil.composeConjunction(cx.rexBuilder, arg1)
                else -> throw IllegalStateException()
            }
        }
    }

    /**
     * Convert conversion functions to use cast operators when possible. In the future we should
     * prioritize the reverse as the function tend to be more general than a cast, but currently
     * more optimizations exist for casts.
     */
    private object ConversionFunctionConverter : SqlRexConvertlet {
        override fun convertCall(
            cx: SqlRexContext,
            call: SqlCall,
        ): RexNode {
            // First convert the argument.
            val args = call.operandList.map { x -> cx.convertExpression(x) }
            return if (args.size > 1) {
                // Most casts only match 1 argument inputs. There may be some exceptions with precisions.
                cx.rexBuilder.makeCall(call.operator, args)
            } else {
                val safe = TRY_CONVERSION_FUNCTIONS.contains(call.operator.name)
                val outputType = cx.validator.getValidatedNodeType(call)
                cx.rexBuilder.makeCast(outputType, args[0], safe, safe)
            }
        }
    }

    companion object {
        @JvmStatic
        val CONVERSION_FUNCTIONS: Set<String> =
            setOf(
                // Note: We remove calls that are aliases since they
                // will be converted to a singular call.
                CastingOperatorTable.TO_VARCHAR.name,
                CastingOperatorTable.TO_BINARY.name,
                CastingOperatorTable.TO_NUMBER.name,
                CastingOperatorTable.TO_DOUBLE.name,
                CastingOperatorTable.TO_BOOLEAN.name,
                CastingOperatorTable.TO_DATE.name,
                CastingOperatorTable.TO_TIME.name,
                CastingOperatorTable.TO_TIMESTAMP.name,
                CastingOperatorTable.TO_TIMESTAMP_NTZ.name,
                CastingOperatorTable.TO_TIMESTAMP_LTZ.name,
                CastingOperatorTable.TO_TIMESTAMP_TZ.name,
                CastingOperatorTable.TO_VARIANT.name,
                // Note: We don't include TO_ARRAY and TO_OBJECT yet
                // because they may not be equivalent to casts.
            )

        @JvmStatic
        val TRY_CONVERSION_FUNCTIONS: Set<String> =
            setOf(
                // Note: We remove calls that are aliases since they
                // will be converted to a singular call.
                CastingOperatorTable.TRY_TO_BINARY.name,
                CastingOperatorTable.TRY_TO_NUMBER.name,
                CastingOperatorTable.TRY_TO_DOUBLE.name,
                CastingOperatorTable.TRY_TO_BOOLEAN.name,
                CastingOperatorTable.TRY_TO_DATE.name,
                CastingOperatorTable.TRY_TO_TIME.name,
                CastingOperatorTable.TRY_TO_TIMESTAMP.name,
                CastingOperatorTable.TRY_TO_TIMESTAMP_NTZ.name,
                CastingOperatorTable.TRY_TO_TIMESTAMP_LTZ.name,
                CastingOperatorTable.TRY_TO_TIMESTAMP_TZ.name,
            )
    }
}
