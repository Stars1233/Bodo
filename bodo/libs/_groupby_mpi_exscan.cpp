// Copyright (C) 2023 Bodo Inc. All rights reserved.

#include "_groupby_mpi_exscan.h"
#include "_array_hash.h"
#include "_array_operations.h"
#include "_array_utils.h"
#include "_distributed.h"
#include "_groupby_common.h"
#include "_groupby_ftypes.h"
#include "_groupby_hashing.h"
#include "_shuffle.h"

// Strategy for determining exscan

/**
 * This file implements the functions that are used to determine and utilize
 * the MPI_Exscan strategy for groupby. This strategy is used when we have
 * only cumulative operations to avoid shuffling the data.
 */

int determine_groupby_strategy(table_info* in_table, int64_t num_keys,
                               int* ftypes, int* func_offsets,
                               bool input_has_index) {
    // First decision: If it is cumulative, then we can use the MPI_Exscan.
    // Otherwise no
    bool has_non_cumulative_op = false;
    bool has_cumulative_op = false;
    int index_i = int(input_has_index);
    for (int i = 0; i < func_offsets[in_table->ncols() - num_keys - index_i];
         i++) {
        int ftype = ftypes[i];
        if (ftype == Bodo_FTypes::cumsum || ftype == Bodo_FTypes::cummin ||
            ftype == Bodo_FTypes::cumprod || ftype == Bodo_FTypes::cummax) {
            has_cumulative_op = true;
        } else {
            has_non_cumulative_op = true;
        }
    }
    if (has_non_cumulative_op) {
        return 0;  // No choice, we have to use the classic hash scheme
    }
    if (!has_cumulative_op) {
        return 0;  // It does not make sense to use MPI_exscan here.
    }
    // Second decision: Whether it is arithmetic or not. If arithmetic, we can
    // use MPI_Exscan. If not, we may make it work for cumsum of strings or list
    // of strings but that would be definitely quite complicated and use more
    // than just MPI_Exscan.
    bool has_non_arithmetic_type = false;
    for (uint64_t i = num_keys; i < in_table->ncols() - index_i; i++) {
        array_info* oper_col = in_table->columns[i];
        if (oper_col->arr_type != bodo_array_type::NUMPY &&
            oper_col->arr_type != bodo_array_type::NULLABLE_INT_BOOL)
            has_non_arithmetic_type = true;
    }
    if (has_non_arithmetic_type) {
        return 0;  // No choice, we have to use the classic hash scheme
    }
    // Third decision: Whether we use categorical with just one key. Working
    // with other keys would require some preprocessing.
    if (num_keys > 1) {
        return 2;  // For more than 1 key column, use multikey mpi_exscan
    }
    bodo_array_type::arr_type_enum key_arr_type =
        in_table->columns[0]->arr_type;
    if (key_arr_type != bodo_array_type::CATEGORICAL) {
        return 2;  // For key column that are not categorical, use multikey
                   // mpi_exscan
    }
    if (in_table->columns[0]->num_categories >
        max_global_number_groups_exscan) {
        return 0;  // For too many categories the hash partition will be better
    }
    return 1;  // all conditions satisfied. Let's go for EXSCAN code
}

// Categorical index info

