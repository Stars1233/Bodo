
#include "_stream_groupby.h"
#include "_array_hash.h"
#include "_array_utils.h"
#include "_bodo_common.h"
#include "_distributed.h"
#include "_groupby_common.h"
#include "_shuffle.h"

template <bool is_local>
uint32_t HashGroupbyTable<is_local>::operator()(const int64_t iRow) const {
    const bodo::vector<uint32_t>& build_hashes =
        is_local ? this->groupby_state->local_table_groupby_hashes
                 : this->groupby_state->shuffle_table_groupby_hashes;
    if (iRow >= 0) {
        return build_hashes[iRow];
    } else {
        return this->groupby_state->in_table_hashes[-iRow - 1];
    }
}

template <bool is_local>
bool KeyEqualGroupbyTable<is_local>::operator()(const int64_t iRowA,
                                                const int64_t iRowB) const {
    const std::shared_ptr<table_info>& build_table =
        is_local ? this->groupby_state->local_table_buffer.data_table
                 : this->groupby_state->shuffle_table_buffer.data_table;
    const std::shared_ptr<table_info>& in_table = this->groupby_state->in_table;

    bool is_build_A = iRowA >= 0;
    bool is_build_B = iRowB >= 0;

    size_t jRowA = is_build_A ? iRowA : -iRowA - 1;
    size_t jRowB = is_build_B ? iRowB : -iRowB - 1;

    const std::shared_ptr<table_info>& table_A =
        is_build_A ? build_table : in_table;
    const std::shared_ptr<table_info>& table_B =
        is_build_B ? build_table : in_table;

    bool test = TestEqualJoin(table_A, table_B, jRowA, jRowB, this->n_keys,
                              /*is_na_equal=*/true);
    return test;
}

/**
 * @brief Get group numbers for input table and update build table with new
 * groups if any.
 *
 * @tparam is_local whether updating local build table or shuffle build table
 * values
 * @param groupby_state streaming groupby state
 * @param build_table groupby hash table for finding group number
 * @param in_table input batch table
 * @param batch_hashes_groupby hashes of input batch
 * @param i_row row number of input batch to use
 */
template <bool is_local>
inline void update_groups(
    GroupbyState* groupby_state,
    bodo::unord_map_container<int64_t, int64_t, HashGroupbyTable<is_local>,
                              KeyEqualGroupbyTable<is_local>>& build_table,
    grouping_info& grp_info, const std::shared_ptr<table_info>& in_table,
    std::shared_ptr<uint32_t[]>& batch_hashes_groupby, size_t i_row) {
    // get local or shuffle build state to update
    TableBuildBuffer& build_table_buffer =
        is_local ? groupby_state->local_table_buffer
                 : groupby_state->shuffle_table_buffer;
    bodo::vector<uint32_t>& build_hashes =
        is_local ? groupby_state->local_table_groupby_hashes
                 : groupby_state->shuffle_table_groupby_hashes;
    int64_t& next_group = is_local ? groupby_state->local_next_group
                                   : groupby_state->shuffle_next_group;
    bodo::vector<int64_t>& row_to_group = grp_info.row_to_group;
    // TODO[BSE-578]: update group_to_first_row, group_to_first_row etc. if
    // necessary

    uint64_t n_keys = groupby_state->n_keys;
    int64_t group;

    if (build_table.contains(-i_row - 1)) {
        // update existing group
        group = build_table[-i_row - 1];
    } else {
        // add new group
        build_table_buffer.AppendRowKeys(in_table, i_row, n_keys);
        build_table_buffer.IncrementSizeDataColumns(n_keys);
        build_hashes.emplace_back(batch_hashes_groupby[i_row]);
        group = next_group++;
        build_table[group] = group;
    }
    row_to_group[i_row] = group;
}

/**
 * @brief Call groupby update function on new input batch data and return the
 * output update table
 *
 * @param groupby_state streaming groupby state
 * @param in_table input batch table
 * @return std::shared_ptr<table_info> output update table
 */
