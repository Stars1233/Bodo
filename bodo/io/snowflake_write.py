import operator

import numba
import numpy as np
import pandas as pd
from llvmlite import ir as lir
from numba.core import cgutils, types
from numba.core.imputils import impl_ret_borrowed
from numba.extending import (
    box,
    intrinsic,
    models,
    overload,
    register_model,
    unbox,
)
from numba.typed import List

import bodo
from bodo.hiframes.pd_dataframe_ext import DataFrameType, get_dataframe_table
from bodo.io.helpers import exception_propagating_thread_type
from bodo.io.parquet_pio import parquet_write_table_cpp
from bodo.io.snowflake import (
    snowflake_connector_cursor_type,
    temporary_directory_type,
)
from bodo.libs.array import (
    array_to_info,
    concat_tables_cpp,
    py_table_to_cpp_table,
    table_type,
)
from bodo.libs.str_ext import unicode_to_utf8
from bodo.utils import tracing
from bodo.utils.typing import (
    BodoError,
    get_overload_const_str,
    is_overload_bool,
    is_overload_constant_str,
    is_overload_none,
)
from bodo.utils.utils import check_and_propagate_cpp_exception


class SnowflakeWriterType(types.Type):
    """Data type for streaming Snowflake writer's internal state"""

    def __init__(self):
        super().__init__(name="SnowflakeWriterType")


snowflake_writer_type = SnowflakeWriterType()


class SnowflakeWriterPayloadType(types.Type):
    """Data type for streaming Snowflake writer's payload"""

    def __init__(self):
        super().__init__(name="SnowflakeWriterPayloadType")


snowflake_writer_payload_type = SnowflakeWriterPayloadType()


snowflake_writer_payload_members = (
    # Snowflake connection string
    ("conn", types.unicode_type),
    # Location on Snowflake to create a table
    ("location", types.unicode_type),
    # Action to take if table already exists: fail, replace, append
    ("if_exists", types.unicode_type),
    # Type of table to create: permanent, temporary, transient
    ("table_type", types.unicode_type),
    # Whether write is occurring in parallel
    ("parallel", types.boolean),
    # Whether this rank has finished appending data to the table
    ("finished", types.boolean),
    # Region of internal stage bucket
    ("bucket_region", types.unicode_type),
    # Total number of Parquet files written on this rank so far
    ("chunk_count", types.int64),
    # Total number of Parquet files written across all ranks
    ("file_count", types.int64),
    # Copy into directory
    ("copy_into_dir", types.unicode_type),
    # Snowflake query ID for previous COPY INTO command
    ("copy_into_prev_sfqid", types.unicode_type),
    # File count for previous COPY INTO command
    ("file_count_prev", types.int64),
    # If we are using the PUT command to upload files, a list of upload
    # threads currently in progress
    ("upload_threads", types.List(exception_propagating_thread_type)),
    # Whether the `upload_threads` list exists. Needed for typing purposes,
    # as initializing an empty list in `init()` causes an error
    ("upload_threads_exists", types.boolean),
    # Snowflake connection cursor. Only on rank 0, unless PUT method is used
    ("cursor", snowflake_connector_cursor_type),
    # Python TemporaryDirectory object, which stores Parquet files during PUT upload
    ("tmp_folder", temporary_directory_type),
    # Name of created internal stage
    ("stage_name", types.unicode_type),
    # Parquet path of internal stage, could be an S3/ADLS URI or a local path
    # in case of upload using PUT. Includes a trailing slash
    ("stage_path", types.unicode_type),
    # Whether we are using the Snowflake PUT command to upload files. This is
    # set to True if we don't support the stage type returned by Snowflake
    ("upload_using_snowflake_put", types.boolean),
    # Old environment variables that were overwritten to update credentials
    # for uploading to stage
    ("old_creds", types.DictType(types.unicode_type, types.unicode_type)),
    # Whether the stage is ADLS backed and we'll be writing parquet files to it
    # directly using our existing HDFS and Parquet infrastructure
    ("azure_stage_direct_upload", types.boolean),
    # If azure_stage_direct_upload=True, we replace bodo.HDFS_CORE_SITE_LOC
    # with a new core-site.xml. `old_core_site` contains the original contents
    # of the file or "__none__" if file didn't originally exist, so that it
    # can be restored later after copy into
    ("old_core_site", types.unicode_type),
    # If azure_stage_direct_upload=True, we replace contents in
    # SF_AZURE_WRITE_SAS_TOKEN_FILE_LOCATION if any with the SAS token for
    # this upload. `old_sas_token` contains the original contents of the file
    # or "__none__" if file didn't originally exist, so that it can be
    # restored later after copy into
    ("old_sas_token", types.unicode_type),
    # Batches collected to write
    ("batches", types.List(table_type)),
    # Whether the `batches` list exists. Needed for typing purposes, as
    # initializing an empty list in `init()` causes an error
    ("batches_exists", types.boolean),
    # Uncompressed memory usage of batches
    ("curr_mem_size", types.int64),
)
snowflake_writer_payload_members_dict = dict(snowflake_writer_payload_members)


