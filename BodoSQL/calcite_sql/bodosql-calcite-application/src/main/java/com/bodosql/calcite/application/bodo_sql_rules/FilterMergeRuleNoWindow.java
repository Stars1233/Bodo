package com.bodosql.calcite.application.bodo_sql_rules;

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

import org.apache.calcite.plan.*;
import org.apache.calcite.plan.RelOptRuleCall;
import org.apache.calcite.plan.RelRule;
import org.apache.calcite.rel.core.*;
import org.apache.calcite.rel.core.Filter;
import org.apache.calcite.rel.rules.*;
import org.apache.calcite.tools.*;
import org.apache.calcite.tools.RelBuilder;
import org.immutables.value.*;
import org.immutables.value.Value;

/**
 * Planner rule that combines two {@link org.apache.calcite.rel.logical.LogicalFilter}s.
 *
 * <p>This is equivalent to FilterMergeRule, but we add a restriction that neither filter contains
 * an Over clause. This is only possible due to our other optimization rules that can push a
 * optimized window function into a filter.
 */
@BodoSQLStyleImmutable
@Value.Enclosing
public class FilterMergeRuleNoWindow extends RelRule<FilterMergeRuleNoWindow.Config>
    implements SubstitutionRule {

  /** Creates a FilterMergeRule. */
  protected FilterMergeRuleNoWindow(FilterMergeRuleNoWindow.Config config) {
    super(config);
  }

  // ~ Methods ----------------------------------------------------------------

  @Override
  public void onMatch(RelOptRuleCall call) {
    final Filter topFilter = call.rel(0);
    final Filter bottomFilter = call.rel(1);

    final RelBuilder relBuilder = call.builder();
    relBuilder
        .push(bottomFilter.getInput())
        .filter(bottomFilter.getCondition(), topFilter.getCondition());

    call.transformTo(relBuilder.build());
  }

  /** Rule configuration. */
  @Value.Immutable
  public interface Config extends RelRule.Config {
    FilterMergeRuleNoWindow.Config DEFAULT =
        ImmutableFilterMergeRuleNoWindow.Config.of().withOperandFor(Filter.class);

    @Override
    default FilterMergeRuleNoWindow toRule() {
      return new FilterMergeRuleNoWindow(this);
    }

    /** Defines an operand tree for the given classes. */
    default FilterMergeRuleNoWindow.Config withOperandFor(Class<? extends Filter> filterClass) {
      // Bodo Change: Add predicates that neither filter contains an over
      return withOperandSupplier(
              b0 ->
                  b0.operand(filterClass)
                      .predicate(f -> !f.containsOver())
                      .oneInput(
                          b1 ->
                              b1.operand(filterClass)
                                  .predicate(f -> !f.containsOver())
                                  .anyInputs()))
          .as(FilterMergeRuleNoWindow.Config.class);
    }
  }
}