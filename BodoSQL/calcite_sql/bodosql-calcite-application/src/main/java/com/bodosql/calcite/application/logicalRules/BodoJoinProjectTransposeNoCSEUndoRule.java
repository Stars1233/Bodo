/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to you under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.bodosql.calcite.application.logicalRules;

import static com.bodosql.calcite.application.logicalRules.BodoJoinProjectTransposeNoCSEUndoRuleHelper.bodoJoinProjectTransposeRuleCanMakeChange;
import static java.util.Objects.requireNonNull;

import com.bodosql.calcite.application.utils.BodoSQLStyleImmutable;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.apache.calcite.plan.RelOptRuleCall;
import org.apache.calcite.plan.RelOptUtil;
import org.apache.calcite.plan.RelRule;
import org.apache.calcite.plan.Strong;
import org.apache.calcite.rel.RelNode;
import org.apache.calcite.rel.core.Join;
import org.apache.calcite.rel.core.JoinRelType;
import org.apache.calcite.rel.core.Project;
import org.apache.calcite.rel.logical.LogicalJoin;
import org.apache.calcite.rel.logical.LogicalProject;
import org.apache.calcite.rel.rules.TransformationRule;
import org.apache.calcite.rel.type.RelDataType;
import org.apache.calcite.rel.type.RelDataTypeField;
import org.apache.calcite.rex.RexBuilder;
import org.apache.calcite.rex.RexInputRef;
import org.apache.calcite.rex.RexLocalRef;
import org.apache.calcite.rex.RexNode;
import org.apache.calcite.rex.RexProgram;
import org.apache.calcite.rex.RexProgramBuilder;
import org.apache.calcite.sql.validate.SqlValidatorUtil;
import org.apache.calcite.tools.RelBuilder;
import org.apache.calcite.util.ImmutableBitSet;
import org.apache.calcite.util.Pair;
import org.checkerframework.checker.nullness.qual.Nullable;
import org.immutables.value.Value;

/**
 * Planner rule that matches a {@link org.apache.calcite.rel.core.Join} one of whose inputs is a
 * {@link org.apache.calcite.rel.logical.LogicalProject}, and pulls the project up.
 *
 * <p>Projections are pulled up if the {@link org.apache.calcite.rel.logical.LogicalProject} doesn't
 * originate from a null generating input in an outer join.
 *
 * <p>BODO CHANGE: The bodo version of this rule will avoid pulling any expressions used in the join
 * condition above the join, so as not to undo CSE. Because this rule may now create a Join with one
 * or more projects as input, This rule MUST be used with a predicate that prevents the rule from
 * matching on itself indefinitely (if used in a HEP pass).
 */