array_info* compute_categorical_index(table_info* in_table, int64_t num_keys,
                                      bool is_parallel, bool key_dropna) {
    tracing::Event ev("compute_categorical_index", is_parallel);
    // A rare case of incref since we are going to need the in_table after the
    // computation of red_table.
    for (int64_t i_key = 0; i_key < num_keys; i_key++) {
        array_info* a = in_table->columns[i_key];
        if (a->arr_type == bodo_array_type::DICT) {
            make_dictionary_global_and_unique(a, is_parallel);
        }
        incref_array(a);
    }
    table_info* red_table =
        drop_duplicates_keys(in_table, num_keys, is_parallel, key_dropna);
    size_t n_rows_full, n_rows = red_table->nrows();
    if (is_parallel) {
        MPI_Allreduce(&n_rows, &n_rows_full, 1, MPI_LONG_LONG_INT, MPI_SUM,
                      MPI_COMM_WORLD);
    } else {
        n_rows_full = n_rows;
    }
    // Two approaches for cumulative operations : shuffle (then reshuffle) or
    // use exscan. Preferable to do shuffle when we have too many unique values.
    // This is a heuristic to decide approach.
    if (n_rows_full > max_global_number_groups_exscan) {
        delete_table_decref_arrays(red_table);
        return nullptr;
    }
    // We are below threshold. Now doing an allgather for determining the keys.
    bool all_gather = true;
    table_info* full_table;
    if (is_parallel) {
        full_table = gather_table(red_table, num_keys, all_gather, is_parallel);
        delete_table(red_table);
    } else {
        full_table = red_table;
    }
    // Now building the map_container.
    uint32_t* hashes_full =
        hash_keys_table(full_table, num_keys, SEED_HASH_MULTIKEY, is_parallel);
    uint32_t* hashes_in_table =
        hash_keys_table(in_table, num_keys, SEED_HASH_MULTIKEY, is_parallel);
    std::vector<array_info*> concat_column(
        full_table->columns.begin(), full_table->columns.begin() + num_keys);
    concat_column.insert(concat_column.end(), in_table->columns.begin(),
                         in_table->columns.begin() + num_keys);

    HashComputeCategoricalIndex hash_fct{hashes_full, hashes_in_table,
                                         n_rows_full};
    HashEqualComputeCategoricalIndex equal_fct{num_keys, n_rows_full,
                                               &concat_column};
    UNORD_MAP_CONTAINER<size_t, size_t, HashComputeCategoricalIndex,
                        HashEqualComputeCategoricalIndex>
        entSet({}, hash_fct, equal_fct);
    for (size_t iRow = 0; iRow < size_t(n_rows_full); iRow++)
        entSet[iRow] = iRow;
    size_t n_rows_in = in_table->nrows();
    array_info* out_arr =
        alloc_categorical(n_rows_in, Bodo_CTypes::INT32, n_rows_full);
    std::vector<array_info*> key_cols(in_table->columns.begin(),
                                      in_table->columns.begin() + num_keys);
    bool has_nulls = does_keys_have_nulls(key_cols);
    for (size_t iRow = 0; iRow < n_rows_in; iRow++) {
        int32_t pos;
        if (has_nulls) {
            if (key_dropna && does_row_has_nulls(key_cols, iRow))
                pos = -1;
            else
                pos = entSet[iRow + n_rows_full];
        } else {
            pos = entSet[iRow + n_rows_full];
        }
        out_arr->at<int32_t>(iRow) = pos;
    }
    delete_table_decref_arrays(full_table);
    return out_arr;
}

// MPI_Exscan: https://www.mpich.org/static/docs/v3.1.x/www3/MPI_Exscan.html
// Useful for cumulative functions. Instead of doing shuffling, we compute the
// groups in advance without doing shuffling using MPI_Exscan. We do the
// cumulative operation first locally on each processor, and we use step
// functions on each processor (sum, min, etc.)

/**
 * @brief MPI exscan implementation on numpy arrays.
 *
 * @tparam Tkey The type of the key column
 * @tparam T The type of the operation column
 * @tparam dtype The dtype of the operation column.
 * @param out_arrs The output arrays
 * @param cat_column The categorical column
 * @param in_table The input table
 * @param num_keys The number of key columns
 * @param k The index of the operation column
 * @param ftypes The types of the functions
 * @param func_offsets The offsets of the functions
 * @param is_parallel Whether the computation is parallel
 * @param skipdropna Whether to skip dropna
 */
