# `pd.Series.gt`

`pandas.Series.gt(other, level=None, fill_value=None, axis=0)`

### Supported Arguments

| argument     | datatypes                                                                                                 |
|--------------|-----------------------------------------------------------------------------------------------------------|
| `other`      | <ul><li>  numeric scalar </li><li>  array with numeric data </li><li> Series with numeric data </li></ul> |
| `fill_value` | numeric scalar                                                                                            |


!!! note
    `Series.gt` is only supported on Series of numeric data.


### Example Usage

``` py
>>> @bodo.jit
... def f(S, other):
...   return S.gt(other)
>>> S = pd.Series(np.arange(1, 1001))
>>> other = pd.Series(reversed(np.arange(1, 1001)))
>>> f(S, other)
0      False
1      False
2      False
3      False
4      False
      ...
995     True
996     True
997     True
998     True
999     True
Length: 1000, dtype: bool
```