@register_model(SnowflakeWriterPayloadType)
class SnowflakeWriterPayloadModel(models.StructModel):
    def __init__(self, dmm, fe_type):  # pragma: no cover
        members = snowflake_writer_payload_members
        models.StructModel.__init__(self, dmm, fe_type, members)


@register_model(SnowflakeWriterType)
class SnowflakeWriterModel(models.StructModel):
    def __init__(self, dmm, fe_type):  # pragma: no cover
        payload_type = snowflake_writer_payload_type
        members = [
            ("meminfo", types.MemInfoPointer(payload_type)),
        ]
        models.StructModel.__init__(self, dmm, fe_type, members)


def define_snowflake_writer_dtor(
    context, builder, snowflake_writer_type, payload_type
):  # pragma: no cover
    """
    Define destructor for Snowflake writer type if not already defined
    """
    mod = builder.module
    # Declare dtor
    fnty = lir.FunctionType(lir.VoidType(), [cgutils.voidptr_t])
    fn = cgutils.get_or_insert_function(mod, fnty, name=".dtor.snowflake_writer")

    # End early if the dtor is already defined
    if not fn.is_declaration:
        return fn

    fn.linkage = "linkonce_odr"
    # Populate the dtor
    builder = lir.IRBuilder(fn.append_basic_block())
    base_ptr = fn.args[0]  # void*

    # Get payload struct
    ptrty = context.get_value_type(payload_type).as_pointer()
    payload_ptr = builder.bitcast(base_ptr, ptrty)
    payload = context.make_helper(builder, payload_type, ref=payload_ptr)

    # Decref each payload field
    for attr, fe_type in snowflake_writer_payload_members:
        context.nrt.decref(builder, fe_type, getattr(payload, attr))

    builder.ret_void()
    return fn


@intrinsic
def sf_writer_alloc(typingctx):  # pragma: no cover
    def codegen(context, builder, sig, args):  # pragma: no cover
        """Creates meminfo and sets dtor for Snowflake writer"""
        # Create payload type
        payload_type = snowflake_writer_payload_type
        alloc_type = context.get_value_type(payload_type)
        alloc_size = context.get_abi_sizeof(alloc_type)

        # Define dtor
        dtor_fn = define_snowflake_writer_dtor(
            context, builder, snowflake_writer_type, payload_type
        )

        # Create meminfo
        meminfo = context.nrt.meminfo_alloc_dtor(
            builder, context.get_constant(types.uintp, alloc_size), dtor_fn
        )
        meminfo_void_ptr = context.nrt.meminfo_data(builder, meminfo)
        meminfo_data_ptr = builder.bitcast(meminfo_void_ptr, alloc_type.as_pointer())

        # Alloc values in payload. Note: garbage values will be stored in all
        # fields until sf_writer_setattr is called for the first time
        payload = cgutils.create_struct_proxy(payload_type)(context, builder)
        builder.store(payload._getvalue(), meminfo_data_ptr)

        # Construct Snowflake writer from payload
        snowflake_writer = context.make_helper(builder, snowflake_writer_type)
        snowflake_writer.meminfo = meminfo
        return snowflake_writer._getvalue()

    return snowflake_writer_type(), codegen