template <typename Tkey, typename T, int dtype>
void mpi_exscan_computation_numpy_T(std::vector<array_info*>& out_arrs,
                                    array_info* cat_column,
                                    table_info* in_table, int64_t num_keys,
                                    int64_t k, int* ftypes, int* func_offsets,
                                    bool is_parallel, bool skipdropna) {
    int64_t n_rows = in_table->nrows();
    int start = func_offsets[k];
    int end = func_offsets[k + 1];
    int n_oper = end - start;
    int64_t max_row_idx = cat_column->num_categories;
    std::vector<T> cumulative(max_row_idx * n_oper);
    for (int j = start; j != end; j++) {
        int ftype = ftypes[j];
        T value_init = -1;  // Dummy value set to avoid a compiler warning
        if (ftype == Bodo_FTypes::cumsum) {
            value_init = 0;
        } else if (ftype == Bodo_FTypes::cumprod) {
            value_init = 1;
        } else if (ftype == Bodo_FTypes::cummax) {
            value_init = std::numeric_limits<T>::min();
        } else if (ftype == Bodo_FTypes::cummin) {
            value_init = std::numeric_limits<T>::max();
        }
        for (int i_row = 0; i_row < max_row_idx; i_row++) {
            cumulative[i_row + max_row_idx * (j - start)] = value_init;
        }
    }
    std::vector<T> cumulative_recv = cumulative;
    array_info* in_col = in_table->columns[k + num_keys];
    T nan_value =
        GetTentry<T>(RetrieveNaNentry((Bodo_CTypes::CTypeEnum)dtype).data());
    Tkey miss_idx = -1;
    for (int j = start; j != end; j++) {
        array_info* work_col = out_arrs[j];
        int ftype = ftypes[j];
        auto apply_oper = [&](auto const& oper) -> void {
            for (int64_t i_row = 0; i_row < n_rows; i_row++) {
                Tkey idx = cat_column->at<Tkey>(i_row);
                if (idx == miss_idx) {
                    work_col->at<T>(i_row) = nan_value;
                } else {
                    size_t pos = idx + max_row_idx * (j - start);
                    T val = in_col->at<T>(i_row);
                    if (skipdropna && isnan_alltype<T, dtype>(val)) {
                        work_col->at<T>(i_row) = val;
                    } else {
                        T new_val = oper(val, cumulative[pos]);
                        work_col->at<T>(i_row) = new_val;
                        cumulative[pos] = new_val;
                    }
                }
            }
        };
        if (ftype == Bodo_FTypes::cumsum) {
            apply_oper([](T val1, T val2) -> T { return val1 + val2; });
        } else if (ftype == Bodo_FTypes::cumprod) {
            apply_oper([](T val1, T val2) -> T { return val1 * val2; });
        } else if (ftype == Bodo_FTypes::cummax) {
            apply_oper(
                [](T val1, T val2) -> T { return std::max(val1, val2); });
        } else if (ftype == Bodo_FTypes::cummin) {
            apply_oper(
                [](T val1, T val2) -> T { return std::min(val1, val2); });
        }
    }
    if (!is_parallel) {
        return;
    }
    MPI_Datatype mpi_typ = get_MPI_typ(dtype);
    for (int j = start; j != end; j++) {
        T* data_s = cumulative.data() + max_row_idx * (j - start);
        T* data_r = cumulative_recv.data() + max_row_idx * (j - start);
        int ftype = ftypes[j];
        if (ftype == Bodo_FTypes::cumsum) {
            MPI_Exscan(data_s, data_r, max_row_idx, mpi_typ, MPI_SUM,
                       MPI_COMM_WORLD);
        } else if (ftype == Bodo_FTypes::cumprod) {
            MPI_Exscan(data_s, data_r, max_row_idx, mpi_typ, MPI_PROD,
                       MPI_COMM_WORLD);
        } else if (ftype == Bodo_FTypes::cummax) {
            MPI_Exscan(data_s, data_r, max_row_idx, mpi_typ, MPI_MAX,
                       MPI_COMM_WORLD);
        } else if (ftype == Bodo_FTypes::cummin) {
            MPI_Exscan(data_s, data_r, max_row_idx, mpi_typ, MPI_MIN,
                       MPI_COMM_WORLD);
        }
    }
    for (int j = start; j != end; j++) {
        array_info* work_col = out_arrs[j];
        int ftype = ftypes[j];
        // For skipdropna:
        //   The cumulative is never a NaN. The sum therefore works
        //   correctly whether val is a NaN or not.
        // For !skipdropna:
        //   the cumulative can be a NaN. The sum also works correctly.
        auto apply_oper = [&](auto const& oper) -> void {
            for (int64_t i_row = 0; i_row < n_rows; i_row++) {
                Tkey idx = cat_column->at<Tkey>(i_row);
                if (idx != miss_idx) {
                    size_t pos = idx + max_row_idx * (j - start);
                    T val = work_col->at<T>(i_row);
                    T new_val = oper(val, cumulative_recv[pos]);
                    work_col->at<T>(i_row) = new_val;
                }
            }
        };
        if (ftype == Bodo_FTypes::cumsum) {
            apply_oper([](T val1, T val2) -> T { return val1 + val2; });
        } else if (ftype == Bodo_FTypes::cumprod) {
            apply_oper([](T val1, T val2) -> T { return val1 * val2; });
        } else if (ftype == Bodo_FTypes::cummax) {
            apply_oper(
                [](T val1, T val2) -> T { return std::max(val1, val2); });
        } else if (ftype == Bodo_FTypes::cummin) {
            apply_oper(
                [](T val1, T val2) -> T { return std::min(val1, val2); });
        }
    }
}

