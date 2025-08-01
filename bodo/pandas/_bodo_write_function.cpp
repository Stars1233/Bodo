#include "_bodo_write_function.h"
#include "physical/write_iceberg.h"
#include "physical/write_parquet.h"
#include "physical/write_s3_vectors.h"

std::shared_ptr<PhysicalSink> ParquetWriteFunctionData::CreatePhysicalOperator(
    std::shared_ptr<bodo::Schema> in_table_schema) {
    return std::make_shared<PhysicalWriteParquet>(in_table_schema, *this);
}

std::shared_ptr<PhysicalSink> IcebergWriteFunctionData::CreatePhysicalOperator(
    std::shared_ptr<bodo::Schema> in_table_schema) {
    return std::make_shared<PhysicalWriteIceberg>(in_table_schema, *this);
}

std::shared_ptr<PhysicalSink>
S3VectorsWriteFunctionData::CreatePhysicalOperator(
    std::shared_ptr<bodo::Schema> in_table_schema) {
    return std::make_shared<PhysicalWriteS3Vectors>(in_table_schema, *this);
}