def _get_snowflake_writer_payload(
    context, builder, writer_typ, writer
):  # pragma: no cover
    """Get payload struct proxy for a Snowflake writer value"""
    snowflake_writer = context.make_helper(builder, writer_typ, writer)
    payload_type = snowflake_writer_payload_type
    meminfo_void_ptr = context.nrt.meminfo_data(builder, snowflake_writer.meminfo)
    meminfo_data_ptr = builder.bitcast(
        meminfo_void_ptr, context.get_value_type(payload_type).as_pointer()
    )
    payload = cgutils.create_struct_proxy(payload_type)(
        context, builder, builder.load(meminfo_data_ptr)
    )
    return payload, meminfo_data_ptr


@intrinsic
def sf_writer_getattr(typingctx, writer_typ, attr_typ):  # pragma: no cover
    """Get attribute of a Snowflake writer"""
    assert isinstance(writer_typ, SnowflakeWriterType), (
        f"sf_writer_getattr: expected `writer` to be a SnowflakeWriterType, "
        f"but found {writer_typ}"
    )
    assert is_overload_constant_str(attr_typ), (
        f"sf_writer_getattr: expected `attr` to be a literal string type, "
        f"but found {attr_typ}"
    )
    attr = get_overload_const_str(attr_typ)
    val_typ = snowflake_writer_payload_members_dict[attr]

    def codegen(context, builder, sig, args):  # pragma: no cover
        writer, _ = args
        payload, _ = _get_snowflake_writer_payload(context, builder, writer_typ, writer)
        return impl_ret_borrowed(
            context, builder, sig.return_type, getattr(payload, attr)
        )

    return val_typ(writer_typ, attr_typ), codegen


@intrinsic
def sf_writer_setattr(typingctx, writer_typ, attr_typ, val_typ):  # pragma: no cover
    """Set attribute of a Snowflake writer"""
    assert isinstance(writer_typ, SnowflakeWriterType), (
        f"sf_writer_setattr: expected `writer` to be a SnowflakeWriterType, "
        f"but found {writer_typ}"
    )
    assert is_overload_constant_str(attr_typ), (
        f"sf_writer_setattr: expected `attr` to be a literal string type, "
        f"but found {attr_typ}"
    )
    attr = get_overload_const_str(attr_typ)

    # Storing a literal type into the payload causes a type mismatch
    val_typ = numba.types.unliteral(val_typ)

    def codegen(context, builder, sig, args):  # pragma: no cover
        writer, _, val = args
        payload, meminfo_data_ptr = _get_snowflake_writer_payload(
            context, builder, writer_typ, writer
        )
        context.nrt.decref(builder, val_typ, getattr(payload, attr))
        context.nrt.incref(builder, val_typ, val)
        setattr(payload, attr, val)
        builder.store(payload._getvalue(), meminfo_data_ptr)
        return context.get_dummy_value()

    return types.none(writer_typ, attr_typ, val_typ), codegen


@overload(operator.getitem, no_unliteral=True)
def snowflake_writer_getitem(writer, attr):
    return lambda writer, attr: sf_writer_getattr(writer, attr)  # pragma: no cover


@overload(operator.setitem, no_unliteral=True)
def snowflake_writer_setitem(writer, attr, val):
    return lambda writer, attr, val: sf_writer_setattr(
        writer, attr, val
    )  # pragma: no cover


@box(SnowflakeWriterType)
def box_snowflake_writer(typ, val, c):
    # Boxing is disabled, to avoid boxing overheads anytime a writer attribute
    # is accessed from objmode. As a workaround, store the necessary attributes
    # into local variables in numba native code before entering objmode
    raise NotImplementedError(
        f"Boxing is disabled for SnowflakeWriter mutable struct."
    )  # pragma: no cover


@unbox(SnowflakeWriterType)
def unbox_snowflake_writer(typ, val, c):
    raise NotImplementedError(
        f"Unboxing is disabled for SnowflakeWriter mutable struct."
    )  # pragma: no cover


