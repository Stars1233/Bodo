# Copyright (C) 2020 Bodo Inc. All rights reserved.
"""
S3 & Hadoop file system supports, and file system dependent calls
"""
import warnings
from urllib.parse import urlparse
import glob
import os
import numba
import bodo

from bodo.utils.typing import BodoWarning, BodoError


def get_s3_fs():
    """
    initialize S3FileSystem with credentials
    """
    try:
        import s3fs
    except:  # pragma: no cover
        raise BodoError("Reading from s3 requires s3fs currently.")

    custom_endpoint = os.environ.get("AWS_S3_ENDPOINT", None)
    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID", None)
    aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY", None)

    # always use s3fs.S3FileSystem.clear_instance_cache()
    # before initializing S3FileSystem due to inconsistent file system
    # between to_parquet to read_parquet
    if custom_endpoint is not None and (
        aws_access_key_id is None or aws_secret_access_key is None
    ):  # pragma: no cover
        warnings.warn(
            BodoWarning(
                "Reading from s3 with custom_endpoint, "
                "but environment variables AWS_ACCESS_KEY_ID or "
                "AWS_SECRET_ACCESS_KEY is not set."
            )
        )
    s3fs.core.S3FileSystem.clear_instance_cache()
    fs = s3fs.core.S3FileSystem(
        key=aws_access_key_id,
        secret=aws_secret_access_key,
        client_kwargs={"endpoint_url": custom_endpoint},
    )

    return fs


# hdfs related functions(hdfs_list_dir_fnames) should be included in
# coverage once hdfs tests are included in CI
def get_hdfs_fs(path):  # pragma: no cover
    """
    initialize pyarrow.hdfs.HadoopFileSystem from path
    This function can be removed once arrow's new HadoopFileSystem is a subclass
    of pyarrow.filesystem.FileSystem, and use the hdfs returned from 
    hdfs_list_dir_fnames.
    https://issues.apache.org/jira/browse/ARROW-7957
    """

    # this HadoopFileSystem is the deprecated file system of pyarrow
    # need this for pq.ParquetDataset
    # because the new HadoopFileSystem is not a subclass of
    # pyarrow.filesystem.FileSystem which causes an error
    from pyarrow.hdfs import HadoopFileSystem as HdFS

    options = urlparse(path)
    path = options.path
    # creates a new Hadoop file system from uri
    try:
        fs = HdFS(host=options.hostname, port=options.port, user=options.username)
    except Exception as e:
        raise BodoError("Hadoop file system cannot be created: {}".format(e))

    return fs


def s3_list_dir_fnames(fs, path):
    """
    If path is a directory, return all file names in the directory:
    ["s3://bucket-name/path/file_name", ...]
    If path is a file, return None
    """
    file_names = None
    try:
        # check if path is a directory, and if there is a zero-size object
        # with the name of the directory. If there is, we have to omit it
        # because pq.ParquetDataset will throw Invalid Parquet file size is 0
        # bytes
        path_info = fs.info(path)
        if (
            path_info["Size"] == 0 and path_info["type"] == "directory"
        ):  # pragma: no cover
            # excluded from coverage because haven't found a reliable way
            # to create 0 size object that is a directory. For example:
            # fs.mkdir(path)  sometimes doesn't do anything at all
            files = fs.ls(path)  # this is "s3://bucket/path-to-dir"
            if (
                files
                and (files[0] == path[5:] or files[0] == path[5:] + "/")
                and fs.info("s3://" + files[0])["Size"] == 0
            ):
                # get actual names of objects inside the dir
                file_names = ["s3://" + fname for fname in files[1:]]
            else:
                file_names = ["s3://" + fname for fname in files]

    except:  # pragma: no cover
        pass

    return file_names


def hdfs_list_dir_fnames(path):  # pragma: no cover
    """
    initialize pyarrow.fs.HadoopFileSystem from path
    If path is a directory, file_names = ["hfsd://host:port/path/file_name", ...]
    If path is a file, file_names = None
    return (pyarrow.fs.HadoopFileSystem, file_names)
    """

    # this HadoopFileSystem is the new file system of pyarrow
    from pyarrow.fs import HadoopFileSystem, FileSelector, FileType

    file_names = None
    options = urlparse(path)
    hdfs_path = options.path  # path within hdfs(i.e. dir/file)

    try:
        hdfs = HadoopFileSystem.from_uri(path)
    except Exception as e:
        raise BodoError(" Hadoop file system cannot be created: {}".format(e))
    # prefix in form of hdfs://host:port
    prefix = path[: len(path) - len(hdfs_path)]
    # target stat of the path: file or just the directory itself
    target_stat = hdfs.get_file_info([path])

    if target_stat[0].type in (FileType.NotFound, FileType.Unknown):
        raise BodoError("{} is a " "non-existing or unreachable file".format(path))

    if (not target_stat[0].size) and target_stat[0].type == FileType.Directory:
        file_selector = FileSelector(hdfs_path, recursive=True)
        try:
            file_stats = hdfs.get_file_info(file_selector)
        except Exception as e:
            raise BodoError(
                "Exception on getting directory info " "of {}: {}".format(hdfs_path, e)
            )
        file_names = [prefix + file_stat.path for file_stat in file_stats]

    return (hdfs, file_names)


