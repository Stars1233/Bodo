#include <Python.h>
#include <cstddef>
#include <unordered_map>

#include <arrow/filesystem/filesystem.h>
#include <arrow/io/interfaces.h>
#include <arrow/python/filesystem.h>
#include <arrow/result.h>
#include <arrow/status.h>

#include "../libs/_bodo_common.h"

// Silence warnings from including generated code
PUSH_IGNORED_COMPILER_ERROR("-Wreturn-type-c-linkage")
PUSH_IGNORED_COMPILER_ERROR("-Wunused-variable")
PUSH_IGNORED_COMPILER_ERROR("-Wunused-function")
#include "pyfs.cpp"
POP_IGNORED_COMPILER_ERROR()

using std::string;

// if status of arrow::Result is not ok, form an err msg and raise a
// runtime_error with it.
#define CHECK_ARROW(expr, msg)                                            \
    if (!(expr.ok())) {                                                   \
        string err_msg = string("Error in arrow[fsspec]: ") + msg + " " + \
                         expr.ToString() + ".\n";                         \
        throw std::runtime_error(err_msg);                                \
    }

// if status of arrow::Result is not ok, form an err msg and raise a
// runtime_error with it. If it is ok, get value using ValueOrDie
// and assign it to lhs using std::move
#undef CHECK_ARROW_AND_ASSIGN
#define CHECK_ARROW_AND_ASSIGN(res, msg, lhs) \
    CHECK_ARROW(res.status(), msg)            \
    lhs = std::move(res).ValueOrDie();

// Initialize filesystems once, and reuse afterwards. Map the filesystem
// type to the fsspec pointer
std::unordered_map<string, PyObject *> pyfs{{"gcs", nullptr},
                                            {"http", nullptr}};

std::shared_ptr<arrow::py::fs::PyFileSystem> get_gcs_fs() {
    // GCSFS is a special case of fsspec, so it is handled separately

    // TODO: allow passing options to gcsfs like project, token, etc.
    // TODO are there regions to handle in GCS?
    // TODO: error checking for CPython API calls

    if (!pyfs["gcs"]) {
        // Python:
        //
        // import gcsfs
        // fs = gcsfs.GCSFileSystem(token='anon')
        // import pyarrow.fs
        // import bodo.io.pyfs
        // pyfs = bodo.io.pyfs.PyFileSystemBodo(pyarrow.fs.FSSpecHandler(fs))
        //
        // In C++ we get pointer to arrow::py::fs::PyFileSystem by calling
        // get_cpp_fs(pyfs) which we defined in pyfs.pyx

        // import gcsfs
        PyObject *gcsfs_mod = PyImport_ImportModule("gcsfs");
        // fs = gcsfs.GCSFileSystem(token=None)
        PyObject *GCSFileSystem =
            PyObject_GetAttrString(gcsfs_mod, "GCSFileSystem");
        Py_DECREF(gcsfs_mod);
        PyObject *args = PyTuple_New(0);  // no positional args
        PyObject *kwargs = Py_BuildValue("{s:s}", "token", NULL);
        PyObject *fs = PyObject_Call(GCSFileSystem, args, kwargs);
        Py_DECREF(args);
        Py_DECREF(kwargs);
        Py_DECREF(GCSFileSystem);

        // import pyarrow.fs
        PyObject *pyarrow_fs_mod = PyImport_ImportModule("pyarrow.fs");
        // handler = pyarrow.fs.FSSpecHandler(fs)
        PyObject *handler =
            PyObject_CallMethod(pyarrow_fs_mod, "FSSpecHandler", "O", fs);
        Py_DECREF(pyarrow_fs_mod);
        Py_DECREF(fs);

        // import bodo.io.pyfs
        PyObject *bodo_pyfs_mod = PyImport_ImportModule("bodo.io.pyfs");
        // pyfs = bodo.io.pyfs.PyFileSystemBodo(handler)
        pyfs["gcs"] = PyObject_CallMethod(bodo_pyfs_mod, "PyFileSystemBodo",
                                          "O", handler);
        Py_DECREF(bodo_pyfs_mod);
        Py_DECREF(handler);
    }
    std::shared_ptr<arrow::py::fs::PyFileSystem> pyfs_cpp =
        std::dynamic_pointer_cast<arrow::py::fs::PyFileSystem>(
            get_cpp_fs((c_PyFileSystemBodo *)pyfs["gcs"]));
    return pyfs_cpp;
}

