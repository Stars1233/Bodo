package com.bodosql.calcite.rel.logical

import com.bodosql.calcite.rel.core.TableCreateBase
import com.bodosql.calcite.sql.ddl.CreateTableMetadata
import org.apache.calcite.plan.Convention
import org.apache.calcite.plan.RelOptCluster
import org.apache.calcite.plan.RelTraitSet
import org.apache.calcite.rel.RelNode
import org.apache.calcite.schema.Schema
import org.apache.calcite.sql.ddl.SqlCreateTable.CreateTableType

open class BodoLogicalTableCreate private constructor(
    cluster: RelOptCluster,
    traits: RelTraitSet,
    input: RelNode,
    schema: Schema,
    tableName: String,
    isReplace: Boolean,
    createTableType: CreateTableType,
    path: List<String>,
    meta: CreateTableMetadata,
) : TableCreateBase(cluster, traits, input, schema, tableName, isReplace, createTableType, path, meta) {
    override fun copy(
        traitSet: RelTraitSet,
        inputs: List<RelNode>,
    ): BodoLogicalTableCreate {
        assert(traitSet.containsIfApplicable(Convention.NONE))
        assert(inputs.size == 1)
        return BodoLogicalTableCreate(
            cluster,
            traitSet,
            inputs[0],
            getSchema(),
            tableName,
            isReplace,
            createTableType,
            path,
            this.meta,
        )
    }

    companion object {
        @JvmStatic
        fun create(
            input: RelNode,
            schema: Schema,
            tableName: String,
            isReplace: Boolean,
            createTableType: CreateTableType,
            path: List<String>,
            meta: CreateTableMetadata,
        ): BodoLogicalTableCreate {
            val cluster = input.cluster
            val traitSet = cluster.traitSetOf(Convention.NONE)
            return BodoLogicalTableCreate(
                cluster,
                traitSet,
                input,
                schema,
                tableName,
                isReplace,
                createTableType,
                path,
                meta,
            )
        }
    }
}
