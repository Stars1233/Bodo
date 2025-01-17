package com.bodo.iceberg;

import com.bodo.iceberg.catalog.CatalogCreator;
import com.bodo.iceberg.catalog.PrefetchSnowflakeCatalog;
import com.bodo.iceberg.catalog.SnowflakeBuilder;
import com.bodo.iceberg.filters.FilterExpr;
import java.io.IOException;
import java.net.URISyntaxException;
import java.sql.SQLException;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import javax.annotation.Nonnull;
import javax.annotation.Nullable;
import org.apache.iceberg.*;
import org.apache.iceberg.catalog.Catalog;
import org.apache.iceberg.catalog.Namespace;
import org.apache.iceberg.catalog.TableIdentifier;
import org.apache.iceberg.io.FileIO;
import org.apache.iceberg.types.TypeUtil;
import org.apache.iceberg.types.Types;
import org.json.JSONArray;

public class BodoIcebergHandler {
  /**
   * Java Class used to map Bodo's required read and write operations to a corresponding Iceberg
   * table. This is meant to provide 1 instance per Table and Bodo is responsible for closing it.
   */
  private final Catalog catalog;

  // Map of transaction hashcode to transaction instance
  private final HashMap<Integer, Transaction> transactions;

  private BodoIcebergHandler(Catalog catalog) {
    this.catalog = catalog;
    this.transactions = new HashMap<>();
  }

  // Note: This API is exposed to Python.
  public BodoIcebergHandler(String connStr, String catalogType, String coreSitePath)
      throws URISyntaxException {
    this(CatalogCreator.create(connStr, catalogType, coreSitePath));
  }

  /**
   * Start the Snowflake query to get the metadata paths for a list of Snowflake-managed Iceberg
   * tables. NOTE: This API is exposed to Python.
   *
   * <p>The query is not expected to finish until the table is needed for initialization / read.
   *
   * @param tablePathsStr A JSON string of a list of tablePaths Py4J can't pass the direct list of
   *     strings in
   */
  public static BodoIcebergHandler buildPrefetcher(
      String connStr,
      String catalogType,
      String coreSitePath,
      String tablePathsStr,
      int verboseLevel)
      throws SQLException, URISyntaxException {

    if (!Objects.equals(catalogType, "snowflake")) {
      throw new RuntimeException(
          "BodoIcebergHandler::buildPrefetcher: Cannot prefetch SF metadata paths for a"
              + " catalog of type "
              + catalogType);
    }

    // Convert table paths to list of strings
    var tablePathsJSON = new JSONArray(tablePathsStr);
    var tablePaths = new ArrayList<String>();
    for (int i = 0; i < tablePathsJSON.length(); i++) {
      tablePaths.add(tablePathsJSON.getString(i));
    }

    // Construct Catalog
    var out = CatalogCreator.prepareInput(connStr, catalogType, coreSitePath);
    var catalog = new PrefetchSnowflakeCatalog(connStr, tablePaths, verboseLevel);
    SnowflakeBuilder.initialize(catalog, out.getFirst(), out.getSecond());
    return new BodoIcebergHandler(CachingCatalog.wrap(catalog));
  }

  private static TableIdentifier genTableID(String dbName, String tableName) {
    // Snowflake uses dot separated strings for DB and schema names
    // Iceberg uses Namespaces with multiple levels to represent this
    Namespace dbNamespace = Namespace.of(dbName.split("\\."));
    return TableIdentifier.of(dbNamespace, tableName);
  }

  /**
   * Inner function to help load tables
   *
   * @return Iceberg table associated with ID
   */
  private Table loadTable(String dbName, String tableName) {
    return catalog.loadTable(genTableID(dbName, tableName));
  }

  /**
   * Get a specific property from Table properties
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param property The name of the property to get.
   * @return the corresponding property value or null if key does not exist
   */
  public String getTableProperty(String dbName, String tableName, String property) {
    Table table = loadTable(dbName, tableName);
    return table.properties().get(property);
  }