@numba.generated_jit(nopython=True, no_cpython_wrapper=True)
def snowflake_writer_init(
    conn, table_name, schema, if_exists, table_type, _is_parallel=False
):  # pragma: no cover
    func_text = (
        "def impl(conn, table_name, schema, if_exists, table_type, _is_parallel=False):\n"
        "    ev = tracing.Event('snowflake_writer_init', is_parallel=_is_parallel)\n"
        "    location = ''\n"
    )

    if not is_overload_none(schema):
        func_text += "    location += '\"' + schema + '\".'\n"

    func_text += (
        "    location += table_name\n"
        # Initialize writer
        "    writer = sf_writer_alloc()\n"
        "    writer['conn'] = conn\n"
        "    writer['location'] = location\n"
        "    writer['if_exists'] = if_exists\n"
        "    writer['table_type'] = table_type\n"
        "    writer['parallel'] = _is_parallel\n"
        "    writer['finished'] = False\n"
        "    writer['chunk_count'] = 0\n"
        "    writer['file_count'] = 0\n"
        "    writer['copy_into_prev_sfqid'] = ''\n"
        "    writer['file_count_prev'] = 0\n"
        "    writer['upload_threads_exists'] = False\n"
        "    writer['batches_exists'] = False\n"
        "    writer['curr_mem_size'] = 0\n"
        # Connect to Snowflake on rank 0 and get internal stage credentials
        # Note: Identical to the initialization code in df.to_sql()
        "    with bodo.objmode(\n"
        "        cursor='snowflake_connector_cursor_type',\n"
        "        tmp_folder='temporary_directory_type',\n"
        "        stage_name='unicode_type',\n"
        "        stage_path='unicode_type',\n"
        "        upload_using_snowflake_put='boolean',\n"
        "        old_creds='DictType(unicode_type, unicode_type)',\n"
        "        azure_stage_direct_upload='boolean',\n"
        "        old_core_site='unicode_type',\n"
        "        old_sas_token='unicode_type',\n"
        "    ):\n"
        "        cursor, tmp_folder, stage_name, stage_path, upload_using_snowflake_put, old_creds, azure_stage_direct_upload, old_core_site, old_sas_token = bodo.io.snowflake.connect_and_get_upload_info(conn)\n"
        "    writer['cursor'] = cursor\n"
        "    writer['tmp_folder'] = tmp_folder\n"
        "    writer['stage_name'] = stage_name\n"
        "    writer['stage_path'] = stage_path\n"
        "    writer['upload_using_snowflake_put'] = upload_using_snowflake_put\n"
        "    writer['old_creds'] = old_creds\n"
        "    writer['azure_stage_direct_upload'] = azure_stage_direct_upload\n"
        "    writer['old_core_site'] = old_core_site\n"
        "    writer['old_sas_token'] = old_sas_token\n"
        # Barrier ensures that internal stage exists before we upload files to it
        "    bodo.barrier()\n"
        # Force reset the existing hadoop filesystem instance, to use new SAS token.
        # See to_sql() for more detailed comments
        "    if azure_stage_direct_upload:\n"
        "        bodo.libs.distributed_api.disconnect_hdfs_njit()\n"
        # Compute bucket region
        "    writer['bucket_region'] = bodo.io.fs_io.get_s3_bucket_region_njit(stage_path, _is_parallel)\n"
        # Set up internal stage directory for COPY INTO
        "    writer['copy_into_dir'] = make_new_copy_into_dir(\n"
        "        upload_using_snowflake_put, stage_path, _is_parallel\n"
        "    )\n"
        "    ev.finalize()\n"
        "    return writer\n"
    )

    glbls = {
        "bodo": bodo,
        "check_and_propagate_cpp_exception": check_and_propagate_cpp_exception,
        "List": List,
        "make_new_copy_into_dir": make_new_copy_into_dir,
        "sf_writer_alloc": sf_writer_alloc,
        "tracing": tracing,
    }

    l = {}
    exec(func_text, glbls, l)
    return l["impl"]


