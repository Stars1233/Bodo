// Copyright (C) 2023 Bodo Inc. All rights reserved.

#ifndef _GROUPBY_COMMON_H_INCLUDED
#define _GROUPBY_COMMON_H_INCLUDED

#include "_bodo_common.h"

/**
 * This function defines helper functions that are shared by multiple possible
 * groupby paths.
 */

/**
 * @brief Initialize the output column for the groupby operation
 * based on the type of the function and if we are using SQL rules.
 *
 * @param[in, out] out_col The array to initialize
 * @param[in] ftype The function type
 * @param[in] use_sql_rules Are we using SQL rules?
 */
void aggfunc_output_initialize(array_info* out_col, int ftype,
                               bool use_sql_rules);

/**
 * Returns the array type and dtype required for output columns based on the
 * aggregation function and input dtype.
 *
 * @param[in] ftype Function type
 * @param[in,out] array type (caller sets a default, this function only changes
 * in certain cases)
 * @param[in,out] output dtype (caller sets a default, this function only
 * changes in certain cases)
 * @param[in] is_key true if column is key column (in this case ignore because
 * output type will be the same)
 * @param[in] is_combine true if we are initializing the output for a combine
 * operation, which may remap certain functions.
 */
void get_groupby_output_dtype(int ftype,
                              bodo_array_type::arr_type_enum& array_type,
                              Bodo_CTypes::CTypeEnum& dtype, bool is_key,
                              bool is_combine);

#endif  // _GROUPBY_COMMON_H_INCLUDED