  /**
   * Remove a transaction from the transactions map if it exists. This is done manually, so we can
   * access the underlying transaction information even after the transaction has been committed.
   * This is useful because the Transaction.table() should hold the status of the last snapshot used
   * to commit the table.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param txnID The id of the transaction to remove. If this id is not found this is a NO-OP.
   */
  public void removeTransaction(int txnID) {
    this.transactions.remove(txnID);
  }

  /**
   * When creating a new table using createOrReplaceTable, we pass it a schema with valid field IDs
   * populated. However, as part of the transaction, Iceberg Java library creates a "fresh schema"
   * and re-assigns field IDs. They say it's for "consistency" reasons. However, this is problematic
   * for us since we only commit after writing the parquet files, which means that the field IDs we
   * wrote in the parquet fields' metadata could be incorrect. To work around this, we instead call
   * this function before writing any parquet files. This simulates and generate the field-ids in
   * the same way that createOrReplaceTable would. Using these field IDs is therefore more reliable.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param bodoSchema Original schema generated by Bodo with field IDs.
   * @return Same schema, except the field IDs would be as if generated by Iceberg Java Library in
   *     the final metadata during commit.
   */
  public static Schema getInitSchema(Schema bodoSchema) {
    // Taken from the 'newTableMetadata' function. This is important since it's
    // what the Catalog implementations call to generate schema during the
    // create/replace transaction.
    // Flow:
    // - catalog.newCreateTableTransaction
    // - buildTable (in Catalog.newCreateTableTransaction). This returns an instance
    // of BaseMetastoreCatalogTableBuilder.
    // - BaseMetastoreCatalogTableBuilder.create_transaction(). This calls
    // TableMetadata.newTableMetadata().
    // - This creates a 'freshSchema' using this code:
    AtomicInteger lastColumnId = new AtomicInteger(0);
    return TypeUtil.assignFreshIds(bodoSchema, lastColumnId::incrementAndGet);
  }

  /**
   * Get Information About Table
   *
   * <p>Note: This API is exposed to Python.
   *
   * @return Information about Table needed by Bodo
   */
  public TableInfo getTableInfo(String dbName, String tableName, boolean error)
      throws SQLException, URISyntaxException, InterruptedException {
    if (!catalog.tableExists(genTableID(dbName, tableName)) && !error) {
      return null;
    }

    // Note that repeated calls to loadTable are cheap due to CachingCatalog
    return new TableInfo(loadTable(dbName, tableName));
  }

  /**
   * Returns a list of parquet files that construct the given Iceberg table.
   *
   * <p>Note: This API is exposed to Python.
   */
  public Triple<
          List<BodoParquetInfo>, Map<Integer, org.apache.arrow.vector.types.pojo.Schema>, Long>
      getParquetInfo(String dbName, String tableName, FilterExpr filters) throws IOException {
    return FileHandler.getParquetInfo(loadTable(dbName, tableName), filters);
  }

  /**
   * Return a boolean list indicating which columns have theta sketch blobs.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @return List of booleans indicating which columns have theta sketches. The booleans correspond
   *     to the columns in the schema, not the field IDs.
   */
  public List<Boolean> tableColumnsHaveThetaSketches(String dbName, String tableName) {
    Table table = loadTable(dbName, tableName);
    Schema schema = table.schema();
    List<Types.NestedField> columns = schema.columns();
    List<Boolean> hasThetaSketches = new ArrayList<>(Collections.nCopies(columns.size(), false));
    Snapshot currentSnapshot = table.currentSnapshot();
    if (currentSnapshot != null) {
      // Create a mapping of field ID to column index
      Map<Integer, Integer> fieldIdToIndex = new HashMap<>();
      for (int i = 0; i < columns.size(); i++) {
        fieldIdToIndex.put(columns.get(i).fieldId(), i);
      }
      List<StatisticsFile> statisticsFiles = table.statisticsFiles();
      for (StatisticsFile statisticsFile : statisticsFiles) {
        if (statisticsFile.snapshotId() == currentSnapshot.snapshotId()) {
          for (BlobMetadata blobMetadata : statisticsFile.blobMetadata()) {
            // We only support theta sketches with a single field that is still
            // in the schema.
            String type = blobMetadata.type();
            if (!type.equals("apache-datasketches-theta-v1")) {
              continue;
            }
            List<Integer> fields = blobMetadata.fields();
            if (fields.size() != 1) {
              continue;
            }
            int field = fields.get(0);
            if (fieldIdToIndex.containsKey(field)) {
              hasThetaSketches.set(fieldIdToIndex.get(field), true);
            }
          }
          // There can only be one statistics file per snapshot
          return hasThetaSketches;
        }
      }
    }
    return hasThetaSketches;
  }

