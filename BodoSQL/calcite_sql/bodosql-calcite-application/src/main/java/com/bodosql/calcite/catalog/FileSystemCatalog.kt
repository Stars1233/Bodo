package com.bodosql.calcite.catalog

import com.bodosql.calcite.adapter.pandas.StreamingOptions
import com.bodosql.calcite.application.PandasCodeGenVisitor
import com.bodosql.calcite.application.write.IcebergWriteTarget
import com.bodosql.calcite.application.write.ParquetWriteTarget
import com.bodosql.calcite.application.write.WriteTarget
import com.bodosql.calcite.application.write.WriteTarget.IfExistsBehavior
import com.bodosql.calcite.ir.Expr
import com.bodosql.calcite.ir.Variable
import com.bodosql.calcite.schema.CatalogSchema
import com.bodosql.calcite.sql.ddl.SnowflakeCreateTableMetadata
import com.bodosql.calcite.table.CatalogTable
import com.bodosql.calcite.table.IcebergCatalogTable
import com.google.common.collect.ImmutableList
import org.apache.arrow.dataset.file.FileFormat
import org.apache.arrow.dataset.file.FileSystemDatasetFactory
import org.apache.arrow.dataset.jni.NativeMemoryPool
import org.apache.arrow.memory.RootAllocator
import org.apache.calcite.sql.SqlIdentifier
import org.apache.calcite.sql.ddl.SqlCreateTable
import org.apache.calcite.sql.ddl.SqlCreateTable.CreateTableType
import org.apache.calcite.sql.parser.SqlParser
import org.apache.hadoop.conf.Configuration
import org.apache.hadoop.fs.FileSystem
import org.apache.hadoop.fs.Path
import org.apache.iceberg.hadoop.HadoopCatalog
import org.apache.iceberg.hadoop.Util
import org.apache.iceberg.util.LocationUtil
import java.io.FileNotFoundException
import java.net.URI

/**
 * Implementation of a BodoSQLCatalog catalog for a filesystem catalog implementation.
 * Within the file system a schema is referred to by a directory and a table is either
 * a file or a directory with some "contents" that define the table type.
 *
 * This class can be used to implement any generic file system with a
 * connection string, but certain file systems may require additional dependencies
 * which may be more suitable for subclasses.
 *
 * Generating code for iceberg tables has been manually tested locally and on s3
 *
 * Note: Hardcoding HadoopCatalog will likely change in the future to be more abstract/robust.
 */
