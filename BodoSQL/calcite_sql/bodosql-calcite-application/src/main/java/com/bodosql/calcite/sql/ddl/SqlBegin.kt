package com.bodosql.calcite.sql.ddl

import com.google.common.collect.ImmutableList
import org.apache.calcite.sql.SqlDdl
import org.apache.calcite.sql.SqlKind
import org.apache.calcite.sql.SqlNode
import org.apache.calcite.sql.SqlOperator
import org.apache.calcite.sql.SqlSpecialOperator
import org.apache.calcite.sql.SqlWriter
import org.apache.calcite.sql.parser.SqlParserPos

/**
 * <code>SqlBegin</code> is a parse tree node representing a BEGIN
 * transaction statement. BodoSQL does not support transactions right now,
 * so it is considered a no-op
 */
class SqlBegin(pos: SqlParserPos) : SqlDdl(OPERATOR, pos) {
    companion object {
        @JvmStatic
        private val OPERATOR: SqlOperator =
            SqlSpecialOperator("BEGIN", SqlKind.BEGIN)
    }

    override fun getOperandList(): List<SqlNode> = ImmutableList.of()

    override fun unparse(
        writer: SqlWriter,
        leftPrec: Int,
        rightPrec: Int,
    ) {
        writer.keyword(operator.name)
    }
}