std::shared_ptr<table_info> get_update_table(
    GroupbyState* groupby_state, std::shared_ptr<table_info> in_table) {
    // similar to update() function of GroupbyPipeline:
    // https://github.com/Bodo-inc/Bodo/blob/58f995dec2507a84afefbb27af01d67bd40fabb4/bodo/libs/_groupby.cpp#L546
    std::shared_ptr<uint32_t[]> batch_hashes_groupby = hash_keys_table(
        in_table, groupby_state->n_keys, SEED_HASH_GROUPBY_SHUFFLE, false);

    std::vector<std::shared_ptr<table_info>> tables = {in_table};
    size_t nunique_hashes = get_nunique_hashes(
        batch_hashes_groupby, in_table->nrows(), groupby_state->parallel);
    std::vector<grouping_info> grp_infos;
    get_group_info(tables, batch_hashes_groupby, nunique_hashes, grp_infos,
                   true, false, groupby_state->parallel);
    grouping_info& grp_info = grp_infos[0];
    grp_info.mode = 1;
    size_t num_groups = grp_info.num_groups;
    int64_t update_col_len = num_groups;
    std::shared_ptr<table_info> update_table = std::make_shared<table_info>();
    alloc_init_keys(tables, update_table, grp_infos, groupby_state->n_keys,
                    num_groups);
    const std::vector<int32_t>& f_in_offsets = groupby_state->f_in_offsets;
    const std::vector<int32_t>& f_in_cols = groupby_state->f_in_cols;
    for (size_t i = 0; i < groupby_state->col_sets.size(); i++) {
        std::shared_ptr<BasicColSet>& col_set = groupby_state->col_sets[i];

        // set input columns of ColSet to new batch data
        std::vector<std::shared_ptr<array_info>> input_cols;
        for (size_t input_ind = (size_t)f_in_offsets[i];
             input_ind < (size_t)f_in_offsets[i + 1]; input_ind++) {
            input_cols.push_back(in_table->columns[f_in_cols[input_ind]]);
        }
        col_set->setInCol(input_cols);
        col_set->clearUpdateCols();
        std::vector<std::shared_ptr<array_info>> list_arr;
        col_set->alloc_update_columns(update_col_len, list_arr);
        for (auto& e_arr : list_arr) {
            update_table->columns.push_back(e_arr);
        }
        col_set->update(grp_infos);
    }
    return update_table;
}

/**
 * @brief Call groupby combine function on new update data and aggregate with
 * existing build table
 *
 * @param groupby_state streaming groupby state
 * @param build_table build table with running values
 * @param init_start_row starting offset of rows in build table that need output
 * data initialization (created by new groups introduced by this batch)
 * @param in_table input update table
 * @param grp_info row to group mapping info
 */
void combine_input_table(GroupbyState* groupby_state,
                         std::shared_ptr<table_info> build_table,
                         int64_t init_start_row,
                         std::shared_ptr<table_info> in_table,
                         grouping_info& grp_info) {
    const std::vector<int32_t>& f_update_offsets =
        groupby_state->f_update_offsets;
    const std::vector<int32_t>& f_combine_offsets =
        groupby_state->f_combine_offsets;
    for (size_t i = 0; i < groupby_state->col_sets.size(); i++) {
        std::shared_ptr<BasicColSet>& col_set = groupby_state->col_sets[i];
        std::vector<std::shared_ptr<array_info>> in_update_cols;
        std::vector<std::shared_ptr<array_info>> out_combine_cols;
        for (size_t col_ind = (size_t)f_update_offsets[i];
             col_ind < (size_t)f_update_offsets[i + 1]; col_ind++) {
            in_update_cols.push_back(in_table->columns[col_ind]);
        }
        for (size_t col_ind = (size_t)f_combine_offsets[i];
             col_ind < (size_t)f_combine_offsets[i + 1]; col_ind++) {
            out_combine_cols.push_back(build_table->columns[col_ind]);
        }
        col_set->setUpdateCols(in_update_cols);
        col_set->setCombineCols(out_combine_cols);
        col_set->combine({grp_info}, init_start_row);
    }
}

/**
 * @brief consume build table batch in streaming groupby (insert into hash
 * table and update running values)
 *
 * @param groupby_state groupby state pointer
 * @param in_table build table batch
 * @param is_last is last batch
 */
