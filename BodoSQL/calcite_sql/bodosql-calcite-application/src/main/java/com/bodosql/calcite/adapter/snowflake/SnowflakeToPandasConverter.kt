package com.bodosql.calcite.adapter.snowflake

import com.bodosql.calcite.adapter.pandas.PandasRel
import com.bodosql.calcite.ir.Dataframe
import com.bodosql.calcite.ir.Expr
import com.bodosql.calcite.ir.Module
import com.bodosql.calcite.ir.Op
import com.bodosql.calcite.plan.makeCost
import org.apache.calcite.plan.*
import org.apache.calcite.rel.RelNode
import org.apache.calcite.rel.convert.ConverterImpl
import org.apache.calcite.rel.metadata.RelMetadataQuery
import org.apache.calcite.rel.rel2sql.RelToSqlConverter
import org.apache.calcite.rel.type.RelDataType
import org.apache.calcite.rel.type.RelDataTypeFieldImpl
import org.apache.calcite.rel.type.RelRecordType

class SnowflakeToPandasConverter(cluster: RelOptCluster, traits: RelTraitSet, input: RelNode) :
    ConverterImpl(cluster, ConventionTraitDef.INSTANCE, traits, input), PandasRel {

    override fun copy(traitSet: RelTraitSet, inputs: List<RelNode>): RelNode {
        return SnowflakeToPandasConverter(cluster, traitSet, sole(inputs))
    }

    /**
     * Even if SnowflakeToPandasConverter is a PandasRel, it is still
     * a single process operation which the other ranks wait on and doesn't
     * benefit from parallelism.
     */
    override fun splitCount(numRanks: Int): Int = 1

    override fun computeSelfCost(planner: RelOptPlanner, mq: RelMetadataQuery): RelOptCost? {
        // Determine the average row size which determines how much data is returned.
        // If this data isn't available, just assume something like 4 bytes for each column.
        val averageRowSize = mq.getAverageRowSize(this)
            ?: (4.0 * getRowType().fieldCount)

        // Complete data size using the row count.
        val rows = mq.getRowCount(this);
        val dataSize = averageRowSize * rows

        // Determine parallelism or default to no parallelism.
        val parallelism = mq.splitCount(this) ?: 1

        // IO and memory are related to the number of bytes returned.
        val io = dataSize / parallelism
        return planner.makeCost(rows = rows, io = io, mem = io)
    }

    /**
     * While snowflake has its own casing convention, the pandas code will
     * convert these names to another casing to match with existing conventions
     * within sqlalchemy.
     *
     * We modify that casing here by changing the field names of the derived type.
     */
    override fun deriveRowType(): RelDataType {
        return RelRecordType(super.deriveRowType().fieldList.map { field ->
            val name = if (field.name.equals(field.name.uppercase())) {
                field.name.lowercase()
            } else field.name
            RelDataTypeFieldImpl(name, field.index, field.type)
        })
    }

    override fun emit(builder: Module.Builder, inputs: () -> List<Dataframe>): Dataframe {
        // Use the snowflake dialect for generating the sql string.
        val rel2sql = RelToSqlConverter(SnowflakeSqlDialect.DEFAULT)
        val sql = rel2sql.visitRoot(input)
            .asSelect()
            .toSqlString { c ->
                c.withClauseStartsLine(false)
                    .withDialect(SnowflakeSqlDialect.DEFAULT)
            }
            .toString()

        val input = input as SnowflakeRel
        val expr = Expr.Call(
            "pd.read_sql",
            args = listOf(
                // First argument is the sql. This should escape itself.
                Expr.StringLiteral(sql),
                // Second argument is the connection string.
                // We don't use a schema name because we've already fully qualified
                // all table references and it's better if this doesn't have any
                // potentially unexpected behavior.
                Expr.StringLiteral(input.generatePythonConnStr("")),
            ),
            namedArgs = listOf(
                // Special parameter to read date as dt64.
                "_bodo_read_date_as_dt64" to Expr.BooleanLiteral(builder.useDateRuntime),
                // We do not include _bodo_is_table_input because this is not a table input.
            )
        )

        val df = builder.genDataframe(this)
        builder.add(Op.Assign(df.variable, expr))
        return df
    }
}