def find_file_name_or_handler(path, ftype):
    """
    Find path_or_buf argument for pd.read_csv()/pd.read_json()

    If the path points to a single file:
        POSIX: file_name_or_handler = file name
        S3 & HDFS: file_name_or_handler = handler to the file
    If the path points to a directory:
        sort all non-empty files with the corresponding suffix
        POSIX: file_name_or_handler = file name of the first file in sorted files
        S3 & HDFS: file_name_or_handler = handler to the first file in sorted files

    Parameters: 
        path: path to the object we are reading, this can be a file or a directory
        ftype: 'csv' or 'json'
    Returns: 
        (is_handler, file_name_or_handler, f_size, fs)
        is_handler: True if file_name_or_handler is a handler,
                    False otherwise(file_name_or_handler is a file_name)
        file_name_or_handler: file_name or handler to pass to pd.read_csv()/pd.read_json()
        f_size: size of file_name_or_handler
        fs: file system for s3/hdfs
    """
    from urllib.parse import urlparse

    parsed_url = urlparse(path)
    fname = path
    fs = None
    func_name = "read_json" if ftype == "json" else "read_csv"
    err_msg = f"pd.{func_name}(): there is no {ftype} file in directory: {fname}"

    def filter_files(file_names):
        return [
            f
            for f in file_names
            if f.lower() != "_success" and not f.lower().endswith(".crc")
        ]

    if parsed_url.scheme == "s3":
        is_handler = True
        fs = get_s3_fs()
        all_files = s3_list_dir_fnames(fs, path)  # can return None if not dir
        f_size = fs.info(fname)["size"]

        if all_files:
            all_files = filter_files(all_files)
            all_csv_files = [f for f in sorted(all_files) if fs.info(f)["size"] > 0]
            if len(all_csv_files) == 0:  # pragma: no cover
                # TODO: test
                raise BodoError(err_msg)
            fname = all_csv_files[0]
            f_size = fs.info(fname)["size"]
            fname = fname[5:]  # strip off s3://

        file_name_or_handler = fs.open(fname, "rb")
    elif parsed_url.scheme == "hdfs":  # pragma: no cover
        is_handler = True
        (fs, all_files) = hdfs_list_dir_fnames(path)
        f_size = fs.get_file_info([fname])[0].size

        if all_files:
            all_files = filter_files(all_files)
            all_csv_files = [
                f for f in sorted(all_files) if fs.get_file_info([f])[0].size > 0
            ]
            if len(all_csv_files) == 0:  # pragma: no cover
                # TODO: test
                raise BodoError(err_msg)
            fname = all_csv_files[0]
            f_size = fs.get_file_info([fname])[0].size
            fname = urlparse(fname).path  # strip off hdfs://port:host/

        file_name_or_handler = fs.open_input_file(fname)
    else:
        assert parsed_url.scheme == ""
        is_handler = False

        if os.path.isdir(path):
            files = filter_files(glob.glob(os.path.join(path, "*")))
            all_csv_files = [f for f in sorted(files) if os.path.getsize(f) > 0]
            if len(all_csv_files) == 0:  # pragma: no cover
                # TODO: test
                raise BodoError(err_msg)
            fname = all_csv_files[0]

        f_size = os.path.getsize(fname)
        file_name_or_handler = fname

    # although fs is never used, we need to return it so that s3/hdfs
    # connections are not closed
    return is_handler, file_name_or_handler, f_size, fs


def get_s3_bucket_name(s3_fs, s3_filepath):
    """Get the name of the bucket from a s3 url of type s3://<BUCKET_NAME>/<FILEPATH (optional)>"""
    path_parts = s3_filepath.replace("s3://", "").split("/")
    bucket = path_parts[0]
    return bucket


def get_s3_bucket_region(s3_filepath):
    """Get the region of the s3 bucket from a s3 url of type s3://<BUCKET_NAME>/<FILEPATH>"""
    try:
        import s3fs
    except:  # pragma: no cover
        raise BodoError("Reading from s3 requires s3fs currently.")

    from mpi4py import MPI

    comm = MPI.COMM_WORLD

    bucket_loc = None
    if bodo.get_rank() == 0:
        s3_fs = get_s3_fs()
        bucket_name = get_s3_bucket_name(s3_fs, s3_filepath)
        try:
            bucket_loc = s3_fs.s3.get_bucket_location(Bucket=bucket_name)[
                "LocationConstraint"
            ]
            if bucket_loc is None:
                bucket_loc = "us-east-1"

        except Exception as e:
            print(
                f"BodoWarning: Unable to get S3 Bucket Region.\n{e}.\nWill use the value defined in the AWS_DEFAULT_REGION environment variable (or us-east-1 if that is not provided either)."
            )
            bucket_loc = ""

    bucket_loc = comm.bcast(bucket_loc)
    return bucket_loc


@numba.njit()
def get_s3_bucket_region_njit(s3_filepath):
    with numba.objmode(bucket_loc="unicode_type"):
        bucket_loc = ""
        if s3_filepath.startswith("s3://"):
            bucket_loc = get_s3_bucket_region(s3_filepath)
    return bucket_loc