  /**
   * Return a boolean list indicating which columns have theta sketches enabled, as per the <code>
   * bodo.write.theta_sketch_enabled.COLUMN_NAME</code> table property. If the property does not
   * exist for a column, the decision will default to enabled / bodo's engine decision.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @return List of booleans indicating which columns have theta sketches enabled. The booleans
   *     correspond to the columns in the schema, not the field IDs.
   */
  public List<Boolean> tableColumnsEnabledThetaSketches(String dbName, String tableName) {
    Table table = loadTable(dbName, tableName);
    Schema schema = table.schema();
    List<Types.NestedField> columns = schema.columns();
    List<Boolean> enabledThetaSketches =
        new ArrayList<>(Collections.nCopies(columns.size(), false));
    // Iterate through properties
    for (int i = 0; i < columns.size(); i++) {
      String colName = columns.get(i).name();
      String isEnabled = table.properties().get("bodo.write.theta_sketch_enabled." + colName);
      if (isEnabled == null || isEnabled.equalsIgnoreCase("true"))
        enabledThetaSketches.set(i, true);
    }
    return enabledThetaSketches;
  }

  /**
   * Helper function that returns the total number of files present in the given Iceberg table.
   * Currently only used for logging purposes.
   *
   * <p>Note: This API is exposed to Python.
   */
  public int getNumParquetFiles(String dbName, String tableName) {

    // First, check if we can get the information from the summary
    Table table = loadTable(dbName, tableName);
    Snapshot currentSnapshot = table.currentSnapshot();

    if (currentSnapshot.summary().containsKey("total-data-files")) {
      return Integer.parseInt(currentSnapshot.summary().get("total-data-files"));
    }

    // If it doesn't exist in the summary, check the manifestList
    // TODO: is this doable? I can get the manifestList location, but I don't see a way
    // to get any metadata from this list.
    // A manifest list includes summary metadata that can be used to avoid scanning all of the
    // manifests
    // in a snapshot when planning a table scan. This includes the number of added, existing, and
    // deleted files,
    // and a summary of values for each field of the partition spec used to write the manifest.

    // If it doesn't exit in the manifestList, calculate it by iterating over each manifest file
    FileIO io = table.io();
    List<ManifestFile> manifestFilesList = currentSnapshot.allManifests(io);
    int totalFiles = 0;
    for (ManifestFile manifestFile : manifestFilesList) {
      // content = 0 means the manifest file is for a data file
      if (0 != manifestFile.content().id()) {
        continue;
      }
      Integer existingFiles = manifestFile.existingFilesCount();
      Integer addedFiles = manifestFile.addedFilesCount();
      Integer deletedFiles = manifestFile.deletedFilesCount();

      if (existingFiles == null || addedFiles == null || deletedFiles == null) {
        // If any of the option fields are null, we have to manually read the file
        ManifestReader<DataFile> manifestContents = ManifestFiles.read(manifestFile, io);
        if (!manifestContents.isDeleteManifestReader()) {
          for (DataFile _manifestContent : manifestContents) {
            totalFiles += 1;
          }
        }
      } else {
        totalFiles += existingFiles + addedFiles - deletedFiles;
      }
    }

    return totalFiles;
  }

