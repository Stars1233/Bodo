package com.bodosql.calcite.application.Utils;

import static org.apache.calcite.rex.RexVisitorImpl.visitArrayAnd;

import com.bodosql.calcite.application.BodoSQLCodegenException;
import com.bodosql.calcite.rex.RexNamedParam;

import org.apache.calcite.rex.RexCall;
import org.apache.calcite.rex.RexCorrelVariable;
import org.apache.calcite.rex.RexDynamicParam;
import org.apache.calcite.rex.RexFieldAccess;
import org.apache.calcite.rex.RexInputRef;
import org.apache.calcite.rex.RexLiteral;
import org.apache.calcite.rex.RexLocalRef;
import org.apache.calcite.rex.RexNode;
import org.apache.calcite.rex.RexOver;
import org.apache.calcite.rex.RexPatternFieldRef;
import org.apache.calcite.rex.RexRangeRef;
import org.apache.calcite.rex.RexSubQuery;
import org.apache.calcite.rex.RexTableInputRef;
import org.apache.calcite.rex.RexVisitor;

public class IsScalar implements RexVisitor<Boolean> {

  @Override
  public Boolean visitInputRef(RexInputRef inputRef) {
    return false;
  }

  @Override
  public Boolean visitLocalRef(RexLocalRef localRef) {
    return false;
  }

  @Override
  public Boolean visitLiteral(RexLiteral literal) {
    return true;
  }

  @Override
  public Boolean visitOver(RexOver over) {
    return false;
  }

  @Override
  public Boolean visitCorrelVariable(RexCorrelVariable correlVariable) {
    throw unsupportedNode();
  }

  @Override
  public Boolean visitCall(RexCall call) {
    if (call.getOperator().getName().equals("RANDOM")) {
      return false;
    } else if (call instanceof RexNamedParam) {
      return true;
    } else {
      return visitArrayAnd(this, call.operands);
    }
  }

  @Override
  public Boolean visitDynamicParam(RexDynamicParam dynamicParam) {
    throw unsupportedNode();
  }

  @Override
  public Boolean visitRangeRef(RexRangeRef rangeRef) {
    throw unsupportedNode();
  }

  @Override
  public Boolean visitFieldAccess(RexFieldAccess fieldAccess) {
    throw unsupportedNode();
  }

  @Override
  public Boolean visitSubQuery(RexSubQuery subQuery) {
    throw unsupportedNode();
  }

  @Override
  public Boolean visitTableInputRef(RexTableInputRef ref) {
    throw unsupportedNode();
  }

  @Override
  public Boolean visitPatternFieldRef(RexPatternFieldRef fieldRef) {
    throw unsupportedNode();
  }

  public static boolean isScalar(RexNode node) {
    return node.accept(new IsScalar());
  }

  protected BodoSQLCodegenException unsupportedNode() {
    return new BodoSQLCodegenException(
        "Internal Error: Calcite Plan Produced an Unsupported RexNode");
  }
}