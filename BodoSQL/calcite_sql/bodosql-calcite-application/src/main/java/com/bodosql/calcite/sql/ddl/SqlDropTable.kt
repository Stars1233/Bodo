package com.bodosql.calcite.sql.ddl

import com.google.common.collect.ImmutableList
import org.apache.calcite.sql.*
import org.apache.calcite.sql.parser.SqlParserPos
import org.apache.calcite.sql.validate.SqlValidator
import org.apache.calcite.sql.validate.SqlValidatorScope

/**
 * Represents the DROP TABLE DDL command in SQL.
 *
 * This is an expansion of the one within Calcite using the same SqlKind
 * as this one also supports specifying CASCADE or RESTRICT.
 */
class SqlDropTable(pos: SqlParserPos, ifExists: Boolean, val name: SqlIdentifier, val cascade: Boolean) : SqlDrop(OPERATOR, pos, ifExists) {
    companion object {
        @JvmStatic
        private val OPERATOR: SqlOperator =
            SqlSpecialOperator("DROP TABLE", SqlKind.DROP_TABLE)
    }

    override fun getOperandList(): List<SqlNode> = ImmutableList.of(name)

    override fun unparse(writer: SqlWriter, leftPrec: Int, rightPrec: Int) {
        writer.keyword(operator.name)
        if (ifExists) {
            writer.keyword("IF EXISTS")
        }
        name.unparse(writer, leftPrec, rightPrec)
        writer.keyword(
            if (cascade) "CASCADE" else "RESTRICT"
        )
    }
}