/**
 * @brief MPI exscan implementation on nullable arrays.
 *
 * @tparam Tkey The type of the key column
 * @tparam T The type of the operation column
 * @tparam dtype The dtype of the operation column.
 * @param out_arrs The output arrays
 * @param cat_column The categorical column
 * @param in_table The input table
 * @param num_keys The number of key columns
 * @param k The index of the operation column
 * @param ftypes The types of the functions
 * @param func_offsets The offsets of the functions
 * @param is_parallel Whether the computation is parallel
 * @param skipdropna Whether to skip dropna
 */
template <typename Tkey, typename T, int dtype>
void mpi_exscan_computation_nullable_T(std::vector<array_info*>& out_arrs,
                                       array_info* cat_column,
                                       table_info* in_table, int64_t num_keys,
                                       int64_t k, int* ftypes,
                                       int* func_offsets, bool is_parallel,
                                       bool skipdropna) {
    int64_t n_rows = in_table->nrows();
    int start = func_offsets[k];
    int end = func_offsets[k + 1];
    int n_oper = end - start;
    int64_t max_row_idx = cat_column->num_categories;
    std::vector<T> cumulative(max_row_idx * n_oper);
    for (int j = start; j != end; j++) {
        int ftype = ftypes[j];
        T value_init = -1;  // Not correct value
        if (ftype == Bodo_FTypes::cumsum) {
            value_init = 0;
        } else if (ftype == Bodo_FTypes::cumprod) {
            value_init = 1;
        } else if (ftype == Bodo_FTypes::cummax) {
            value_init = std::numeric_limits<T>::min();
        } else if (ftype == Bodo_FTypes::cummin) {
            value_init = std::numeric_limits<T>::max();
        }
        for (int i_row = 0; i_row < max_row_idx; i_row++) {
            cumulative[i_row + max_row_idx * (j - start)] = value_init;
        }
    }
    std::vector<T> cumulative_recv = cumulative;
    std::vector<uint8_t> cumulative_mask, cumulative_mask_recv;
    // If we use skipdropna then we do not need to keep track of
    // the previous values
    if (!skipdropna) {
        cumulative_mask = std::vector<uint8_t>(max_row_idx * n_oper, 0);
        cumulative_mask_recv = std::vector<uint8_t>(max_row_idx * n_oper, 0);
    }
    array_info* in_col = in_table->columns[k + num_keys];
    Tkey miss_idx = -1;
    for (int j = start; j != end; j++) {
        array_info* work_col = out_arrs[j];
        int ftype = ftypes[j];
        auto apply_oper = [&](auto const& oper) -> void {
            for (int64_t i_row = 0; i_row < n_rows; i_row++) {
                Tkey idx = cat_column->at<Tkey>(i_row);
                if (idx == miss_idx) {
                    work_col->set_null_bit(i_row, false);
                } else {
                    size_t pos = idx + max_row_idx * (j - start);
                    T val = in_col->at<T>(i_row);
                    bool bit_i = in_col->get_null_bit(i_row);
                    T new_val = oper(val, cumulative[pos]);
                    bool bit_o = bit_i;
                    work_col->at<T>(i_row) = new_val;
                    if (skipdropna) {
                        if (bit_i) cumulative[pos] = new_val;
                    } else {
                        if (bit_i) {
                            if (cumulative_mask[pos] == 1)
                                bit_o = false;
                            else
                                cumulative[pos] = new_val;
                        } else
                            cumulative_mask[pos] = 1;
                    }
                    work_col->set_null_bit(i_row, bit_o);
                }
            }
        };
        if (ftype == Bodo_FTypes::cumsum) {
            apply_oper([](T val1, T val2) -> T { return val1 + val2; });
        } else if (ftype == Bodo_FTypes::cumprod) {
            apply_oper([](T val1, T val2) -> T { return val1 * val2; });
        } else if (ftype == Bodo_FTypes::cummax) {
            apply_oper(
                [](T val1, T val2) -> T { return std::max(val1, val2); });
        } else if (ftype == Bodo_FTypes::cummin) {
            apply_oper(
                [](T val1, T val2) -> T { return std::min(val1, val2); });
        }
    }
    if (!is_parallel) {
        return;
    }
    MPI_Datatype mpi_typ = get_MPI_typ(dtype);
    for (int j = start; j != end; j++) {
        T* data_s = cumulative.data() + max_row_idx * (j - start);
        T* data_r = cumulative_recv.data() + max_row_idx * (j - start);
        int ftype = ftypes[j];
        if (ftype == Bodo_FTypes::cumsum) {
            MPI_Exscan(data_s, data_r, max_row_idx, mpi_typ, MPI_SUM,
                       MPI_COMM_WORLD);
        } else if (ftype == Bodo_FTypes::cumprod) {
            MPI_Exscan(data_s, data_r, max_row_idx, mpi_typ, MPI_PROD,
                       MPI_COMM_WORLD);
        } else if (ftype == Bodo_FTypes::cummax) {
            MPI_Exscan(data_s, data_r, max_row_idx, mpi_typ, MPI_MAX,
                       MPI_COMM_WORLD);
        } else if (ftype == Bodo_FTypes::cummin) {
            MPI_Exscan(data_s, data_r, max_row_idx, mpi_typ, MPI_MIN,
                       MPI_COMM_WORLD);
        }
    }
    if (!skipdropna) {
        mpi_typ = get_MPI_typ(Bodo_CTypes::UINT8);
        MPI_Exscan(cumulative_mask.data(), cumulative_mask_recv.data(),
                   max_row_idx * n_oper, mpi_typ, MPI_MAX, MPI_COMM_WORLD);
    }
    for (int j = start; j != end; j++) {
        array_info* work_col = out_arrs[j];
        int ftype = ftypes[j];
        auto apply_oper = [&](auto const& oper) -> void {
            for (int64_t i_row = 0; i_row < n_rows; i_row++) {
                Tkey idx = cat_column->at<Tkey>(i_row);
                if (idx != miss_idx) {
                    size_t pos = idx + max_row_idx * (j - start);
                    T val = work_col->at<T>(i_row);
                    T new_val = oper(val, cumulative_recv[pos]);
                    work_col->at<T>(i_row) = new_val;
                    if (!skipdropna && cumulative_mask_recv[pos] == 1)
                        work_col->set_null_bit(i_row, false);
                }
            }
        };
        if (ftype == Bodo_FTypes::cumsum) {
            apply_oper([](T val1, T val2) -> T { return val1 + val2; });
        } else if (ftype == Bodo_FTypes::cumprod) {
            apply_oper([](T val1, T val2) -> T { return val1 * val2; });
        } else if (ftype == Bodo_FTypes::cummax) {
            apply_oper(
                [](T val1, T val2) -> T { return std::max(val1, val2); });
        } else if (ftype == Bodo_FTypes::cummin) {
            apply_oper(
                [](T val1, T val2) -> T { return std::min(val1, val2); });
        }
    }
}

