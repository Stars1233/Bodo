# Date Offsets


Bodo supports a subset of the offset types in `pandas.tseries.offsets`:

## DateOffset

#### `pd.tseries.offsets.DateOffset`

<code><apihead>pandas.tseries.offsets.<apiname>DateOffset</apiname>(n=1, normalize=False, years=None, months=None, weeks=None, days=None, hours=None, minutes=None, seconds=None, microseconds=None, nanoseconds=None, year=None, month=None, day=None, weekday=None, hour=None, minute=None, second=None, microsecond=None, nanosecond=None)</apihead></code><br><br><br>
***Supported Arguments***

- `n`: integer
- `normalize`: boolean
- `years`: integer
- `months`: integer
- `weeks`: integer
- `days`: integer
- `hours`:  integer
- `minutes`: integer
- `seconds`:  integer
- `microseconds`:  integer
- `nanoseconds`: integer
- `year`:  integer
- `month`:  integer
- `weekday`: integer
- `day`: integer
- `hour`: integer
- `minute`: integer
- `second`: integer
- `microsecond`: integer
- `nanosecond`: integer

***Example Usage***

```py
>>> @bodo.jit
>>> def f(ts):
...     return ts + pd.tseries.offsets.DateOffset(n=4, normalize=True, weeks=11, hour=2)
>>> ts = pd.Timestamp(year=2020, month=10, day=30, hour=22)
>>> f(ts)

Timestamp('2021-09-03 02:00:00')
```

### Properties

#### pd.tseries.offsets.DateOffset.normalize`


- <code><apihead>pandas.tseries.offsets.DateOffset.<apiname>normalize</apiname></apihead></code>
<br><br>
#### `pd.tseries.offsets.DateOffset.n`



- <code><apihead>pandas.tseries.offsets.<apiname>DateOffset</apiname>.n</apihead></code>
<br><br>
## MonthBegin


#### `pd.tseries.offsets.MonthBegin`



- <code><apihead>pandas.tseries.offsets.<apiname>MonthBegin</apiname>(n=1, normalize=False)</apihead></code>
<br><br>
    ***Supported Arguments***
    
    - `n`: integer
    - `normalize`: boolean
    
    ***Example Usage***
    ```py
    >>> @bodo.jit
    >>> def f(ts):
    ...     return ts + pd.tseries.offsets.MonthBegin(n=4, normalize=True)
    >>> ts = pd.Timestamp(year=2020, month=10, day=30, hour=22)
    >>> f(ts)
    
    Timestamp('2021-02-01 00:00:00')
    ```

## MonthEnd


#### `pd.tseries.offsets.MonthEnd`



- <code><apihead>pandas.tseries.offsets.<apiname>MonthEnd</apiname>(n=1, normalize=False)</apihead></code>
<br><br>
    ***Supported Arguments***

    - `n`: integer
    - `normalize`: boolean

    ***Example Usage***
    ```py
    >>> @bodo.jit
    >>> def f(ts):
    ...     return ts + pd.tseries.offsets.MonthEnd(n=4, normalize=False)
    >>> ts = pd.Timestamp(year=2020, month=10, day=30, hour=22)
    >>> f(ts)
    
    Timestamp('2021-01-31 22:00:00')
    ```

## Week


#### `pd.tseries.offsets.Week`



- <code><apihead>pandas.tseries.offsets.<apiname>Week</apiname>(n=1, normalize=False, weekday=None)</apihead></code>
<br><br>
    ***Supported Arguments***

    - `n`: integer
    - `normalize`: boolean
    - `weekday`: integer

    ***Example Usage***
    ```py
    >>> @bodo.jit
    >>> def f(ts):
    ...     return ts + pd.tseries.offsets.Week(n=4, normalize=True, weekday=5)
    >>> ts = pd.Timestamp(year=2020, month=10, day=30, hour=22)
    >>> f(ts)

    Timestamp('2020-11-21 00:00:00')
    ```
  
## Binary Operations

For all offsets, addition and subtraction with a scalar
`datetime.date`, `datetime.datetime` or `pandas.Timestamp`
is supported. Multiplication is also supported with a scalar integer.