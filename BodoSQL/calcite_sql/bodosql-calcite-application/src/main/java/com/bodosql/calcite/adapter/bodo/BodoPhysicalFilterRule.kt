package com.bodosql.calcite.adapter.bodo

import com.bodosql.calcite.rel.logical.BodoLogicalFilter
import org.apache.calcite.plan.Convention
import org.apache.calcite.rel.RelNode
import org.apache.calcite.rel.convert.ConverterRule
import org.apache.calcite.rel.core.Filter

class BodoPhysicalFilterRule private constructor(
    config: Config,
) : ConverterRule(config) {
    companion object {
        @JvmField
        val DEFAULT_CONFIG: Config =
            Config.INSTANCE
                .withConversion(
                    BodoLogicalFilter::class.java,
                    Convention.NONE,
                    BodoPhysicalRel.CONVENTION,
                    "BodoPhysicalFilterRule",
                ).withRuleFactory { config -> BodoPhysicalFilterRule(config) }
    }

    override fun convert(rel: RelNode): RelNode {
        val filter = rel as Filter
        return BodoPhysicalFilter.create(
            rel.cluster,
            convert(
                filter.input,
                filter.input.traitSet
                    .replace(BodoPhysicalRel.CONVENTION),
            ),
            filter.condition,
        )
    }
}