/**
 * @brief MPI exscan computation on all columns.
 *
 * @tparam Tkey The type of the key column
 * @param cat_column The categorical column
 * @param in_table The input table
 * @param num_keys The number of key columns
 * @param ftypes The types of the functions
 * @param func_offsets The offsets of the functions
 * @param is_parallel Whether the computation is parallel
 * @param skipdropna Whether to skip dropna
 */
template <typename Tkey, typename T, int dtype>
void mpi_exscan_computation_T(std::vector<array_info*>& out_arrs,
                              array_info* cat_column, table_info* in_table,
                              int64_t num_keys, int64_t k, int* ftypes,
                              int* func_offsets, bool is_parallel,
                              bool skipdropna) {
    array_info* in_col = in_table->columns[k + num_keys];
    if (in_col->arr_type == bodo_array_type::NUMPY) {
        return mpi_exscan_computation_numpy_T<Tkey, T, dtype>(
            out_arrs, cat_column, in_table, num_keys, k, ftypes, func_offsets,
            is_parallel, skipdropna);
    } else {
        return mpi_exscan_computation_nullable_T<Tkey, T, dtype>(
            out_arrs, cat_column, in_table, num_keys, k, ftypes, func_offsets,
            is_parallel, skipdropna);
    }
}

/**
 * @brief MPI exscan implementation on a particular key type.
 *
 * @tparam Tkey The type of the key column
 * @param cat_column The categorical column
 * @param in_table The input table
 * @param num_keys The number of key columns
 * @param ftypes The types of the functions
 * @param func_offsets The offsets of the functions
 * @param is_parallel Whether the computation is parallel
 * @param skipdropna Whether to skip dropna
 * @param return_key Whether to return the key column
 * @param return_index Whether to return the index column
 * @param use_sql_rules Whether to use SQL rules in allocation
 * @return table_info* The output table
 */
