package com.bodosql.calcite.rel.metadata

import com.bodosql.calcite.adapter.iceberg.IcebergTableScan
import com.bodosql.calcite.adapter.iceberg.IcebergToBodoPhysicalConverter
import com.bodosql.calcite.adapter.pandas.PandasTableScan
import com.bodosql.calcite.adapter.snowflake.SnowflakeTableScan
import com.bodosql.calcite.adapter.snowflake.SnowflakeToBodoPhysicalConverter
import com.bodosql.calcite.application.operatorTables.CondOperatorTable
import com.bodosql.calcite.application.operatorTables.StringOperatorTable
import com.bodosql.calcite.application.utils.IsScalar
import com.bodosql.calcite.rel.core.CachedSubPlanBase
import com.bodosql.calcite.rel.core.Flatten
import com.bodosql.calcite.rel.core.MinRowNumberFilterBase
import com.bodosql.calcite.rel.core.WindowBase
import com.bodosql.calcite.table.BodoSqlTable
import org.apache.calcite.plan.RelOptUtil
import org.apache.calcite.plan.volcano.RelSubset
import org.apache.calcite.prepare.RelOptTableImpl
import org.apache.calcite.rel.RelNode
import org.apache.calcite.rel.SingleRel
import org.apache.calcite.rel.core.Aggregate
import org.apache.calcite.rel.core.Filter
import org.apache.calcite.rel.core.Join
import org.apache.calcite.rel.core.Project
import org.apache.calcite.rel.core.Union
import org.apache.calcite.rel.metadata.MetadataDef
import org.apache.calcite.rel.metadata.MetadataHandler
import org.apache.calcite.rel.metadata.RelMetadataQuery
import org.apache.calcite.rex.RexCall
import org.apache.calcite.rex.RexInputRef
import org.apache.calcite.rex.RexLiteral
import org.apache.calcite.rex.RexNode
import org.apache.calcite.rex.RexOver
import org.apache.calcite.rex.RexUtil
import org.apache.calcite.sql.SqlKind
import org.apache.calcite.sql.`fun`.SqlStdOperatorTable
import org.apache.calcite.sql.type.SqlTypeName
import org.apache.calcite.util.ImmutableBitSet
import java.math.BigDecimal

class BodoRelMdColumnDistinctCount : MetadataHandler<ColumnDistinctCount> {
    override fun getDef(): MetadataDef<ColumnDistinctCount> = ColumnDistinctCount.DEF

    /** Catch-all implementation for
     * [ColumnDistinctCount.getColumnDistinctCount],
     * invoked using reflection.
     *
     * By default, we only return information is we can ensure the column is unique.
     *
     * @see ColumnDistinctCount
     */
    fun getColumnDistinctCount(
        rel: RelNode,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val isUnique = mq.areColumnsUnique(rel, ImmutableBitSet.of(column))
        return if (isUnique != null && isUnique) {
            mq.getRowCount(rel)
        } else {
            null
        }
    }

    /**
     * Get column distinct count for a column produced by a window rel node. This takes one of
     * two paths:
     * - If the requested column is a pass-through reference, forward the distinctness request to
     *   the corresponding column of the child RelNode.
     * - If the requested column is a window function call, attempt to approximate its distinctness.
     */
    fun getColumnDistinctCount(
        window: WindowBase,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val numPassThroughCols = window.inputsToKeep.cardinality()
        return if (column < numPassThroughCols) {
            (mq as BodoRelMetadataQuery).getColumnDistinctCount(window.input, window.inputsToKeep.nth(column))
        } else {
            val asExpr = window.convertToProjExprs()[column]
            if (asExpr is RexOver) {
                getWindowCallDistinctCount(window.input, asExpr, (mq as BodoRelMetadataQuery))
            } else {
                null
            }
        }
    }

