# F29-06 Desconexión: PostgreSQL rows

<a id="rows-1"></a>
## 1. the account's sales in the window

```text
 transaction_id |             source_transaction_id              |                          request_sha256                          |             customer_id              | store_id |          occurred_at          | total |          captured_at          
----------------+------------------------------------------------+------------------------------------------------------------------+--------------------------------------+----------+-------------------------------+-------+-------------------------------
            309 | svc-sales:0522a5de-2a7e-47ba-aa85-a8b303c04545 | 3f90664628992145a9d0f64359e7b2d2359898777a8cc6c7fe52eb87756b97f1 | 00000000-0000-0000-0000-000000000019 |       20 | 2026-11-24 05:05:43.714701+00 | 18.75 | 2026-11-24 05:10:40.345832+00
            310 | svc-sales:77c8b2fa-add5-43c1-bb7a-05d84c08b1f6 | 3bb0fabf4ad790d7053e6dc59d98794af0e981680e1aa0574356bd45b1f58054 | 00000000-0000-0000-0000-000000000019 |       20 | 2026-11-24 05:08:13.26961+00  | 22.50 | 2026-11-24 05:10:40.814185+00
(2 rows)
```

<a id="rows-2"></a>
## 2. the same request recorded twice

```text
 encode | count | array_agg 
--------+-------+-----------
(0 rows)
```

<a id="rows-3"></a>
## 3. the same customer, store, time and total twice

```text
 customer_id | store_id | occurred_at | total | count | array_agg 
-------------+----------+-------------+-------+-------+-----------
(0 rows)
```

<a id="rows-4"></a>
## 4. the sale of Idempotency-Key 0522a5de-2a7e-47ba-aa85-a8b303c04545

```text
 transaction_id |             source_transaction_id              | total |          captured_at          
----------------+------------------------------------------------+-------+-------------------------------
            309 | svc-sales:0522a5de-2a7e-47ba-aa85-a8b303c04545 | 18.75 | 2026-11-24 05:10:40.345832+00
(1 row)
```

<a id="rows-5"></a>
## 5. the sale of Idempotency-Key 77c8b2fa-add5-43c1-bb7a-05d84c08b1f6

```text
 transaction_id |             source_transaction_id              | total |          captured_at          
----------------+------------------------------------------------+-------+-------------------------------
            310 | svc-sales:77c8b2fa-add5-43c1-bb7a-05d84c08b1f6 | 22.50 | 2026-11-24 05:10:40.814185+00
(1 row)
```
