package com.bodosql.calcite.adapter.bodo

import com.bodosql.calcite.application.BodoSQLCodegenException
import com.bodosql.calcite.application.operatorTables.TableFunctionOperatorTable
import com.bodosql.calcite.ir.BodoEngineTable
import com.bodosql.calcite.ir.Expr
import com.bodosql.calcite.ir.StateVariable
import com.bodosql.calcite.rel.core.FlattenBase
import com.bodosql.calcite.traits.BatchingProperty
import com.bodosql.calcite.traits.ExpectedBatchingProperty
import org.apache.calcite.plan.RelOptCluster
import org.apache.calcite.plan.RelTraitSet
import org.apache.calcite.rel.RelNode
import org.apache.calcite.rel.type.RelDataType
import org.apache.calcite.rex.RexCall
import org.apache.calcite.rex.RexInputRef
import org.apache.calcite.rex.RexLiteral
import org.apache.calcite.util.ImmutableBitSet

class BodoPhysicalFlatten(
    cluster: RelOptCluster,
    traits: RelTraitSet,
    input: RelNode,
    call: RexCall,
    callType: RelDataType,
    usedColOutputs: ImmutableBitSet,
    repeatColumns: ImmutableBitSet,
) :
    FlattenBase(
            cluster,
            traits.replace(BodoPhysicalRel.CONVENTION),
            input,
            call,
            callType,
            usedColOutputs,
            repeatColumns,
        ),
        BodoPhysicalRel {
    override fun copy(
        traitSet: RelTraitSet,
        input: RelNode,
        call: RexCall,
        callType: RelDataType,
        usedColOutputs: ImmutableBitSet,
        repeatColumns: ImmutableBitSet,
    ): BodoPhysicalFlatten {
        return BodoPhysicalFlatten(cluster, traitSet, input, call, callType, usedColOutputs, repeatColumns)
    }

    /**
     * Emits the code necessary for implementing this relational operator.
     *
     * @param implementor implementation handler.
     * @return the variable that represents this relational expression.
     */
    override fun emit(implementor: BodoPhysicalRel.Implementor): BodoEngineTable {
        return implementor::build {
                ctx ->
            if (call.operator.name == TableFunctionOperatorTable.FLATTEN.name) {
                emitFlatten(implementor, ctx, call)
            } else {
                throw BodoSQLCodegenException("Flatten node does not currently support codegen for operation $call")
            }
        }
    }

    /**
     * Emits the code necessary to calculate a call to the FLATTEN function.
     *
     * @param implementor implementation handler.
     * @param ctx the build context
     * @param flattenCall the function call to FLATTEN
     * @return the variable that represents this relational expression.
     */
    fun emitFlatten(
        implementor: BodoPhysicalRel.Implementor,
        ctx: BodoPhysicalRel.BuildContext,
        flattenCall: RexCall,
    ): BodoEngineTable {
        val inputVar = implementor.visitChild(input, 0)
        val replicatedColsExpresions = repeatColumns.toList().map { idx -> Expr.IntegerLiteral(idx) }
        val replicatedColsGlobal = ctx.lowerAsGlobal(Expr.Call("MetaType", Expr.Tuple(replicatedColsExpresions)))
        val columnToExplode = flattenCall.operands[0]
        val explodeColIdx =
            if (columnToExplode is RexInputRef) {
                columnToExplode.index
            } else {
                throw Exception("Expected input to FLATTEN to be an input column reference")
            }
        val explodeCol = Expr.IntegerLiteral(explodeColIdx)
        val outputColsExpressions = callType.fieldList.mapIndexed { idx, _ -> Expr.BooleanLiteral(this.usedColOutputs.contains(idx)) }
        val outputColsGlobal = ctx.lowerAsGlobal(Expr.Call("MetaType", Expr.Tuple(outputColsExpressions)))
        val outer = Expr.BooleanLiteral(RexLiteral.booleanValue(flattenCall.operands[2]))
        return ctx.returns(
            Expr.Call("bodo.libs.lateral.lateral_flatten", listOf(inputVar, replicatedColsGlobal, explodeCol, outputColsGlobal, outer)),
        )
    }

    /**
     * Function to create the initial state for a streaming pipeline.
     * This should be called from emit.
     */
    override fun initStateVariable(ctx: BodoPhysicalRel.BuildContext): StateVariable {
        TODO("Not yet implemented")
    }

    /**
     * Function to delete the initial state for a streaming pipeline.
     * This should be called from emit.
     */
    override fun deleteStateVariable(
        ctx: BodoPhysicalRel.BuildContext,
        stateVar: StateVariable,
    ) {
        TODO("Not yet implemented")
    }

    override fun expectedOutputBatchingProperty(inputBatchingProperty: BatchingProperty): BatchingProperty {
        return ExpectedBatchingProperty.streamingIfPossibleProperty(getRowType())
    }

    companion object {
        @JvmStatic
        fun create(
            cluster: RelOptCluster,
            input: RelNode,
            call: RexCall,
            callType: RelDataType,
        ): BodoPhysicalFlatten {
            return create(cluster, input, call, callType, ImmutableBitSet.range(callType.fieldCount), ImmutableBitSet.of())
        }

        @JvmStatic
        fun create(
            cluster: RelOptCluster,
            input: RelNode,
            call: RexCall,
            callType: RelDataType,
            usedColOutputs: ImmutableBitSet,
        ): BodoPhysicalFlatten {
            return create(cluster, input, call, callType, usedColOutputs, ImmutableBitSet.of())
        }

        @JvmStatic
        fun create(
            cluster: RelOptCluster,
            input: RelNode,
            call: RexCall,
            callType: RelDataType,
            usedColOutputs: ImmutableBitSet,
            repeatColumns: ImmutableBitSet,
        ): BodoPhysicalFlatten {
            return BodoPhysicalFlatten(cluster, cluster.traitSet(), input, call, callType, usedColOutputs, repeatColumns)
        }
    }
}