template <typename Tkey>
table_info* mpi_exscan_computation_Tkey(array_info* cat_column,
                                        table_info* in_table, int64_t num_keys,
                                        int* ftypes, int* func_offsets,
                                        bool is_parallel, bool skipdropna,
                                        bool return_key, bool return_index,
                                        bool use_sql_rules) {
    std::vector<array_info*> out_arrs;
    // We do not return the keys in output in the case of cumulative operations.
    int64_t n_rows = in_table->nrows();
    int return_index_i = return_index;
    int k = 0;
    for (uint64_t i = num_keys; i < in_table->ncols() - return_index_i;
         i++, k++) {
        array_info* col = in_table->columns[i];
        int start = func_offsets[k];
        int end = func_offsets[k + 1];
        for (int j = start; j != end; j++) {
            array_info* out_col =
                alloc_array(n_rows, 1, 1, col->arr_type, col->dtype, 0,
                            col->num_categories);
            int ftype = ftypes[j];
            aggfunc_output_initialize(out_col, ftype, use_sql_rules);
            out_arrs.push_back(out_col);
        }
    }
    // Since each column can have different data type and MPI_Exscan can only do
    // one type at a time. thus we have an iteration over the columns of the
    // input table. But we can consider the various cumsum / cumprod / cummax /
    // cummin in turn.
    k = 0;
    for (uint64_t i = num_keys; i < in_table->ncols() - return_index_i;
         i++, k++) {
        array_info* col = in_table->columns[i];
        const Bodo_CTypes::CTypeEnum dtype = col->dtype;
        if (dtype == Bodo_CTypes::INT8) {
            mpi_exscan_computation_T<Tkey, int8_t, Bodo_CTypes::INT8>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::UINT8) {
            mpi_exscan_computation_T<Tkey, uint8_t, Bodo_CTypes::UINT8>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::INT16) {
            mpi_exscan_computation_T<Tkey, int16_t, Bodo_CTypes::INT16>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::UINT16) {
            mpi_exscan_computation_T<Tkey, uint16_t, Bodo_CTypes::UINT16>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::INT32) {
            mpi_exscan_computation_T<Tkey, int32_t, Bodo_CTypes::INT32>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::UINT32) {
            mpi_exscan_computation_T<Tkey, uint32_t, Bodo_CTypes::UINT32>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::INT64) {
            mpi_exscan_computation_T<Tkey, int64_t, Bodo_CTypes::INT64>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::UINT64) {
            mpi_exscan_computation_T<Tkey, uint64_t, Bodo_CTypes::UINT64>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::FLOAT32) {
            mpi_exscan_computation_T<Tkey, float, Bodo_CTypes::FLOAT32>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        } else if (dtype == Bodo_CTypes::FLOAT64) {
            mpi_exscan_computation_T<Tkey, double, Bodo_CTypes::FLOAT64>(
                out_arrs, cat_column, in_table, num_keys, k, ftypes,
                func_offsets, is_parallel, skipdropna);
        }
    }
    if (return_index) {
        out_arrs.push_back(copy_array(in_table->columns.back()));
    }

    return new table_info(out_arrs);
}

