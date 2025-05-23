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
package com.bodosql.calcite.rel.core;

import com.bodosql.calcite.plan.RelOptRowSamplingParameters;
import java.util.List;
import org.apache.calcite.plan.Convention;
import org.apache.calcite.plan.RelOptCluster;
import org.apache.calcite.plan.RelTraitSet;
import org.apache.calcite.rel.RelInput;
import org.apache.calcite.rel.RelNode;
import org.apache.calcite.rel.RelWriter;
import org.apache.calcite.rel.SingleRel;

/**
 * Relational expression that returns a fixed-size sample of the rows from its input.
 *
 * <p>In SQL, a sample is expressed using the {@code TABLESAMPLE BERNOULLI} or {@code SYSTEM}
 * keyword applied to a table, view or sub-query.
 */
public class RowSample extends SingleRel {
  // ~ Instance fields --------------------------------------------------------

  private final RelOptRowSamplingParameters params;

  // ~ Constructors -----------------------------------------------------------

  public RowSample(RelOptCluster cluster, RelNode child, RelOptRowSamplingParameters params) {
    super(cluster, cluster.traitSetOf(Convention.NONE), child);
    this.params = params;
  }

  /** Creates a RowSample by parsing serialized output. */
  public RowSample(RelInput input) {
    this(input.getCluster(), input.getInput(), getRowSamplingParameters(input));
  }

  // ~ Methods ----------------------------------------------------------------

  private static RelOptRowSamplingParameters getRowSamplingParameters(RelInput input) {
    String mode = input.getString("mode");
    Object rows = input.get("rows");
    Object repeatableSeed = input.get("repeatableSeed");
    boolean repeatable = repeatableSeed instanceof Number;
    int numberOfRows = rows != null ? ((Number) rows).intValue() : 0;
    return new RelOptRowSamplingParameters(
        "bernoulli".equals(mode),
        numberOfRows,
        repeatable,
        repeatable && repeatableSeed != null ? ((Number) repeatableSeed).intValue() : 0);
  }

  @Override
  public RelNode copy(RelTraitSet traitSet, List<RelNode> inputs) {
    assert traitSet.containsIfApplicable(Convention.NONE);
    return new RowSample(getCluster(), sole(inputs), params);
  }

  /** Retrieve the row sampling parameters for this RowSample. */
  public RelOptRowSamplingParameters getRowSamplingParameters() {
    return params;
  }

  @Override
  public RelWriter explainTerms(RelWriter pw) {
    return super.explainTerms(pw)
        .item("mode", params.isBernoulli() ? "bernoulli" : "system")
        .item("rows", params.getNumberOfRows())
        .item("repeatableSeed", params.isRepeatable() ? params.getRepeatableSeed() : "-");
  }
}
