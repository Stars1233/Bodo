General functions {#general}
=================

## Data manipulations

#### `pd.pivot`


- <code><apihead>pandas.<apiname>pivot</apiname>(data, values=None, index=None, columns=None)</apihead></code>
<br><br>

    ***Supported Arguments***

    +-----------------------------+-----------------------------------------+
    | argument                    | datatypes                               |
    +=============================+=========================================+
    | `data`                      | -   DataFrame                           |
    +-----------------------------+-----------------------------------------+
    | `values`                    | -   Constant Column Label or list of    |
    |                             |     labels                              |
    +-----------------------------+-----------------------------------------+
    | `index`                     | -   Constant Column Label or list of    |
    |                             |     labels                              |
    +-----------------------------+-----------------------------------------+
    | `columns`                   | -   Constant Column Label               |
    +-----------------------------+-----------------------------------------+

    !!! note
        The the number of columns and names of the output DataFrame won't be known
        at compile time. To update typing information on DataFrame you should pass it back to Python.


    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f():
    ...   df = pd.DataFrame({"A": ["X","X","X","X","Y","Y"], "B": [1,2,3,4,5,6], "C": [10,11,12,20,21,22]})
    ...   pivoted_tbl = pd.pivot(data, columns="A", index="B", values="C")
    ...   return pivoted_tbl
    >>> f()
    A     X     Y
    B
    1  10.0   NaN
    2  11.0   NaN
    3  12.0   NaN
    4  20.0   NaN
    5   NaN  21.0
    6   NaN  22.0
    ```

#### `pd.pivot_table`



- <code><apihead>pandas.<apiname>pivot_table</apiname>(data, values=None, index=None, columns=None, aggfunc='mean', fill_value=None, margins=False, dropna=True, margins_name='All', observed=False, sort=True)</apihead></code>
<br><br>

    ***Supported Arguments***

    +-----------------------------+-----------------------------------------+
    | argument                    | datatypes                               |
    +=============================+=========================================+
    | `data`                      | -   DataFrame                           |
    +-----------------------------+-----------------------------------------+
    | `values`                    | -   Constant Column Label or list of    |
    |                             |     labels                              |
    +-----------------------------+-----------------------------------------+
    | `index`                     | -   Constant Column Label or list of    |
    |                             |     labels                              |
    +-----------------------------+-----------------------------------------+
    | `columns`                   | -   Constant Column Label               |
    +-----------------------------+-----------------------------------------+
    | `aggfunc`                   | -   String Constant                     |
    +-----------------------------+-----------------------------------------+


    !!! note
        This code takes two different paths depending on if pivot values are annotated. When
        pivot values are annotated then output columns are set to the annotated values.
        For example, `@bodo.jit(pivots={'pt': ['small', 'large']})`
        declares the output pivot table `pt` will have columns called `small` and `large`.

        If pivot values are not annotated, then the number of columns and names of the output DataFrame won't be known
        at compile time. To update typing information on DataFrame you should pass it back to Python.


    ***Example Usage***

    ```py

    >>> @bodo.jit(pivots={'pivoted_tbl': ['X', 'Y']})
    ... def f():
    ...   df = pd.DataFrame({"A": ["X","X","X","X","Y","Y"], "B": [1,2,3,4,5,6], "C": [10,11,12,20,21,22]})
    ...   pivoted_tbl = pd.pivot_table(df, columns="A", index="B", values="C", aggfunc="mean")
    ...   return pivoted_tbl
    >>> f()
          X     Y
    B
    1  10.0   NaN
    2  11.0   NaN
    3  12.0   NaN
    4  20.0   NaN
    5   NaN  21.0
    6   NaN  22.0
    ```

#### `pd.crosstab`


- <code><apihead>pandas.<apiname>crosstab</apiname>(index, columns, values=None, rownames=None, colnames=None, aggfunc=None, margins=False, margins_name='All', dropna=True, normalize=False)</apihead></code>
<br><br>
    ***Supported Arguments***

     +---------------------+--------------------+
     |argument             |        datatypes   |
     +=====================+====================+
     |`index`              |        SeriesType  |
     +---------------------+--------------------+
     |`columns`            |        SeriesType  |
     +---------------------+--------------------+

    !!! note

        Annotation of pivot values is required. For example,
        `@bodo.jit(pivots={'pt': ['small', 'large']})` declares
        the output table `pt` will have columns called `small` and `large`.

    ***Example Usage***

    ```py

    >>> @bodo.jit(pivots={"pt": ["small", "large"]})
    ... def f(df):
    ...   pt = pd.crosstab(df.A, df.C)
    ...   return pt

    >>> list_A = ["foo", "foo", "bar", "bar", "bar", "bar"]
    >>> list_C = ["small", "small", "large", "small", "small", "middle"]
    >>> df = pd.DataFrame({"A": list_A, "C": list_C})
    >>> f(df)

           small  large
    index
    foo        2      0
    bar        2      1
    ```

#### `pd.cut`



- <code><apihead>pandas.<apiname>cut</apiname>(x, bins, right=True, labels=None, retbins=False, precision=3, include_lowest=False, duplicates="raise", ordered=True)</apihead></code>
<br><br>
    ***Supported Arguments***

    +-------------------------+--------------------------+
    |argument                 |    datatypes             |
    +=========================+==========================+
    |`x`                      |    Series or Array like  |
    +-------------------------+--------------------------+
    |`bins`                   |    Integer or Array like |
    +-------------------------+--------------------------+
    |`include_lowest`         |    Boolean               |
    +-------------------------+--------------------------+

    ***Example Usage***

    ```py

     >>> @bodo.jit
     ... def f(S):
     ...   bins = 4
     ...   include_lowest = True
     ...   return pd.cut(S, bins, include_lowest=include_lowest)

     >>> S = pd.Series(
     ...    [-2, 1, 3, 4, 5, 11, 15, 20, 22],
     ...    ["a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9"],
     ...    name="ABC",
     ... )
     >>> f(S)

    a1    (-2.025, 4.0]
    a2    (-2.025, 4.0]
    a3    (-2.025, 4.0]
    a4    (-2.025, 4.0]
    a5      (4.0, 10.0]
    a6     (10.0, 16.0]
    a7     (10.0, 16.0]
    a8     (16.0, 22.0]
    a9     (16.0, 22.0]
    Name: ABC, dtype: category
    Categories (4, interval[float64, right]): [(-2.025, 4.0] < (4.0, 10.0] < (10.0, 16.0] < (16.0, 22.0]]
    ```

#### `pd.qcut`



- <code><apihead>pandas.<apiname>qcut</apiname>(x, q, labels=None, retbins=False, precision=3, duplicates="raise")</apihead></code>
<br><br>
    ***Supported Arguments***

    +---------------------------+-----------------------------------+
    |argument                   |  datatypes                        |
    +===========================+===================================+
    |`x`                        |  Series or Array like             |
    +---------------------------+-----------------------------------+
    |`q`                        |  Integer or Array like of floats  |
    +---------------------------+-----------------------------------+

    ***Example Usage***

    ```py

     >>> @bodo.jit
     ... def f(S):
     ...   q = 4
     ...   return pd.qcut(S, q)

     >>> S = pd.Series(
     ...      [-2, 1, 3, 4, 5, 11, 15, 20, 22],
     ...      ["a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9"],
     ...      name="ABC",
     ... )
     >>> f(S)

     a1    (-2.001, 3.0]
     a2    (-2.001, 3.0]
     a3    (-2.001, 3.0]
     a4       (3.0, 5.0]
     a5       (3.0, 5.0]
     a6      (5.0, 15.0]
     a7      (5.0, 15.0]
     a8     (15.0, 22.0]
     a9     (15.0, 22.0]
     Name: ABC, dtype: category
     Categories (4, interval[float64, right]): [(-2.001, 3.0] < (3.0, 5.0] < (5.0, 15.0] < (15.0, 22.0]]
    ```

#### `pd.merge`



- <code><apihead>pandas.<apiname>merge</apiname>(left, right, how="inner", on=None, left_on=None, right_on=None, left_index=False, right_index=False, sort=False, suffixes=("_x", "_y"), copy=True, indicator=False, validate=None, _bodo_na_equal=True)</apihead></code>
<br><br>

    ***Supported Arguments***

    +--------------------+--------------------+----------------------------+
    | argument           | datatypes          | other requirements         |
    +====================+====================+============================+
    | `left`             | DataFrame          |                            |
    +--------------------+--------------------+----------------------------+
    | `right`            | DataFrame          |                            |
    +--------------------+--------------------+----------------------------+
    | `how`              | String             | -   Must be one of         |
    |                    |                    |     `"inner"`, `"outer"`,  |
    |                    |                    |     `"left"`, `"right"`    |
    |                    |                    | -   **Must be constant at  |
    |                    |                    |     Compile Time**         |
    +--------------------+--------------------+----------------------------+
    | `on`               | Column Name, List  | -   **Must be constant at  |
    |                    | of Column Names,   |     Compile Time**         |
    |                    | or General Merge   |                            |
    |                    | Condition String   |                            |
    |                    | (see               |                            |
    |                    | [merge-notes][])   |                            |
    +--------------------+--------------------+----------------------------+
    | `left_on`          | Column Name or     | -   **Must be constant at  |
    |                    | List of Column     |     Compile Time**         |
    |                    | Names              |                            |
    +--------------------+--------------------+----------------------------+
    | `right_on`         | Column Name or     | -   **Must be constant at  |
    |                    | List of Column     |     Compile Time**         |
    |                    | Names              |                            |
    +--------------------+--------------------+----------------------------+
    | `left_index`       | Boolean            | -   **Must be constant at  |
    |                    |                    |     Compile Time**         |
    +--------------------+--------------------+----------------------------+
    | `right_index`      | Boolean            | -   **Must be constant at  |
    |                    |                    |     Compile Time**         |
    +--------------------+--------------------+----------------------------+
    | `suffixes`         | Tuple of Strings   | -   **Must be constant at  |
    |                    |                    |     Compile Time**         |
    +--------------------+--------------------+----------------------------+
    | `indicator`        | Boolean            | -   **Must be constant at  |
    |                    |                    |     Compile Time**         |
    +--------------------+--------------------+----------------------------+
    | `_bodo_na_equal`   | Boolean            | -   **Must be constant at  |
    |                    |                    |     Compile Time**         |
    |                    |                    | -   This argument is       |
    |                    |                    |     unique to Bodo and not |
    |                    |                    |     available in Pandas.   |
    |                    |                    |     If False, Bodo won't   |
    |                    |                    |     consider NA/nan keys   |
    |                    |                    |     as equal, which        |
    |                    |                    |     differs from Pandas.   |
    +--------------------+--------------------+----------------------------+

    !!! important
        The argument `_bodo_na_equal` is unique to Bodo and not available in Pandas. If it is `False`, Bodo won't consider NA/nan keys as equal, which differs from Pandas.


##### Merge Notes


-   *Output Ordering*:

    The output dataframe is not sorted by default for better parallel performance
    (Pandas may preserve key order depending on `how`).
    One can use explicit sort if needed.

-   *General Merge Conditions*:

    Within Pandas, the merge criteria supported by `pd.merge` are limited to equality between 1
    or more pairs of keys. For some use cases, this is not sufficient and more generalized
    support is necessary. For example, with these limitations, a `left outer join` where
    `df1.A == df2.B & df2.C < df1.A` cannot be efficiently computed.

    Bodo supports these use cases by allowing users to pass general merge conditions to `pd.merge`.
    We plan to contribute this feature to Pandas to ensure full compatibility of Bodo and Pandas code.

    General merge conditions are performed by providing the condition as a string via the `on` argument. Columns in the left table
    are referred to by `left.{column name}` and columns in the right table are referred to by `right.{column name}`.


    Here's an example demonstrating the above:

    ```py

    >>> @bodo.jit
    ... def general_merge(df1, df2):
    ...   return df1.merge(df2, on="left.`A` == right.`B` & right.`C` < left.`A`", how="left")

    >>> df1 = pd.DataFrame({"col": [2, 3, 5, 1, 2, 8], "A": [4, 6, 3, 9, 9, -1]})
    >>> df2 = pd.DataFrame({"B": [1, 2, 9, 3, 2], "C": [1, 7, 2, 6, 5]})
    >>> general_merge(df1, df2)

       col  A     B     C
    0    2  4  <NA>  <NA>
    1    3  6  <NA>  <NA>
    2    5  3  <NA>  <NA>
    3    1  9     9     2
    4    2  9     9     2
    5    8 -1  <NA>  <NA>
    ```

    These calls have a few additional requirements:

    * The condition must be constant string.
    * The condition must be of the form `cond_1 & ... & cond_N` where at least one `cond_i`
      is a simple equality. This restriction will be removed in a future release.
    * The columns specified in these conditions are limited to certain column types.
      We currently support `boolean`, `integer`, `float`, `datetime64`, `timedelta64`, `datetime.date`,
      and `string` columns.

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(df1, df2):
    ...   return pd.merge(df1, df2, how="inner", on="key")

    >>> df1 = pd.DataFrame({"key": [2, 3, 5, 1, 2, 8], "A": np.array([4, 6, 3, 9, 9, -1], float)})
    >>> df2 = pd.DataFrame({"key": [1, 2, 9, 3, 2], "B": np.array([1, 7, 2, 6, 5], float)})
    >>> f(df1, df2)

    key    A    B
    0    2  4.0  7.0
    1    2  4.0  5.0
    2    3  6.0  6.0
    3    1  9.0  1.0
    4    2  9.0  7.0
    5    2  9.0  5.0
    ```


#### `pd.concat`



- <code><apihead>pandas.<apiname>concat</apiname>(objs, axis=0, join="outer", join_axes=None, ignore_index=False, keys=None, levels=None, names=None, verify_integrity=False, sort=None, copy=True)</apihead></code>
<br><br>
    ***Supported Arguments***

    +--------------------+--------------------+----------------------------+
    | argument           | datatypes          | other requirements         |
    +====================+====================+============================+
    | `objs`             | List or Tuple of   |                            |
    |                    | DataFrames/Series  |                            |
    +--------------------+--------------------+----------------------------+
    | `axis`             | Integer with       | -   **Must be constant at  |
    |                    | either 0 or 1      |     Compile Time**         |
    +--------------------+--------------------+----------------------------+
    | `ignore_index`     | Boolean            | -   **Must be constant at  |
    |                    |                    |     Compile Time**         |
    +--------------------+--------------------+----------------------------+

    !!! important
        Bodo currently concatenates local data chunks for distributed datasets, which does not preserve global order of concatenated objects in output.

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(df1, df2):
    ...     return pd.concat([df1, df2], axis=1)

    >>> df1 = pd.DataFrame({"A": [3, 2, 1, -4, 7]})
    >>> df2 = pd.DataFrame({"B": [3, 25, 1, -4, -24]})
    >>> f(df1, df2)

    A   B
    0  3   3
    1  2  25
    2  1   1
    3 -4  -4
    4  7 -24
    ```

#### `pd.get_dummies`



- <code><apihead>pandas.<apiname>get_dummies</apiname>(data, prefix=None, prefix_sep="_", dummy_na=False, columns=None, sparse=False, drop_first=False, dtype=None)</apihead></code>
<br><br>
    ***Supported Arguments***

    +---------------------+---------------------+--------------------------+
    | argument            | datatypes           | other requirements       |
    +=====================+=====================+==========================+
    | `data`              | Array or Series     | -   **Categories must be |
    |                     | with Categorical    |     known at compile     |
    |                     | dtypes              |     time.**              |
    +---------------------+---------------------+--------------------------+

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(S):
    ...     return pd.get_dummies(S)

    >>> S = pd.Series(["CC", "AA", "B", "D", "AA", None, "B", "CC"]).astype("category")
    >>> f(S)

    AA  B  CC  D
    0   0  0   1  0
    1   1  0   0  0
    2   0  1   0  0
    3   0  0   0  1
    4   1  0   0  0
    5   0  0   0  0
    6   0  1   0  0
    7   0  0   1  0
    ```

#### `pd.unique`



- <code><apihead>pandas.<apiname>unique</apiname>(values)</apihead></code>
<br><br>
    ***Supported Arguments***

    +---------------------+---------------------+
    | argument            | datatypes           |
    +=====================+=====================+
    | `values`            | Series or 1-d array |
    |                     | with Categorical    |
    |                     | dtypes              |
    +---------------------+---------------------+

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(S):
    ...     return pd.unique(S)

    >>> S = pd.Series([1, 2, 1, 3, 2, 1])
    >>> f(S)
    array([1, 2, 3])
    ```

## Top-level missing data


#### `pd.isna`



- <code><apihead>pandas.<apiname>isna</apiname>(obj)</apihead></code>
<br><br>
    ***Supported Arguments***

    +-----------------------------------+-----------------------------------+
    |argument                           |datatypes                          |
    +===================================+===================================+
    |`obj`                              |DataFrame, Series, Index, Array, or|
    |                                   |Scalar                             |
    |                                   |                                   |
    +-----------------------------------+-----------------------------------+

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(df):
    ...     return pd.isna(df)

    >>> df = pd.DataFrame(
    ...    {"A": ["AA", np.nan, "", "D", "GG"], "B": [1, 8, 4, -1, 2]},
    ...    [1.1, -2.1, 7.1, 0.1, 3.1],
    ... )
    >>> f(df)

           A      B
    1.1  False  False
    -2.1   True  False
    7.1  False  False
    0.1  False  False
    3.1  False  False
    ```

#### `pd.isnull`



- <code><apihead>pandas.<apiname>isnull</apiname>(obj)</apihead></code>
<br><br>

    ***Supported Arguments***

    +-----------------------------------+-----------------------------------+
    |argument                           |datatypes                          |
    +===================================+===================================+
    |`obj`                              |DataFrame, Series, Index, Array, or|
    |                                   |Scalar                             |
    |                                   |                                   |
    +-----------------------------------+-----------------------------------+

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(df):
    ...     return pd.isnull(df)

    >>> df = pd.DataFrame(
    ...    {"A": ["AA", np.nan, "", "D", "GG"], "B": [1, 8, 4, -1, 2]},
    ...    [1.1, -2.1, 7.1, 0.1, 3.1],
    ... )
    >>> f(df)

           A      B
    1.1  False  False
    -2.1   True  False
    7.1  False  False
    0.1  False  False
    3.1  False  False
    ```

#### `pd.notna`



- <code><apihead>pandas.<apiname>notna</apiname>(obj)</apihead></code>
<br><br>
    ***Supported Arguments***

    +-----------------------------------+-----------------------------------+
    |argument                           |datatypes                          |
    +===================================+===================================+
    |`obj`                              |DataFrame, Series, Index, Array, or|
    |                                   |Scalar                             |
    |                                   |                                   |
    +-----------------------------------+-----------------------------------+

    ***Example Usage***

    ```py

     >>> @bodo.jit
     ... def f(df):
     ...     return pd.notna(df)

     >>> df = pd.DataFrame(
     ...    {"A": ["AA", np.nan, "", "D", "GG"], "B": [1, 8, 4, -1, 2]},
     ...    [1.1, -2.1, 7.1, 0.1, 3.1],
     ... )
     >>> f(df)

               A     B
      1.1   True  True
     -2.1  False  True
      7.1   True  True
      0.1   True  True
      3.1   True  True
    ```

#### `pd.notnull`



- <code><apihead>pandas.<apiname>notnull</apiname>(obj)</apihead></code>
<br><br>
    ***Supported Arguments***

    +-----------------------------------+-----------------------------------+
    |argument                           |datatypes                          |
    +===================================+===================================+
    |`obj`                              |DataFrame, Series, Index, Array, or|
    |                                   |Scalar                             |
    |                                   |                                   |
    +-----------------------------------+-----------------------------------+

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(df):
    ...     return pd.notnull(df)

    >>> df = pd.DataFrame(
    ...    {"A": ["AA", np.nan, "", "D", "GG"], "B": [1, 8, 4, -1, 2]},
    ...    [1.1, -2.1, 7.1, 0.1, 3.1],
    ... )
    >>> f(df)

           A     B
    1.1   True  True
    -2.1  False  True
    7.1   True  True
    0.1   True  True
    3.1   True  True

    ```

## Top-level conversions


#### `pd.to_numeric`



- <code><apihead>pandas.<apiname>to_numeric</apiname>(arg, errors="raise", downcast=None)</apihead></code>
<br><br>
    ***Supported Arguments***

    +--------------------+--------------------+----------------------------+
    | argument           | datatypes          | other requirements         |
    +====================+====================+============================+
    | `arg`              | Series or Array    |                            |
    +--------------------+--------------------+----------------------------+
    | `downcast`         | String and one of  | -   **Must be constant at  |
    |                    | (`'integer'`,      |     Compile Time**         |
    |                    | `'signed'`,        |                            |
    |                    | `'unsigned'`,      |                            |
    |                    | `'float'`)         |                            |
    +--------------------+--------------------+----------------------------+

    !!! note

        * Output type is float64 by default
        * Unlike Pandas, Bodo does not dynamically determine output type,
          and does not downcast to the smallest numerical type.
        * `downcast` parameter should be used for type annotation of output.

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(S):
    ...     return pd.to_numeric(S, errors="coerce", downcast="integer")

    >>> S = pd.Series(["1", "3", "12", "4", None, "-555"])
    >>> f(S)

    0       1
    1       3
    2      12
    3       4
    4    <NA>
    5    -555
    dtype: Int64
    ```

## Top-level dealing with datetime and timedelta like

#### `pd.to_datetime`



- <code><apihead>pandas.<apiname>to_datetime</apiname>(arg, errors='raise', dayfirst=False, yearfirst=False, utc=None, format=None, exact=True, unit=None, infer_datetime_format=False, origin='unix', cache=True)</apihead></code>
<br><br>
    ***Supported Arguments***

    +--------------------+--------------------+----------------------------+
    | argument           | datatypes          | other requirements         |
    +====================+====================+============================+
    | `arg`              | Series, Array or   |                            |
    |                    | scalar of integers |                            |
    |                    | or strings         |                            |
    +--------------------+--------------------+----------------------------+
    | `errors`           | String and one of  |                            |
    |                    | ('ignore',         |                            |
    |                    | 'raise',           |                            |
    |                    | 'coerce')          |                            |
    +--------------------+--------------------+----------------------------+
    | `dayfirst`         | Boolean            |                            |
    +--------------------+--------------------+----------------------------+
    | `yearfirst`        | Boolean            |                            |
    +--------------------+--------------------+----------------------------+
    | `utc`              | Boolean            |                            |
    +--------------------+--------------------+----------------------------+
    | `format`           | String matching    |                            |
    |                    | Pandas             |                            |
    |                    | [strftime          |                            |
    |                    | /strptime](https:/ |                            |
    |                    | /docs.python.org/3 |                            |
    |                    | /library/datetime. |                            |
    |                    | html#strftime-and- |                            |
    |                    | strptime-behavior) |                            |
    +--------------------+--------------------+----------------------------+
    | `exact`            | Boolean            |                            |
    +--------------------+--------------------+----------------------------+
    | `unit`             | String             | -   Must be a [valid       |
    |                    |                    |     Pandas timedelta       |
    |                    |                    |     unit](https://pandas.py|
    |                    |                    |     data.org/pandas-docs/st|
    |                    |                    |     able/user_guide/timeser|
    |                    |                    |     ies.html#timeseries-off|
    |                    |                    |     set-aliases)           |
    +--------------------+--------------------+----------------------------+
    | `infer             | Boolean            |                            |
    |  _datetime_format` |                    |                            |
    +--------------------+--------------------+----------------------------+
    | `origin`           | Scalar string or   |                            |
    |                    | timestamp value    |                            |
    +--------------------+--------------------+----------------------------+
    | `cache`            | Boolean            |                            |
    +--------------------+--------------------+----------------------------+

    !!! note

        * The function is not optimized.
        * Bodo doesn't support Timezone-Aware datetime values

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(val):
    ...     return pd.to_datetime(val, format="%Y-%d-%m")

    >>> val = "2016-01-06"
    >>> f(val)

    Timestamp('2016-06-01 00:00:00')
    ```

#### `pd.to_timedelta`



- <code><apihead>pandas.<apiname>to_timedelta</apiname>(arg, unit=None, errors='raise')</apihead></code>
<br><br>
    ***Supported Arguments***

    +--------------------+--------------------+----------------------------+
    | argument           | datatypes          | other requirements         |
    +====================+====================+============================+
    | `arg`              | Series, Array or   |                            |
    |                    | scalar of integers |                            |
    |                    | or strings         |                            |
    +--------------------+--------------------+----------------------------+
    | `unit`             | String             | -   Must be a valid Pandas |
    |                    |                    |     [timedelta unit](https:|
    |                    |                    |     //pandas.pydata.org/pan|
    |                    |                    |     das-docs/stable/user_gu|
    |                    |                    |     ide/timeseries.html#tim|
    |                    |                    |     eseries-offset-aliases)|
    +--------------------+--------------------+----------------------------+

    !!! note
        Passing string data as `arg` is not optimized.

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f(S):
    ...     return pd.to_timedelta(S, unit="D")

    >>> S = pd.Series([1.0, 2.2, np.nan, 4.2], [3, 1, 0, -2], name="AA")
    >>> f(val)

    3   1 days 00:00:00
    1   2 days 04:48:00
    0               NaT
    -2   4 days 04:48:00
    Name: AA, dtype: timedelta64[ns]
    ```

#### `pd.date_range`

- <code><apihead>pandas.<apiname>date_range</apiname>(start=None, end=None, periods=None, freq=None, tz=None, normalize=False, name=None, closed=None, **kwargs)</apihead></code>

    ***Supported Arguments***

    +--------------------+--------------------+----------------------------+
    | argument           | datatypes          | other requirements         |
    +====================+====================+============================+
    | `start`            | String or          |                            |
    |                    | Timestamp          |                            |
    +--------------------+--------------------+----------------------------+
    | `end`              | String or          |                            |
    |                    | Timestamp          |                            |
    +--------------------+--------------------+----------------------------+
    | `periods`          | Integer            |                            |
    +--------------------+--------------------+----------------------------+
    | `freq`             | String             | -   Must be a [valid       |
    |                    |                    |     Pandas                 |
    |                    |                    |     frequ                  |
    |                    |                    | ency](https://pandas.pydat |
    |                    |                    | a.org/pandas-docs/stable/u |
    |                    |                    | ser_guide/timeseries.html# |
    |                    |                    | timeseries-offset-aliases) |
    +--------------------+--------------------+----------------------------+
    | `name`             | String             |                            |
    +--------------------+--------------------+----------------------------+

    !!! note

        * Exactly three of `start`, `end`, `periods`, and `freq` must
          be provided.
        * Bodo **Does Not** support `kwargs`, even for compatibility.

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f():
    ...     return pd.date_range(start="2018-04-24", end="2018-04-27", periods=3)

    >>> f()

    DatetimeIndex(['2018-04-24 00:00:00', '2018-04-25 12:00:00',
                  '2018-04-27 00:00:00'],
                 dtype='datetime64[ns]', freq=None)
    ```

#### `pd.timedelta_range`



- <code><apihead>pandas.<apiname>timedelta_range</apiname>(start=None, end=None, periods=None, freq=None, name=None, closed=None)</apihead></code>
<br><br>

    ***Supported Arguments***

    +--------------------+--------------------+----------------------------+
    | argument           | datatypes          | other requirements         |
    +====================+====================+============================+
    | `start`            | String or          |                            |
    |                    | Timedelta          |                            |
    +--------------------+--------------------+----------------------------+
    | `end`              | String or          |                            |
    |                    | Timedelta          |                            |
    +--------------------+--------------------+----------------------------+
    | `periods`          | Integer            |                            |
    +--------------------+--------------------+----------------------------+
    | `freq`             | String             | -   Must be a [valid       |
    |                    |                    |     Pandas                 |
    |                    |                    |     frequ                  |
    |                    |                    | ency](https://pandas.pydat |
    |                    |                    | a.org/pandas-docs/stable/u |
    |                    |                    | ser_guide/timeseries.html# |
    |                    |                    | timeseries-offset-aliases) |
    +--------------------+--------------------+----------------------------+
    | `name`             | String             |                            |
    +--------------------+--------------------+----------------------------+
    | `closed`           | String and one of  |                            |
    |                    | ('left',           |                            |
    |                    | 'right')           |                            |
    +--------------------+--------------------+----------------------------+


    !!! note

        * Exactly three of `start`, `end`, `periods`, and `freq` must
          be provided.
        * This function is not parallelized yet.

    ***Example Usage***

    ```py

    >>> @bodo.jit
    ... def f():
    ...     return pd.timedelta_range(start="1 day", end="11 days 1 hour", periods=3)

    >>> f()

    TimedeltaIndex(['1 days 00:00:00', '6 days 00:30:00', '11 days 01:00:00'], dtype='timedelta64[ns]', freq=None)

    ```