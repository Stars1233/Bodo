package com.bodosql.calcite.application.logicalRules

import com.bodosql.calcite.adapter.snowflake.AbstractSnowflakeProjectRule.Companion.isPushableNode
import org.apache.calcite.plan.RelOptUtil.InputReferencedVisitor
import org.apache.calcite.rel.RelNode
import org.apache.calcite.rel.core.Filter
import org.apache.calcite.rel.core.Join
import org.apache.calcite.rel.core.Project
import org.apache.calcite.rex.RexBuilder
import org.apache.calcite.rex.RexCall
import org.apache.calcite.rex.RexCorrelVariable
import org.apache.calcite.rex.RexDynamicParam
import org.apache.calcite.rex.RexFieldAccess
import org.apache.calcite.rex.RexInputRef
import org.apache.calcite.rex.RexLiteral
import org.apache.calcite.rex.RexLocalRef
import org.apache.calcite.rex.RexNode
import org.apache.calcite.rex.RexOver
import org.apache.calcite.rex.RexPatternFieldRef
import org.apache.calcite.rex.RexRangeRef
import org.apache.calcite.rex.RexShuttle
import org.apache.calcite.rex.RexSubQuery
import org.apache.calcite.rex.RexTableInputRef
import org.apache.calcite.rex.RexWindow
import org.apache.calcite.tools.RelBuilder

/**
 * File that contains a selection of helper functions for rules that enable pushing down projections to Snowflake.
 * Specifically, this code handles the `onMatch` behavior for the following functions:
 *   - SnowflakeProjectPushdownHelpers.kt
 *   - ProjectFilterTransposePushableExpressionsRule.kt
 *
 * Control over what projections are pushable to Snowflake is managed by the AbstractSnowflakeProjectRule. All functions
 * that determine if an expression is pushable in this file wrap API's exposed by the AbstractSnowflakeProjectRule. See
 * AbstractSnowflakeProjectRule.kt::isPushableNode for the full list of pushable expressions.
 */
class SnowflakeProjectPushdownHelpers {