  /**
   * Updates a Snapshot update operation with the app-id field. This is used to easily identify
   * tables created by Bodo.
   */
  private static void appendAppID(SnapshotUpdate<?> op) {
    op.set("app-id", "bodo");
  }

  /**
   * Get the location of the table in the underlying storage
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param txnID Transaction ID of transaction to get the table location
   * @return Location of the table
   */
  public String getTransactionTableLocation(int txnID) {
    Transaction txn = this.transactions.get(txnID);
    return txn.table().location();
  }

  /**
   * Get the snapshot ID of the table for the underlying transaction. To ensure this is the final
   * snapshot ID, we likely want to call this after the transaction has been committed. However, the
   * transaction ID should remain constant even if we retry.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param txnID Transaction ID of transaction.
   * @return Snapshot ID of the table for the transaction or null if there is no snapshot.
   */
  public @Nullable Long getTransactionSnapshotID(int txnID) {
    Transaction txn = this.transactions.get(txnID);
    Snapshot snapshot = txn.table().currentSnapshot();
    if (snapshot == null) {
      return null;
    } else {
      return snapshot.snapshotId();
    }
  }

  /**
   * Get the sequence number of the table for the underlying transaction. This must be called AFTER
   * the transaction has been committed because the sequence number is only finalized after commit.
   * Any value before may be incorrect if the commit needs to retry.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param txnID Transaction ID of transaction.
   * @return Sequence number of the table for the transaction or null if there is no snapshot.
   */
  public @Nullable Long getTransactionSequenceNumber(int txnID) {
    Transaction txn = this.transactions.get(txnID);
    Snapshot snapshot = txn.table().currentSnapshot();
    if (snapshot == null) {
      return null;
    } else {
      return snapshot.sequenceNumber();
    }
  }

  /**
   * Get the Statistics file location for a transaction's table. This should be safe to call before
   * or after the transaction has been committed, but we will always call it after commit.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param txnID Transaction ID of transaction.
   * @return Sequence number of the table for the transaction or null if there is no snapshot.
   */
  public @Nonnull String getTransactionStatisticFileLocation(int txnID) {
    String tableLocation = getTransactionTableLocation(txnID);
    @Nullable Long snapshotId = getTransactionSnapshotID(txnID);
    if (snapshotId == null) {
      throw new RuntimeException(
          "Table does not have a snapshot. Cannot get statistics file location.");
    }
    // Generate a random file name based upon the snapshot ID, so it's always unique.
    String statsFileName = String.format(Locale.ROOT, "%d-%s.stats", snapshotId, UUID.randomUUID());
    return String.format(Locale.ROOT, "%s/metadata/%s", tableLocation, statsFileName);
  }

  /**
   * Get the existing statistics files for the table. This function raises an exception if the table
   * does not have a valid statistics file as that should already be checked.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param txnID Transaction ID of transaction.
   * @return An existing table file for snapshot of the table in the current transaction state.
   */
  public @Nonnull String getStatisticsFilePath(int txnID) {
    Transaction txn = this.transactions.get(txnID);
    Table table = txn.table();
    Snapshot snapshot = txn.table().currentSnapshot();
    if (snapshot == null) {
      throw new RuntimeException(
          "Table does not have a snapshot. Cannot get statistics file location.");
    }
    List<StatisticsFile> statisticsFiles = table.statisticsFiles();
    for (StatisticsFile statisticsFile : statisticsFiles) {
      if (statisticsFile.snapshotId() == snapshot.snapshotId()) {
        return statisticsFile.path();
      }
    }
    throw new RuntimeException(
        "Table does not have a valid statistics file. Cannot get statistics file location.");
  }

