package org.apache.calcite.sql.validate.implicit

import com.bodosql.calcite.application.operatorTables.AggOperatorTable
import com.bodosql.calcite.application.operatorTables.ArrayOperatorTable
import com.bodosql.calcite.application.operatorTables.CondOperatorTable
import com.bodosql.calcite.application.operatorTables.DatetimeOperatorTable
import com.bodosql.calcite.application.operatorTables.NumericOperatorTable
import com.bodosql.calcite.application.operatorTables.ObjectOperatorTable
import com.bodosql.calcite.application.operatorTables.StringOperatorTable
import com.bodosql.calcite.rel.type.BodoRelDataTypeFactory
import com.bodosql.calcite.sql.func.SqlBodoOperatorTable
import org.apache.calcite.rel.type.RelDataType
import org.apache.calcite.rel.type.RelDataTypeFactory
import org.apache.calcite.sql.`fun`.SqlAggOperatorTable
import org.apache.calcite.sql.`fun`.SqlLibraryOperators
import org.apache.calcite.sql.`fun`.SqlStdOperatorTable
import org.apache.calcite.sql.type.SqlTypeName

internal class VariantCastTable {

    companion object {
        private val anyArgBooleanCast = {
                inType: RelDataType, factory: RelDataTypeFactory, _: Int, _: List<RelDataType> ->
            factory.createTypeWithNullability(
                factory.createSqlType(SqlTypeName.BOOLEAN),
                inType.isNullable,
            )
        }

        // Helper function to create a RelDataType for ARRAY[variant] with the given nullability
        private val makeArrayType = { factory: RelDataTypeFactory, nullable: Boolean ->
            factory.createTypeWithNullability(
                factory.createArrayType(
                    BodoRelDataTypeFactory.createVariantSqlType(factory),
                    -1,
                ),
                nullable,
            )
        }

        // Converts all inputs to array
        private val anyArgArrayCast = {
                inType: RelDataType, factory: RelDataTypeFactory, _: Int, _: List<RelDataType> ->
            makeArrayType(factory, inType.isNullable)
        }

        // Converts the first argument to an array
        private val arg0ArrayCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            when (idx) {
                0 -> makeArrayType(factory, inType.isNullable)
                else -> inType
            }
        }