    companion object {

        /**
         * Wrapper around the snowflakeProject rule's isPushableNode, that also checks
         * that the value is non-trivial (not an input ref).
         */
        @JvmStatic
        fun isNonTrivialPushableExpr(node: RexNode): Boolean {
            return node !is RexInputRef && isPushableNode(node)
        }

        @JvmStatic
        fun containsNonTrivialPushableExprs(node: RelNode): Boolean {
            val finder = FinderAsShuttle { x -> isNonTrivialPushableExpr(x) }
            node.accept(finder)
            return finder.seenPredicate
        }

        /**
         * Returns A pair of lists. The first element of the list is the modified list of rexNodes, whose pushable
         * expressions have been replaced with RexNodes. The second is the list of pushed expressions, which
         * must be projected before the expressions in list 1.
         *
         * @param rexBuilder: RexBuilder to use for constructing the new input Refs
         * @param fieldCount: The index at which to start the input Refs. Typically, this should the fieldCount of
         *                      the RelNode that contains the passed RexNodes.
         * @param toReplace: The list of RexNodes from we will replace pushable expressions, with new inputRefs.
         *
         * @return A pair of lists containing the modified rexNodes, and the extracted expressions.
         */
        @JvmStatic
        fun extractValues(rexBuilder: RexBuilder, fieldCount: Int, toReplace: List<RexNode>): Pair<List<RexNode>, List<RexNode>> {
            val replacer = Replacer(fieldCount, rexBuilder)
            val replacedRexNodes = replacer.apply(toReplace)
            return Pair(replacedRexNodes, replacer.replacedExprs)
        }

        /**
         * This contains the logic for the onMatch code for ExtractPushableExpressionsJoin.
         * This code extracts all values that can be pushed into the snowflake, and pushes them into separate project(s)
         * below the join.
         */
        @JvmStatic
        fun replaceValuesJoinCondition(builder: RelBuilder, toReplace: Join): RelNode? {
            val conditionValue: List<RexNode> = listOf(toReplace.condition)

            // First what values we need to push, so we can determine how many expressions we're pushing to the left/right
            val newNodeAndRexExprs = extractValues(builder.rexBuilder, -1, conditionValue)
            val expressionsToTryAndPush = newNodeAndRexExprs.second

            if (expressionsToTryAndPush.isEmpty()) {
                return null
            }
            // Split the needed projects into two expressions, a project on top of each of the inputs
            val leftBound = toReplace.left.rowType.fieldCount

            // NOTE: we don't need to care about left/right/outer joins here,
            // since we're only pushing the join condition.

            fun canPushLeft(node: RexNode, leftIdx: Int): Boolean {
                val finder = InputReferencedVisitor()
                node.accept(finder)
                return finder.inputPosReferenced.all() { idx -> idx < leftIdx }
            }

            fun canPushRight(node: RexNode, leftIdx: Int): Boolean {
                val finder = InputReferencedVisitor()
                node.accept(finder)
                return finder.inputPosReferenced.all() { idx -> idx >= leftIdx }
            }

            // NOTE2: There's probably a smarter way to do this with some sort of partitioning iterator to avoid
            // recomputing canPushLeft, but I couldn't figure it out
            val leftProjects = expressionsToTryAndPush.filter { curVal -> canPushLeft(curVal, leftBound) }
            val rightProjects = expressionsToTryAndPush.filter { curVal -> canPushRight(curVal, leftBound) && !canPushLeft(curVal, leftBound) }

            val oldLeft = toReplace.getInput(0)
            val oldRight = toReplace.getInput(1)
            builder.push(oldLeft)
            builder.projectPlus(leftProjects)
            builder.push(oldRight)
            builder.projectPlus(rightProjects)

            // Now create a map of project expression => index
            val leftExpressionsIndexed: Iterable<Pair<RexNode, Int>> = leftProjects.mapIndexed { idx, node -> Pair(node, idx + oldLeft.rowType.fieldCount) }
            val rightExpressionsIndexed: Iterable<Pair<RexNode, Int>> = rightProjects.mapIndexed { idx, node -> Pair(node, idx + oldLeft.rowType.fieldCount) }
            val expressionMap: Map<RexNode, RexNode> = leftExpressionsIndexed.plus(rightExpressionsIndexed).map { rexNodeAndNewIndex: Pair<RexNode, Int> ->
                Pair(rexNodeAndNewIndex.first, builder.rexBuilder.makeInputRef(rexNodeAndNewIndex.first.type, rexNodeAndNewIndex.second))
            }.toMap()

            val newJoinCondition: RexNode = toReplace.condition.accept(MapReplacer(expressionMap, builder.rexBuilder))
            builder.join(
                toReplace.joinType,
                newJoinCondition,
            )
            return builder.build()
        }

        /**
         * This code contains the logic for the onMatch code for ProjectFilterTransposePushableExpressionsRule.
         *
         * This code finds all pushable expressions in the passed project/filter, and pushes them into a new
         * project below the filter.
         */
        @JvmStatic
        fun replaceValuesProjectFilter(project: Project, filter: Filter, builder: RelBuilder): RelNode? {
            val filterInput = filter.input
            // Returns a list of values that need to be extracted/pushed below the provided project and filter
            val replacer = Replacer(filterInput.rowType.fieldCount, builder.rexBuilder)
            val newProjects = replacer.apply(project.projects)
            val newFilterCond = replacer.apply(filter.condition)

            if (replacer.replacedExprs.isNotEmpty()) {
                builder.push(filterInput)
                builder.projectPlus(replacer.replacedExprs)
                builder.filter(
                    newFilterCond,
                )
                builder.project(
                    newProjects,
                )
                return builder.build()
            } else {
                return null
            }
        }
    }

