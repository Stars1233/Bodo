import pytest
from bodosql.tests.test_window.window_common import (  # noqa
    count_window_applies,
    uint8_window_df,
)
from bodosql.tests.utils import check_query


@pytest.mark.skip("TODO: currently defaults to unbounded window in some case")
@pytest.mark.parametrize(
    "query",
    [
        pytest.param(
            "SELECT W4, SUM(A) OVER (PARTITION BY W1 ORDER BY W4 ROWS 2 PRECEDING) FROM table1",
            id="sum-only_upper_bound-2_preceding",
        ),
        pytest.param(
            "SELECT W4, AVG(A) OVER (PARTITION BY W1 ORDER BY W4 ROWS 3 FOLLOWING) FROM table1",
            id="avg-only_upper_bound-3_following",
        ),
        pytest.param(
            "SELECT W4, COUNT(A) OVER (PARTITION BY W1 ORDER BY W4 ROWS CURRENT ROW) FROM table1",
            id="count-only_upper_bound-current_row",
        ),
    ],
)
def test_only_upper_bound(query, uint8_window_df, spark_info, memory_leak_check):
    """Tests when only the upper bound is provided to a window function call"""
    check_query(
        query,
        uint8_window_df,
        spark_info,
        sort_output=True,
        check_dtype=False,
        check_names=False,
        only_jit_1DVar=True,
    )


@pytest.mark.skip("TODO")
def test_empty_window(uint8_window_df, spark_info, memory_leak_check):
    """Tests when the clause inside the OVER term is empty"""
    query = "SELECT STDDEV(A) OVER () FROM table1"
    check_query(
        query,
        uint8_window_df,
        spark_info,
        sort_output=True,
        check_dtype=False,
        check_names=False,
        only_jit_1DVar=True,
    )


def test_window_no_order(uint8_window_df, spark_info, memory_leak_check):
    """Tests when the window clause does not have an order"""
    query = "SELECT W4, SUM(A) OVER (PARTITION BY W1) FROM table1"
    check_query(
        query,
        uint8_window_df,
        spark_info,
        sort_output=True,
        check_dtype=False,
        check_names=False,
        only_jit_1DVar=True,
    )


def test_window_no_rows(uint8_window_df, spark_info, memory_leak_check):
    """Tests when the window clause does not have a rows specification"""
    query = "SELECT W4, SUM(A) OVER (PARTITION BY W1 ORDER BY W4) FROM table1"
    check_query(
        query,
        uint8_window_df,
        spark_info,
        sort_output=True,
        check_dtype=False,
        check_names=False,
        only_jit_1DVar=True,
    )


def test_window_case(uint8_window_df, spark_info):
    """Tests windowed window function calls inside of CASE statements. The
       case_args is a list of lists of tuples with the following format:

    [
        [
            ("A", "B"),
            ("C", "D"),
            (None, "E")
        ],
        [
            ("F", "G"),
            (None, "I")
        ]
    ]

    Corresponds to the following query:

    SELECT
        W4,
        CASE
            WHEN A THEN B
            WHEN C THEN D
            ELSE E
        END,
        CASE
            WHEN F THEN G
            ELSE I
        END
    from table1
    """
    cases = []
    window1A = (
        "PARTITION BY W2 ORDER BY W4 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
    )
    window1B = "PARTITION BY W2 ORDER BY W4"
    window2 = (
        "PARTITION BY W1 ORDER BY W4 ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING"
    )
    case_args = [
        [
            (f"AVG(A) OVER ({window1A}) > 6.0", "'A+'"),
            (f"AVG(A) OVER ({window1A}) < 4.0", "'A-'"),
            (f"AVG(A) OVER ({window2}) > 6.0", "'B+'"),
            (f"AVG(A) OVER ({window2}) < 4.0", "'B-'"),
            (None, f"'C'"),
        ],
        [
            (f"A < 5", f"COUNT(A) OVER ({window2})"),
            (f"A >= 5", f"COUNT(A) OVER ({window1A})"),
            (None, f"LEAD(A, 3) OVER ({window1B})"),
        ],
    ]
    for case in case_args:
        new_case = ""
        for i, args in enumerate(case):
            if i == len(case) - 1:
                new_case += f"ELSE {args[1]}"
            else:
                new_case += f"WHEN {args[0]} THEN {args[1]} "
        cases.append(f"CASE {new_case} END")
    query = f"SELECT W4, {', '.join(cases)} FROM table1"
    pandas_code = check_query(
        query,
        uint8_window_df,
        spark_info,
        sort_output=True,
        check_dtype=False,
        check_names=False,
        only_jit_1DVar=True,
        return_codegen=True,
    )["pandas_code"]

    # TODO: enable checking window fusion once window function calls inside
    # of CASE statements can be fused [BE-3962]
    # count_window_applies(pandas_code, 2, ["AVG", "COUNT", "LEAD"])