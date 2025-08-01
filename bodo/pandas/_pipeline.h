#pragma once

#include <memory>
#include <utility>
#include <vector>
#include "_util.h"

#include "physical/operator.h"

// enable and build to print debug info on the pipeline
// #define DEBUG_PIPELINE
#ifdef DEBUG_PIPELINE
#include <iostream>
#endif

/// @brief Pipeline class for executing a sequence of physical operators.
class Pipeline {
   private:
    std::shared_ptr<PhysicalSource> source;
    std::vector<std::shared_ptr<PhysicalSourceSink>> between_ops;
    std::shared_ptr<PhysicalSink> sink;
    bool executed;
    std::vector<std::shared_ptr<Pipeline>> dependencies;

    /**
     * @brief Execute the pipeline starting at a certain point.
     *
     * @param idx - the operator index in between_ops to start at
     * @param batch - the output of the previous operator in the pipeline
     * @param prev_op_result - the result flag of the previous operator in the
     * pipeline
     * @return - bool that is True if some operator in the pipeline has
     * indicated that no more output needs to be generated.
     */
    bool midPipelineExecute(unsigned idx, std::shared_ptr<table_info> batch,
                            OperatorResult prev_op_result);

    friend class PipelineBuilder;

   public:
    /**
     * @brief Execute the pipeline and return the result (placeholder for now).
     * @return - number of batches processed
     */
    uint64_t Execute();

    /// @brief Get the final result. Result collector returns table_info,
    // Parquet write returns null table_info pointer, and Iceberg write
    // returns a PyObject* of Iceberg files infos.
    std::variant<std::shared_ptr<table_info>, PyObject*> GetResult();
};

class PipelineBuilder {
   private:
    std::shared_ptr<PhysicalSource> source;
    std::vector<std::shared_ptr<PhysicalSourceSink>> between_ops;

   public:
    explicit PipelineBuilder(std::shared_ptr<PhysicalSource> _source)
        : source(std::move(_source)) {}

    // Add a physical operator to the pipeline
    void AddOperator(std::shared_ptr<PhysicalSourceSink> op) {
        between_ops.emplace_back(op);
    }

    /// @brief Build the pipeline and return it
    std::shared_ptr<Pipeline> Build(std::shared_ptr<PhysicalSink> sink);

    /**
     * @brief Build the last pipeline for a plan, using a result collector as
     * the sink.
     *
     * @param in_schema Schema of input data to the sink from the previous
     * operator.
     * @param out_schema Schema of output data from the sink expected by Python.
     * Only column orders may be different from the input schema due to DuckDB
     * optimizers changes (e.g. reorder build/probe sides in join).
     * @return std::shared_ptr<Pipeline> finalized pipeline
     */
    std::shared_ptr<Pipeline> BuildEnd(
        std::shared_ptr<bodo::Schema> in_schema,
        std::shared_ptr<bodo::Schema> out_schema);

    /**
     * @brief Get the physical schema of the output of the last operator in the
     pipeline (same logical schema may have different physical schema such as
     regular string arrays and dictionary-encoded ones).
     *
     * @return std::shared_ptr<bodo::Schema> physical schema
     */
    std::shared_ptr<bodo::Schema> getPrevOpOutputSchema() {
        if (this->between_ops.empty()) {
            return this->source->getOutputSchema();
        }
        return this->between_ops.back()->getOutputSchema();
    }
};