@BodoSQLStyleImmutable
@Value.Enclosing
public class BodoJoinProjectTransposeNoCSEUndoRule
    extends RelRule<BodoJoinProjectTransposeNoCSEUndoRule.Config> implements TransformationRule {

  /** Creates a JoinProjectTransposeRule. */
  protected BodoJoinProjectTransposeNoCSEUndoRule(Config config) {
    super(config);
  }

  // BODO CHANGE: removed some unused deprecated initializers here

  // ~ Methods ----------------------------------------------------------------

  @Override
  public void onMatch(RelOptRuleCall call) {
    final Join join = call.rel(0);
    final JoinRelType joinType = join.getJoinType();

    Project leftProject;
    Project rightProject;
    RelNode leftJoinChild;
    RelNode rightJoinChild;

    // If 1) the rule works on outer joins, or
    //    2) input's projection doesn't generate nulls
    final boolean includeOuter = config.isIncludeOuter();
    if (hasLeftChild(call) && (includeOuter || !joinType.generatesNullsOnLeft())) {
      leftProject = call.rel(1);
      leftJoinChild = getProjectChild(call, leftProject, true);
    } else {
      leftProject = null;
      leftJoinChild = call.rel(1);
    }
    if (hasRightChild(call) && (includeOuter || !joinType.generatesNullsOnRight())) {
      rightProject = getRightChild(call);
      rightJoinChild = getProjectChild(call, rightProject, false);
    } else {
      rightProject = null;
      rightJoinChild = join.getRight();
    }

    // Skip projects containing over clause
    if (leftProject != null && leftProject.containsOver()) {
      leftProject = null;
      leftJoinChild = join.getLeft();
    }
    if (rightProject != null && rightProject.containsOver()) {
      rightProject = null;
      rightJoinChild = join.getRight();
    }

    if ((leftProject == null) && (rightProject == null)) {
      return;
    }

    if (includeOuter) {
      if (leftProject != null
          && joinType.generatesNullsOnLeft()
          && !Strong.allStrong(leftProject.getProjects())
          && !join.getCondition().isAlwaysTrue()) {
        return;
      }

      if (rightProject != null
          && joinType.generatesNullsOnRight()
          && !Strong.allStrong(rightProject.getProjects())
          && !join.getCondition().isAlwaysTrue()) {
        return;
      }
    }

    // Find all input references in the join condition
    ImmutableBitSet joinConditionUsages = RelOptUtil.InputFinder.bits(join.getCondition());

    // BODO CHANGE: If there is no non-trivial expression that is not used in the join condition,
    // do nothing
    if (!bodoJoinProjectTransposeRuleCanMakeChange(
        joinConditionUsages,
        leftProject,
        rightProject,
        leftJoinChild.getRowType().getFieldCount())) {
      return;
    }

    // BODO CHANGE: push the expressions that we know we want to keep into the leftJoinChild and
    // the rightJoinChild
    final RelBuilder relBuilder = call.builder();

    // ImmutableBitSet joinConditionUsages, Project project, RelNode joinChild, int offset,
    // RelBuilder relBuilder

    Pair<Project, RelNode> newLeftProjectAndJoinChild =
        pushValuesNeededInJoinConditionIntoJoinChild(
            joinConditionUsages, leftProject, leftJoinChild, 0, relBuilder);
    leftProject = newLeftProjectAndJoinChild.left;
    leftJoinChild = newLeftProjectAndJoinChild.right;

    Pair<Project, RelNode> newRightProjectAndJoinChild =
        pushValuesNeededInJoinConditionIntoJoinChild(
            joinConditionUsages,
            rightProject,
            rightJoinChild,
            leftJoinChild.getRowType().getFieldCount(),
            relBuilder);
    rightProject = newRightProjectAndJoinChild.left;
    rightJoinChild = newRightProjectAndJoinChild.right;

    // Construct two RexPrograms and combine them.  The bottom program
    // is a join of the projection expressions from the left and/or
    // right projects that feed into the join.  The top program contains
    // the join condition.

    // Create a row type representing a concatenation of the inputs
    // underneath the projects that feed into the join.  This is the input
    // into the bottom RexProgram.  Note that the join type is an inner
    // join because the inputs haven't actually been joined yet.
    final RelDataType joinChildrenRowType =
        SqlValidatorUtil.deriveJoinRowType(
            leftJoinChild.getRowType(),
            rightJoinChild.getRowType(),
            JoinRelType.INNER,
            join.getCluster().getTypeFactory(),
            null,
            Collections.emptyList());

    // Create projection expressions, combining the projection expressions
    // from the projects that feed into the join.  For the RHS projection
    // expressions, shift them to the right by the number of fields on
    // the LHS.  If the join input was not a projection, simply create
    // references to the inputs.
    final int nProjExprs = join.getRowType().getFieldCount();
    final List<Pair<RexNode, String>> projects = new ArrayList<>();
    final RexBuilder rexBuilder = join.getCluster().getRexBuilder();

    createProjectExprs(
        leftProject, leftJoinChild, 0, rexBuilder, joinChildrenRowType.getFieldList(), projects);

    final List<RelDataTypeField> leftFields = leftJoinChild.getRowType().getFieldList();
    final int nFieldsLeft = leftFields.size();
    createProjectExprs(
        rightProject,
        rightJoinChild,
        nFieldsLeft,
        rexBuilder,
        joinChildrenRowType.getFieldList(),
        projects);

    final List<RelDataType> projTypes = new ArrayList<>();
    for (int i = 0; i < nProjExprs; i++) {
      projTypes.add(projects.get(i).left.getType());
    }
    RelDataType projRowType =
        rexBuilder.getTypeFactory().createStructType(projTypes, Pair.right(projects));

    // create the RexPrograms and merge them
    final RexProgram bottomProgram =
        RexProgram.create(joinChildrenRowType, Pair.left(projects), null, projRowType, rexBuilder);
    final RexProgramBuilder topProgramBuilder = new RexProgramBuilder(projRowType, rexBuilder);
    topProgramBuilder.addIdentity();
    topProgramBuilder.addCondition(join.getCondition());
    final RexProgram topProgram = topProgramBuilder.getProgram();
    final RexProgram mergedProgram =
        RexProgramBuilder.mergePrograms(topProgram, bottomProgram, rexBuilder);

    // expand out the join condition and construct a new LogicalJoin that
    // directly references the join children without the intervening
    // ProjectRels
    final RexNode newCondition =
        mergedProgram.expandLocalRef(
            requireNonNull(
                mergedProgram.getCondition(),
                () -> "mergedProgram.getCondition() for " + mergedProgram));
    final Join newJoin =
        join.copy(
            join.getTraitSet(),
            newCondition,
            leftJoinChild,
            rightJoinChild,
            join.getJoinType(),
            join.isSemiJoinDone());

    // expand out the new projection expressions; if the join is an
    // outer join, modify the expressions to reference the join output
    final List<RexNode> newProjExprs = new ArrayList<>();
    final List<RexLocalRef> projList = mergedProgram.getProjectList();
    final List<RelDataTypeField> newJoinFields = newJoin.getRowType().getFieldList();
    final int nJoinFields = newJoinFields.size();
    int[] adjustments = new int[nJoinFields];
    for (int i = 0; i < nProjExprs; i++) {
      RexNode newExpr = mergedProgram.expandLocalRef(projList.get(i));
      if (joinType.isOuterJoin()) {
        newExpr =
            newExpr.accept(
                new RelOptUtil.RexInputConverter(
                    rexBuilder, joinChildrenRowType.getFieldList(), newJoinFields, adjustments));
      }
      newProjExprs.add(newExpr);
    }

    // finally, create the projection on top of the join
    relBuilder.push(newJoin);
    relBuilder.project(newProjExprs, join.getRowType().getFieldNames());
    // if the join was outer, we might need a cast after the
    // projection to fix differences wrt nullability of fields
    if (joinType.isOuterJoin()) {
      relBuilder.convert(join.getRowType(), false);
    }

    call.transformTo(relBuilder.build());
  }

  /** Returns whether the rule was invoked with a left project child. */
  protected boolean hasLeftChild(RelOptRuleCall call) {
    return call.rel(1) instanceof Project;
  }

  /** Returns whether the rule was invoked with 2 children. */
  protected boolean hasRightChild(RelOptRuleCall call) {
    return call.rels.length == 3;
  }

  /** Returns the Project corresponding to the right child. */
  protected Project getRightChild(RelOptRuleCall call) {
    return call.rel(2);
  }

  Pair<Project, RelNode> pushValuesNeededInJoinConditionIntoJoinChild(
      ImmutableBitSet joinConditionUsages,
      Project project,
      RelNode joinChild,
      int offset,
      RelBuilder relBuilder) {
    if (project == null) {
      return new Pair<>(null, joinChild);
    }
    RexBuilder rexBuilder = relBuilder.getRexBuilder();

    List<RexNode> newProjectsToBeMergedIntoJoinChild = new ArrayList<>();
    List<RexNode> newProjectOnTopOfJoinChild = new ArrayList<>();
    int newInputRefCount = joinChild.getRowType().getFieldCount();
    for (int i = 0; i < project.getProjects().size(); i++) {
      RexNode currentProject = project.getProjects().get(i);
      if (joinConditionUsages.get(i + offset) && !(currentProject instanceof RexInputRef)) {
        newProjectsToBeMergedIntoJoinChild.add(currentProject);
        newProjectOnTopOfJoinChild.add(
            rexBuilder.makeInputRef(currentProject.getType(), newInputRefCount++));
      } else {
        newProjectOnTopOfJoinChild.add(currentProject);
      }
    }

    // There's no value we need to push into the join child.
    if (newProjectsToBeMergedIntoJoinChild.isEmpty()) {
      return new Pair<>(project, joinChild);
    }

    relBuilder.push(joinChild);
    relBuilder.projectPlus(newProjectsToBeMergedIntoJoinChild);
    relBuilder.project(newProjectOnTopOfJoinChild, project.getRowType().getFieldNames(), true);

    Project newProject = (Project) relBuilder.build();
    RelNode newJoinChild = newProject.getInput();

    return new Pair(newProject, newJoinChild);
  }

  /**
   * Returns the child of the project that will be used as input into the new LogicalJoin once the
   * projects are pulled above the LogicalJoin.
   *
   * @param call RelOptRuleCall
   * @param project project RelNode
   * @param leftChild true if the project corresponds to the left projection
   * @return child of the project that will be used as input into the new LogicalJoin once the
   *     projects are pulled above the LogicalJoin
   */
  protected RelNode getProjectChild(RelOptRuleCall call, Project project, boolean leftChild) {
    return project.getInput();
  }

  /**
   * Creates projection expressions corresponding to one of the inputs into the join.
   *
   * @param project the projection input into the join (if it exists)
   * @param joinChild the child of the projection input (if there is a projection); otherwise, this
   *     is the join input
   * @param adjustmentAmount the amount the expressions need to be shifted by
   * @param rexBuilder rex builder
   * @param joinChildrenFields concatenation of the fields from the left and right join inputs (once
   *     the projections have been removed)
   * @param projects Projection expressions &amp; names to be created
   */
  protected void createProjectExprs(
      @Nullable Project project,
      RelNode joinChild,
      int adjustmentAmount,
      RexBuilder rexBuilder,
      List<RelDataTypeField> joinChildrenFields,
      List<Pair<RexNode, String>> projects) {
    List<RelDataTypeField> childFields = joinChild.getRowType().getFieldList();
    if (project != null) {
      List<Pair<RexNode, String>> namedProjects = project.getNamedProjects();
      int nChildFields = childFields.size();
      int[] adjustments = new int[nChildFields];
      for (int i = 0; i < nChildFields; i++) {
        adjustments[i] = adjustmentAmount;
      }
      for (Pair<RexNode, String> pair : namedProjects) {
        RexNode e = pair.left;
        if (adjustmentAmount != 0) {
          // shift the references by the adjustment amount
          e =
              e.accept(
                  new RelOptUtil.RexInputConverter(
                      rexBuilder, childFields, joinChildrenFields, adjustments));
        }
        projects.add(Pair.of(e, pair.right));
      }
    } else {
      // no projection; just create references to the inputs
      for (int i = 0; i < childFields.size(); i++) {
        final RelDataTypeField field = childFields.get(i);
        projects.add(
            Pair.of(
                rexBuilder.makeInputRef(field.getType(), i + adjustmentAmount), field.getName()));
      }
    }
  }

  /** Rule configuration. */
  @Value.Immutable
  public interface Config extends RelRule.Config {
    Config DEFAULT =
        ImmutableBodoJoinProjectTransposeNoCSEUndoRule.Config.of()
            .withOperandSupplier(
                b0 ->
                    b0.operand(LogicalJoin.class)
                        .inputs(
                            b1 -> b1.operand(LogicalProject.class).anyInputs(),
                            b2 -> b2.operand(LogicalProject.class).anyInputs()))
            .withDescription("JoinProjectTransposeRule(Project-Project)");

    Config LEFT =
        DEFAULT
            .withOperandSupplier(
                b0 ->
                    b0.operand(LogicalJoin.class)
                        .inputs(b1 -> b1.operand(LogicalProject.class).anyInputs()))
            .withDescription("JoinProjectTransposeRule(Project-Other)")
            .as(Config.class);

    Config RIGHT =
        DEFAULT
            .withOperandSupplier(
                b0 ->
                    b0.operand(LogicalJoin.class)
                        .inputs(
                            b1 -> b1.operand(RelNode.class).anyInputs(),
                            b2 -> b2.operand(LogicalProject.class).anyInputs()))
            .withDescription("JoinProjectTransposeRule(Other-Project)")
            .as(Config.class);

    Config OUTER =
        DEFAULT
            .withDescription("Join(IncludingOuter)ProjectTransposeRule(Project-Project)")
            .as(Config.class)
            .withIncludeOuter(true);

    Config LEFT_OUTER =
        LEFT.withDescription("Join(IncludingOuter)ProjectTransposeRule(Project-Other)")
            .as(Config.class)
            .withIncludeOuter(true);

    Config RIGHT_OUTER =
        RIGHT
            .withDescription("Join(IncludingOuter)ProjectTransposeRule(Other-Project)")
            .as(Config.class)
            .withIncludeOuter(true);

    @Override
    default BodoJoinProjectTransposeNoCSEUndoRule toRule() {
      return new BodoJoinProjectTransposeNoCSEUndoRule(this);
    }

    /** Whether to include outer joins, default false. */
    @Value.Default
    default boolean isIncludeOuter() {
      return false;
    }

    /** Sets {@link #isIncludeOuter()}. */
    Config withIncludeOuter(boolean includeOuter);
  }
}
