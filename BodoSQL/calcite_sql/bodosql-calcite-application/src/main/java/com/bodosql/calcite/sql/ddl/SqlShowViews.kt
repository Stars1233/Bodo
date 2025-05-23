package com.bodosql.calcite.sql.ddl

import com.google.common.collect.ImmutableList
import org.apache.calcite.sql.SqlIdentifier
import org.apache.calcite.sql.SqlKind
import org.apache.calcite.sql.SqlNode
import org.apache.calcite.sql.SqlOperator
import org.apache.calcite.sql.SqlSpecialOperator
import org.apache.calcite.sql.SqlWriter
import org.apache.calcite.sql.parser.SqlParserPos

/**
 * Parse tree node representing a `SHOW VIEWS` clause.
 */
class SqlShowViews(
    pos: SqlParserPos,
    val schemaName: SqlIdentifier,
    isTerse: Boolean,
) : SqlShow(pos, isTerse) {
    companion object {
        @JvmStatic
        private val OPERATOR: SqlOperator =
            SqlSpecialOperator(
                "SHOW VIEWS",
                SqlKind.SHOW_VIEWS,
            )
    }

    override fun getOperator(): SqlOperator = OPERATOR

    override fun getOperandList(): MutableList<SqlNode>? = ImmutableList.of(schemaName)

    /**
     * This begins unparsing the clause after SHOW as the superclass
     * already deals with the SHOW statement.
     */
    override fun unparseShowOperation(
        writer: SqlWriter,
        leftPrec: Int,
        rightPrec: Int,
    ) {
        // May need to raise the IN keyword into a boolean
        // when later adding support for SHOW without specifying schema/db
        writer.keyword("VIEWS")
        writer.keyword("IN")
        schemaName.unparse(writer, leftPrec, rightPrec)
    }
}
