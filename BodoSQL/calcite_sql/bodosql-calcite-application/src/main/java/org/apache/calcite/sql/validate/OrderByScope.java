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
package org.apache.calcite.sql.validate;

import org.apache.calcite.rel.type.RelDataType;
import org.apache.calcite.rel.type.RelDataTypeField;
import org.apache.calcite.sql.SqlIdentifier;
import org.apache.calcite.sql.SqlNode;
import org.apache.calcite.sql.SqlNodeList;
import org.apache.calcite.sql.SqlSelect;
import org.apache.calcite.sql.SqlTableIdentifierWithID;

import org.checkerframework.checker.nullness.qual.Nullable;

import java.util.List;

import static org.apache.calcite.sql.validate.SqlNonNullableAccessors.getSelectList;
import static org.apache.calcite.util.Static.RESOURCE;

/**
 * Represents the name-resolution context for expressions in an ORDER BY clause.
 *
 * <p>In some dialects of SQL, the ORDER BY clause can reference column aliases
 * in the SELECT clause. For example, the query
 *
 * <blockquote><code>SELECT empno AS x<br>
 * FROM emp<br>
 * ORDER BY x</code></blockquote>
 *
 * <p>is valid.
 */
public class OrderByScope extends DelegatingScope {
  //~ Instance fields --------------------------------------------------------

  private final SqlNodeList orderList;
  private final SqlSelect select;

  //~ Constructors -----------------------------------------------------------

  OrderByScope(
      SqlValidatorScope parent,
      SqlNodeList orderList,
      SqlSelect select) {
    super(parent);
    this.orderList = orderList;
    this.select = select;
  }

  //~ Methods ----------------------------------------------------------------

  @Override public SqlNode getNode() {
    return orderList;
  }

  @Override public void findAllColumnNames(List<SqlMoniker> result) {
    final SqlValidatorNamespace ns = validator.getNamespaceOrThrow(select);
    addColumnNames(ns, result);
  }

  @Override public SqlQualified fullyQualify(SqlIdentifier identifier) {
    // Bodo Change: Check for a parameter first.
    @Nullable SqlQualified paramIdentifier = fullyQualifyIdentifierIfParameter(identifier);
    if (paramIdentifier != null) {
      return paramIdentifier;
    }
    // If it's a simple identifier, look for an alias.
    if (identifier.isSimple()
        && validator.config().conformance().isSortByAlias()) {
      final String name = identifier.names.get(0);
      final SqlValidatorNamespace selectNs =
          validator.getNamespaceOrThrow(select);
      final RelDataType rowType = selectNs.getRowType();

      final SqlNameMatcher nameMatcher = validator.catalogReader.nameMatcher();
      final RelDataTypeField field = nameMatcher.field(rowType, name);
      final int aliasCount = aliasCount(nameMatcher, name);
      if (aliasCount > 1) {
        // More than one column has this alias.
        throw validator.newValidationError(identifier,
            RESOURCE.columnAmbiguous(name));
      }
      if (field != null && !field.isDynamicStar() && aliasCount == 1) {
        // if identifier is resolved to a dynamic star, use super.fullyQualify() for such case.
        return SqlQualified.create(this, 1, selectNs, identifier);
      }
    }
    return super.fullyQualify(identifier);
  }


  /**
   * Converts a table identifier with an ID column into a fully-qualified identifier.
   * For example, the dept in "select empno from emp natural join dept" may become
   * "myschema.dept".
   *
   * @param identifier SqlTableIdentifierWithID to qualify
   * @return A qualified identifier, never null
   */
  @Override public SqlTableIdentifierWithIDQualified fullyQualify(
      SqlTableIdentifierWithID identifier) {
    // If it's a simple identifier, look for an alias.
    if (identifier.isSimple()
        && validator.config().conformance().isSortByAlias()) {
      final String name = identifier.getNames().get(0);
      final SqlValidatorNamespace selectNs =
          validator.getNamespaceOrThrow(select);
      final RelDataType rowType = selectNs.getRowType();

      final SqlNameMatcher nameMatcher = validator.catalogReader.nameMatcher();
      final RelDataTypeField field = nameMatcher.field(rowType, name);
      final int aliasCount = aliasCount(nameMatcher, name);
      if (aliasCount > 1) {
        // More than one table has this alias.
        throw validator.newValidationError(identifier,
            RESOURCE.columnAmbiguous(name));
      }
      if (field != null && aliasCount == 1) {
        // if identifier is resolved to a dynamic star, use super.fullyQualify() for such case.
        return SqlTableIdentifierWithIDQualified.create(this, 1, selectNs, identifier);
      }
    }
    return super.fullyQualify(identifier);
  }

  /** Returns the number of columns in the SELECT clause that have {@code name}
   * as their implicit (e.g. {@code t.name}) or explicit (e.g.
   * {@code t.c as name}) alias. */
  private int aliasCount(SqlNameMatcher nameMatcher, String name) {
    int n = 0;
    for (SqlNode s : getSelectList(select)) {
      final @Nullable String alias = SqlValidatorUtil.alias(s);
      if (alias != null && nameMatcher.matches(alias, name)) {
        n++;
      }
    }
    return n;
  }

  @Override public @Nullable RelDataType resolveColumn(String name, SqlNode ctx) {
    // Bodo Change: Check for a parameter first.
    @Nullable RelDataType paramType = resolveColumnIfParameter(name, ctx);
    if (paramType != null) {
      return paramType;
    }

    final SqlValidatorNamespace selectNs = validator.getNamespaceOrThrow(select);
    final RelDataType rowType = selectNs.getRowType();
    final SqlNameMatcher nameMatcher = validator.catalogReader.nameMatcher();
    final RelDataTypeField field = nameMatcher.field(rowType, name);
    if (field != null) {
      return field.getType();
    }
    final SqlValidatorScope selectScope = validator.getSelectScope(select);
    return selectScope.resolveColumn(name, ctx);
  }

  @Override public void validateExpr(SqlNode expr) {
    SqlNode expanded = validator.expandOrderExpr(select, expr);

    // expression needs to be valid in parent scope too
    parent.validateExpr(expanded);
  }
}