@numba.generated_jit(nopython=True, no_cpython_wrapper=True)
def snowflake_writer_append_df(writer, df, is_last):  # pragma: no cover
    if not isinstance(writer, SnowflakeWriterType):  # pragma: no cover
        raise BodoError(
            f"snowflake_writer_append_df: Expected type SnowflakeWriterType "
            f"for `writer`, found {writer}"
        )
    if not isinstance(df, DataFrameType):  # pragma: no cover
        raise BodoError(
            f"snowflake_writer_append_df: Expected type DataFrameType "
            f"for `df`, found {df}"
        )
    if not is_overload_bool(is_last):  # pragma: no cover
        raise BodoError(
            f"snowflake_writer_append_df: Expected type boolean "
            f"for `is_last`, found {is_last}"
        )

    col_names_arr = pd.array(df.columns)
    col_types_arr = df.data
    sf_schema = bodo.io.snowflake.gen_snowflake_schema(df.columns, df.data)

    func_text = (
        "def impl(writer, df, is_last):\n"
        "    if writer['finished']:\n"
        "        return\n"
        "    ev = tracing.Event('snowflake_writer_append_df', is_parallel=writer['parallel'])\n"
        # ===== Part 1: Accumulate batch in writer and compute total size
        "    ev_append_batch = tracing.Event(f'append_batch', is_parallel=True)\n"
        "    py_table = get_dataframe_table(df)\n"
        "    cpp_table = py_table_to_cpp_table(py_table, py_table_typ)\n"
        "    if writer['batches_exists']:\n"
        "        writer['batches'].append(cpp_table)\n"
        "    else:\n"
        "        writer['batches_exists'] = True\n"
        "        writer['batches'] = [cpp_table]\n"
        f"    nbytes_arr = np.empty({len(df.columns)}, np.int64)\n"
        "    bodo.utils.table_utils.generate_table_nbytes(py_table, nbytes_arr, 0)\n"
        "    nbytes = np.sum(nbytes_arr)\n"
        "    writer['curr_mem_size'] += nbytes\n"
        "    ev_append_batch.add_attribute('nbytes', nbytes)\n"
        "    ev_append_batch.finalize()\n"
        # ===== Part 2: Write Parquet file if file size threshold is exceeded
        "    if is_last or writer['curr_mem_size'] >= bodo.io.snowflake.SF_WRITE_PARQUET_CHUNK_SIZE:\n"
        "        ev_sf_write_concat = tracing.Event(f'sf_write_concat', is_parallel=False)\n"
        "        ev_sf_write_concat.add_attribute('num_batches', len(writer['batches']))\n"
        # Note: Using `concat` here means that our write batches are at least
        # as large as our read batches. It may be advantageous in the future to
        # split up large incoming batches into multiple Parquet files to write
        "        out_table = concat_tables_cpp(writer['batches'])\n"
        "        out_table_len = len(bodo.libs.array.array_from_cpp_table(out_table, 0, col_types_arr[0]))\n"
        "        ev_sf_write_concat.add_attribute('out_table_len', out_table_len)\n"
        "        ev_sf_write_concat.finalize()\n"
        "        if out_table_len > 0:\n"
        # Note: writer['stage_path'] already has trailing slash
        "            ev_upload_df = tracing.Event('upload_df', is_parallel=False)\n"
        '            chunk_path = f\'{writer["stage_path"]}{writer["copy_into_dir"]}/file{writer["chunk_count"]}_rank{bodo.get_rank()}_{bodo.io.helpers.uuid4_helper()}.parquet\'\n'
        # To escape backslashes, we want to replace ( \ ) with ( \\ ), so the func_text
        # should contain the string literals ( \\ ) and ( \\\\ ). To add these to func_text,
        # we need to write ( \\\\ ) and ( \\\\\\\\ ) here.
        # To escape quotes, we want to replace ( ' ) with ( \' ), so the func_text
        # should contain the string literals ( ' ) and ( \\' ). To add these to func_text,
        # we need to write ( \' ) and ( \\\\\' ) here.
        '            chunk_path = chunk_path.replace("\\\\", "\\\\\\\\")\n'
        '            chunk_path = chunk_path.replace("\'", "\\\\\'")\n'
        # Copied from bodo.hiframes.pd_dataframe_ext.to_sql_overload
        # TODO: Refactor both sections to generate this code in a helper function
        "            ev_pq_write_cpp = tracing.Event('pq_write_cpp', is_parallel=False)\n"
        "            ev_pq_write_cpp.add_attribute('out_table_len', out_table_len)\n"
        "            ev_pq_write_cpp.add_attribute('chunk_idx', writer['chunk_count'])\n"
        "            ev_pq_write_cpp.add_attribute('chunk_path', chunk_path)\n"
        "            parquet_write_table_cpp(\n"
        "                unicode_to_utf8(chunk_path),\n"
        "                out_table, array_to_info(col_names_arr), 0,\n"
        "                False,\n"  # write_index
        "                unicode_to_utf8('null'),\n"  # metadata
        "                unicode_to_utf8(bodo.io.snowflake.SF_WRITE_PARQUET_COMPRESSION),\n"
        "                False,\n"  # is_parallel
        "                0,\n"  # write_rangeindex_to_metadata
        "                0, 0, 0,\n"  # range index start, stop, step
        "                unicode_to_utf8('null'),\n"  # idx_name
        "                unicode_to_utf8(writer['bucket_region']),\n"
        "                out_table_len,\n"  # row_group_size
        "                unicode_to_utf8('null'),\n"  # prefix
        "                True,\n"  # Explicitly cast timedelta to int64
        "                unicode_to_utf8('UTC'),\n"  # Explicitly set tz='UTC'
        "                True,\n"  # Explicitly downcast nanoseconds to microseconds
        "            )\n"
        "            ev_pq_write_cpp.finalize()\n"
        # In case of Snowflake PUT, upload local parquet to internal stage
        # in a separate Python thread
        "            if writer['upload_using_snowflake_put']:\n"
        "                cursor = writer['cursor']\n"
        "                chunk_count = writer['chunk_count']\n"
        "                stage_name = writer['stage_name']\n"
        "                copy_into_dir = writer['copy_into_dir']\n"
        "                if bodo.io.snowflake.SF_WRITE_OVERLAP_UPLOAD:\n"
        "                    with bodo.objmode(upload_thread='exception_propagating_thread_type'):\n"
        "                        upload_thread = bodo.io.snowflake.do_upload_and_cleanup(\n"
        "                            cursor, chunk_count, chunk_path, stage_name, copy_into_dir\n"
        "                        )\n"
        "                    if writer['upload_threads_exists']:\n"
        "                        writer['upload_threads'].append(upload_thread)\n"
        "                    else:\n"
        "                        writer['upload_threads_exists'] = True\n"
        "                        writer['upload_threads'] = [upload_thread]\n"
        "                else:\n"
        "                    with bodo.objmode():\n"
        "                        bodo.io.snowflake.do_upload_and_cleanup(\n"
        "                            cursor, chunk_count, chunk_path, stage_name, copy_into_dir\n"
        "                        )\n"
        "            writer['chunk_count'] += 1\n"
        "            ev_upload_df.finalize()\n"
        "        writer['batches'].clear()\n"
        "        writer['curr_mem_size'] = 0\n"
        # Count number of newly written files. This is also an implicit barrier
        "    if writer['parallel']:\n"
        "        sum_op = np.int32(bodo.libs.distributed_api.Reduce_Type.Sum.value)\n"
        "        writer['file_count'] = bodo.libs.distributed_api.dist_reduce(writer['chunk_count'], sum_op)\n"
        "    else:\n"
        "        writer['file_count'] = writer['chunk_count']\n"
        # ===== Part 3: Execute COPY INTO from Rank 0 if file count threshold is exceeded.
        # In case of Snowflake PUT, first wait for all upload threads to finish
        "    if is_last or writer['file_count'] >= bodo.io.snowflake.SF_WRITE_STREAMING_NUM_FILES:\n"
        "        if writer['upload_using_snowflake_put'] and bodo.io.snowflake.SF_WRITE_OVERLAP_UPLOAD:\n"
        "            parallel = writer['parallel']\n"
        "            if writer['upload_threads_exists']:\n"
        "                upload_threads = writer['upload_threads']\n"
        "                with bodo.objmode():\n"
        "                    bodo.io.helpers.join_all_threads(upload_threads, parallel)\n"
        "                writer['upload_threads'].clear()\n"
        "            else:\n"
        "                with bodo.objmode():\n"
        "                    bodo.io.helpers.join_all_threads([], parallel)\n"
        # For the first COPY INTO, create table if it doesn't exist
        "        if writer['copy_into_prev_sfqid'] == '' and bodo.get_rank() == 0:\n"
        "            cursor = writer['cursor']\n"
        "            location = writer['location']\n"
        "            if_exists = writer['if_exists']\n"
        "            table_type = writer['table_type']\n"
        "            with bodo.objmode():\n"
        "                bodo.io.snowflake.create_table_handle_exists(\n"
        "                    cursor, location, sf_schema, if_exists, table_type\n"
        "                )\n"
        # If an async COPY INTO command is in progress, retrieve and validate it.
        # Broadcast errors across ranks as needed.
        "        parallel = writer['parallel']\n"
        "        if (not parallel or bodo.get_rank() == 0) and writer['copy_into_prev_sfqid'] != '':\n"
        "            cursor = writer['cursor']\n"
        "            copy_into_prev_sfqid = writer['copy_into_prev_sfqid']\n"
        "            file_count_prev = writer['file_count_prev']\n"
        "            with bodo.objmode():\n"
        "                err = bodo.io.snowflake.retrieve_async_copy_into(\n"
        "                    cursor, copy_into_prev_sfqid, file_count_prev\n"
        "                )\n"
        "                bodo.io.helpers.sync_and_reraise_error(err, _is_parallel=parallel)\n"
        "        else:\n"
        "            with bodo.objmode():\n"
        "                bodo.io.helpers.sync_and_reraise_error(None, _is_parallel=parallel)\n"
        # Execute async COPY INTO form rank 0
        "        if bodo.get_rank() == 0:\n"
        "            cursor = writer['cursor']\n"
        "            stage_name = writer['stage_name']\n"
        "            location = writer['location']\n"
        "            copy_into_dir = writer['copy_into_dir']\n"
        "            with bodo.objmode(copy_into_new_sfqid='unicode_type'):\n"
        "                copy_into_new_sfqid = bodo.io.snowflake.execute_copy_into(\n"
        "                    cursor, stage_name, location, sf_schema,\n"
        "                    synchronous=False, stage_dir=copy_into_dir,\n"
        "                )\n"
        "            writer['copy_into_prev_sfqid'] = copy_into_new_sfqid\n"
        "            writer['file_count_prev'] = writer['file_count']\n"
        # Create a new COPY INTO internal stage directory
        "        writer['chunk_count'] = 0\n"
        "        writer['file_count'] = 0\n"
        "        writer['copy_into_dir'] = make_new_copy_into_dir(\n"
        "            writer['upload_using_snowflake_put'],\n"
        "            writer['stage_path'],\n"
        "            writer['parallel'],\n"
        "        )\n"
        # ===== Part 4: Snowflake Post Handling
        # Retrieve and validate the last COPY INTO command
        "    if is_last:\n"
        "        parallel = writer['parallel']\n"
        "        if (not parallel or bodo.get_rank() == 0) and writer['copy_into_prev_sfqid'] != '':\n"
        "            cursor = writer['cursor']\n"
        "            copy_into_prev_sfqid = writer['copy_into_prev_sfqid']\n"
        "            file_count_prev = writer['file_count_prev']\n"
        "            with bodo.objmode():\n"
        "                err = bodo.io.snowflake.retrieve_async_copy_into(\n"
        "                    cursor, copy_into_prev_sfqid, file_count_prev\n"
        "                )\n"
        "                bodo.io.helpers.sync_and_reraise_error(err, _is_parallel=parallel)\n"
        "        else:\n"
        "            with bodo.objmode():\n"
        "                bodo.io.helpers.sync_and_reraise_error(None, _is_parallel=parallel)\n"
        "        if bodo.get_rank() == 0:\n"
        "            writer['copy_into_prev_sfqid'] = ''\n"
        "            writer['file_count_prev'] = 0\n"
        # Force reset the existing Hadoop filesystem instance to avoid
        # conflicts with any future ADLS operations in the same process
        "        if writer['azure_stage_direct_upload']:\n"
        "            bodo.libs.distributed_api.disconnect_hdfs_njit()\n"
        # Drop internal stage, close Snowflake connection cursor, put back
        # environment variables, restore contents in case of ADLS stage
        "        cursor = writer['cursor']\n"
        "        stage_name = writer['stage_name']\n"
        "        old_creds = writer['old_creds']\n"
        "        tmp_folder = writer['tmp_folder']\n"
        "        azure_stage_direct_upload = writer['azure_stage_direct_upload']\n"
        "        old_core_site = writer['old_core_site']\n"
        "        old_sas_token = writer['old_sas_token']\n"
        "        with bodo.objmode():\n"
        "            if cursor is not None:\n"
        "                bodo.io.snowflake.drop_internal_stage(cursor, stage_name)\n"
        "                cursor.close()\n"
        "            bodo.io.snowflake.update_env_vars(old_creds)\n"
        "            tmp_folder.cleanup()\n"
        "            if azure_stage_direct_upload:\n"
        "                bodo.io.snowflake.update_file_contents(\n"
        "                    bodo.HDFS_CORE_SITE_LOC, old_core_site\n"
        "                )\n"
        "                bodo.io.snowflake.update_file_contents(\n"
        "                    bodo.io.snowflake.SF_AZURE_WRITE_SAS_TOKEN_FILE_LOCATION, old_sas_token\n"
        "                )\n"
        "        if writer['parallel']:\n"
        "            bodo.barrier()\n"
        "        writer['finished'] = True\n"
        "    ev.finalize()\n"
    )

    glbls = {
        "array_to_info": array_to_info,
        "bodo": bodo,
        "check_and_propagate_cpp_exception": check_and_propagate_cpp_exception,
        "col_names_arr": col_names_arr,
        "col_types_arr": col_types_arr,
        "concat_tables_cpp": concat_tables_cpp,
        "get_dataframe_table": get_dataframe_table,
        "make_new_copy_into_dir": make_new_copy_into_dir,
        "np": np,
        "parquet_write_table_cpp": parquet_write_table_cpp,
        "py_table_to_cpp_table": py_table_to_cpp_table,
        "py_table_typ": df.table_type,
        "sf_schema": sf_schema,
        "tracing": tracing,
        "unicode_to_utf8": unicode_to_utf8,
    }

    l = {}
    exec(func_text, glbls, l)
    return l["impl"]