class FileSystemCatalog(
    connStr: String,
    private val writeDefault: WriteTarget.WriteTargetEnum,
    defaultSchema: String,
) : IcebergCatalog(createHadoopCatalog(connStr)) {
    private val fs = createFileSystem(connStr)
    private val rootPath = fs.resolvePath(Path("."))
    private val defaultSchemaList = parseDefaultSchema(defaultSchema)

    /**
     * Convert a Hadoop FileSystem Path to a Bodo supported connection string.
     * To do this we need to reformat the local file system.
     *
     * @param path The path to convert.
     * @return The converted path to a Bodo supported connection string.
     */
    private fun pathToBodoString(
        path: Path,
        useUriScheme: Boolean,
    ): String {
        val baseString = path.toString()
        return if (baseString.startsWith("s3a://")) {
            baseString.replace("s3a://", "s3://")
        } else if (baseString.startsWith("file:")) {
            val replacement = if (useUriScheme) "file://" else ""
            baseString.replace("file:", replacement)
        } else {
            baseString
        }
    }

    /**
     * Convert a given schemaPath which is a list of components to the
     * location in the file system.
     * @param schemaPath The list of schemas to traverse.
     * @return A converted schemaPath to the file system representation
     * of the location.
     */
    private fun schemaPathToFilePath(schemaPath: ImmutableList<String>): Path {
        return if (schemaPath.isEmpty()) {
            rootPath
        } else {
            fs.resolvePath(Path(schemaPath.joinToString(separator = "/")))
        }
    }

    /**
     * Convert a given schemaPath and tableName, which is the "path" to a table,
     * location in the file system.
     * @param schemaPath The list of schemas to traverse.
     * @param tableName The name of the table.
     * @return A converted path to the file system representation
     * of the location.
     */
    private fun tableInfoToFilePath(
        schemaPath: ImmutableList<String>,
        tableName: String,
    ): Path {
        return Path(schemaPathToFilePath(schemaPath), Path(tableName))
    }

    private fun getDirectoryContents(path: Path): List<Path> {
        return if (fs.getFileStatus(path).isDirectory) {
            fs.listStatus(path).map { it.path }
        } else {
            listOf()
        }
    }

    /**
     * Determine if the given path information refers to a schema. Within the
     * FileSystemCatalog a schema is any directory that doesn't map to a table.
     * @param path The file system path.
     * @return Is this path referring to a schema?
     */
    private fun isSchema(path: Path): Boolean {
        return !isTable(path) && fs.getFileStatus(path).isDirectory
    }

    /**
     * Determine if the given path information refers to a table. Within the
     * FileSystemCatalog a table is either a directory with some special
     * "indicator" contents or it's an individual file that's directly
     * supported. Files that are unknown or unsupported are not considered
     * tables.
     * @param path The file system path.
     * @return Is this path referring to a table?
     */
    private fun isTable(path: Path): Boolean {
        return isIcebergTable(path) || isParquetTable(path)
    }

    /**
     * Is the given path an Iceberg table. An iceberg table is defined
     * as a directory that contains a metadata directory in a
     * plain file system.
     * @param path The file system path.
     * @return Is this path referring to an Iceberg table?
     */
    private fun isIcebergTable(path: Path): Boolean {
        return if (!fs.getFileStatus(path).isDirectory) {
            false
        } else {
            val metadataPath = Path(path, Path("metadata"))
            val metadataStatus =
                try {
                    fs.getFileStatus(metadataPath)
                } catch (e: FileNotFoundException) {
                    null
                }
            return metadataStatus?.isDirectory ?: false
        }
    }

    /**
     * Is the given path a Parquet table which is represented
     * by a single file. A parquet table is a file that ends in
     * either ".parquet" or ".pq".
     * @param path The file system path.
     * @return Is this path referring to a Parquet table?
     */
    private fun isParquetTable(path: Path): Boolean {
        return if (!fs.getFileStatus(path).isFile) {
            false
        } else {
            val name = path.name
            name.endsWith(".parquet") || name.endsWith(".pq")
        }
    }

    /**
     * Returns a set of all table names with the given schema name.
     *
     * @param schemaPath The list of schemas to traverse before finding the table.
     * @return Set of table names.
     */
    override fun getTableNames(schemaPath: ImmutableList<String>): Set<String> {
        val fullPath = schemaPathToFilePath(schemaPath)
        val elements = getDirectoryContents(fullPath).filter { isTable(it) }.map { it.name }
        return elements.toSet()
    }

    /**
     * Returns a table with the given name and found in the given schema.
     *
     * @param schemaPath The list of schemas to traverse before finding the table.
     * @param tableName Name of the table.
     * @return The table object.
     */
    override fun getTable(
        schemaPath: ImmutableList<String>,
        tableName: String,
    ): CatalogTable {
        val path = tableInfoToFilePath(schemaPath, tableName)
        if (isIcebergTable(path)) {
            val columns = getIcebergTableColumns(schemaPath, tableName)
            return IcebergCatalogTable(tableName, schemaPath, columns, this)
        } else {
            val allocator = RootAllocator()
            val factory =
                FileSystemDatasetFactory(allocator, NativeMemoryPool.getDefault(), FileFormat.PARQUET, uriToArrowString(path.toUri()))
            val schema = factory.inspect()
            throw RuntimeException("FileSystemCatalog Error: Only Iceberg tables are currently supported.")
        }
    }

    /**
     * Get the available subSchema names for the given path.
     *
     * @param schemaPath The parent schema path to check.
     * @return Set of available schema names.
     */
    override fun getSchemaNames(schemaPath: ImmutableList<String>): Set<String> {
        val fullPath = schemaPathToFilePath(schemaPath)
        val elements = getDirectoryContents(fullPath).filter { isSchema(it) }.map { it.name }
        // Insert "." because it always exists. We use this to handle having
        // the default schema be the root if not provided by the user.
        return elements.toSet() + setOf(".")
    }

    /**
     * Returns a schema found within the given parent path.
     *
     * @param schemaPath The parent schema path to check.
     * @param schemaName Name of the schema to fetch.
     * @return A schema object.
     */
    override fun getSchema(
        schemaPath: ImmutableList<String>,
        schemaName: String,
    ): CatalogSchema {
        return CatalogSchema(schemaName, schemaPath.size + 1, schemaPath, this)
    }

    /**
     * Return the list of implicit/default schemas for the given catalog, in the order that they
     * should be prioritized during table resolution. The provided depth gives the "level" at which to
     * provide the default. Each entry in the list is a schema name at that level, not the path to reach
     * that level.
     *
     * @param depth The depth at which to find the default.
     * @return List of default Schema for this catalog.
     */
    override fun getDefaultSchema(depth: Int): List<String> {
        if (depth < defaultSchemaList.size) {
            return listOf(defaultSchemaList[depth])
        }
        return listOf()
    }

    /**
     * Return the number of levels at which a default schema may be found.
     * @return The number of levels a default schema can be found.
     */
    override fun numDefaultSchemaLevels(): Int {
        return defaultSchemaList.size
    }

    /**
     * Generates the code necessary to produce an append write expression from the given catalog.
     *
     * @param varName Name of the variable to write.
     * @param tableName The path of schema used to reach the table from the root that includes the
     * table.
     * @return The generated code to produce the append write.
     */
    override fun generateAppendWriteCode(
        visitor: PandasCodeGenVisitor?,
        varName: Variable?,
        tableName: ImmutableList<String>?,
    ): Expr {
        TODO("Not yet implemented")
    }

    /**
     * Generates the code necessary to produce a write expression from the given catalog.
     *
     * @param varName Name of the variable to write.
     * @param tableName The path of schema used to reach the table from the root that includes the
     * table.
     * @param ifExists Behavior to perform if the table already exists
     * @param createTableType Type of table to create if it doesn't exist
     * @return The generated code to produce a write.
     */
    override fun generateWriteCode(
        visitor: PandasCodeGenVisitor?,
        varName: Variable?,
        tableName: ImmutableList<String>?,
        ifExists: IfExistsBehavior,
        createTableType: SqlCreateTable.CreateTableType?,
        meta: SnowflakeCreateTableMetadata?,
    ): Expr {
        TODO("Not yet implemented")
    }

    /**
     * Generates the code necessary to produce a read expression from the given catalog.
     *
     * @param useStreaming Should we generate code to read the table as streaming (currently only
     * supported for snowflake tables)
     * @param tableName The path of schema used to reach the table from the root that includes the
     * table.
     * @param useStreaming Should we generate code to read the table as streaming (currently only
     * supported for snowflake tables)
     * @param streamingOptions The options to use if streaming is enabled.
     * @return The generated code to produce a read.
     */
    override fun generateReadCode(
        tableName: ImmutableList<String>?,
        useStreaming: Boolean,
        streamingOptions: StreamingOptions?,
    ): Expr {
        TODO("Not yet implemented")
    }

    /**
     * Generates the code necessary to submit the remote query to the catalog DB.
     *
     * @param query Query to submit.
     * @return The generated code.
     */
    override fun generateRemoteQuery(query: String): Expr {
        TODO("Not yet implemented")
    }

    /**
     * Return the db location to which this Catalog refers.
     *
     * @return The source DB location.
     */
    override fun getDBType(): String {
        TODO("Not yet implemented")
    }

    /**
     * Returns if a schema with the given depth is allowed to contain tables.
     * A file system catalog has no rules for where a table can be located.
     *
     * @param depth The number of parent schemas that would need to be visited to reach the root.
     * @return True
     */
    override fun schemaDepthMayContainTables(depth: Int): Boolean {
        return true
    }

    /**
     * Returns if a schema with the given depth is allowed to contain subSchemas.
     * A file system catalog has no rules for where a subschema can be located.
     *
     * @param depth The number of parent schemas that would need to be visited to reach the root.
     * @return True.
     */
    override fun schemaDepthMayContainSubSchemas(depth: Int): Boolean {
        return true
    }

    /**
     * Generate a Python connection string used to read from or write to a Catalog in Bodo's SQL
     * Python code.
     *
     *
     * TODO(jsternberg): This method is needed for the XXXToPandasConverter nodes, but exposing
     * this is a bad idea and this class likely needs to be refactored in a way that the connection
     * information can be passed around more easily.
     *
     * @param schemaPath The schema component to define the connection not including the table name.
     * @return The connection string
     */
    override fun generatePythonConnStr(schemaPath: ImmutableList<String>): String {
        return pathToBodoString(rootPath, true)
    }

    /**
     *  Create a FileSystem object from the given connection string.
     *  @param connStr The connection string to the file system.
     *  @return The file system object.
     */
    private fun createFileSystem(connStr: String): FileSystem {
        val updatedConnStr = updateConnStr(connStr)
        val conf = createConf()

        val fs =
            Util.getFs(
                Path(LocationUtil.stripTrailingSlash(updatedConnStr)),
                conf,
            )
        val rootPath = Path(updatedConnStr)
        fs.workingDirectory = rootPath
        if (!fs.getFileStatus(rootPath).isDirectory) {
            throw RuntimeException("FileSystemCatalog Error: Root Path provided is not a valid directory.")
        }
        return fs
    }

    /**
     * Return the desired WriteTarget for a create table operation.
     * Currently, we only allow writing as an Iceberg table.
     *
     * @param schema The schemaPath to the table.
     * @param tableName The name of the type that will be created.
     * @param createTableType The createTable type. This is unused by the file system catalog.
     * @param ifExistsBehavior The createTable behavior for if there is already a table defined.
     * @param columnNamesGlobal Global Variable holding the output column names.
     * @return The selected WriteTarget.
     */
    override fun getCreateTableWriteTarget(
        schema: ImmutableList<String>,
        tableName: String,
        createTableType: CreateTableType,
        ifExistsBehavior: IfExistsBehavior,
        columnNamesGlobal: Variable,
    ): WriteTarget {
        return if (writeDefault == WriteTarget.WriteTargetEnum.ICEBERG) {
            IcebergWriteTarget(
                tableName,
                schema,
                ifExistsBehavior,
                columnNamesGlobal,
                pathToBodoString(rootPath, true),
            )
        } else {
            assert(writeDefault == WriteTarget.WriteTargetEnum.PARQUET)
            ParquetWriteTarget(
                tableName,
                schema,
                ifExistsBehavior,
                columnNamesGlobal,
                pathToBodoString(tableInfoToFilePath(schema, tableName), false),
            )
        }
    }

    companion object {
        /**
         * Create a HadoopCatalog object from the given connection string.
         * @param connStr The connection string to the file system.
         * @return The HadoopCatalog object.
         */
        @JvmStatic
        private fun createHadoopCatalog(connStr: String): HadoopCatalog {
            val updatedConnStr = updateConnStr(connStr)
            val conf = createConf()
            return HadoopCatalog(conf, updatedConnStr)
        }

        /**
         * Update the connection string to be compatible with Hadoop.
         * @param connStr The connection string to the file system.
         * @return The updated connection string.
         */
        @JvmStatic
        private fun updateConnStr(connStr: String): String {
            if (connStr.isEmpty()) {
                throw RuntimeException("FileSystemCatalog Error: Connection string must be provided.")
            }
            // Hadoop wants s3a:// instead of s3://
            return if (connStr.startsWith("s3://")) connStr.replace("s3://", "s3a://") else connStr
        }

        /**
         * Create a Configuration object additional AWS authentication providers enabled.
         * @return The Configuration object.
         */
        @JvmStatic
        private fun createConf(): Configuration {
            val conf = Configuration()
            // This is to add the Profile credential provider which for some reason isn't used by default
            conf.set(
                "fs.s3a.aws.credentials.provider",
                "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider," +
                    "com.amazonaws.auth.EnvironmentVariableCredentialsProvider," +
                    "com.amazonaws.auth.profile.ProfileCredentialsProvider," +
                    "com.amazonaws.auth.InstanceProfileCredentialsProvider," +
                    "org.apache.hadoop.fs.s3a.AnonymousAWSCredentialsProvider",
            )
            return conf
        }

        @JvmStatic
        private fun parseDefaultSchema(defaultSchema: String): ImmutableList<String> {
            // Special handling for "." so we always have a default schema.
            if (defaultSchema == ".") {
                return ImmutableList.of(".")
            }
            // Note: We don't need a config for basic functionality
            // because we only need identifier parsing and the casing
            // matches the default.
            // TODO: Match our Parser config static and use it for
            // additional peace of mind.
            val parser = SqlParser.create(defaultSchema)
            val node = parser.parseExpression()
            if (node !is SqlIdentifier) {
                throw RuntimeException("FileSystemCatalog Error: Default schema must be a valid SQL DOT separated compound identifier.")
            }
            return node.names
        }

        /**
         * Converts a URI to the format usable by Arrow,
         * including changing any filesystem information.
         * @param uri The URI to convert.
         * @return The converted URI as a string.
         */
        @JvmStatic
        private fun uriToArrowString(uri: URI): String {
            val strVal = uri.toString()
            return if (strVal.startsWith("s3a://")) {
                strVal.replace("s3a://", "s3://")
            } else {
                strVal
            }
        }
    }
}