void groupby_build_consume_batch(GroupbyState* groupby_state,
                                 std::shared_ptr<table_info> in_table,
                                 bool is_last) {
    // High level workflow (reusing as much of existing groupby infrastructure
    // as possible):
    // 1. Get update values from input. Example with sum function:
    //      A   B
    //      1   3
    //      2   1
    //      1   1
    //    becomes:
    //      A   B
    //      1   4
    //      2   1
    // 2. Get group numbers for each input row from local and shuffle build
    // tables.
    //    This inserts a new group to the table if it doesn't exist.
    // 3. Combine update values with local and shuffle build tables.

    int n_pes, myrank;
    MPI_Comm_size(MPI_COMM_WORLD, &n_pes);
    MPI_Comm_rank(MPI_COMM_WORLD, &myrank);

    // NOTE: in_table->num_keys is used by groupby infrastructure (e.g.
    // get_group_info)
    in_table->num_keys = groupby_state->n_keys;
    in_table = get_update_table(groupby_state, in_table);

    // TODO[BSE-566]: handle dictionary encoded arrays
    std::shared_ptr<uint32_t[]> batch_hashes_groupby = hash_keys_table(
        in_table, groupby_state->n_keys, SEED_HASH_GROUPBY_SHUFFLE, false);
    std::shared_ptr<uint32_t[]> batch_hashes_partition =
        hash_keys_table(in_table, groupby_state->n_keys, SEED_HASH_PARTITION,
                        groupby_state->parallel, false);

    // set state batch input
    groupby_state->in_table = in_table;
    groupby_state->in_table_hashes = batch_hashes_groupby;

    // reserve space in local/shuffle buffers for potential new groups
    // NOTE: only key types are always the same as input
    groupby_state->local_table_buffer.ReserveTableKeys(in_table,
                                                       groupby_state->n_keys);
    // TODO[BSE-616]: support variable size output like strings
    groupby_state->local_table_buffer.ReserveSizeDataColumns(
        in_table->nrows(), groupby_state->n_keys);
    groupby_state->local_table_groupby_hashes.reserve(
        groupby_state->local_table_groupby_hashes.size() + in_table->nrows());
    groupby_state->shuffle_table_buffer.ReserveTableKeys(in_table,
                                                         groupby_state->n_keys);
    groupby_state->shuffle_table_buffer.ReserveSizeDataColumns(
        in_table->nrows(), groupby_state->n_keys);
    groupby_state->shuffle_table_groupby_hashes.reserve(
        groupby_state->shuffle_table_groupby_hashes.size() + in_table->nrows());

    // Fill row group numbers in grouping_info to reuse existing infrastructure.
    // We set group=-1 for rows that don't belong to the current buffer (e.g.
    // row belongs to shuffle buffer but we are processing local buffer) for
    // them to be ignored in combine step later.
    grouping_info local_grp_info;
    grouping_info shuffle_grp_info;
    local_grp_info.row_to_group.resize(in_table->nrows(), -1);
    shuffle_grp_info.row_to_group.resize(in_table->nrows(), -1);
    // Get current size of the buffers to know starting offset of new
    // keys which need output data column initialization.
    int64_t local_init_start_row = groupby_state->local_next_group;
    int64_t shuffle_init_start_row = groupby_state->shuffle_next_group;

    for (size_t i_row = 0; i_row < in_table->nrows(); i_row++) {
        bool process_on_rank =
            !groupby_state->parallel ||
            hash_to_rank(batch_hashes_partition[i_row], n_pes) == myrank;
        if (process_on_rank) {
            update_groups<true>(groupby_state, groupby_state->local_build_table,
                                local_grp_info, in_table, batch_hashes_groupby,
                                i_row);
        } else {
            update_groups<false>(
                groupby_state, groupby_state->shuffle_build_table,
                shuffle_grp_info, in_table, batch_hashes_groupby, i_row);
        }
    }

    // combine update data with local/shuffle running values
    combine_input_table(groupby_state,
                        groupby_state->local_table_buffer.data_table,
                        local_init_start_row, in_table, local_grp_info);

    combine_input_table(groupby_state,
                        groupby_state->shuffle_table_buffer.data_table,
                        shuffle_init_start_row, in_table, shuffle_grp_info);

    if (shuffle_this_iter(groupby_state->parallel, is_last,
                          groupby_state->shuffle_table_buffer.data_table,
                          groupby_state->build_iter)) {
        // shuffle data of other ranks
        std::shared_ptr<table_info> shuffle_table =
            groupby_state->shuffle_table_buffer.data_table;

        std::shared_ptr<uint32_t[]> shuffle_hashes = hash_keys_table(
            shuffle_table, groupby_state->n_keys, SEED_HASH_PARTITION,
            groupby_state->parallel, false);
        // make dictionaries global for shuffle
        for (size_t i = 0; i < shuffle_table->ncols(); i++) {
            std::shared_ptr<array_info> arr = shuffle_table->columns[i];
            if (arr->arr_type == bodo_array_type::DICT) {
                make_dictionary_global_and_unique(arr, groupby_state->parallel);
            }
        }
        mpi_comm_info comm_info_table(shuffle_table->columns);
        comm_info_table.set_counts(shuffle_hashes, groupby_state->parallel);
        std::shared_ptr<table_info> new_data =
            shuffle_table_kernel(std::move(shuffle_table), shuffle_hashes,
                                 comm_info_table, groupby_state->parallel);
        shuffle_hashes.reset();
        groupby_state->shuffle_table_buffer.Reset();

        batch_hashes_groupby = hash_keys_table(
            new_data, groupby_state->n_keys, SEED_HASH_GROUPBY_SHUFFLE, false);
        groupby_state->in_table = new_data;
        groupby_state->in_table_hashes = batch_hashes_groupby;

        groupby_state->local_table_buffer.ReserveTable(new_data);
        groupby_state->local_table_groupby_hashes.reserve(
            groupby_state->local_table_groupby_hashes.size() +
            new_data->nrows());

        grouping_info local_grp_info;
        local_grp_info.row_to_group.resize(new_data->nrows());
        int64_t local_init_start_row = groupby_state->local_next_group;

        for (size_t i_row = 0; i_row < new_data->nrows(); i_row++) {
            update_groups<true>(groupby_state, groupby_state->local_build_table,
                                local_grp_info, new_data, batch_hashes_groupby,
                                i_row);
        }

        combine_input_table(groupby_state,
                            groupby_state->local_table_buffer.data_table,
                            local_init_start_row, new_data, local_grp_info);
    }

    groupby_state->build_iter++;
}

