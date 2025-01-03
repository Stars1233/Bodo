# ORDER BY



The `#!sql ORDER BY` keyword sorts the resulting DataFrame in ascending
or descending order. By default, it sorts the records in ascending order.
NULLs are sorted in accordance with the optional `#!sql NULLS FIRST` or
`#!sql NULLS LAST` keywords.

BodoSQL's default NULLS FIRST and NULLS LAST behavior is controlled by
an environment variable `BODO_SQL_STYLE` which has two currently supported
values:

- `SNOWFLAKE` (the default)
- `SPARK`

If `BODO_SQL_STYLE` is set to `SNOWFLAKE` then the default behavior is `NULLS LAST`
for ascending order and `NULLS FIRST` for descending order. If `BODO_SQL_STYLE` is
set to `SPARK` then the default behavior is `NULLS FIRST` for ascending order and
`NULLS LAST` for descending order. If you are transitioning a query from any other
system we strongly recommend manually specifying `NULLS FIRST` or `NULLS LAST` to
ensure the correct behavior.

```sql
SELECT <COLUMN_NAMES>
FROM <TABLE_NAME>
ORDER BY <ORDERED_COLUMN_NAMES> [ASC|DESC] [NULLS FIRST|LAST]
```

For Example:
```sql
SELECT A, B FROM table1 ORDER BY B, A DESC NULLS LAST
```

### Example Usage


```py
>>>@bodo.jit
... def g(df):
...    bc = bodosql.BodoSQLContext({"CUSTOMERS":df})
...    query = "SELECT name, balance FROM customers ORDER BY balance"
...    res = bc.sql(query)
...    return res

>>>customers_df = pd.DataFrame({
...     "CUSTOMERID": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
...     "NAME": ["Deangelo Todd","Nikolai Kent","Eden Heath", "Taliyah Martinez",
...                 "Demetrius Chavez","Weston Jefferson","Jonathon Middleton",
...                 "Shawn Winters","Keely Hutchinson", "Darryl Rosales",],
...     "BALANCE": [1123.34, 2133.43, 23.58, 8345.15, 943.43, 68.34, 12764.50, 3489.25, 654.24, 25645.39]
... })

>>>g(customers_df)
                NAME   BALANCE
2          Eden Heath     23.58
5    Weston Jefferson     68.34
8    Keely Hutchinson    654.24
4    Demetrius Chavez    943.43
0       Deangelo Todd   1123.34
1        Nikolai Kent   2133.43
7       Shawn Winters   3489.25
3    Taliyah Martinez   8345.15
6  Jonathon Middleton  12764.50
9      Darryl Rosales  25645.39
```