table_info* mpi_exscan_computation(array_info* cat_column, table_info* in_table,
                                   int64_t num_keys, int* ftypes,
                                   int* func_offsets, bool is_parallel,
                                   bool skipdropna, bool return_key,
                                   bool return_index, bool use_sql_rules) {
    tracing::Event ev("mpi_exscan_computation", is_parallel);
    const Bodo_CTypes::CTypeEnum dtype = cat_column->dtype;
    if (dtype == Bodo_CTypes::INT8) {
        return mpi_exscan_computation_Tkey<int8_t>(
            cat_column, in_table, num_keys, ftypes, func_offsets, is_parallel,
            skipdropna, return_key, return_index, use_sql_rules);
    } else if (dtype == Bodo_CTypes::UINT8) {
        return mpi_exscan_computation_Tkey<uint8_t>(
            cat_column, in_table, num_keys, ftypes, func_offsets, is_parallel,
            skipdropna, return_key, return_index, use_sql_rules);
    } else if (dtype == Bodo_CTypes::INT16) {
        return mpi_exscan_computation_Tkey<int16_t>(
            cat_column, in_table, num_keys, ftypes, func_offsets, is_parallel,
            skipdropna, return_key, return_index, use_sql_rules);
    } else if (dtype == Bodo_CTypes::UINT16) {
        return mpi_exscan_computation_Tkey<uint16_t>(
            cat_column, in_table, num_keys, ftypes, func_offsets, is_parallel,
            skipdropna, return_key, return_index, use_sql_rules);
    } else if (dtype == Bodo_CTypes::INT32) {
        return mpi_exscan_computation_Tkey<int32_t>(
            cat_column, in_table, num_keys, ftypes, func_offsets, is_parallel,
            skipdropna, return_key, return_index, use_sql_rules);
    } else if (dtype == Bodo_CTypes::UINT32) {
        return mpi_exscan_computation_Tkey<uint32_t>(
            cat_column, in_table, num_keys, ftypes, func_offsets, is_parallel,
            skipdropna, return_key, return_index, use_sql_rules);
    } else if (dtype == Bodo_CTypes::INT64) {
        return mpi_exscan_computation_Tkey<int64_t>(
            cat_column, in_table, num_keys, ftypes, func_offsets, is_parallel,
            skipdropna, return_key, return_index, use_sql_rules);
    } else if (dtype == Bodo_CTypes::UINT64) {
        return mpi_exscan_computation_Tkey<uint64_t>(
            cat_column, in_table, num_keys, ftypes, func_offsets, is_parallel,
            skipdropna, return_key, return_index, use_sql_rules);
    } else {
        throw std::runtime_error(
            "MPI EXSCAN groupby implementation failed to find a matching "
            "dtype");
    }
}