  /**
   * Return the location of the table metadata file.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @return Location of the table metadata file.
   */
  public @Nonnull String getTableMetadataPath(String dbName, String tableName) {
    Table table = loadTable(dbName, tableName);
    if (table instanceof HasTableOperations) {
      HasTableOperations opsTable = (HasTableOperations) table;
      return opsTable.operations().current().metadataFileLocation();
    } else {
      throw new RuntimeException("Unable to determine table metadata path.");
    }
  }

  /**
   * Update properties of a transaction and remove (existing) table comments. Currently using a map
   * for possible generalization of other properties.
   *
   * @param txn Transaction ID
   * @param prop Map of key-value pairs of properties to insert into transaction
   */
  public void UpdateTxnProperties(Transaction txn, Map<String, String> prop) {
    UpdateProperties txnupd = txn.updateProperties();
    for (Map.Entry<String, String> entry : prop.entrySet()) {
      String key = entry.getKey();
      String value = entry.getValue();
      txnupd = txnupd.set(key, value);
    }
    txnupd.commit();
  }

  /**
   * Create a transaction to create a new table in the DB.
   *
   * <p>Note: This API is exposed to Python.
   *
   * @param schema Schema of the table
   * @param replace Whether to replace the table if it already exists
   * @return Transaction ID
   */
  public Integer startCreateOrReplaceTable(
      String dbName, String tableName, Schema schema, boolean replace, Map<String, String> prop)
      throws SQLException, URISyntaxException, InterruptedException {
    Map<String, String> properties = new HashMap<>();
    properties.put(TableProperties.FORMAT_VERSION, "2");
    // TODO: Support passing in new partition spec and sort order as well
    final Transaction txn;
    TableIdentifier id = genTableID(dbName, tableName);

    if (replace) {
      if (getTableInfo(dbName, tableName, false) == null) {
        // Temporarily create the table to avoid breaking the rest catalog.
        // TODO: REMOVE. The REST catalog runtime should use the information
        // from the active transaction.
        catalog.createTable(id, schema, PartitionSpec.unpartitioned(), properties);
      }
      txn =
          catalog.newReplaceTableTransaction(
              id, schema, PartitionSpec.unpartitioned(), properties, true);
    } else {
      // Create the table and then replace it,
      // this is so we can fetch credentials for the table in python, otherwise we get a table not
      // found error
      catalog.createTable(id, schema, PartitionSpec.unpartitioned(), properties);

      txn =
          catalog.newReplaceTableTransaction(
              id, schema, PartitionSpec.unpartitioned(), properties, false);
    }
    // Same as directly adding prop into properties dictionary above.
    UpdateTxnProperties(txn, prop);
    this.transactions.put(txn.hashCode(), txn);
    return txn.hashCode();
  }

  /**
   * Commit a new table in the DB.
   *
   * <p>Note: This API is exposed to Python.
   */
  public void commitCreateOrReplaceTable(int txnID, String fileInfoJson) {
    Transaction txn = this.transactions.get(txnID);
    List<DataFileInfo> fileInfos = DataFileInfo.fromJson(fileInfoJson);
    this.addData(txn.newAppend(), PartitionSpec.unpartitioned(), SortOrder.unsorted(), fileInfos);
    txn.commitTransaction();
  }

  /**
   * Start a transaction to append data to a pre-existing table
   *
   * <p>Note: This API is exposed to Python.
   */
  public Integer startAppendTable(String dbName, String tableName, Map<String, String> prop) {
    Transaction txn = loadTable(dbName, tableName).newTransaction();
    UpdateTxnProperties(txn, prop);
    this.transactions.put(txn.hashCode(), txn);
    return txn.hashCode();
  }

  /**
   * Commit appending rows into a pre-existing table.
   *
   * <p>Note: This API is exposed to Python.
   */
  public void commitAppendTable(int txnID, String fileInfoJson, int schemaID) {
    Transaction txn = this.transactions.get(txnID);
    Table table = txn.table();

    List<DataFileInfo> fileInfos = DataFileInfo.fromJson(fileInfoJson);
    this.addData(txn.table().newAppend(), table.spec(), table.sortOrder(), fileInfos);
    txn.commitTransaction();
  }

