# Copyright (C) 2022 Bodo Inc. All rights reserved.
"""Test Bodo's array kernel utilities for BodoSQL hashing functions
"""

import datetime

import numpy as np
import pandas as pd
import pytest

import bodo
from bodo.tests.utils import check_func


@pytest.mark.parametrize(
    "args, distinct",
    [
        pytest.param(
            (
                pd.Series(
                    [
                        None if (i % 10000) == 77 else (i**3) % 2**20
                        for i in range(2**12)
                    ],
                    dtype=pd.Int32Dtype(),
                ),
            ),
            3987,
            id="int32",
        ),
        pytest.param(
            (
                pd.Series(
                    [
                        None if "0" in str(i) else str(int(str(i)[:3]) ** 5)
                        for i in range(2**12)
                    ]
                ),
                pd.Series(
                    [[True, False, None][i % 3] for i in range(2**12)],
                    dtype=pd.BooleanDtype(),
                ),
            ),
            1308,
            id="string-bool",
        ),
        pytest.param(
            (
                pd.Series(
                    [
                        None
                        if "9" in str(i)
                        else bytes(
                            str(int(str(i**2)[:3]) ** 5) * len(str(i**2)),
                            encoding="utf-8",
                        )
                        for i in range(2**12)
                    ]
                ),
            ),
            1479,
            id="binary",
        ),
        pytest.param(
            (
                pd.Series(
                    [
                        None if i % 10 == 0 else bodo.Time(minute=i**2)
                        for i in range(2**12)
                    ]
                ),
                pd.Series([np.round(np.tan(i), 0) + 0.5 for i in range(2**12)]),
                pd.Series(
                    [
                        datetime.date.fromordinal(736695 + (i % 60) ** 2)
                        for i in range(2**12)
                    ]
                ),
            ),
            2359,
            id="time-float-date",
        ),
        pytest.param(
            (
                pd.Series(
                    [
                        pd.Timestamp("2020") + pd.Timedelta(days=(i % 1000))
                        for i in range(2**12)
                    ]
                ),
                pd.Series(
                    [
                        pd.Timestamp("2020", tz="US/Pacific")
                        + pd.Timedelta(hours=(2 ** (i % 18)))
                        for i in range(2**12)
                    ]
                ),
                None,
            ),
            4096,
            id="naive-timezone-null",
        ),
        pytest.param(
            (
                42,
                pd.Series([str(i**2)[-3:] for i in range(2**12)]),
                "foo",
                None,
                pd.Series(
                    [int(i**0.5) for i in range(2**12)], dtype=pd.Int32Dtype()
                ),
                pd.Series(
                    [[b"foo", b"bar", b"fizz", b"buzz"][i % 4] for i in range(2**12)]
                ),
                datetime.date(2020, 7, 3),
            ),
            3564,
            id="mixed",
        ),
    ],
)
def test_sql_hash_qualities(args, distinct, memory_leak_check):
    """
    Tests the quality of the sql HASH kernel by verifying that the number of
    distinct hashes matches the number of distinct inputs and that the hashes
    are arbitrarily distributed across the domain of int64. It checks this by
    verifying that each of of the 64 bits is set 50% of the time, and each pair
    of bits has both bits set 25% of the time. If this is true, it means that
    each bit is effectively an independent random bernouli variable with p=0.5.

    All of these requirements are reasonable expectations for an array of inputs
    that is not trivially small yet not big enough that there is a reasonable
    chance of a hash collision in the domain of int64.

    Note: the various types are tested together so that these tests can explore
    the variadic nature of the HASH function.
    """

    n_args = len(args)
    args_str = ", ".join([f"A{i}" for i in range(n_args)])
    params_str = args_str
    if n_args == 1:
        args_str += ","

    # Returns a series where the first 64 entries are the ratio of times where
    # that bit was set to one, and the remainder each correspond to the ratio
    # of times that a specific combination of bits are both set. If the hash
    # function is working correctly, the first 64 values should be approximately
    # 0.5 and the rest should be approximately 0.25. Also returns the number
    # of distinct hash values. Uses abs() so that the highest order bit does
    # not mess with the sign of the ratios.
    test_impl = f"def impl({params_str}):\n"
    test_impl += (
        f"  H = pd.Series(bodo.libs.bodosql_array_kernels.sql_hash(({args_str})))\n"
    )
    test_impl += f"  distinct_hashes = pd.Series(H.unique())\n"
    test_impl += f"  masks = []\n"
    test_impl += f"  L = []\n"
    test_impl += f"  for i in range(64):\n"
    test_impl += f"    mask = pd.Series((distinct_hashes.values & (1 << i)) >> i)\n"
    test_impl += f"    masks.append(mask)\n"
    test_impl += f"    L.append(abs(mask.mean()))\n"
    test_impl += f"  for i in range(64):\n"
    test_impl += f"    for j in range(i+1, 64):\n"
    test_impl += f"      mask_both = pd.Series(masks[i] & masks[j])\n"
    test_impl += f"      L.append(abs(mask_both.mean()))\n"
    test_impl += f"  return len(distinct_hashes), pd.Series(L)\n"
    impl_vars = {}
    exec(test_impl, {"bodo": bodo, "pd": pd}, impl_vars)
    impl = impl_vars["impl"]

    # 2016 = number of combinations of i & j
    expected_bits = pd.Series([0.5] * 64 + [0.25] * 2016)

    check_func(
        impl,
        args,
        py_output=(distinct, expected_bits),
        check_dtype=False,
        is_out_distributed=False,
        atol=0.05,
    )


@pytest.mark.parametrize(
    "args, expected_hash",
    [
        pytest.param((10, None, True), -2243023243364725697, id="int-null-bool"),
        pytest.param((np.float32(3.1415),), -9176520268571280804, id="float"),
        pytest.param((np.int8(100),), 2279747396317938166, id="int8"),
        pytest.param((np.uint8(100),), 2279747396317938166, id="uint8"),
        pytest.param((np.int32(100),), 2279747396317938166, id="int32"),
        pytest.param(
            (datetime.date(1999, 12, 31), bodo.Time(12, 30, 0)),
            5066071753504198015,
            id="date-time",
        ),
        pytest.param(
            (
                pd.Timestamp("2023-7-4 6:00:00"),
                pd.Timestamp("2020-4-1", tz="US/Pacific"),
            ),
            -8057843889034702324,
            id="timestamps_baseline",
        ),
        pytest.param(
            (
                pd.Timestamp("2023-7-4 8:00:00", tz="Europe/Berlin"),
                pd.Timestamp("2020-4-1 3:00:00", tz="US/Eastern"),
            ),
            -8057843889034702324,
            id="timestamps_equivalent",
        ),
        pytest.param(("theta",), 7137812097207502893, id="string"),
        pytest.param((b"theta",), -4192600820579827718, id="binary"),
    ],
)
def test_sql_hash_determinism(args, expected_hash, memory_leak_check):
    """
    Verifies that the sql_hash kernel returns the same outputs for the same
    combination of inputs every time, including for equivalent values
    of different types"
    """
    n_args = len(args)
    args_str = ", ".join([f"A{i}" for i in range(n_args)])
    params_str = args_str
    if n_args == 1:
        args_str += ","

    test_impl = f"def impl({params_str}):\n"
    test_impl += f"  return bodo.libs.bodosql_array_kernels.sql_hash(({args_str}))"
    impl_vars = {}
    exec(test_impl, {"bodo": bodo}, impl_vars)
    impl = impl_vars["impl"]
    check_func(impl, args, py_output=expected_hash)