    /**
     * Return a guess for the number of distinct rows produced by a window function call if possible.
     * Currently supported on a limited number of functions depending on the nature of the
     * window group.
     *
     * @param input The child RelNode that all input refs refer to
     * @param over The window function call having its distinctness checked
     * @param mq The metadata query handler
     * @return An approximate guess for the number of distinct rows produced by the window function call,
     * or null if not one of the supported forms.
     */
    fun getWindowCallDistinctCount(
        input: RelNode,
        over: RexOver,
        mq: BodoRelMetadataQuery,
    ): Double? {
        return when (over.operator.kind) {
            SqlKind.MIN,
            SqlKind.MAX,
            SqlKind.MODE,
            SqlKind.FIRST_VALUE,
            SqlKind.ANY_VALUE,
            SqlKind.LAST_VALUE,
            SqlKind.NTH_VALUE,
            -> {
                /**
                 * FIRST_VALUE, LAST_VALUE, ANY_VALUE, NTH_VALUE, MIN, MAX, MODE without a window frame
                 *   select 1 value per partition, so the distinct count is the minimum of the original
                 *   distinct count & the number of distinct partitions. For first/last/any, only checks
                 *   the relevant side of the bound.
                 *   */
                val inputColumn = over.operands[0]
                val lowerCheck = listOf(SqlKind.LAST_VALUE).contains(over.operator.kind) || over.window.lowerBound.isUnbounded
                val upperCheck =
                    listOf(
                        SqlKind.FIRST_VALUE,
                        SqlKind.ANY_VALUE,
                        SqlKind.NTH_VALUE,
                    ).contains(over.operator.kind) ||
                        over.window.upperBound.isUnbounded
                if (inputColumn is RexInputRef && lowerCheck && upperCheck) {
                    val partitionKeys =
                        over.window.partitionKeys.map {
                            if (it is RexInputRef) {
                                it.index
                            } else {
                                throw Exception("Malformed window call $over")
                            }
                        }
                    val distinctRows = mq.getDistinctRowCount(input, ImmutableBitSet.of(partitionKeys), null)
                    val distinctInputs = mq.getColumnDistinctCount(input, inputColumn.index)
                    distinctInputs?.let { di -> distinctRows?.let { dr -> minOf(di, dr) } }
                } else {
                    null
                }
            }
            SqlKind.NTILE -> {
                // NTILE(n) always has n unique outputs
                val nBins = over.operands[0]
                if (nBins is RexLiteral) {
                    return nBins.getValueAs(BigDecimal::class.java)?.toDouble()
                } else {
                    null
                }
            }
            SqlKind.LEAD,
            SqlKind.LAG,
            -> {
                // LEAD and LAG approximately maintain distinctness (except for values cut off at the ends)
                val inputColumn = over.operands[0]
                return if (inputColumn is RexInputRef) {
                    mq.getColumnDistinctCount(input, inputColumn.index)
                } else {
                    null
                }
            }
            SqlKind.ROW_NUMBER -> {
                // ROW_NUMBER has at most as many distinct values as the largest partition
                // size, which can be approximated by rowCount divided by # of partitions
                val partitionKeys =
                    over.window.partitionKeys.map {
                        if (it is RexInputRef) {
                            it.index
                        } else {
                            throw Exception("Malformed window call $over")
                        }
                    }
                val distinctRows = mq.getDistinctRowCount(input, ImmutableBitSet.of(partitionKeys), null)
                val inRows = mq.getRowCount(input)
                distinctRows?.let { inRows / distinctRows }
            }
            // MIN_ROW_NUMBER_FILTER is a boolean, so it can only be true/false
            SqlKind.MIN_ROW_NUMBER_FILTER -> 2.0
            else -> null
        }
    }

