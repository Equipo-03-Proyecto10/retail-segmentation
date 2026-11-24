# F29-05 Recomendaciones: PostgreSQL rows

<a id="rows-1"></a>
## 1. active products with no stock at the store

```text
 product_id 
------------
          6
          7
          8
          9
         10
         11
         12
         13
         14
         15
         16
         17
         18
         19
         20
         21
         22
         23
         24
         25
         26
         27
         28
         29
         30
         31
         32
         33
         34
         35
         36
         37
         38
         39
         40
(35 rows)
```

<a id="rows-2"></a>
## 2. the stored decision

```text
             decision_id              |             customer_id              |   status    |          computed_at          | store_id | store_source | channel_id 
--------------------------------------+--------------------------------------+-------------+-------------------------------+----------+--------------+------------
 5449c5bd-7c7b-4c6f-9b1a-087f4a903bf2 | 00000000-0000-0000-0000-000000000003 | recommended | 2026-11-23 21:55:45.690832-06 |        3 | given        |     (null)
(1 row)


 position | product_id |  kind   | category_id | quantity_on_hand | held_at_store_id | alternative_for | unavailable_reason 
----------+------------+---------+-------------+------------------+------------------+-----------------+--------------------
        1 |          2 | primary |           2 |               10 |           (null) |          (null) | (null)
        2 |          1 | primary |           1 |               34 |           (null) |          (null) | (null)
        3 |          3 | primary |           3 |               61 |           (null) |          (null) | (null)
        4 |          5 | primary |           5 |               18 |           (null) |          (null) | (null)
(4 rows)


          reason           | product_count 
---------------------------+---------------
 PERSONALIZATION_WITHDRAWN |             1
 already_bought            |            14
 out_of_stock              |            21
(3 rows)
```
