package com.bodosql.calcite.adapter.snowflake

import com.bodosql.calcite.catalog.SnowflakeCatalogImpl
import com.bodosql.calcite.table.CatalogTableImpl
import com.bodosql.calcite.traits.BatchingProperty
import org.apache.calcite.plan.RelOptCluster
import org.apache.calcite.plan.RelTraitSet
import org.apache.calcite.rel.RelCollation
import org.apache.calcite.rel.RelNode
import org.apache.calcite.rel.core.Sort
import org.apache.calcite.rex.RexNode

class SnowflakeSort private constructor(
    cluster: RelOptCluster,
    traitSet: RelTraitSet,
    input: RelNode,
    collation: RelCollation,
    offset: RexNode?,
    fetch: RexNode?,
    val catalogTable: CatalogTableImpl,
) :
    Sort(cluster, traitSet, input, collation, offset, fetch), SnowflakeRel {

    override fun copy(
        traitSet: RelTraitSet,
        newInput: RelNode,
        newCollation: RelCollation,
        offset: RexNode?,
        fetch: RexNode?
    ): Sort {
        return SnowflakeSort(cluster, traitSet, newInput, newCollation, offset, fetch, catalogTable)
    }

    override fun generatePythonConnStr(schema: String): String {
        val catalog = catalogTable.catalog as SnowflakeCatalogImpl
        return catalog.generatePythonConnStr(schema)
    }

    companion object {
        @JvmStatic
        fun create(
            cluster: RelOptCluster,
            traitSet: RelTraitSet,
            input: RelNode,
            collation: RelCollation,
            offset: RexNode?,
            fetch: RexNode?,
            catalogTable: CatalogTableImpl
        ): SnowflakeSort {
            val newTraitSet = traitSet.replace(SnowflakeRel.CONVENTION).replace(BatchingProperty.STREAMING)
            return SnowflakeSort(cluster, newTraitSet, input, collation, offset, fetch, catalogTable)
        }
    }
}