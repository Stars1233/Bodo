package com.bodosql.calcite.application.timers

import com.bodosql.calcite.ir.Expr
import com.bodosql.calcite.ir.Module
import com.bodosql.calcite.ir.Op
import com.bodosql.calcite.ir.Op.Assign
import com.bodosql.calcite.ir.Op.Stmt
import com.bodosql.calcite.ir.Variable

private val noOpVar = Variable("NOOP")

private const val IO_TIMING_VERBOSE_LEVEL = 1
private const val REL_NODE_TIMING_VERBOSE_LEVEL = 2

/**
 * Class that builds the framework for implementing runtime timers around the various components in a non-streaming
 * operator. This holds state shared between timer steps but the code generation is responsible for calling the
 * correct APIs. This class should be removed when streaming is fully supported so there is some duplicate code for now.
 */
class SingleBatchRelNodeTimer(
    private val builder: Module.Builder,
    private val isNoOp: Boolean,
    private val operationDescriptor: String,
    private val loggingTitle: String,
    private val nodeDetails: String,
) {
    private var startTimerVar =
        if (isNoOp) {
            // Avoid impacting tests when timers are disabled.
            noOpVar
        } else {
            builder.symbolTable.genGenericTempVar()
        }

    /**
     * Helper function for generating a timer start.
     * Used for non-traditional cases
     */
    fun genStartTimer(): Op? {
        if (isNoOp) {
            return null
        }
        val timeCall = Expr.Call("time.time")
        return Assign(startTimerVar, timeCall)
    }

    /**
     * Insert the starting time.time() call before a
     * non-streaming operator. This must be called before
     * the code is generated for the operator.
     */
    fun insertStartTimer() {
        genStartTimer()?.let { builder.add(it) }
    }

    /**
     * Helper function to generate instructions for finishing
     * and printing the timer. Used for non-traditional cases
     */
    fun genEndTimer(): List<Op> {
        val ops = mutableListOf<Op>()
        if (isNoOp) {
            return ops
        }

        val endTimerVar = builder.symbolTable.genGenericTempVar()
        // Generate the time call
        val timeCall = Expr.Call("time.time")
        val endTimer = Assign(endTimerVar, timeCall)
        ops.add(endTimer)

        // Compute the difference
        val subVar = builder.symbolTable.genGenericTempVar()
        val subCall = Expr.Binary("-", endTimerVar, startTimerVar)
        val subAssign = Assign(subVar, subCall)
        ops.add(subAssign)

        // Generate a variable with the node details to print
        val nodeDetailsVariable = builder.symbolTable.genGenericTempVar()
        ops.add(Assign(nodeDetailsVariable, Expr.StringLiteral(nodeDetails)))

        val printMessage =
            String.format(
                "f'''Execution time for %s {%s}: {%s}'''",
                operationDescriptor,
                nodeDetailsVariable.emit(),
                subVar.emit(),
            )
        val logMessageCall: Op =
            Stmt(
                Expr.Call(
                    "bodo.user_logging.log_message",
                    // TODO: Add a format string op?
                    Expr.StringLiteral(loggingTitle),
                    Expr.Raw(printMessage),
                ),
            )
        ops.add(logMessageCall)
        return ops
    }

    /**
     * Insert a terminating time.time() call and print the information
     * after a non-streaming operator. This requires insertStartTimer()
     * to have previously been called and the operator code to already
     * be generated.
     */
    fun insertEndTimer() {
        genEndTimer().let { builder.addAll(it) }
    }

    companion object {
        @JvmStatic
        fun createSingleBatchTimer(
            builder: Module.Builder,
            verboseLevel: Int,
            operationDescriptor: String,
            loggingTitle: String,
            nodeDetails: String,
            type: OperationType,
        ): SingleBatchRelNodeTimer {
            val verboseThreshold =
                if (type == OperationType.BATCH) {
                    REL_NODE_TIMING_VERBOSE_LEVEL
                } else {
                    IO_TIMING_VERBOSE_LEVEL
                }
            return SingleBatchRelNodeTimer(
                builder,
                verboseLevel < verboseThreshold,
                operationDescriptor,
                loggingTitle,
                nodeDetails,
            )
        }
    }

    enum class OperationType {
        BATCH,
        IO_BATCH,
    }
}
