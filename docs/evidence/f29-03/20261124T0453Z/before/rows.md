# F29-03 snapshot before the sale: PostgreSQL rows

<a id="rows-1"></a>
## 1. inventory, store 30, product 1

```text
 store_id | product_id | quantity_on_hand |          updated_at           
----------+------------+------------------+-------------------------------
       30 |          1 |               40 | 2026-11-24 03:51:17.700846+00
(1 row)
```

<a id="rows-2"></a>
## 2. inventory, store 30, product 2

```text
 store_id | product_id | quantity_on_hand |          updated_at           
----------+------------+------------------+-------------------------------
       30 |          2 |               41 | 2026-11-24 03:51:17.700846+00
(1 row)
```

<a id="rows-3"></a>
## 3. transactions of customer 00000000-0000-0000-0000-000000000028

```text
 count | coalesce 
-------+----------
    12 |  5340.75
(1 row)
```
