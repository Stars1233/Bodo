# `pd.Timestamp.hour`


`pandasTimestamp.hour`

### Example Usage

```py

>>> @bodo.jit
... def f():
...   ts2 = pd.Timestamp(year=2021, month=12, day=9, hour = 9, minute=57, second=44, microsecond=114123)
...   return ts2.hour
>>> f()
9
```
