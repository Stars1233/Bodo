package com.bodosql.calcite.adapter.snowflake

import com.bodosql.calcite.adapter.pandas.PandasProject
import com.bodosql.calcite.adapter.pandas.PandasRel
import org.apache.calcite.plan.RelRule
import org.apache.calcite.rel.RelNode
import org.apache.calcite.rel.convert.ConverterRule
import org.apache.calcite.rex.RexInputRef

class SnowflakeToPandasConverterRule private constructor(config: Config) : ConverterRule(config) {
    companion object {
        @JvmField
        val DEFAULT_CONFIG: Config = Config.INSTANCE
            .withConversion(RelNode::class.java, SnowflakeRel.CONVENTION,
                PandasRel.CONVENTION, "SnowflakeToPandasConverterRule")
            .withRuleFactory { config -> SnowflakeToPandasConverterRule(config) }
    }

    override fun convert(rel: RelNode): RelNode {
        val newTraitSet = rel.traitSet.replace(outConvention)
        val converter = SnowflakeToPandasConverter(rel.cluster, newTraitSet, rel)
        // In addition to the converter, add a projection to return the type
        // to the original type of the input relation.
        val projects = rel.rowType.fieldList.mapIndexed { index, field ->
            RexInputRef(index, field.type)
        }
        return PandasProject.create(converter, projects, rel.rowType)
    }
}