/**
 * @brief return output of groupby computation
 *
 * @param groupby_state groupby state pointer
 * @return std::tuple<std::shared_ptr<table_info>, bool> output data batch and
 * flag for last batch
 */
std::tuple<std::shared_ptr<table_info>, bool> groupby_produce_output_batch(
    GroupbyState* groupby_state) {
    // TODO[BSE-573]: use proper chunked output buffer
    // TODO[BSE-578]: call eval() to support functions that need it
    bool is_last = true;
    return std::tuple(groupby_state->local_table_buffer.data_table, is_last);
}

/**
 * @brief Python wrapper to consume build table batch
 *
 * @param groupby_state groupby state pointer
 * @param in_table build table batch
 * @param is_last is last batch
 */
void groupby_build_consume_batch_py_entry(GroupbyState* groupby_state,
                                          table_info* in_table, bool is_last) {
    try {
        groupby_build_consume_batch(
            groupby_state, std::unique_ptr<table_info>(in_table), is_last);
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
    }
}

/**
 * @brief Python wrapper to produce output table
 * batch
 *
 * @param groupby_state groupby state pointer
 * @param[out] out_is_last is last batch
 * @return table_info* output table batch
 */
table_info* groupby_produce_output_batch_py_entry(GroupbyState* groupby_state,
                                                  bool* out_is_last) {
    try {
        bool is_last;
        std::shared_ptr<table_info> out;
        std::tie(out, is_last) = groupby_produce_output_batch(groupby_state);
        *out_is_last = is_last;
        return new table_info(*out);
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
        return NULL;
    }
}

/**
 * @brief Initialize a new streaming groubpy state for specified array types and
 * number of keys (called from Python)
 *
 * @param build_arr_c_types array types of build table columns (Bodo_CTypes
 * ints)
 * @param build_arr_array_types array types of build table columns
 * (bodo_array_type ints)
 * @param n_build_arrs number of build table columns
 * @param n_keys number of groupby keys
 * @return GroupbyState* groupby state to return to Python
 */
GroupbyState* groupby_state_init_py_entry(int8_t* build_arr_c_types,
                                          int8_t* build_arr_array_types,
                                          int n_build_arrs, int32_t* ftypes,
                                          int32_t* f_in_offsets,
                                          int32_t* f_in_cols, int n_funcs,
                                          uint64_t n_keys, bool parallel) {
    return new GroupbyState(
        std::vector<int8_t>(build_arr_c_types,
                            build_arr_c_types + n_build_arrs),
        std::vector<int8_t>(build_arr_array_types,
                            build_arr_array_types + n_build_arrs),

        std::vector<int32_t>(ftypes, ftypes + n_funcs),
        std::vector<int32_t>(f_in_offsets, f_in_offsets + n_funcs + 1),
        std::vector<int32_t>(f_in_cols, f_in_cols + f_in_offsets[n_funcs]),

        n_keys, parallel);
}

/**
 * @brief delete groupby state (called from Python after output loop is
 * finished)
 *
 * @param groupby_state groupby state pointer to delete
 */
void delete_groupby_state(GroupbyState* groupby_state) { delete groupby_state; }

PyMODINIT_FUNC PyInit_stream_groupby_cpp(void) {
    PyObject* m;
    MOD_DEF(m, "stream_groupby_cpp", "No docs", NULL);
    if (m == NULL)
        return NULL;

    bodo_common_init();

    SetAttrStringFromVoidPtr(m, groupby_state_init_py_entry);
    SetAttrStringFromVoidPtr(m, groupby_build_consume_batch_py_entry);
    SetAttrStringFromVoidPtr(m, groupby_produce_output_batch_py_entry);
    SetAttrStringFromVoidPtr(m, delete_groupby_state);
    return m;
}