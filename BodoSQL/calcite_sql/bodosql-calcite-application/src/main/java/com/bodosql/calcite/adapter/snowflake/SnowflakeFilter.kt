package com.bodosql.calcite.adapter.snowflake

import com.bodosql.calcite.adapter.pandas.PandasRel
import com.bodosql.calcite.ir.Dataframe
import com.bodosql.calcite.ir.Module
import com.bodosql.calcite.table.CatalogTableImpl
import org.apache.calcite.plan.RelOptCluster
import org.apache.calcite.plan.RelTraitSet
import org.apache.calcite.rel.RelNode
import org.apache.calcite.rel.core.Filter
import org.apache.calcite.rex.RexNode

class SnowflakeFilter(cluster: RelOptCluster?, traitSet: RelTraitSet?, input: RelNode, condition: RexNode, val catalogTable: CatalogTableImpl) :
    Filter(cluster, traitSet, input, condition), PandasRel {
    override fun copy(traitSet: RelTraitSet?, input: RelNode, condition: RexNode): Filter {
        return SnowflakeFilter(cluster, traitSet, input, condition, catalogTable)
    }

    override fun emit(builder: Module.Builder, inputs: () -> List<Dataframe>): Dataframe {
        TODO("Don't implement")
    }
}