    fun getColumnDistinctCount(
        subset: RelSubset,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? = (mq as BodoRelMetadataQuery).getColumnDistinctCount(subset.bestOrOriginal, column)

    fun getColumnDistinctCount(
        rel: Union,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val distinctCount = getColumnDistinctCount(rel as RelNode, mq, column)
        return if (distinctCount != null) {
            distinctCount
        } else {
            // Assume the worst case that all groups overlap, so we must take the max of any input.
            rel.inputs.map { r -> (mq as BodoRelMetadataQuery).getColumnDistinctCount(r, column) }.reduce { a, b ->
                if (a == null || b == null) {
                    null
                } else {
                    kotlin.math.max(a, b)
                }
            }
        }
    }

    /**
     * Estimates the number of distinct rows in a column after a filter, excluding any terms
     * in a conjunction that are IS NOT NULL filters on the column whose distinctness is
     * being requested.
     *
     * @param rel The filter that NDV values are being requested from.
     * @param mq The metadata query handler.
     * @param column The index of the column whose NDV values are being estimated.
     * @param distinctInput The estimated NDV value for the column before the filter.
     * @return If such a condition exists in the conjunction, returns the row count
     * estimate excluding that condition. Otherwise, returns null.
     */
    fun getNullFilterExcludingEstimate(
        rel: Filter,
        mq: RelMetadataQuery,
        column: Int,
        distinctInput: Double,
    ): Double? {
        val conjunctions = RelOptUtil.conjunctions(rel.condition)
        // Split into IS NOT NULL filters on the desired column versus everything else
        val (_, remainingFilters) =
            conjunctions.partition {
                it is RexCall &&
                    it.kind == SqlKind.IS_NOT_NULL &&
                    it.operands[0] is RexInputRef &&
                    (it.operands[0] as RexInputRef).index == column
            }
        // If all the filters were on the RHS, this function cannot help,
        // so we should return null.
        if (remainingFilters.size == conjunctions.size) return null
        val ratio =
            if (remainingFilters.isEmpty()) {
                1.0
            } else {
                val conjunctionNode = RexUtil.composeConjunction(rel.cluster.rexBuilder, remainingFilters)
                mq.getSelectivity(rel.input, conjunctionNode)
            }
        // Return the input rows multiplied by the selectivity ratio, then subtract 1 to account for null
        // being removed. This is safe since the output will be clamped between 1 and the row count.
        return if (ratio == null) {
            null
        } else {
            (distinctInput * ratio) - 1.0
        }
    }

    fun getColumnDistinctCount(
        rel: Filter,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val distinctCount = getColumnDistinctCount(rel as RelNode, mq, column)
        return if (distinctCount != null) {
            distinctCount
        } else {
            val distinctInput = (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.input, column)
            return distinctInput?.let {
                // If possible, identify the selectivity ratio that largely excludes IS NOT NULL filters
                // on the column that we are estimating the NDV for.
                val nullFilterExcludingEstimate = getNullFilterExcludingEstimate(rel, mq, column, distinctInput)
                if (nullFilterExcludingEstimate == null) {
                    // As a fallback, just use the ratio between the output and input row counts.
                    val outRows = mq.getRowCount(rel)
                    val ratio = mq.getRowCount(rel) / mq.getRowCount(rel.input)
                    return distinctInput.times(ratio)
                } else {
                    nullFilterExcludingEstimate
                }
            }
        }
    }

    fun getColumnDistinctCount(
        rel: MinRowNumberFilterBase,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val distinctCount = getColumnDistinctCount(rel as RelNode, mq, column)
        return if (distinctCount != null) {
            distinctCount
        } else {
            // First, transform the column to account for inputsToKeep
            val newColumn = rel.inputsToKeep.nth(column)
            val distinctInput = (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.input, newColumn)
            val outputRowCount = mq.getRowCount(rel)
            val inputRowCount = mq.getRowCount(rel.input)
            if (rel.partitionColSet.get(newColumn)) {
                // If the column is a partition column, its distinct count does not decrease
                return distinctInput
            } else {
                // For default filters assume the ratio remains the same after filtering.
                val ratio = outputRowCount / inputRowCount
                return distinctInput?.let { distinctInput.times(ratio) }
            }
        }
    }

    /**
     * Attempts to infer the distinctiveness of a call to CAST based on the distinctiveness
     * of its input.
     *
     * @param rel The original projection containing this rex node
     * @param rex The value being casted
     * @param mq The metadata query handler
     * @param targetType The type that rex gets casted to
     * @return The number of distinct rows produced by rex when casted, or null if we cannot infer it.
     */
    private fun inferCastDistinctiveness(
        rel: Project,
        rex: RexNode,
        mq: RelMetadataQuery,
        targetType: SqlTypeName,
    ): Double? {
        // For certain types, the output always matches the input's distinctiveness
        return when (targetType) {
            SqlTypeName.TIMESTAMP,
            SqlTypeName.TINYINT,
            SqlTypeName.SMALLINT,
            SqlTypeName.INTEGER,
            SqlTypeName.BIGINT,
            SqlTypeName.DECIMAL,
            SqlTypeName.FLOAT,
            SqlTypeName.REAL,
            SqlTypeName.DOUBLE,
            ->
                inferRexDistinctness(rel, rex, mq)
            else -> null
        }
    }

    /**
     * Infer the distinctiveness for concat. Currently, we only support the case where
     * all literals are being appended to at most 1 column containing compute. We do not attempt to
     * make any estimations as to how concatenating multiple columns impacts uniqueness.
     *
     * [BSE-2213] Investigate adding distinctness propagation for more BodoSQL functions.
     *
     * @param rel The original projection containing this rex node
     * @param operands The arguments being passed to the concat function.
     * @param mq The metadata query handler
     * @return The number of distinct rows produced by rex when concatenated, or null if we cannot infer it.
     */
    private fun inferConcatDistinctiveness(
        rel: Project,
        operands: List<RexNode>,
        mq: RelMetadataQuery,
    ): Double? {
        var index = -1
        for ((i, operand) in operands.withIndex()) {
            if (operand !is RexLiteral) {
                if (index != -1) {
                    // Multiple columns encountered. Return NULL.
                    return null
                }
                index = i
            }
        }
        // If its all literals there is a singular unique value.
        if (index == -1) {
            return 1.0
        }
        return inferRexDistinctness(rel, operands[index], mq)
    }

    /**
     * Infer the distinctiveness for CASE. All of the odd-numbered arguments (+ the last argument) are the outputs.
     * In the worst case scenario, the NDV is the sum of distinct values for each possible output. We attempt to fetch
     * the distinct values for each of these arguments, but return null if any of them  cannot be derived. We can
     * also ignore any output terms that are duplicates of each other.
     *
     * For example, consider the following RexNode:
     *
     * <code>CASE(COND_A, $0, COND_B, $1, COND_C $0, $2)<code>
     *
     * Then the outputs are $0, $1, $0, and $2, so our NDV approximation would be the sum of the NDV approximations for
     * $0, $1, and $2 (only counting $0 once).
     *
     * [BSE-2213] Investigate adding distinctness propagation for more BodoSQL functions.
     *
     * @param rel The original projection containing this rex node
     * @param operands The arguments being passed to the CASE function.
     * @param mq The metadata query handler
     * @return The number of distinct rows produced by CASE, or null if we cannot infer it.
     */
    private fun inferCaseDistinctness(
        rel: Project,
        operands: List<RexNode>,
        mq: RelMetadataQuery,
    ): Double? {
        val nOperands = operands.size
        // Get the operands from the correct locations corresponding to output arguments, and convert to a set
        // to remove all duplicates
        val outputArgs =
            operands
                .filterIndexed { idx, _ ->
                    (idx % 2 == 1) || (idx == nOperands - 1)
                }.toSet()
        // Get the distinct values of each of the possible outputs
        val outputDistinct = outputArgs.map { inferRexDistinctness(rel, it, mq) }
        // If any of the distinct values were null, it means that one of hte outputs has unknown approximate NDV,
        // so we cannot approximate the NDV of the entire CASE statement
        if (outputDistinct.any { it == null }) return null
        // Return the sum of the remaining values.
        return outputDistinct.reduce { x, y -> x!! + y!! }
    }

    /**
     * Attempts to infer the distinctiveness of a RexNode by inferring the distinctiveness
     * of the Input Ref(s) involved and either passing it through unmodified, or transforming
     * it somehow if it goes through a function call.
     *
     * @param rel The original projection containing this rex node
     * @param rex The rex node whose distinctiveness information we are trying to infer
     * @param mq The metadata query handler
     * @return The number of distinct rows produced by rex, or null if we cannot infer it.
     */
    private fun inferRexDistinctness(
        rel: Project,
        rex: RexNode,
        mq: RelMetadataQuery,
    ): Double? {
        // Base case: known scalar values have only 1 distinct value
        if (rex.accept(IsScalar())) {
            return 1.0
        }
        // Base case: once an InputRef is reached, its distinctiveness information is calculated
        // so that it can be propagated upward.
        if (rex is RexInputRef) {
            return (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.input, rex.index)
        }
        // Base case: booleans have exactly 2 unique values (3 if nullable)
        if (rex.type.sqlTypeName == SqlTypeName.BOOLEAN) {
            return 2.0 + (
                if (rex.type.isNullable) {
                    1.0
                } else {
                    0.0
                }
            )
        }
        if (rex is RexCall) {
            when (rex.kind) {
                SqlKind.SAFE_CAST,
                SqlKind.CAST,
                -> {
                    return inferCastDistinctiveness(rel, rex.operands[0], mq, rex.type.sqlTypeName)
                }
                SqlKind.CASE -> {
                    return inferCaseDistinctness(rel, rex.operands, mq)
                }
                SqlKind.OTHER_FUNCTION -> {
                    val concatFunctions = listOf(StringOperatorTable.CONCAT.name, StringOperatorTable.CONCAT_WS.name)
                    if (concatFunctions.contains(rex.operator.name)) {
                        return inferConcatDistinctiveness(rel, rex.operands, mq)
                    }
                }
                SqlKind.OTHER,
                SqlKind.OTHER_FUNCTION,
                -> {
                    if (rex.operator.name == SqlStdOperatorTable.CONCAT.name) {
                        return inferConcatDistinctiveness(rel, rex.operands, mq)
                    }
                    if (rex.operator.name == CondOperatorTable.IFF_FUNC.name) {
                        return inferCaseDistinctness(rel, rex.operands, mq)
                    }
                }

                else -> {}
            }
        }
        return null
    }

    fun getColumnDistinctCount(
        rel: Project,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val distinctCount = getColumnDistinctCount(rel as RelNode, mq, column)
        return if (distinctCount != null) {
            distinctCount
        } else {
            val columnNode = rel.projects[column]
            return inferRexDistinctness(rel, columnNode, mq)
        }
    }

    fun getColumnDistinctCount(
        rel: SingleRel,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? = (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.input, column)

    fun getColumnDistinctCount(
        rel: Join,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val distinctCount = getColumnDistinctCount(rel as RelNode, mq, column)
        return if (distinctCount != null) {
            distinctCount
        } else {
            val leftCount = rel.left.rowType.fieldCount
            val isLeftInput = column < leftCount
            // For join assume an unchanged ratio and fetch the inputs.
            val input =
                if (isLeftInput) {
                    rel.left
                } else {
                    rel.right
                }
            val inputColumn =
                if (isLeftInput) {
                    column
                } else {
                    column - leftCount
                }
            // 1.0 if the join can create nulls in this column, otherwise 0.0.
            val extraValue =
                if ((isLeftInput && rel.joinType.generatesNullsOnLeft()) ||
                    (!isLeftInput && rel.joinType.generatesNullsOnRight())
                ) {
                    1.0
                } else {
                    0.0
                }

            val distinctInput = (mq as BodoRelMetadataQuery).getColumnDistinctCount(input, inputColumn)
            val expectedRowCount = mq.getRowCount(rel)
            // If we have an outer join you cannot decrease the number of distinct values
            // unless you add NULL.
            return if ((isLeftInput && rel.joinType.generatesNullsOnRight()) || (!isLeftInput && rel.joinType.generatesNullsOnLeft())) {
                // Note: Add a sanity check that we never exceed the expected row count.
                distinctInput?.let { distinctInput + extraValue }
            } else {
                // Assume the ratio remains the same after filtering with the caveat that the number
                // of distinct rows cannot increase as a result of joining, except for one new value
                // that could be introduced as the result of creating nulls.
                val ratio = minOf(expectedRowCount / mq.getRowCount(input), 1.0)
                distinctInput?.let { distinctInput.times(ratio) + extraValue }
            }
        }
    }

    fun getColumnDistinctCount(
        rel: Aggregate,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val distinctCount = getColumnDistinctCount(rel as RelNode, mq, column)
        return if (distinctCount != null) {
            distinctCount
        } else {
            val groupSetList = rel.groupSet.asList()
            return if (groupSetList.size == 0) {
                1.0
            } else if (column >= rel.groupSet.asList().size &&
                rel.aggCallList[column - rel.groupSet.asList().size].aggregation.kind == SqlKind.LITERAL_AGG
            ) {
                // A LITERAL_AGG is always a single value.
                return 1.0
            } else if (rel.groupSets.size != 1 || column >= rel.groupSet.asList().size) {
                return null
            } else {
                // The number of distinct rows in any grouping key the same as the input
                val inputColumn = rel.groupSet.asList()[column]
                return (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.input, inputColumn)
            }
        }
    }

    fun getColumnDistinctCount(
        rel: SnowflakeToBodoPhysicalConverter,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? = (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.input, column)

    fun getColumnDistinctCount(
        rel: IcebergToBodoPhysicalConverter,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? = (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.input, column)

    fun getColumnDistinctCount(
        rel: SnowflakeTableScan,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val trueCol = rel.keptColumns.nth(column)
        return rel.getCatalogTable().getColumnDistinctCount(trueCol)
    }

    fun getColumnDistinctCount(
        rel: IcebergTableScan,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val trueCol = rel.keptColumns.nth(column)
        return rel.getCatalogTable().getColumnDistinctCount(trueCol)
    }

    fun getColumnDistinctCount(
        rel: PandasTableScan,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? = ((rel.table as RelOptTableImpl).table() as BodoSqlTable).getColumnDistinctCount(column)

    fun getColumnDistinctCount(
        rel: Flatten,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? {
        val nonNullEstimate =
            if (rel.rowType.fieldList[column]
                    .type.isNullable
            ) {
                0.9
            } else {
                1.0
            }
        val offset = rel.usedColOutputs.cardinality()
        return if (column >= offset) {
            (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.input, rel.repeatColumns.nth(column - offset))?.times(nonNullEstimate)
        } else {
            null
        }
    }

    fun getColumnDistinctCount(
        rel: CachedSubPlanBase,
        mq: RelMetadataQuery,
        column: Int,
    ): Double? = (mq as BodoRelMetadataQuery).getColumnDistinctCount(rel.cachedPlan.plan, column)
}