@numba.generated_jit(nopython=True, no_cpython_wrapper=True)
def make_new_copy_into_dir(
    upload_using_snowflake_put, stage_path, _is_parallel
):  # pragma: no cover
    """Generate a new COPY INTO directory using uuid4 and synchronize the
    result across ranks. This is intended to be called from every rank, as
    each rank's copy_into_dir will be created in a different TemporaryDirectory.
    All ranks share the same `copy_into_dir` suffix."""
    if not is_overload_bool(_is_parallel):  # pragma: no cover
        raise BodoError(
            f"make_new_copy_into_dir: Expected type boolean "
            f"for _is_parallel, found {_is_parallel}"
        )

    func_text = (
        "def impl(upload_using_snowflake_put, stage_path, _is_parallel):\n"
        "    copy_into_dir = ''\n"
        "    if not _is_parallel or bodo.get_rank() == 0:\n"
        "        copy_into_dir = bodo.io.helpers.uuid4_helper()\n"
        "    if _is_parallel:\n"
        "        copy_into_dir = bodo.libs.distributed_api.bcast_scalar(copy_into_dir)\n"
        # In case of upload using PUT, chunk_path is a local directory,
        # so it must be created. `makedirs_helper` is intended to be called
        # from all ranks at once, as each rank has a different TemporaryDirectory
        # and thus a different input `stage_path`.
        "    if upload_using_snowflake_put:\n"
        "        copy_into_path = stage_path + copy_into_dir\n"
        "        bodo.io.helpers.makedirs_helper(copy_into_path, exist_ok=True)\n"
        "    return copy_into_dir\n"
    )

    glbls = {
        "bodo": bodo,
    }

    l = {}
    exec(func_text, glbls, l)
    return l["impl"]