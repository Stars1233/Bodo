package com.bodosql.calcite.application.Testing;

import com.bodosql.calcite.adapter.pandas.PandasUtilKt;
import com.bodosql.calcite.application.RelationalAlgebraGenerator;
import com.bodosql.calcite.application.utils.RelCostAndMetaDataWriter;
import com.bodosql.calcite.application.write.WriteTarget;
import com.bodosql.calcite.catalog.BodoSQLCatalog;
import com.bodosql.calcite.catalog.FileSystemCatalog;
import com.bodosql.calcite.schema.LocalSchema;
import com.bodosql.calcite.traits.BatchingProperty;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.Map;
import org.apache.calcite.rel.RelNode;
import org.apache.calcite.rel.RelRoot;

/** Class for locally testing codegen using a FileSystem Catalog */
public class FileSystemCatalogGenTest {
  public static void main(String[] args) throws Exception {
    String sql = "select * from SIMPLE_STRUCT_TABLE";
    Map envVars = System.getenv();
    BodoSQLCatalog catalog =
        new FileSystemCatalog(
            (String) envVars.get("ROOT_PATH"),
            WriteTarget.WriteTargetEnum.fromString("parquet"),
            (String) envVars.get("DEFAULT_SCHEMA"));
    LocalSchema schema = new LocalSchema("__BODOLOCAL__");

    RelationalAlgebraGenerator generator =
        new RelationalAlgebraGenerator(
            catalog,
            schema,
            "dummy_param_table_name",
            RelationalAlgebraGenerator.STREAMING_PLANNER,
            0,
            BatchingProperty.defaultBatchSize,
            true, // Always hide credentials
            true, // Always inline views
            true, // Enable Iceberg for testing
            false // Do not enable TIMESTAMPTZ for Iceberg testing
            );
    System.out.println("SQL query:");
    System.out.println(sql + "\n");
    String optimizedPlanStr = getRelationalAlgebraString(generator, sql, true);
    System.out.println("Optimized plan:");
    System.out.println(optimizedPlanStr + "\n");
    String pandasStr = generator.getPandasString(sql);
    System.out.println("Generated code:");
    System.out.println(pandasStr + "\n");
  }

  private static String getRelationalAlgebraString(
      RelationalAlgebraGenerator generator, String sql, boolean optimizePlan) {
    try {
      RelRoot root = generator.getRelationalAlgebra(sql, optimizePlan);
      RelNode newRoot = PandasUtilKt.pandasProject(root);
      StringWriter sw = new StringWriter();
      RelCostAndMetaDataWriter costWriter =
          new RelCostAndMetaDataWriter(new PrintWriter(sw), newRoot);
      newRoot.explain(costWriter);
      return sw.toString();
    } catch (Exception e) {
      throw new RuntimeException(e);
    }
  }
}