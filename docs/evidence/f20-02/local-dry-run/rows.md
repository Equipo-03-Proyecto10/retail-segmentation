# F20-02 Electron reads the sale and writes a draft: PostgreSQL rows

<a id="rows-1"></a>
## 1. transaction 30

```text
 transaction_id |             customer_id              | store_id | channel_id | total |          occurred_at          
----------------+--------------------------------------+----------+------------+-------+-------------------------------
             30 | 00000000-0000-0000-0000-000000000004 |        1 |          1 | 74.00 | 2026-09-11 19:32:54.573565+00
(1 row)
```

<a id="rows-2"></a>
## 2. campaign 32

```text
 campaign_id |                 name                 | label_code | starts_on  |  ends_on   | status 
-------------+--------------------------------------+------------+------------+------------+--------
          32 | F20-02 evidence draft 20261122T2241Z | CHAMPION   | 2026-11-29 | 2026-12-29 | DRAFT
(1 row)
```

<a id="rows-3"></a>
## 3. campaigns with the draft's name

```text
 count 
-------
     1
(1 row)
```