void gcs_get_fs(std::shared_ptr<arrow::py::fs::PyFileSystem> *fs) {
    // Get the gcs filesystem
    try {
        *fs = get_gcs_fs();
    } catch (const std::exception &e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
    }
}

std::shared_ptr<arrow::py::fs::PyFileSystem> get_fsspec_fs(
    const std::string &protocol) {
    // Get the fsspec filesystem
    if (!pyfs[protocol]) {
        PyObject *fsspec = PyImport_ImportModule("fsspec");
        PyObject *fsspec_filesystem =
            PyObject_GetAttrString(fsspec, "filesystem");
        Py_DECREF(fsspec);
        PyObject *args = PyTuple_New(0);
        PyObject *kwargs = Py_BuildValue("{s:s}", "protocol", protocol.c_str());
        PyObject *fs = PyObject_Call(fsspec_filesystem, args, kwargs);
        Py_DECREF(args);
        Py_DECREF(kwargs);
        Py_DECREF(fsspec_filesystem);
        PyObject *pyarrow_fs = PyImport_ImportModule("pyarrow.fs");
        PyObject *handler =
            PyObject_CallMethod(pyarrow_fs, "FSSpecHandler", "O", fs);
        Py_DECREF(pyarrow_fs);
        Py_DECREF(fs);
        PyObject *bodo_pyfs = PyImport_ImportModule("bodo.io.pyfs");
        pyfs[protocol] =
            PyObject_CallMethod(bodo_pyfs, "PyFileSystemBodo", "O", handler);
        Py_DECREF(bodo_pyfs);
        Py_DECREF(handler);
    }
    std::shared_ptr<arrow::py::fs::PyFileSystem> pyfs_cpp =
        std::dynamic_pointer_cast<arrow::py::fs::PyFileSystem>(
            get_cpp_fs((c_PyFileSystemBodo *)pyfs[protocol]));
    return pyfs_cpp;
}

void fsspec_open_file(const std::string &fname, const std::string &protocol,
                      std::shared_ptr<::arrow::io::RandomAccessFile> *file) {
    std::shared_ptr<arrow::py::fs::PyFileSystem> fs;
    if ((protocol == "gcs") || (protocol == "gs")) {
        fs = get_gcs_fs();
    } else {
        fs = get_fsspec_fs(protocol);
    }
    arrow::Result<std::shared_ptr<::arrow::io::RandomAccessFile>> result;
    result = fs->OpenInputFile(fname);
    CHECK_ARROW_AND_ASSIGN(result, "fs->OpenInputFile", *file)
}

int32_t finalize_fsspec() {
    // Delete the filesystem objects
    for (auto &[_, fileptr] : pyfs) {
        if (fileptr) {
            Py_DECREF(fileptr);
            fileptr = nullptr;
        }
    }
    return 0;
}

/**
 * @brief Wrapper around finalize_fsspec() to be called from Python (avoids
 Numba JIT overhead and makes compiler debugging easier by eliminating extra
 compilation)
 *
 */
static PyObject *finalize_fsspec_py_wrapper(PyObject *self, PyObject *args) {
    if (PyTuple_Size(args) != 0) {
        PyErr_SetString(PyExc_TypeError,
                        "finalize_fsspec() does not take arguments");
        return nullptr;
    }
    PyObject *ret_obj = PyLong_FromLong(finalize_fsspec());
    return ret_obj;
}

static PyMethodDef ext_methods[] = {
#define declmethod(func) {#func, (PyCFunction)func, METH_VARARGS, NULL}
    declmethod(finalize_fsspec_py_wrapper),
    {nullptr},
#undef declmethod
};

PyMODINIT_FUNC PyInit_fsspec_reader(void) {
    PyObject *m;
    MOD_DEF(m, "fsspec_reader", "No docs", ext_methods);
    if (m == nullptr)
        return nullptr;

    return m;
}

#undef CHECK_ARROW
#undef CHECK_ARROW_AND_ASSIGN