    /**
     * Function that visits a rex tree, and replaces all expressions that can be pushed into snowflake with input refs.
     * The index of the new input refs is dependent on the value passed for initial field count. Replaced expressions
     * are located in the replacedExprs argument.
     *
     * After running, for all values of n, all instances of replacedExpres[n]
     * will have been replaced with a RexInputRef $(i+n).
     *
     * Note that this shouldn't be called directly on a RelNode, as this can lead to RelBuilders throwing
     * errors due to incorrectly typed RelNodes.
     */
    class Replacer(val initialFieldCount: Int, val rexBuilder: RexBuilder) : RexShuttle() {
        val replacedExprs = mutableListOf<RexNode>()
        var currentFieldIndex = initialFieldCount

        private fun visitRexInternal(node: RexNode): RexNode? {
            if (isNonTrivialPushableExpr(node)) {
                // If there's duplicates, don't create duplicate input refs
                val idxIfExists = replacedExprs.indexOf(node)
                if (idxIfExists != -1) {
                    return rexBuilder.makeInputRef(node.type, initialFieldCount + idxIfExists)
                }
                replacedExprs.add(node)
                return rexBuilder.makeInputRef(node.type, currentFieldIndex++)
            }
            return null
        }

        // ~ Methods ----------------------------------------------------------------

        override fun visitCall(call: RexCall): RexNode {
            return visitRexInternal(call) ?: super.visitCall(call)
        }

        override fun visitOver(over: RexOver): RexNode {
            return visitRexInternal(over) ?: super.visitOver(over)
        }

        override fun visitCorrelVariable(correlVariable: RexCorrelVariable): RexNode {
            return visitRexInternal(correlVariable) ?: super.visitCorrelVariable(correlVariable)
        }

        override fun visitDynamicParam(dynamicParam: RexDynamicParam): RexNode {
            return visitRexInternal(dynamicParam) ?: super.visitDynamicParam(dynamicParam)
        }

        override fun visitRangeRef(rangeRef: RexRangeRef): RexNode {
            return visitRexInternal(rangeRef) ?: super.visitRangeRef(rangeRef)
        }

        override fun visitFieldAccess(fieldAccess: RexFieldAccess): RexNode {
            return visitRexInternal(fieldAccess) ?: super.visitFieldAccess(fieldAccess)
        }

        override fun visitSubQuery(subQuery: RexSubQuery): RexNode {
            return visitRexInternal(subQuery) ?: super.visitSubQuery(subQuery)
        }

        override fun visitTableInputRef(fieldRef: RexTableInputRef): RexNode {
            return visitRexInternal(fieldRef) ?: super.visitTableInputRef(fieldRef)
        }

        override fun visitPatternFieldRef(fieldRef: RexPatternFieldRef): RexNode {
            return visitRexInternal(fieldRef) ?: super.visitPatternFieldRef(fieldRef)
        }
    }

    /**
     * RexShuttle that takes a map of RexNodes to RexNode, and replaces all encountered key RexNodes with their values
     * found in the map.
     *
     * Note that this shouldn't be called directly on a RelNode, due to issues with typing.
     */
    class MapReplacer(val map: Map<RexNode, RexNode>, val builder: RexBuilder) : RexShuttle() {

        private fun returnIfInMap(node: RexNode): RexNode? {
            return map.get(node)
        }

        // ~ Methods ----------------------------------------------------------------

        override fun visitCall(call: RexCall): RexNode {
            return returnIfInMap(call) ?: super.visitCall(call)
        }

        override fun visitOver(over: RexOver): RexNode {
            return returnIfInMap(over) ?: super.visitOver(over)
        }

        override fun visitCorrelVariable(correlVariable: RexCorrelVariable): RexNode {
            return returnIfInMap(correlVariable) ?: super.visitCorrelVariable(correlVariable)
        }

        override fun visitDynamicParam(dynamicParam: RexDynamicParam): RexNode {
            return returnIfInMap(dynamicParam) ?: super.visitDynamicParam(dynamicParam)
        }

        override fun visitRangeRef(rangeRef: RexRangeRef): RexNode {
            return returnIfInMap(rangeRef) ?: super.visitRangeRef(rangeRef)
        }

