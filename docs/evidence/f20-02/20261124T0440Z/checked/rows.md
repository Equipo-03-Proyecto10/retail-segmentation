# F20-02 Electron reads the sale and writes a draft: PostgreSQL rows

<a id="rows-1"></a>
## 1. transaction 307

```text
 transaction_id |             customer_id              | store_id | channel_id | total |          occurred_at          
----------------+--------------------------------------+----------+------------+-------+-------------------------------
            307 | 00000000-0000-0000-0000-000000000028 |       30 |          3 | 41.25 | 2026-11-24 03:51:10.148779+00
(1 row)
```

<a id="rows-2"></a>
## 2. campaign 31

```text
 campaign_id |                 name                 | label_code | starts_on  |  ends_on   | status 
-------------+--------------------------------------+------------+------------+------------+--------
          31 | F20-02 evidence draft 20261124T0438Z | CHAMPION   | 2026-12-01 | 2026-12-31 | DRAFT
(1 row)
```

<a id="rows-3"></a>
## 3. campaigns with the draft's name after campaign 31

```text
 count 
-------
     0
(1 row)
```
