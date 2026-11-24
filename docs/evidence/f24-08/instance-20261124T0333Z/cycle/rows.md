# F24-08 a stock change and the recommendation: PostgreSQL rows

<a id="rows-1"></a>
## 1. inventory before

```text
 store_id | product_id | quantity_on_hand |          updated_at           
----------+------------+------------------+-------------------------------
        3 |          2 |               10 | 2026-11-23 20:16:25.843243+00
(1 row)
```

<a id="rows-2"></a>
## 2. stored decision (1. Agotado)

```text
             decision_id              |             customer_id              |   status    |          computed_at          | store_id | store_source | channel_id 
--------------------------------------+--------------------------------------+-------------+-------------------------------+----------+--------------+------------
 967eb947-3c6c-43aa-9204-edd2545b84bb | 00000000-0000-0000-0000-000000000003 | recommended | 2026-11-24 03:33:03.226584+00 |        3 | given        |     (null)
(1 row)


 position | product_id |   kind    | category_id | quantity_on_hand | held_at_store_id | alternative_for | unavailable_reason 
----------+------------+-----------+-------------+------------------+------------------+-----------------+--------------------
        1 |          1 | primary   |           1 |               34 |           (null) |          (null) | (null)
        2 |          3 | primary   |           3 |               61 |           (null) |          (null) | (null)
        3 |          5 | primary   |           5 |               18 |           (null) |          (null) | (null)
        4 |          2 | elsewhere |           2 |               25 |               13 |          (null) | out_of_stock
(4 rows)


          reason           | product_count 
---------------------------+---------------
 already_bought            |            14
 out_of_stock              |            22
 PERSONALIZATION_WITHDRAWN |             1
(3 rows)
```

<a id="rows-3"></a>
## 3. inventory after (1. Agotado)

```text
 store_id | product_id | quantity_on_hand |          updated_at           
----------+------------+------------------+-------------------------------
        3 |          2 |                0 | 2026-11-24 03:33:03.157985+00
(1 row)
```

<a id="rows-4"></a>
## 4. stored decision (2. Reabastecido)

```text
             decision_id              |             customer_id              |   status    |          computed_at          | store_id | store_source | channel_id 
--------------------------------------+--------------------------------------+-------------+-------------------------------+----------+--------------+------------
 0fd3af51-15c3-463d-b7af-b057ca631c26 | 00000000-0000-0000-0000-000000000003 | recommended | 2026-11-24 03:33:08.514964+00 |        3 | given        |     (null)
(1 row)


 position | product_id |  kind   | category_id | quantity_on_hand | held_at_store_id | alternative_for | unavailable_reason 
----------+------------+---------+-------------+------------------+------------------+-----------------+--------------------
        1 |          1 | primary |           1 |               34 |           (null) |          (null) | (null)
        2 |          2 | primary |           2 |               10 |           (null) |          (null) | (null)
        3 |          3 | primary |           3 |               61 |           (null) |          (null) | (null)
        4 |          5 | primary |           5 |               18 |           (null) |          (null) | (null)
(4 rows)


          reason           | product_count 
---------------------------+---------------
 already_bought            |            14
 out_of_stock              |            21
 PERSONALIZATION_WITHDRAWN |             1
(3 rows)
```

<a id="rows-5"></a>
## 5. inventory after (2. Reabastecido)

```text
 store_id | product_id | quantity_on_hand |          updated_at           
----------+------------+------------------+-------------------------------
        3 |          2 |               10 | 2026-11-24 03:33:08.499553+00
(1 row)
```

<a id="rows-6"></a>
## 6. the two movements

```text
 movement_id | movement_type | store_id | product_id | quantity_delta | quantity_before | quantity_after |          recorded_at          
-------------+---------------+----------+------------+----------------+-----------------+----------------+-------------------------------
          36 | COUNT         |        3 |          2 |            -10 |              10 |              0 | 2026-11-24 03:33:03.157985+00
          37 | RECEIPT       |        3 |          2 |             10 |               0 |             10 | 2026-11-24 03:33:08.499553+00
(2 rows)
```