        override fun visitFieldAccess(fieldAccess: RexFieldAccess): RexNode {
            return returnIfInMap(fieldAccess) ?: super.visitFieldAccess(fieldAccess)
        }

        override fun visitSubQuery(subQuery: RexSubQuery): RexNode {
            return returnIfInMap(subQuery) ?: super.visitSubQuery(subQuery)
        }

        override fun visitTableInputRef(fieldRef: RexTableInputRef): RexNode {
            return returnIfInMap(fieldRef) ?: super.visitTableInputRef(fieldRef)
        }

        override fun visitPatternFieldRef(fieldRef: RexPatternFieldRef): RexNode {
            return returnIfInMap(fieldRef) ?: super.visitPatternFieldRef(fieldRef)
        }
    }

    /**
     * Wrapper that takes a predicate, and sets seenPredicate if it encounters any RexNode that fulfils the predicate.
     * Currently used for predicates.
     */
    private class FinderAsShuttle(val predicate: (RexNode) -> Boolean) : RexShuttle() {
        var seenPredicate = false

        override fun visitInputRef(inputRef: RexInputRef): RexNode {
            if (seenPredicate || predicate(inputRef)) {
                seenPredicate = true
                return inputRef
            }
            return inputRef
        }

        override fun visitLocalRef(localRef: RexLocalRef): RexNode {
            if (seenPredicate || predicate(localRef)) {
                seenPredicate = true
                return localRef
            }
            return localRef
        }

        override fun visitLiteral(literal: RexLiteral): RexNode {
            if (seenPredicate || predicate(literal)) {
                seenPredicate = true
                return literal
            }
            return literal
        }

        override fun visitCall(call: RexCall): RexNode {
            if (seenPredicate || predicate(call)) {
                seenPredicate = true
                return call
            }
            this.visitList(call.operands)
            return call
        }

        override fun visitOver(over: RexOver): RexNode {
            if (seenPredicate || predicate(over)) {
                seenPredicate = true
                return over
            }
            this.visitList(over.operands).any()
            visitWindow(over.getWindow())
            return over
        }

        override fun visitWindow(window: RexWindow): RexWindow {
            this.visitList(window.partitionKeys)
            this.visitList(window.orderKeys.map { it -> it.left })
            window.lowerBound.offset?.accept(this)
            window.upperBound.offset?.accept(this)
            return window
        }

        override fun visitCorrelVariable(correlVariable: RexCorrelVariable): RexNode {
            if (seenPredicate || predicate(correlVariable)) {
                seenPredicate = true
                return correlVariable
            }
            return correlVariable
        }

        override fun visitDynamicParam(dynamicParam: RexDynamicParam): RexNode {
            if (seenPredicate || predicate(dynamicParam)) {
                seenPredicate = true
                return dynamicParam
            }
            return dynamicParam
        }

        override fun visitRangeRef(rangeRef: RexRangeRef): RexNode {
            if (seenPredicate || predicate(rangeRef)) {
                seenPredicate = true
                return rangeRef
            }
            return rangeRef
        }

        override fun visitFieldAccess(fieldAccess: RexFieldAccess): RexNode {
            if (seenPredicate || predicate(fieldAccess)) {
                seenPredicate = true
                return fieldAccess
            }
            return fieldAccess
        }

        override fun visitSubQuery(subQuery: RexSubQuery): RexNode {
            TODO("Cannot handle subqueries in the finder at this time")
        }

        override fun visitTableInputRef(fieldRef: RexTableInputRef): RexNode {
            if (seenPredicate || predicate(fieldRef)) {
                seenPredicate = true
                return fieldRef
            }
            return fieldRef
        }

        override fun visitPatternFieldRef(fieldRef: RexPatternFieldRef): RexNode {
            if (seenPredicate || predicate(fieldRef)) {
                seenPredicate = true
                return fieldRef
            }
            return fieldRef
        }
    }
}