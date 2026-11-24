# F29-05 Exposición y conversión: PostgreSQL rows

<a id="rows-1"></a>
## 1. assignments

```text
 assignment_id |   kind    
---------------+-----------
            61 | CONTROL
            62 | CONTROL
            63 | TREATMENT
            64 | TREATMENT
(4 rows)
```

<a id="rows-2"></a>
## 2. exposures

```text
 assignment_id |   kind    | before_assignment | from_engagement 
---------------+-----------+-------------------+-----------------
            63 | TREATMENT | f                 | f
            64 | TREATMENT | f                 | f
(2 rows)
```

<a id="rows-3"></a>
## 3. conversions

```text
 assignment_id | transaction_id | outside_window 
---------------+----------------+----------------
            61 |              5 | f
            61 |              4 | f
            62 |             11 | f
            62 |             10 | f
            62 |              9 | f
            62 |              8 | f
            63 |             26 | f
            63 |             25 | f
            63 |             24 | f
            64 |             39 | f
            64 |             38 | f
            64 |             37 | f
            64 |             36 | f
            64 |             35 | f
(14 rows)
```
