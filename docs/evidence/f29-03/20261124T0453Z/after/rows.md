# F29-03 the sale and the parity after it: PostgreSQL rows

<a id="rows-1"></a>
## 1. sale_evidence.sql for the sale

```text
Null display is "(null)".
BEGIN READ ONLY;
BEGIN
-- The transaction, sale '308' with Idempotency-Key '99a05079-6950-48f5-b3d8-f592b12a63ce'
SELECT t.transaction_id, t.source_transaction_id,
       t.source_transaction_id = 'svc-sales:' || lower($2) AS key_matches,
       t.customer_id, t.store_id, t.channel_id, t.occurred_at, t.total,
       t.data_origin
  FROM transaction AS t
 WHERE t.transaction_id = $1::bigint
 transaction_id |             source_transaction_id              | key_matches |             customer_id              | store_id | channel_id |          occurred_at          | total | data_origin 
----------------+------------------------------------------------+-------------+--------------------------------------+----------+------------+-------------------------------+-------+-------------
            308 | svc-sales:99a05079-6950-48f5-b3d8-f592b12a63ce | t           | 00000000-0000-0000-0000-000000000028 |       30 |          3 | 2026-11-24 04:53:54.206689+00 | 41.25 | OBSERVED
(1 row)

-- Its lines
SELECT l.transaction_id, l.product_id, l.quantity, l.unit_price,
       l.quantity * l.unit_price AS line_total
  FROM transaction_line AS l
 WHERE l.transaction_id = $1::bigint
 ORDER BY l.product_id
 transaction_id | product_id | quantity | unit_price | line_total 
----------------+------------+----------+------------+------------
            308 |          1 |        1 |      18.75 |      18.75
            308 |          2 |        1 |      22.50 |      22.50
(2 rows)

-- Its capture row, and who recorded it
SELECT c.transaction_id, encode(c.request_sha256, 'hex') AS request_sha256,
       c.recorded_by, u.email, r.code AS role_code, c.captured_at
  FROM sales.sale_capture AS c
  JOIN app_user AS u ON u.user_id = c.recorded_by
  JOIN role AS r USING (role_id)
 WHERE c.transaction_id = $1::bigint
 transaction_id |                          request_sha256                          |             recorded_by              |               email               |   role_code   |          captured_at          
----------------+------------------------------------------------------------------+--------------------------------------+-----------------------------------+---------------+-------------------------------
            308 | f94f785fb69966b98b85d0c66d46f167efb292f1b9b01973b78509fc39c82d18 | b0111a08-bc6f-4638-8e49-5b3bf10fdd92 | f29-03-store-manager@example.test | STORE_MANAGER | 2026-11-24 04:54:07.883359+00
(1 row)

-- Inventory after the sale, for the sale's store and products
SELECT i.store_id, i.product_id, l.quantity AS sold, i.quantity_on_hand,
       i.updated_at
  FROM transaction AS t
  JOIN transaction_line AS l USING (transaction_id)
  JOIN inventory AS i
    ON i.store_id = t.store_id AND i.product_id = l.product_id
 WHERE t.transaction_id = $1::bigint
 ORDER BY i.product_id
 store_id | product_id | sold | quantity_on_hand |          updated_at           
----------+------------+------+------------------+-------------------------------
       30 |          1 |    1 |               39 | 2026-11-24 04:54:07.883359+00
       30 |          2 |    1 |               40 | 2026-11-24 04:54:07.883359+00
(2 rows)

-- The sale's outbox row; none once the relay has published and removed it
SELECT o.stream, o.event_type, o.entity_id, o.occurred_at, o.relayed_at
  FROM sales_events.outbox AS o
 WHERE o.entity_id = $1
          stream          | event_type | entity_id |          occurred_at          |          relayed_at           
--------------------------+------------+-----------+-------------------------------+-------------------------------
 stream:sales.v1.recorded | INSERT     | 308       | 2026-11-24 04:54:07.883359+00 | 2026-11-24 04:54:07.903544+00
(1 row)

ROLLBACK;
ROLLBACK
```

<a id="rows-2"></a>
## 2. inventory, store 30, product 1

```text
 store_id | product_id | quantity_on_hand |          updated_at           
----------+------------+------------------+-------------------------------
       30 |          1 |               39 | 2026-11-24 04:54:07.883359+00
(1 row)
```

<a id="rows-3"></a>
## 3. inventory, store 30, product 2

```text
 store_id | product_id | quantity_on_hand |          updated_at           
----------+------------+------------------+-------------------------------
       30 |          2 |               40 | 2026-11-24 04:54:07.883359+00
(1 row)
```

<a id="rows-4"></a>
## 4. transactions of customer 00000000-0000-0000-0000-000000000028

```text
 count | coalesce 
-------+----------
    13 |  5382.00
(1 row)
```
