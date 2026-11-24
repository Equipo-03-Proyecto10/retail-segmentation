# F24-08 discontinued and restricted products: PostgreSQL rows

<a id="rows-1"></a>
## 1. the discontinued product

```text
 product_id |    sku    | is_active | commercial_status 
------------+-----------+-----------+-------------------
          5 | SKU-00005 | f         | discontinued
(1 row)
```

<a id="rows-2"></a>
## 2. products in stock at the store, in a category the customer restricted

```text
 product_id | category_id | quantity_on_hand 
------------+-------------+------------------
          4 |           4 |               15
(1 row)
```

<a id="rows-3"></a>
## 3. the stored decision

```text
             decision_id              |             customer_id              |   status    |          computed_at          | store_id | store_source | channel_id 
--------------------------------------+--------------------------------------+-------------+-------------------------------+----------+--------------+------------
 a2c1ee15-3f7c-4363-a063-c65a9b1f6ca5 | 00000000-0000-0000-0000-000000000003 | recommended | 2026-11-24 03:34:21.043668+00 |        3 | given        |     (null)
(1 row)


 position | product_id |  kind   | category_id | quantity_on_hand | held_at_store_id | alternative_for | unavailable_reason 
----------+------------+---------+-------------+------------------+------------------+-----------------+--------------------
        1 |          1 | primary |           1 |               34 |           (null) |          (null) | (null)
        2 |          2 | primary |           2 |               10 |           (null) |          (null) | (null)
        3 |          3 | primary |           3 |               61 |           (null) |          (null) | (null)
(3 rows)


          reason           | product_count 
---------------------------+---------------
 already_bought            |            14
 not_sellable              |             1
 out_of_stock              |            21
 PERSONALIZATION_WITHDRAWN |             1
(4 rows)
```

<a id="rows-4"></a>
## 4. the stored decision before it was discontinued

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