  /**
   * Commit a statistics file to the table.
   *
   * <p>Note: This API is exposed to Python.
   */
  public void commitStatisticsFile(
      String dbName, String tableName, long snapshotID, String statisticsFileJson) {
    StatisticsFile statisticsFile = BodoStatisticFile.fromJson(statisticsFileJson);
    Table table = loadTable(dbName, tableName);
    Transaction txn = table.newTransaction();
    txn.updateStatistics().setStatistics(snapshotID, statisticsFile).commit();
    txn.commitTransaction();
  }

  /**
   * Merge Rows into Pre-existing Table by Copy-on-Write Rules
   *
   * <p>Note: This API is exposed to Python.
   */
  public void mergeCOWTable(
      String dbName,
      String tableName,
      List<String> oldFileNames,
      String newFileInfoJson,
      long snapshotID) {

    // Remove the Table instance associated with `id` from the cache
    // So that the next load gets the current instance from the underlying catalog
    TableIdentifier id = genTableID(dbName, tableName);
    catalog.invalidateTable(id);
    Table table = catalog.loadTable(id);
    if (table.currentSnapshot().snapshotId() != snapshotID)
      throw new IllegalStateException(
          "Iceberg Table has been updated since reading. Can not complete MERGE INTO");

    List<DataFileInfo> fileInfos = DataFileInfo.fromJson(newFileInfoJson);

    this.overwriteData(
        table.newTransaction(), table.spec(), table.sortOrder(), oldFileNames, fileInfos);
  }

  /** Insert data files into the table */
  public void addData(
      AppendFiles action, PartitionSpec spec, SortOrder order, List<DataFileInfo> fileInfos) {
    // Make sure to set the app-id field to "bodo" for easy identification
    appendAppID(action);
    boolean isPartitionedPaths = spec.isPartitioned();

    for (DataFileInfo info : fileInfos) {
      DataFile dataFile = info.toDataFile(spec, order, isPartitionedPaths);
      action.appendFile(dataFile);
    }

    action.commit();
  }

  /** Overwrite Data Files with New Modified Versions */
  public void overwriteData(
      Transaction transaction,
      PartitionSpec spec,
      SortOrder order,
      List<String> oldFileNames,
      List<DataFileInfo> newFiles) {
    boolean isPartitionedPaths = spec.isPartitioned();

    // Data Files should be uniquely identified by path only. Other values should
    // not matter
    DeleteFiles delAction = transaction.newDelete();
    // Make sure to set the app-id field to "bodo" for easy identification
    appendAppID(delAction);
    for (String oldFileName : oldFileNames) {
      delAction.deleteFile(oldFileName);
    }
    delAction.commit();

    AppendFiles action = transaction.newAppend();
    // Make sure to set the app-id field to "bodo" for easy identification
    appendAppID(action);
    for (DataFileInfo newFile : newFiles) {
      action.appendFile(newFile.toDataFile(spec, order, isPartitionedPaths));
    }
    action.commit();

    transaction.commitTransaction();
  }

  /**
   * Fetch the snapshot id for a table. Returns -1 for a newly created table without any snapshots
   */
  public long getSnapshotId(String dbName, String tableName) {
    Snapshot snapshot = loadTable(dbName, tableName).currentSnapshot();
    // When the table has just been created
    if (snapshot == null) {
      return -1;
    }
    return snapshot.snapshotId();
  }

  /**
   * Delete the table from the catalog
   *
   * @param purge Whether to purge the table from the underlying storage
   * @return Whether the table was successfully deleted
   */
  public boolean deleteTable(String dbName, String tableName, boolean purge) {
    return catalog.dropTable(genTableID(dbName, tableName), purge);
  }
}
