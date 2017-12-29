import unittest
import pandas as pd
import numpy as np
import hpat
from hpat.str_arr_ext import StringArray
from hpat.tests.test_utils import (count_array_REPs, count_parfor_REPs,
                            count_parfor_OneDs, count_array_OneDs, dist_IR_contains)

class TestHiFrames(unittest.TestCase):
    def test_basics(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.random.ranf(n)})
            Ac = df['A'].values
            return Ac.sum()

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)
        self.assertEqual(count_parfor_OneDs(), 1)

    def test_fillna(self):
        def test_impl():
            A = np.array([1., 2., 3.])
            A[0] = np.nan
            df = pd.DataFrame({'A': A})
            B = df.A.fillna(5.0)
            return B.sum()

        hpat_func = hpat.jit(test_impl)
        self.assertEqual(hpat_func(), test_impl())

    def test_fillna_inplace(self):
        def test_impl():
            A = np.array([1., 2., 3.])
            A[0] = np.nan
            df = pd.DataFrame({'A': A})
            df.A.fillna(5.0, inplace=True)
            return df.A.sum()

        hpat_func = hpat.jit(test_impl)
        self.assertEqual(hpat_func(), test_impl())

    def test_column_sum(self):
        def test_impl():
            A = np.array([1., 2., 3.])
            A[0] = np.nan
            df = pd.DataFrame({'A': A})
            return df.A.sum()

        hpat_func = hpat.jit(test_impl)
        self.assertEqual(hpat_func(), test_impl())

    def test_column_mean(self):
        def test_impl():
            A = np.array([1., 2., 3.])
            A[0] = np.nan
            df = pd.DataFrame({'A': A})
            return df.A.mean()

        hpat_func = hpat.jit(test_impl)
        self.assertEqual(hpat_func(), test_impl())

    def test_column_var(self):
        def test_impl():
            A = np.array([1., 2., 3.])
            A[0] = 4.0
            df = pd.DataFrame({'A': A})
            return df.A.var()

        hpat_func = hpat.jit(test_impl)
        np.testing.assert_almost_equal(hpat_func(), test_impl())

    def test_column_std(self):
        def test_impl():
            A = np.array([1., 2., 3.])
            A[0] = 4.0
            df = pd.DataFrame({'A': A})
            return df.A.std()

        hpat_func = hpat.jit(test_impl)
        np.testing.assert_almost_equal(hpat_func(), test_impl())

    def test_cumsum(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.random.ranf(n)})
            Ac = df.A.cumsum()
            return Ac.sum()

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_array_OneDs(), 2)
        self.assertEqual(count_parfor_REPs(), 0)
        self.assertEqual(count_parfor_OneDs(), 2)
        self.assertTrue(dist_IR_contains('dist_cumsum'))

    def test_column_distribution(self):
        # make sure all column calls are distributed
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.random.ranf(n)})
            df.A.fillna(5.0, inplace=True)
            DF = df.A.fillna(5.0)
            s = DF.sum()
            m = df.A.mean()
            v = df.A.var()
            t = df.A.std()
            Ac = df.A.cumsum()
            return Ac.sum() + s + m + v + t

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)
        self.assertTrue(dist_IR_contains('dist_cumsum'))

    def test_quantile_parallel(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.arange(0, n, 1, np.float64)})
            return df.A.quantile(.25)

        hpat_func = hpat.jit(test_impl)
        n = 1001
        np.testing.assert_almost_equal(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_quantile_sequential(self):
        def test_impl(A):
            df = pd.DataFrame({'A': A})
            return df.A.quantile(.25)

        hpat_func = hpat.jit(test_impl)
        n = 1001
        A = np.arange(0, n, 1, np.float64)
        np.testing.assert_almost_equal(hpat_func(A), test_impl(A))

    def test_describe(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.arange(0, n, 1, np.float64)})
            return df.A.describe()

        hpat_func = hpat.jit(test_impl)
        n = 1001
        hpat_func(n)
        # XXX: test actual output
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_str_contains_regex(self):
        def test_impl():
            A = StringArray(['ABC', 'BB', 'ADEF'])
            df = pd.DataFrame({'A': A})
            B = df.A.str.contains('AB*', regex=True)
            return B.sum()

        hpat_func = hpat.jit(test_impl)
        self.assertEqual(hpat_func(), 2)

    def test_str_contains_noregex(self):
        def test_impl():
            A = StringArray(['ABC', 'BB', 'ADEF'])
            df = pd.DataFrame({'A': A})
            B = df.A.str.contains('BB', regex=False)
            return B.sum()

        hpat_func = hpat.jit(test_impl)
        self.assertEqual(hpat_func(), 1)

    def test_filter1(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.ones(n)})
            df1 = df[df.A > .5]
            return np.sum(df1.B)

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_filter2(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.ones(n)})
            df1 = df.loc[df.A > .5]
            return np.sum(df1.B)

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_1D_Var_len(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.arange(n), 'B': np.arange(n)+1.0})
            df1 = df[df.A > 5]
            return len(df1.B)

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_rolling1(self):
        # size 3 without unroll
        def test_impl(n):
            df = pd.DataFrame({'A': np.arange(n), 'B': np.random.ranf(n)})
            Ac = df.A.rolling(3).sum()
            return Ac.sum()

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)
        # size 7 with unroll
        def test_impl(n):
            df = pd.DataFrame({'A': np.arange(n)+1.0, 'B': np.random.ranf(n)})
            Ac = df.A.rolling(7).sum()
            return Ac.sum()

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_rolling2(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.random.ranf(n)})
            df['moving average'] = df.A.rolling(window=5, center=True).mean()
            return df['moving average'].sum()

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        # small input array to mean is REP
        self.assertEqual(count_array_REPs(), 1)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_rolling3(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.random.ranf(n)})
            Ac = df.A.rolling(3, center=True).apply(lambda a: a[0]+2*a[1]+a[2])
            return Ac.sum()

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_shift1(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.random.ranf(n)})
            Ac = df.A.shift(1)
            return Ac.sum()

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_shift2(self):
        def test_impl(n):
            df = pd.DataFrame({'A': np.ones(n), 'B': np.random.ranf(n)})
            Ac = df.A.pct_change()
            return Ac.sum()

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_REPs(), 0)
        self.assertEqual(count_parfor_REPs(), 0)

    def test_list_convert(self):
        def test_impl():
            df = pd.DataFrame({'one': np.array([-1, np.nan, 2.5]),
                        'two': ['foo', 'bar', 'baz'],
                        'three': [True, False, True]})
            return df.one.values, df.two.values, df.three.values

        hpat_func = hpat.jit(test_impl)
        one, two, three = hpat_func()
        self.assertTrue(isinstance(one, np.ndarray))
        self.assertTrue(isinstance(two, list))
        self.assertTrue(isinstance(three, np.ndarray))

    def test_intraday(self):
        def test_impl(nsyms):
            max_num_days = 100
            all_res = 0.0
            for i in hpat.prange(nsyms):
                s_open = 20 * np.ones(max_num_days)
                s_low = 28 * np.ones(max_num_days)
                s_close = 19 * np.ones(max_num_days)
                df = pd.DataFrame({'Open': s_open, 'Low': s_low,
                                    'Close': s_close})
                df['Stdev'] = df['Close'].rolling(window=90).std()
                df['Moving Average'] = df['Close'].rolling(window=20).mean()
                df['Criteria1'] = (df['Open'] - df['Low'].shift(1)) < -df['Stdev']
                df['Criteria2'] = df['Open'] > df['Moving Average']
                df['BUY'] = df['Criteria1'] & df['Criteria2']
                df['Pct Change'] = (df['Close'] - df['Open']) / df['Open']
                df['Rets'] = df['Pct Change'][df['BUY'] == True]
                all_res += df['Rets'].mean()
            return all_res

        hpat_func = hpat.jit(test_impl)
        n = 11
        self.assertEqual(hpat_func(n), test_impl(n))
        self.assertEqual(count_array_OneDs(), 0)
        self.assertEqual(count_parfor_OneDs(), 1)

if __name__ == "__main__":
    unittest.main()