        // Converts the second argument to an array
        private val arg1ArrayCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            when (idx) {
                1 -> makeArrayType(factory, inType.isNullable)
                else -> inType
            }
        }

        // Converts the first argument to an array and the rest to number(9, 0)
        // Note: uses INTEGER for now
        private val arg0ArrayRestNumber9Cast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            when (idx) {
                0 -> makeArrayType(factory, inType.isNullable)
                else -> factory.createTypeWithNullability(
                    factory.createSqlType(SqlTypeName.INTEGER),
                    inType.isNullable,
                )
            }
        }

        // converts the second argument to varchar
        private val arg1StringCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            when (idx) {
                1 -> factory.createTypeWithNullability(
                    factory.createSqlType(SqlTypeName.VARCHAR),
                    inType.isNullable,
                )
                else -> inType
            }
        }

        private val anyArgDateCast = {
                inType: RelDataType, factory: RelDataTypeFactory, _: Int, _: List<RelDataType> ->
            factory.createTypeWithNullability(
                factory.createSqlType(SqlTypeName.DATE),
                inType.isNullable,
            )
        }

        private val anyArgVarcharCast = {
                inType: RelDataType, factory: RelDataTypeFactory, _: Int, _: List<RelDataType> ->
            factory.createTypeWithNullability(
                factory.createSqlType(SqlTypeName.VARCHAR),
                inType.isNullable,
            )
        }

        private val anyArgIntegerCast = {
                inType: RelDataType, factory: RelDataTypeFactory, _: Int, _: List<RelDataType> ->
            factory.createTypeWithNullability(
                factory.createSqlType(SqlTypeName.INTEGER),
                inType.isNullable,
            )
        }

        private val anyArgDoubleCast = {
                inType: RelDataType, factory: RelDataTypeFactory, _: Int, _: List<RelDataType> ->
            factory.createTypeWithNullability(
                factory.createSqlType(SqlTypeName.DOUBLE),
                inType.isNullable,
            )
        }

        /**
         * @param precision The precision of the decimal type.
         * @return An integer type that can represent it.
         */
        private fun chooseIntegerType(precision: Int): SqlTypeName = when {
            precision < 3 -> SqlTypeName.TINYINT
            precision < 5 -> SqlTypeName.SMALLINT
            precision < 10 -> SqlTypeName.INTEGER
            else -> SqlTypeName.BIGINT
        }

        /**
         * @param precisions: the precision that each argument should be casted to.
         *
         * @return A function that returns the type to implicitly cast each variant
         * arguments to, where each argument is cast to a decimal type with the
         * specified precision.
         *
         * Note: for the time being it is cast to an integer type based on
         * the precision. In future, we will switch this to use the correct
         * decimal type.
         */
        private fun anyArgNumberCast(vararg precisions: Int) =
            {
                    inType: RelDataType, factory: RelDataTypeFactory, i: Int, _: List<RelDataType> ->
                if (i >= precisions.size) { inType } else {
                    factory.createTypeWithNullability(
                        factory.createSqlType(chooseIntegerType(precisions[i])),
                        inType.isNullable,
                    )
                }
            }

        // TODO: switch to decimal types with precisions
        private val padCasting = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            val typeName = if (idx == 1) {
                SqlTypeName.INTEGER
            } else {
                SqlTypeName.VARCHAR
            }
            factory.createTypeWithNullability(
                factory.createSqlType(typeName),
                inType.isNullable,
            )
        }

        /**
         * Generate the new types for the given argument for any function that
         * has some arguments which need to be cast to string and other
         * arguments that need to be cast to the same numeric type (integer, bigInt, etc).
         *
         * TODO: change behavior to coerce the numeric arguments to decimal types
         * with certain precisions.
         *
         * @param inType: The original input type. This is returned if the argument number
         * is beyond the argument limit and is used to determine nullability.
         * @param factory: The factory used to create the output data type.
         * @param idx: The argument of the input to replace. This is used to determine which type
         * should be output.
         * @param argLimit: The maximum number of expected arguments for a function's signature.
         * If any index is passed in that suggests more arguments than accounted for then we do not cast
         * the variant type at all to avoid incorrect undefined behavior.
         * @param stringIndices A set of argument numbers that map to string types. All other valid arguments
         * map to the numeric type.
         * @param numericTypeName The typename for the numeric type bing cast to. All numeric arguments must cast
         * to the same type for this function
         *
         * @return The output data type that should be produced for the given variant argument.
         */
        private fun varcharNumericHelper(inType: RelDataType, factory: RelDataTypeFactory, idx: Int, argLimit: Int, stringIndices: Set<Int>, numericTypeName: SqlTypeName): RelDataType {
            if (idx >= argLimit) {
                return inType
            }
            val typeName = if (stringIndices.contains(idx)) {
                SqlTypeName.VARCHAR
            } else {
                numericTypeName
            }
            return factory.createTypeWithNullability(
                factory.createSqlType(typeName),
                inType.isNullable,
            )
        }

        /**
         * Casting for 2 argument functions where the first argument is varchar and the last
         * is tinyInt.
         */
        private val varcharTinyintCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            varcharNumericHelper(inType, factory, idx, 2, setOf(0), SqlTypeName.TINYINT)
        }

        /**
         * Casting for 2 argument functions where the first argument is varchar and the last
         * is integer.
         */
        private val varcharIntegerCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            varcharNumericHelper(inType, factory, idx, 2, setOf(0), SqlTypeName.INTEGER)
        }

        /**
         * Casting for 3 argument functions where the first 2 arguments are varchar and the last
         * is integer.
         */
        private val varcharVarcharIntegerCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            varcharNumericHelper(inType, factory, idx, 3, setOf(0, 1), SqlTypeName.INTEGER)
        }

        /**
         * Casting for 3 argument functions where the first arguments is varchar and the last
         * two are BIGINT.
         */
        private val varcharBigintBigintCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            varcharNumericHelper(inType, factory, idx, 3, setOf(0), SqlTypeName.BIGINT)
        }

        /**
         * Function that casts variant to string if they are found in the allowed indices
         * and just returns variant for all other arguments.
         */
        private val onlyStringCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, stringIndices: Set<Int> ->
            if (stringIndices.contains(idx)) {
                factory.createTypeWithNullability(
                    factory.createSqlType(SqlTypeName.VARCHAR),
                    inType.isNullable,
                )
            } else {
                inType
            }
        }

        /**
         * Cast for functions that change arg0 to string but other variant
         * casting is not supported (either because it should never be supported
         * or we don't support it yet).
         */
        private val arg0VarcharCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            onlyStringCast(inType, factory, idx, setOf(0))
        }

        /**
         * Cast for insert, for which we currently only support casting the string
         * arguments.
         *
         * Insert casts to of a function signature
         * (VARCHAR, INTEGER, INTEGER, VARCHAR), but Snowflake's variant casting
         * for the integers does VARIANT:DOUBLE:INTEGER, so we don't support this yet
         * and only enable the VARCHAR
         */
        private val insertCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            onlyStringCast(inType, factory, idx, setOf(0, 3))
        }

        /**
         * Cast for dateadd/timeadd/timestampadd. The unit argument
         * cannot be a variant, but the other two arguments cast to
         * NUMBER(9, 0) and TIMESTAMP_NTZ.
         *
         * Note: for the time being the number argument is casted to an integer
         * type based on the precision. In future, we will switch this to use
         * the correct decimal type.
         */
        private val dateTimeAddCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            when (idx) {
                1 ->
                    factory.createTypeWithNullability(
                        factory.createSqlType(chooseIntegerType(9)),
                        inType.isNullable,
                    )
                2 ->
                    factory.createTypeWithNullability(
                        factory.createSqlType(SqlTypeName.TIMESTAMP),
                        inType.isNullable,
                    )
                else -> inType
            }
        }

        /**
         * Cast for datediff/timediff/timestampdiff. The unit argument
         * cannot be a variant, but the other two arguments cast to
         * TIMESTAMP_NTZ.
         */
        private val dateTimeDiffCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            when (idx) {
                1, 2 ->
                    factory.createTypeWithNullability(
                        factory.createSqlType(SqlTypeName.TIMESTAMP),
                        inType.isNullable,
                    )
                else -> inType
            }
        }

        /**
         * Cast for next_day/previous_day. The first argument
         * is cast to date, and the second to char.
         */
        private val nextPrevDayCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            if (idx == 0) { factory.createTypeWithNullability(
                factory.createSqlType(SqlTypeName.DATE),
                inType.isNullable,
            ) } else {
                factory.createTypeWithNullability(
                    factory.createSqlType(SqlTypeName.VARCHAR),
                    inType.isNullable,
                )
            }
        }

        /**
         * Cast for timestamp_from_parts family of functions, with the following
         * rules depending on the number of original operands:
         *
         * - 2 operands: FUNC(DATE, TIME)
         * - 6+ operands: FUNC(NUMBER(9, 0), NUMBER(9, 0), NUMBER(9, 0), NUMBER(9, 0), NUMBER(9, 0), NUMBER(9, 0), NUMBER(18, 0), CHAR)
         *
         * The 2-operand version is only allowed for the tz-naive versions of this function, but validation will
         * reject those earlier so there is no need to add such a check to this implicit casting rule.
         *
         * Note: for the time being the number arguments are casted to an integer
         * type based on the precision. In future, we will switch this to use
         * the correct decimal type.
         */
        private val timestampPartsCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, operandTypes: List<RelDataType> ->
            if (operandTypes.size == 2) {
                val typeName = if (idx == 0) { SqlTypeName.DATE } else { SqlTypeName.TIME }
                factory.createTypeWithNullability(
                    factory.createSqlType(typeName),
                    inType.isNullable,
                )
            } else {
                val typeName = when (idx) {
                    6 -> SqlTypeName.BIGINT
                    7 -> SqlTypeName.CHAR
                    else -> SqlTypeName.INTEGER
                }
                factory.createTypeWithNullability(
                    factory.createSqlType(typeName),
                    inType.isNullable,
                )
            }
        }

        // Helper function to create a RelDataType for MAP[varchar, variant] with the given nullability
        private val makeMapType = { factory: RelDataTypeFactory, nullable: Boolean ->
            factory.createTypeWithNullability(
                factory.createMapType(
                    factory.createSqlType(SqlTypeName.VARCHAR),
                    BodoRelDataTypeFactory.createVariantSqlType(factory),
                ),
                nullable,
            )
        }

        /**
         * Cast all arguments to MAP[varchar, variant]
         */
        private val anyArgMapCast = {
                inType: RelDataType, factory: RelDataTypeFactory, _: Int, _: List<RelDataType> ->
            makeMapType(factory, inType.isNullable)
        }

        /**
         * Cast for OBJECT_INSERT function, with the following rule:
         *
         * FUNC(object, char, any)
         */
        private val objectInsertCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            when (idx) {
                0 -> makeMapType(factory, inType.isNullable)
                1 -> factory.createTypeWithNullability(
                    factory.createSqlType(SqlTypeName.VARCHAR),
                    inType.isNullable,
                )
                else -> inType
            }
        }

        /**
         * Cast for OBJECT_DELETE/OBJECT_PICK functions, with the following rule:
         *
         * FUNC(object, char[, char, ...])
         */
        private val objectPickDeleteCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            when (idx) {
                0 -> makeMapType(factory, inType.isNullable)
                else -> factory.createTypeWithNullability(
                    factory.createSqlType(SqlTypeName.VARCHAR),
                    inType.isNullable,
                )
            }
        }

        /**
         * Cast for (VARCHAR, VARCHAR, BIGINT, VARCHAR).
         */
        private val varcharVarcharBigIntVarcharCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            varcharNumericHelper(inType, factory, idx, 4, setOf(0, 1, 3), SqlTypeName.BIGINT)
        }

        /**
         * Cast for REGEXP_SUBSTR.
         *
         * REGEXP_SUBSTR casts to a function signature
         * (VARCHAR, VARCHAR, BIGINT, BIGINT, VARCHAR, BIGINT)
         */
        private val regexpSubstrCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            varcharNumericHelper(inType, factory, idx, 6, setOf(0, 1, 4), SqlTypeName.BIGINT)
        }

        /**
         * Cast for REGEXP_REPLACE.
         *
         * REGEXP_REPLACE casts to a function signature
         * (VARCHAR, VARCHAR, VARCHAR, BIGINT, BIGINT, VARCHAR)
         */
        private val regexpReplaceCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            varcharNumericHelper(inType, factory, idx, 6, setOf(0, 1, 2, 5), SqlTypeName.BIGINT)
        }

        /**
         * Cast for REGEXP_INSTR.
         *
         * REGEXP_INSTR casts to a function signature
         * (VARCHAR, VARCHAR, BIGINT, BIGINT, BIGINT, VARCHAR, BIGINT)
         */
        private val regexpInstrCast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, _: List<RelDataType> ->
            varcharNumericHelper(inType, factory, idx, 7, setOf(0, 1, 5), SqlTypeName.BIGINT)
        }

        private val arg0ToArg1Cast = {
                inType: RelDataType, factory: RelDataTypeFactory, idx: Int, operandTypes: List<RelDataType> ->
            when (idx) {
                0 -> factory.createTypeWithNullability(operandTypes[1], inType.isNullable)
                else -> inType
            }
        }

        /**
         * Mapping of function names to a lambda function used to derive the default variant type.
         *
         * TODO: Ensure these lists are exhaustive.
         */
        val variantNameMapping = mapOf(
            ArrayOperatorTable.ARRAY_EXCEPT to anyArgArrayCast,
            ArrayOperatorTable.ARRAY_INTERSECTION to anyArgArrayCast,
            ArrayOperatorTable.ARRAYS_OVERLAP to anyArgArrayCast,
            ArrayOperatorTable.ARRAY_REMOVE to arg0ArrayCast,
            ArrayOperatorTable.ARRAY_REMOVE_AT to arg0ArrayRestNumber9Cast,
            ArrayOperatorTable.ARRAY_SLICE to arg0ArrayRestNumber9Cast,
            AggOperatorTable.BOOLOR_AGG to anyArgBooleanCast,
            AggOperatorTable.BOOLAND_AGG to anyArgBooleanCast,
            AggOperatorTable.BOOLXOR_AGG to anyArgBooleanCast,
            AggOperatorTable.CONDITIONAL_TRUE_EVENT to anyArgBooleanCast,
            DatetimeOperatorTable.DAYNAME to anyArgDateCast,
            DatetimeOperatorTable.MONTHNAME to anyArgDateCast,
            DatetimeOperatorTable.MONTH_NAME to anyArgDateCast,
            AggOperatorTable.OBJECT_AGG to arg0VarcharCast,
            ObjectOperatorTable.JSON_EXTRACT_PATH_TEXT to anyArgVarcharCast,
            StringOperatorTable.REPEAT to varcharIntegerCast,
            SqlAggOperatorTable.LISTAGG to arg0VarcharCast,
            SqlStdOperatorTable.AND to anyArgBooleanCast,
            SqlStdOperatorTable.OR to anyArgBooleanCast,
            SqlStdOperatorTable.NOT to anyArgBooleanCast,
            SqlStdOperatorTable.LOWER to anyArgVarcharCast,
            SqlStdOperatorTable.UPPER to anyArgVarcharCast,
            SqlStdOperatorTable.TRIM to anyArgVarcharCast,
            SqlStdOperatorTable.ASCII to anyArgVarcharCast,
            SqlStdOperatorTable.CHAR_LENGTH to anyArgVarcharCast,
            SqlStdOperatorTable.CHARACTER_LENGTH to anyArgVarcharCast,
            StringOperatorTable.LEN to anyArgVarcharCast,
            StringOperatorTable.CONCAT_WS to anyArgVarcharCast,
            StringOperatorTable.CONCAT to anyArgVarcharCast,
            SqlStdOperatorTable.CONCAT to anyArgVarcharCast,
            StringOperatorTable.LENGTH to anyArgVarcharCast,
            StringOperatorTable.LTRIM to anyArgVarcharCast,
            StringOperatorTable.REVERSE to anyArgVarcharCast,
            StringOperatorTable.RTRIM to anyArgVarcharCast,
            StringOperatorTable.RTRIMMED_LENGTH to anyArgVarcharCast,
            StringOperatorTable.INITCAP to anyArgVarcharCast,
            StringOperatorTable.MD5 to anyArgVarcharCast,
            StringOperatorTable.MD5_HEX to anyArgVarcharCast,
            StringOperatorTable.STARTSWITH to anyArgVarcharCast,
            StringOperatorTable.ENDSWITH to anyArgVarcharCast,
            StringOperatorTable.LPAD to padCasting,
            StringOperatorTable.RPAD to padCasting,
            SqlStdOperatorTable.SUM to anyArgDoubleCast,
            SqlStdOperatorTable.SUM0 to anyArgDoubleCast,
            SqlStdOperatorTable.PLUS to anyArgDoubleCast,
            SqlStdOperatorTable.MINUS to anyArgDoubleCast,
            SqlStdOperatorTable.MULTIPLY to anyArgDoubleCast,
            SqlStdOperatorTable.DIVIDE to anyArgDoubleCast,
            SqlStdOperatorTable.ACOS to anyArgDoubleCast,
            NumericOperatorTable.ACOSH to anyArgDoubleCast,
            SqlStdOperatorTable.ASIN to anyArgDoubleCast,
            NumericOperatorTable.ASINH to anyArgDoubleCast,
            SqlStdOperatorTable.ATAN to anyArgDoubleCast,
            SqlStdOperatorTable.ATAN2 to anyArgDoubleCast,
            NumericOperatorTable.ATANH to anyArgDoubleCast,
            SqlStdOperatorTable.CBRT to anyArgDoubleCast,
            SqlStdOperatorTable.COS to anyArgDoubleCast,
            NumericOperatorTable.COSH to anyArgDoubleCast,
            SqlStdOperatorTable.COT to anyArgDoubleCast,
            SqlStdOperatorTable.DEGREES to anyArgDoubleCast,
            NumericOperatorTable.DIV0 to anyArgDoubleCast,
            NumericOperatorTable.DIV0NULL to anyArgDoubleCast,
            SqlStdOperatorTable.EXP to anyArgDoubleCast,
            NumericOperatorTable.HAVERSINE to anyArgDoubleCast,
            SqlStdOperatorTable.LN to anyArgDoubleCast,
            NumericOperatorTable.LOG to anyArgDoubleCast,
            NumericOperatorTable.POW to anyArgDoubleCast,
            SqlStdOperatorTable.POWER to anyArgDoubleCast,
            SqlStdOperatorTable.RADIANS to anyArgDoubleCast,
            CondOperatorTable.REGR_VALX to anyArgDoubleCast,
            CondOperatorTable.REGR_VALY to anyArgDoubleCast,
            SqlStdOperatorTable.SIGN to anyArgDoubleCast,
            SqlStdOperatorTable.SIN to anyArgDoubleCast,
            NumericOperatorTable.SINH to anyArgDoubleCast,
            SqlStdOperatorTable.SQRT to anyArgDoubleCast,
            NumericOperatorTable.SQUARE to anyArgDoubleCast,
            SqlStdOperatorTable.TAN to anyArgDoubleCast,
            NumericOperatorTable.TANH to anyArgDoubleCast,
            SqlStdOperatorTable.ABS to anyArgDoubleCast,
            SqlStdOperatorTable.AVG to anyArgDoubleCast,
            AggOperatorTable.CORR to anyArgDoubleCast,
            SqlStdOperatorTable.COVAR_POP to anyArgDoubleCast,
            SqlStdOperatorTable.COVAR_SAMP to anyArgDoubleCast,
            AggOperatorTable.KURTOSIS to anyArgDoubleCast,
            AggOperatorTable.RATIO_TO_REPORT to anyArgDoubleCast,
            AggOperatorTable.SKEW to anyArgDoubleCast,
            SqlStdOperatorTable.STDDEV to anyArgDoubleCast,
            SqlStdOperatorTable.STDDEV_POP to anyArgDoubleCast,
            SqlStdOperatorTable.STDDEV_SAMP to anyArgDoubleCast,
            SqlStdOperatorTable.VARIANCE to anyArgDoubleCast,
            SqlStdOperatorTable.VAR_SAMP to anyArgDoubleCast,
            SqlStdOperatorTable.VAR_POP to anyArgDoubleCast,
            AggOperatorTable.VARIANCE_POP to anyArgDoubleCast,
            AggOperatorTable.VARIANCE_SAMP to anyArgDoubleCast,
            CondOperatorTable.ZEROIFNULL to anyArgDoubleCast,
            StringOperatorTable.CHAR to anyArgIntegerCast,
            StringOperatorTable.CHR to anyArgIntegerCast,
            StringOperatorTable.SPACE to anyArgIntegerCast,
            StringOperatorTable.SPLIT to anyArgVarcharCast,
            ObjectOperatorTable.PARSE_JSON to anyArgVarcharCast,
            StringOperatorTable.STRTOK to varcharVarcharIntegerCast,
            StringOperatorTable.STRTOK_TO_ARRAY to anyArgVarcharCast,
            StringOperatorTable.SPLIT_PART to varcharVarcharIntegerCast,
            SqlStdOperatorTable.LIKE to anyArgVarcharCast,
            SqlStdOperatorTable.NOT_LIKE to anyArgVarcharCast,
            SqlLibraryOperators.ILIKE to anyArgVarcharCast,
            SqlLibraryOperators.NOT_ILIKE to anyArgVarcharCast,
            SqlLibraryOperators.RLIKE to anyArgVarcharCast,
            SqlLibraryOperators.NOT_RLIKE to anyArgVarcharCast,
            SqlBodoOperatorTable.ANY_LIKE to arg0VarcharCast,
            SqlBodoOperatorTable.ANY_ILIKE to arg0VarcharCast,
            SqlBodoOperatorTable.ALL_LIKE to arg0VarcharCast,
            SqlBodoOperatorTable.ALL_ILIKE to arg0VarcharCast,
            StringOperatorTable.SHA2 to arg0VarcharCast,
            StringOperatorTable.SHA2_HEX to arg0VarcharCast,
            StringOperatorTable.HEX_DECODE_STRING to arg0VarcharCast,
            StringOperatorTable.HEX_DECODE_BINARY to arg0VarcharCast,
            StringOperatorTable.TRY_HEX_DECODE_STRING to arg0VarcharCast,
            StringOperatorTable.TRY_HEX_DECODE_BINARY to arg0VarcharCast,
            StringOperatorTable.BASE64_ENCODE to arg0VarcharCast,
            StringOperatorTable.BASE64_DECODE_STRING to arg0VarcharCast,
            StringOperatorTable.BASE64_DECODE_BINARY to arg0VarcharCast,
            StringOperatorTable.TRY_BASE64_DECODE_STRING to arg0VarcharCast,
            StringOperatorTable.TRY_BASE64_DECODE_BINARY to arg0VarcharCast,
            StringOperatorTable.HEX_ENCODE to varcharTinyintCast,
            StringOperatorTable.LEFT to varcharIntegerCast,
            StringOperatorTable.RIGHT to varcharIntegerCast,
            StringOperatorTable.CONTAINS to anyArgVarcharCast,
            StringOperatorTable.JAROWINKLER_SIMILARITY to anyArgVarcharCast,
            SqlLibraryOperators.TRANSLATE3 to anyArgVarcharCast,
            StringOperatorTable.CHARINDEX to varcharVarcharIntegerCast,
            StringOperatorTable.EDITDISTANCE to varcharVarcharIntegerCast,
            SqlStdOperatorTable.POSITION to varcharVarcharIntegerCast,
            SqlStdOperatorTable.REPLACE to anyArgVarcharCast,
            SqlStdOperatorTable.SUBSTRING to varcharBigintBigintCast,
            StringOperatorTable.SUBSTR to varcharBigintBigintCast,
            StringOperatorTable.INSERT to insertCast,
            StringOperatorTable.REGEXP_LIKE to anyArgVarcharCast,
            StringOperatorTable.REGEXP_COUNT to varcharVarcharBigIntVarcharCast,
            StringOperatorTable.REGEXP_SUBSTR to regexpSubstrCast,
            StringOperatorTable.REGEXP_INSTR to regexpInstrCast,
            StringOperatorTable.REGEXP_REPLACE to regexpReplaceCast,
            AggOperatorTable.BITAND_AGG to anyArgNumberCast(18),
            AggOperatorTable.BITOR_AGG to anyArgNumberCast(18),
            AggOperatorTable.BITXOR_AGG to anyArgNumberCast(18),
            NumericOperatorTable.BITAND to anyArgNumberCast(18, 18),
            NumericOperatorTable.BITOR to anyArgNumberCast(18, 18),
            NumericOperatorTable.BITXOR to anyArgNumberCast(18, 18),
            NumericOperatorTable.BITNOT to anyArgNumberCast(18),
            NumericOperatorTable.BITSHIFTLEFT to anyArgNumberCast(18, 18),
            NumericOperatorTable.BITSHIFTRIGHT to anyArgNumberCast(18, 18),
            NumericOperatorTable.GETBIT to anyArgNumberCast(18, 18),
            NumericOperatorTable.FACTORIAL to anyArgNumberCast(2),
            CondOperatorTable.BOOLAND to anyArgNumberCast(18, 18),
            CondOperatorTable.BOOLOR to anyArgNumberCast(18, 18),
            CondOperatorTable.BOOLXOR to anyArgNumberCast(18, 18),
            CondOperatorTable.BOOLNOT to anyArgNumberCast(18),
            CondOperatorTable.NULLIF to arg0ToArg1Cast,
            DatetimeOperatorTable.DATE_FROM_PARTS to anyArgNumberCast(9, 9, 9),
            DatetimeOperatorTable.DATEFROMPARTS to anyArgNumberCast(9, 9, 9),
            DatetimeOperatorTable.TIME_FROM_PARTS to anyArgNumberCast(9, 9, 9, 18),
            DatetimeOperatorTable.TIMEFROMPARTS to anyArgNumberCast(9, 9, 9, 18),
            AggOperatorTable.MEDIAN to anyArgNumberCast(9),
            DatetimeOperatorTable.DATEADD to dateTimeAddCast,
            DatetimeOperatorTable.TIMEADD to dateTimeAddCast,
            SqlBodoOperatorTable.TIMESTAMP_ADD to dateTimeAddCast,
            DatetimeOperatorTable.DATEDIFF to dateTimeDiffCast,
            DatetimeOperatorTable.TIMEDIFF to dateTimeDiffCast,
            SqlBodoOperatorTable.TIMESTAMP_DIFF to dateTimeDiffCast,
            DatetimeOperatorTable.PREVIOUS_DAY to nextPrevDayCast,
            DatetimeOperatorTable.NEXT_DAY to nextPrevDayCast,
            DatetimeOperatorTable.TIMESTAMP_FROM_PARTS to timestampPartsCast,
            DatetimeOperatorTable.TIMESTAMP_NTZ_FROM_PARTS to timestampPartsCast,
            DatetimeOperatorTable.TIMESTAMP_LTZ_FROM_PARTS to timestampPartsCast,
            DatetimeOperatorTable.TIMESTAMP_TZ_FROM_PARTS to timestampPartsCast,
            DatetimeOperatorTable.TIMESTAMPFROMPARTS to timestampPartsCast,
            DatetimeOperatorTable.TIMESTAMPNTZFROMPARTS to timestampPartsCast,
            DatetimeOperatorTable.TIMESTAMPLTZFROMPARTS to timestampPartsCast,
            DatetimeOperatorTable.TIMESTAMPTZFROMPARTS to timestampPartsCast,
            ObjectOperatorTable.OBJECT_KEYS to anyArgMapCast,
            ObjectOperatorTable.OBJECT_INSERT to objectInsertCast,
            ObjectOperatorTable.OBJECT_PICK to objectPickDeleteCast,
            ObjectOperatorTable.OBJECT_DELETE to objectPickDeleteCast,
            ArrayOperatorTable.ARRAY_MAP_GET to arg1StringCast,
            ArrayOperatorTable.ARRAY_MAP_GET_BRACKET to arg1StringCast,
            ArrayOperatorTable.ARRAY_POSITION to arg1ArrayCast,
            ArrayOperatorTable.ARRAY_CONTAINS to arg1ArrayCast,
            ArrayOperatorTable.ARRAY_TO_STRING to arg1StringCast,
        ).mapKeys { it.key.name }
    }
}
