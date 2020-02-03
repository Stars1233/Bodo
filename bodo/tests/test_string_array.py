# Copyright (C) 2019 Bodo Inc. All rights reserved.
import pandas as pd
import numpy as np
import pytest

import numba
import bodo
from bodo.tests.utils import check_func


@pytest.fixture(
    params=[
        # unicode
        pytest.param(
            pd.array([
                    "¿abc¡Y tú, quién te crees?",
                    "ÕÕÕú¡úú,úũ¿ééé",
                    "россия очень, холодная страна",
                    pd.NA,
                    "مرحبا, العالم ، هذا هو بودو",
                    "Γειά σου ,Κόσμε",
                    "Español es agra,dable escuchar",
                    "한국,가,고싶다ㅠ",
                    "🢇🄐,🏈𠆶💑😅",
                ],),
            marks=pytest.mark.slow,
        ),
        # ASCII array
        pd.array(["AB", "", "ABC", pd.NA, "abcd"]),
    ]
)
def str_arr_value(request):
    return request.param


def test_unbox(str_arr_value):
    # just unbox
    def impl(arr_arg):
        return True

    check_func(impl, (str_arr_value,))

    # unbox and box
    def impl2(arr_arg):
        return arr_arg

    check_func(impl2, (str_arr_value,))


def test_string_dtype():
    # unbox and box
    def impl(d):
        return d

    check_func(impl, (pd.StringDtype(),))

    # constructor
    def impl2():
        return pd.StringDtype()

    check_func(impl2, ())


def test_getitem_int(str_arr_value):
    def test_impl(A, i):
        return A[i]

    bodo_func = bodo.jit(test_impl)
    i = 1
    assert bodo_func(str_arr_value, i) == test_impl(str_arr_value, i)


def test_getitem_bool(str_arr_value):
    def test_impl(A, ind):
        return A[ind]

    bodo_func = bodo.jit(test_impl)
    np.random.seed(0)
    ind = np.random.ranf(len(str_arr_value)) < 0.2
    # TODO: parallel test
    pd.util.testing.assert_extension_array_equal(
        bodo_func(str_arr_value, ind), test_impl(str_arr_value, ind)
    )


def test_getitem_slice(str_arr_value):
    def test_impl(A, ind):
        return A[ind]

    bodo_func = bodo.jit(test_impl)
    ind = slice(1, 4)
    # TODO: parallel test
    pd.util.testing.assert_extension_array_equal(
        bodo_func(str_arr_value, ind), test_impl(str_arr_value, ind)
    )
