#include "_shuffle.h"
#include "_stream_join.h"

/**
 * @brief consume build table batch in streaming nested loop join
 * Design doc:
 * https://bodo.atlassian.net/wiki/spaces/B/pages/1373896721/Vectorized+Nested+Loop+Join+Design
 *
 * @param join_state join state pointer
 * @param in_table build table batch
 * @param is_last is last batch
 */
void nested_loop_join_build_consume_batch(NestedLoopJoinState* join_state,
                                          std::shared_ptr<table_info> in_table,
                                          bool is_last, bool parallel) {
    // just add batch to build table buffer
    std::vector<std::shared_ptr<table_info>> tables(
        {join_state->build_table_buffer.data_table, in_table});
    join_state->build_table_buffer.data_table = concat_tables(tables);
    tables.clear();
}

/**
 * @brief local nested loop computation on input probe table chunk (assuming
 * join state has all of build table)
 *
 * @param join_state join state pointer
 * @param probe_table probe table batch
 * @param is_parallel parallel flag for tracing purposes
 * @return std::shared_ptr<table_info> output table batch
 */
std::shared_ptr<table_info> nested_loop_join_local_chunk(
    NestedLoopJoinState* join_state, std::shared_ptr<table_info> probe_table,
    bool parallel) {
    bodo::vector<int64_t> build_idxs;
    bodo::vector<int64_t> probe_idxs;

    // TODO[BSE-460]: support outer joins
    bodo::vector<uint8_t> build_row_is_matched(0, 0);
    bodo::vector<uint8_t> probe_row_is_matched(0, 0);

    // cfunc is passed in batch format for nested loop join
    // see here:
    // https://github.com/Bodo-inc/Bodo/blob/fd987eca2684b9178a13caf41f23349f92a0a96e/bodo/libs/stream_join.py#L470
    // TODO: template for cases without condition (cross join) to improve
    // performance
    cond_expr_fn_batch_t cond_func =
        (cond_expr_fn_batch_t)join_state->cond_func;

    nested_loop_join_table_local(join_state->build_table_buffer.data_table,
                                 probe_table, false, false, cond_func, parallel,
                                 build_idxs, probe_idxs, build_row_is_matched,
                                 probe_row_is_matched);

    // TODO[BSE-460]: pass outer join flags
    // similar to here:
    // https://github.com/Bodo-inc/Bodo/blob/a0bc325fc5e92eb4d9a43ad09d178eb7754b4eb7/bodo/libs/_stream_join.cpp#L223
    std::shared_ptr<table_info> build_out_table =
        RetrieveTable(join_state->build_table_buffer.data_table, build_idxs);
    std::shared_ptr<table_info> probe_out_table =
        RetrieveTable(probe_table, probe_idxs);
    build_idxs.clear();
    probe_idxs.clear();

    std::vector<std::shared_ptr<array_info>> out_arrs;
    out_arrs.insert(out_arrs.end(), build_out_table->columns.begin(),
                    build_out_table->columns.end());
    out_arrs.insert(out_arrs.end(), probe_out_table->columns.begin(),
                    probe_out_table->columns.end());
    return std::make_shared<table_info>(out_arrs);
}

std::shared_ptr<table_info> nested_loop_join_probe_consume_batch(
    NestedLoopJoinState* join_state, std::shared_ptr<table_info> in_table,
    bool is_last, bool parallel) {
    if (parallel) {
        int n_pes, myrank;
        MPI_Comm_size(MPI_COMM_WORLD, &n_pes);
        MPI_Comm_rank(MPI_COMM_WORLD, &myrank);
        std::vector<std::shared_ptr<table_info>> out_table_chunks;
        out_table_chunks.reserve(n_pes);

        for (int p = 0; p < n_pes; p++) {
            std::shared_ptr<table_info> bcast_probe_chunk = broadcast_table(
                in_table, in_table, in_table->ncols(), parallel, p);
            std::shared_ptr<table_info> out_table_chunk =
                nested_loop_join_local_chunk(join_state, bcast_probe_chunk,
                                             parallel);
            out_table_chunks.emplace_back(out_table_chunk);
        }
        return concat_tables(out_table_chunks);
    } else {
        return nested_loop_join_local_chunk(join_state, in_table, parallel);
    }
}

void nested_loop_join_build_consume_batch_py_entry(
    NestedLoopJoinState* join_state, table_info* in_table, bool is_last,
    bool parallel) {
    try {
        nested_loop_join_build_consume_batch(
            join_state, std::shared_ptr<table_info>(in_table), is_last,
            parallel);
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
    }
}

/**
 * @brief Python wrapper to consume probe table batch and produce output table
 * batch
 *
 * @param join_state join state pointer
 * @param in_table probe table batch
 * @param is_last is last batch
 * @return table_info* output table batch
 */
table_info* nested_loop_join_probe_consume_batch_py_entry(
    NestedLoopJoinState* join_state, table_info* in_table, bool is_last,
    bool* out_is_last, bool parallel) {
    try {
        // TODO: Actually output out_is_last based on is_last + the state
        // of the output buffer.
        *out_is_last = is_last;
        std::shared_ptr<table_info> out = nested_loop_join_probe_consume_batch(
            join_state, std::unique_ptr<table_info>(in_table), is_last,
            parallel);

        return new table_info(*out);
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
        return NULL;
    }
}