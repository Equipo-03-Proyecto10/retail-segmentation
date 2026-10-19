# F8-01 and F8-02 — Sales ingestion: the source identifier, the CSV import and its rejection report

Evidence for F8-01 (#209), F8-02 (#210) and the import screen, #334, which was
closed with no screenshot of the CSV import or its rejection report. F8-01 gives
a sale the identifier its file supplies, stored durably and unique, so a
re-sent file does not create a second copy. F8-02 loads a versioned sales CSV
one row at a time and reports what was received, accepted and rejected with the
file line number and the reason for every rejected row. #334 is the page an
administrator does that on.

No schema change was made and no application code was changed. The sample
CSV files were written outside the repository (`data/` and `*.csv` are
gitignored) and their content is shown below.

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`. The stack from
an empty volume, so the three scripts ran in order:

```text
docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up --build -d
```

PostgreSQL 16.14, the application under gunicorn, NGINX 1.27.5 with the
production `Content-Security-Policy`, at `http://127.0.0.1:8080`. Screens were
driven with Chromium 153 through Playwright, signed in as the demonstration
administrator (`admin@mosaiq-demo.com`); the other profiles were signed in
through the real form by a small script. Captures are full-page at 375 px and
1440 px with the classic scrollbar hidden, so the layout is exactly 375 and 1440
CSS pixels wide; `scrollWidth` equalled `clientWidth` in every one.

The seed has 300 transactions and one inventory row per store for products 1 to 5.
An accepted row takes stock (ADR-0031), so valid sample rows use those pairs.
Four files were loaded through the page, in this order, and these are the loads
the screens show:

| Load | File | Content |
|---|---|---|
| #1 | `sales-4-rows.csv` | Four rows: two valid, one for an unknown customer, one with quantity 0 |
| #2 | `sales-every-reason.csv` | 17 rows covering each rejection reason the importer has |
| #3 | `sales-resend-valid-only.csv` | The two valid rows of load #1 sent again |
| #4 | `sales-newcomer.csv` | One valid row for a customer added for this evidence (customer 31, *Evidence Newcomer*, inserted with `psql`, as was customer 32) |

Four more files were refused before any row was read, and left no load behind:
`sales-wrong-header.csv`, `sales-latin1.csv` (a Latin-1 byte), and the two
size-limit files.

<details><summary>The files, verbatim</summary>

```csv
# sales-4-rows.csv
transaction_id,customer_id,store_id,channel_id,occurred_at,product_id,quantity,unit_price
EV443-0001,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T10:00:00+00:00,1,2,10.50
EV443-0002,00000000-0000-0000-0000-000000000002,2,2,2026-10-17T11:30:00+00:00,2,1,4.25
EV443-0003,00000000-0000-0000-0000-000000000999,3,3,2026-10-17T12:00:00+00:00,3,1,9.99
EV443-0004,00000000-0000-0000-0000-000000000003,3,3,2026-10-17T12:30:00+00:00,3,0,9.99
```

```csv
# sales-resend-valid-only.csv
transaction_id,customer_id,store_id,channel_id,occurred_at,product_id,quantity,unit_price
EV443-0001,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T10:00:00+00:00,1,2,10.50
EV443-0002,00000000-0000-0000-0000-000000000002,2,2,2026-10-17T11:30:00+00:00,2,1,4.25
```

```csv
# sales-every-reason.csv  (line numbers: the header is line 1)
transaction_id,customer_id,store_id,channel_id,occurred_at,product_id,quantity,unit_price
EV443-0010,00000000-0000-0000-0000-000000000003,3,3,2026-10-17T09:00:00+00:00,1,3,8.00
EV443-0010,00000000-0000-0000-0000-000000000003,3,3,2026-10-17T09:00:00+00:00,2,1,12.50
EV443-0011,00000000-0000-0000-0000-000000000001,4,1,2026-10-17T13:00:00+00:00,1,1,6.75
EV443-0012,00000000-0000-0000-0000-000000000002,5,2,2026-10-17T14:00:00+00:00,2,1,5.00
EV443-0010,00000000-0000-0000-0000-000000000003,3,3,2026-10-17T09:00:00+00:00,1,3,8.00
,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T15:00:00+00:00,1,1,3.00
=CMD(1),00000000-0000-0000-0000-000000000001,1,1,2026-10-17T15:00:00+00:00,1,1,3.00
EV443-0013,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T15:00:00+00:00,1,two,3.00
EV443-0014,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T15:00:00,1,1,3.00
EV443-0015,00000000-0000-0000-0000-000000000001,1,1,2036-01-01T15:00:00+00:00,1,1,3.00
EV443-0016,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T15:00:00+00:00,1,1,3.005
EV443-0017,00000000-0000-0000-0000-000000000001,99,1,2026-10-17T15:00:00+00:00,1,1,3.00
EV443-0018,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T15:00:00+00:00,6,1,3.00
EV443-0019,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T15:00:00+00:00,2,500,3.00
EV443-0020,00000000-0000-0000-0000-000000000001,1,1,2026-01-05T15:00:00+00:00,3,1,3.00
EV443-0021,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T15:00:00+00:00,3
EV443-0010,00000000-0000-0000-0000-000000000003,5,3,2026-10-17T09:00:00+00:00,3,1,2.00
```

```csv
# sales-newcomer.csv
transaction_id,customer_id,store_id,channel_id,occurred_at,product_id,quantity,unit_price
EV443-0040,00000000-0000-0000-0000-000000000031,6,2,2026-10-16T16:20:00+00:00,3,2,20.00
```

```csv
# sales-wrong-header.csv
id,customer,amount
1,00000000-0000-0000-0000-000000000001,10.00
```

```text
# the two size-limit files came from the same loop with two targets
h = 'transaction_id,customer_id,store_id,channel_id,occurred_at,product_id,quantity,unit_price\n'
row = 'EV443-BIG-%07d,00000000-0000-0000-0000-000000000001,1,1,2026-10-17T10:00:00+00:00,1,1,3.00\n'
for name, extra in (('sales-over-limit.csv', 200000), ('sales-just-over-limit.csv', 10000)):
    target = 5*1024*1024 + extra
    with open(name, 'w') as f:
        f.write(h); i = 0; size = len(h)
        while size < target:
            r = row % i; f.write(r); size += len(r); i += 1

$ ls -l sales-just-over-limit.csv sales-over-limit.csv      MAX_UPLOAD_BYTES = 5242880, plus a 65536-byte form allowance = 5308416
5252904 sales-just-over-limit.csv
5442972 sales-over-limit.csv
(5252904 is over the limit and inside the allowance; 5442972 is beyond the allowance)

$ od -c sales-latin1.csv | tail -3          (the last row ends 3.00 caf<0xE9>: Latin-1, not UTF-8)
0000240   0   +   0   0   :   0   0   ,   1   ,   1   ,   3   .   0   0
0000260       c   a   f 351  \n
0000266
```

</details>

## F8-01 — the source identifier (#209)

| Acceptance criterion | Proof |
|---|---|
| A transaction carries its source identifier and the generated key, and the identifier is unique per producer | `\d transaction` below: `transaction_id` identity, `source_transaction_id` `NOT NULL` with a unique constraint; a direct `INSERT` of a repeated identifier is refused |
| A file re-sent in full: every row is rejected as a duplicate | Load #3, below |
| A row whose identifier is absent is rejected with that reason | Load #2, line 7, below |
| The 4NF argument and data dictionary updated | `docs/data-model.md` §3 (the `source_transaction_id` row) and §4 (*Source transaction identifiers*) |

```text
                                       Table "public.transaction"
        Column         |           Type           | Collation | Nullable |           Default            
-----------------------+--------------------------+-----------+----------+------------------------------
 transaction_id        | bigint                   |           | not null | generated always as identity
 source_transaction_id | character varying(64)    |           | not null | 
 customer_id           | uuid                     |           | not null | 
 store_id              | smallint                 |           | not null | 
 channel_id            | smallint                 |           | not null | 
 occurred_at           | timestamp with time zone |           | not null | 
 total                 | numeric(12,2)            |           | not null | 
Indexes:
    "transaction_pkey" PRIMARY KEY, btree (transaction_id)
    "idx_transaction_customer_date" btree (customer_id, occurred_at)
    "idx_transaction_store_date" btree (store_id, occurred_at)
    "transaction_source_transaction_id_key" UNIQUE CONSTRAINT, btree (source_transaction_id)
Check constraints:
    "transaction_total_check" CHECK (total >= 0::numeric)
Foreign-key constraints:
    "transaction_channel_id_fkey" FOREIGN KEY (channel_id) REFERENCES channel(channel_id) ON DELETE RESTRICT
    "transaction_customer_id_fkey" FOREIGN KEY (customer_id) REFERENCES customer(customer_id) ON DELETE RESTRICT
    "transaction_store_id_fkey" FOREIGN KEY (store_id) REFERENCES store(store_id) ON DELETE RESTRICT
Referenced by:
        ...
```

```text
$ psql -d retail -f f8_01.sql
== a transaction carries the generated key and the source identifier (seeded and imported rows)
 transaction_id | source_transaction_id |             customer_id              | total  
----------------+-----------------------+--------------------------------------+--------
              1 | TXN-00000001          | 00000000-0000-0000-0000-000000000001 | 142.00
              2 | TXN-00000002          | 00000000-0000-0000-0000-000000000002 | 127.25
            301 | EV443-0001            | 00000000-0000-0000-0000-000000000001 |  21.00
            303 | EV443-0010            | 00000000-0000-0000-0000-000000000003 |  36.50
(4 rows)
== the source identifier is unique: a direct INSERT of an identifier already present is refused
ERROR:  duplicate key value violates unique constraint "transaction_source_transaction_id_key"
DETAIL:  Key (source_transaction_id)=(TXN-00000001) already exists.
== the source identifier is mandatory: a NULL is refused, not defaulted
ERROR:  source_transaction_id is outside the namespace of postgres
CONTEXT:  PL/pgSQL function public.fn_sales_namespace_guard_transaction() line 5 at RAISE
== the surrogate key is generated by the database and cannot be supplied
ERROR:  cannot insert a non-DEFAULT value into column "transaction_id"
DETAIL:  Column "transaction_id" is an identity column defined as GENERATED ALWAYS.
HINT:  Use OVERRIDING SYSTEM VALUE to override.
== a file re-sent in full: load 3 re-sent the two accepted rows of load 1, and every row was rejected as a duplicate
 load_id |          filename           | received_count | accepted_count | rejected_count | line_number |                         reason                         
---------+-----------------------------+----------------+----------------+----------------+-------------+--------------------------------------------------------
       3 | sales-resend-valid-only.csv |              2 |              0 |              2 |           2 | duplicate: EV443-0001 already has a line for product 1
       3 | sales-resend-valid-only.csv |              2 |              0 |              2 |           3 | duplicate: EV443-0002 already has a line for product 2
(2 rows)
== and no second copy exists: transactions per source identifier (any identifier with more than one transaction)
 source_transaction_id | count 
-----------------------+-------
(0 rows)
== a row whose identifier is absent is rejected with that reason (load 2, line 7)
 load_id | line_number |                        reason                         
---------+-------------+-------------------------------------------------------
       2 |           7 | source transaction id is required
       2 |           8 | source transaction id cannot be a spreadsheet formula
(2 rows)
```

Two readings of that output. A direct `INSERT` with a `NULL` identifier was
refused, but by the namespace guard `fn_sales_namespace_guard_transaction` (a
`BEFORE INSERT` trigger for the `svc-sales:` prefix, ADR-0039), which runs before
the `NOT NULL` check; the column's `NOT NULL` is the `\d` line above. And
"unique per producer" is a global `UNIQUE` today: `docs/data-model.md` §4 records
that this holds because CSV is the only producer (ADR-0020), and what a second
producer would change.

A file re-sent: load #3 sent the two accepted rows of load #1 again. Both were
rejected as duplicates, no transaction was added, and the query for any source
identifier held by more than one transaction returned no rows. Page:

| Viewport | Capture |
|---|---|
| 1440 px | [Load #3: 2 received, 0 accepted, 2 rejected as duplicates](f334-report-resent-duplicates-1440.png) |
| 375 px | [Load #3](f334-report-resent-duplicates-375.png) |

A re-send of the whole of `sales-4-rows.csv` would also reject the first two rows
as duplicates, and reject the other two for their own reasons again; the
two-row file isolates the duplicate rule.

## F8-02 — the importer (#210)

| Acceptance criterion | Proof |
|---|---|
| Two valid and two invalid rows: received 4, accepted 2, rejected 2, reconciled | Load #1, below |
| A rejected row carries its row number and its reason | Load #1's lines 4 and 5; load #2's 13 lines |
| `grep -nEi 'flask\|csv\|filestorage\|request\|filename\|stream' web/services/ingestion.py` prints nothing | Below |
| A file whose contract version is unsupported is refused deterministically before any row is parsed | A file with the wrong header, below; the version check is `test_an_unsupported_contract_version_is_refused_before_the_file_is_opened` |
| An accepted load: each total equals the sum of its lines' quantity times unit price | The SQL below, over the imported rows and over all 306 transactions |
| Every SQL statement in the ingestion path is parameterized | The grep below and the named tests |

```text
$ grep -nEi 'flask|csv|filestorage|request|filename|stream' web/services/ingestion.py
(no output, exit status 1)

$ grep -nE 'execute\(\s*f"|execute\(\s*"[^"]*"\s*%|\.format\(' web/db/sales.py web/db/sales_loads.py web/db/inventory.py web/services/sales_csv.py web/services/ingestion.py
(no output, exit status 1)
```

The second grep looks for an f-string, `%` formatting or `.format` handed to
`execute()` in the files of the ingestion path; every `execute()` there passes its
values as a parameter tuple.

Load #1 on the page:

| Viewport | Capture |
|---|---|
| 1440 px | [Load #1: received 4, accepted 2, rejected 2; line 4 `unknown customer`, line 5 `quantity must be positive`](f334-report-4-rows-1440.png) |
| 375 px | [Load #1](f334-report-4-rows-375.png) |

The totals, from `f8_02.sql`:

```text
$ psql -d retail -f f8_02.sql
== each accepted transaction: its stored total against the sum of its lines (quantity x unit price)
 source_transaction_id | total | sum_of_lines | equal 
-----------------------+-------+--------------+-------
 EV443-0001            | 21.00 |        21.00 | t
 EV443-0002            |  4.25 |         4.25 | t
 EV443-0010            | 36.50 |        36.50 | t
 EV443-0011            |  6.75 |         6.75 | t
 EV443-0012            |  5.00 |         5.00 | t
 EV443-0040            | 40.00 |        40.00 | t
(6 rows)
== and across every transaction in the database (seed and imported): totals that differ from their lines
 transactions | totals_that_differ 
--------------+--------------------
          306 |                  0
(1 row)
== a rejected row leaves nothing behind: the source identifiers of rows rejected in load 2 that exist as transactions
 source_transaction_id 
-----------------------
(0 rows)
== stock is taken by accepted rows only: store 1 product 1 (seed 12) sold 2 in load 1; store 1 product 2 (seed 13) untouched, the 500-unit row was refused; store 2 product 2 (seed 14) sold 1
 store_id | product_id | quantity_on_hand 
----------+------------+------------------
        1 |          1 |               10
        1 |          2 |               13
        2 |          2 |               13
(3 rows)
== the load records: received = accepted + rejected, and the stored rejection rows match the rejected count
 load_id |          filename           | received | accepted | rejected | reconciles | rejection_rows 
---------+-----------------------------+----------+----------+----------+------------+----------------
       1 | sales-4-rows.csv            |        4 |        2 |        2 | t          |              2
       2 | sales-every-reason.csv      |       17 |        4 |       13 | t          |             13
       3 | sales-resend-valid-only.csv |        2 |        0 |        2 | t          |              2
       4 | sales-newcomer.csv          |        1 |        1 |        0 | t          |              0
(4 rows)
```

`EV443-0010` is a two-line transaction (three units at 8.00 and one at 12.50 =
36.50). The rejected rows of load #2 left no transaction, and stock moved only
for accepted rows (the refused 500-unit row left store 1 product 2 at 13).

## #334 — the import page

| Acceptance criterion | Proof |
|---|---|
| The administrator opens the page, chooses a CSV of the versioned contract, submits it with a CSRF token | The form at the top of the history capture below; it carries a `csrf_token` field (43 characters); a POST with no token and one with a forged token are both 403 |
| After the load: received, accepted and rejected counts that reconcile, and a table of every rejected row with its file line number and reason | Load #2 below: 17 = 4 + 13, thirteen rows each with its line and reason |
| The report is retrievable after the page is left, and downloadable as CSV | The upload history, the stored report reopened by URL, and the CSV download, below |
| A role without `sales_ingest.execute` gets 403 on the page and on a post | The role table below |
| A file over `MAX_UPLOAD_BYTES`, or not UTF-8: a readable refusal, never a 500 | Four refusal captures below |
| Reasons are business wording, not raw database or Python text | The 13 reasons below |

| What | 1440 px | 375 px |
|---|---|---|
| The page with its upload form above the history of four loads, newest first | [capture](f334-upload-history-1440.png) | [capture](f334-upload-history-375.png) |
| Load #2: 17 received, 4 accepted, 13 rejected, each with line and reason | [capture](f334-report-every-reason-1440.png) | [capture](f334-report-every-reason-375.png) |
| Load #4: every row accepted, `No rejected rows` | [capture](f334-report-all-accepted-1440.png) | [capture](f334-report-all-accepted-375.png) |

The 13 rejected rows of load #2, as the page (and the downloaded CSV) report them.
Line 1 is the header, so the first data row is line 2:

```text
GET /admin/sales-import/2/rejections.csv -> 200 text/csv; charset=utf-8 | attachment; filename="sales-load-2-rejections.csv"
line_number,reason
6,duplicate: EV443-0010 already has a line for product 1
7,source transaction id is required
8,source transaction id cannot be a spreadsheet formula
9,malformed row: quantity must be a whole number
10,malformed row: occurred_at must include a UTC offset
11,occurred_at cannot be in the future
12,unit price must have at most 2 decimal places
13,unknown store
14,no inventory for store 1 and product 6
15,insufficient stock for store 1 and product 2: 13 available
16,sale occurred before customer registration
17,row has fewer columns than the header
18,row disagrees with the accepted header for EV443-0010
```

Every reason names a column or a business fact; none contains PostgreSQL or Python
text (for example `quantity must be a whole number`, not `invalid literal for
int()`; `unknown store`, not a foreign-key name). Lines 2 to 5 of the file were the
four accepted rows (a two-line `EV443-0010`, `EV443-0011`, `EV443-0012`).

**Retrievable and downloadable.** The report is a stored record: after leaving the
page, `/admin/sales-import/2` rendered the same counts and lines, and the history
lists all four loads. The `Download CSV` link is `/admin/sales-import/2/rejections.csv`
(200, `text/csv`, `attachment; filename="sales-load-2-rejections.csv"`), the
output above.

**Who may.** From `f8_roles.py`, each profile signed in through the form:

```text
anonymous    GET /admin/sales-import/ -> 302 /login?next=/admin/sales-import/
ADMIN              GET index 200  GET report 200  GET rejections.csv 200  POST upload 400
ANALYST            GET index 403  GET report 403  GET rejections.csv 403  POST upload 403
STORE_MANAGER      GET index 403  GET report 403  GET rejections.csv 403  POST upload 403
MARKETING          GET index 403  GET report 403  GET rejections.csv 403  POST upload 403
INVENTORY_PLANNER  GET index 403  GET report 403  GET rejections.csv 403  POST upload 403
AUDITOR            GET index 403  GET report 403  GET rejections.csv 403  POST upload 403
CUSTOMER           GET index 403  GET report 403  GET rejections.csv 403  POST upload 403
ADMIN POST without a CSRF token -> 403
ADMIN POST with a forged CSRF token -> 403
```

`POST upload 400` for the administrator is a post with no file, answered by `Choose
a CSV file to upload.`. A post with a valid session but no CSRF token, or a
forged one, is 403 for the administrator too.

| Refused | 1440 px | 375 px |
|---|---|---|
| `ANALYST` on the page: 403 | [capture](f334-refused-analyst-1440.png) | [capture](f334-refused-analyst-375.png) |

**Refusals that are not a 500.** Each was submitted through the form; none created
a load, and the history stayed at four loads throughout.

| File | Result | 1440 px | 375 px |
|---|---|---|---|
| `sales-wrong-header.csv` | 400, in the page: `The file was not loaded: the header does not match contract version 1.` | [capture](f334-refused-wrong-header-1440.png) | [capture](f334-refused-wrong-header-375.png) |
| `sales-latin1.csv` | 400: `The file was not loaded: the file is not valid utf-8 text; save it as "CSV UTF-8" and upload it again.` | [capture](f334-refused-not-utf8-1440.png) | [capture](f334-refused-not-utf8-375.png) |
| `sales-just-over-limit.csv`, 5,252,904 bytes, over `MAX_UPLOAD_BYTES` (5,242,880) | 400, in the page: `The file is larger than the 5 MB upload limit. Split it and upload each part.` | [capture](f334-refused-just-over-limit-1440.png) | [capture](f334-refused-just-over-limit-375.png) |
| `sales-over-limit.csv`, 5,442,972 bytes, past the limit plus the 64 KiB form allowance | 413, the application's error page: `Request Entity Too Large`, with a reference | [capture](f334-refused-over-limit-1440.png) | [capture](f334-refused-over-limit-375.png) |

The last row is a different path: past the form allowance, Flask's
`MAX_CONTENT_LENGTH` answers before the route runs, so the refusal is the
generic 413 page (`The application could not complete that request. The problem
has been recorded; nothing you did caused it.`) and not the in-page sentence. It
is readable and is not a 500, but it does not say what to do. A file above
NGINX's 6 MB `client_max_body_size` would be refused by NGINX before the
application; that was not exercised.

## Tests

```text
$ pytest tests/test_sales_csv.py tests/test_ingestion_service.py tests/test_sales_import_route.py \
         tests/test_sales_loads_db.py tests/test_sales_db.py
80 passed in 0.52s
```

`test_four_rows_two_valid_two_invalid_report_4_2_2`,
`test_a_rejection_carries_its_row_number_and_reason`,
`test_an_unsupported_contract_version_is_refused_before_the_file_is_opened`,
`test_a_malformed_value_is_named_by_its_column_not_by_python[...]`,
`test_a_file_that_is_not_utf8_is_refused_in_words_not_codec_text`,
`test_a_file_over_the_upload_limit_is_refused_before_any_row_is_read`,
`test_a_profile_without_sales_ingest_is_refused_and_nothing_is_loaded[ANALYST]`
(and `MARKETING`, `AUDITOR`), `test_the_rejections_download_as_csv_with_their_file_line_numbers`,
`test_a_repeated_product_line_for_the_same_source_id_is_rejected_as_duplicate`,
`test_a_load_and_every_rejected_line_are_written_and_committed_together`. The
whole suite is `3040 passed, 14 skipped`.

## What was not run

- The issue's 10,000-row example (9,941 accepted, 59 rejected, about 38 s) was not
  re-run: the seeded stock would refuse most of 10,000 rows, and loading that many
  would have needed stock the seed does not have. The timing is the issue's
  figure, not an observation here.
- The page before any upload (an empty history) was not captured: the database
  already held the four loads when the captures were made.
- The `Content-Disposition` download was read with a script, not through the
  browser's download dialog.
- The `postgres`-marked `tests/test_sales_namespace_guard.py` was skipped.
- A file above NGINX's 6 MB limit, and the contract-version mismatch beyond a
  wrong header, were not driven through the page.
- Nothing was run on the Compute Engine instance.

## The scripts

<details><summary><code>f8_01.sql</code></summary>

```sql
\pset pager off
\echo '== a transaction carries the generated key and the source identifier (seeded and imported rows)'
SELECT transaction_id, source_transaction_id, customer_id, total FROM transaction WHERE transaction_id IN (1, 2, 301, 303) ORDER BY transaction_id;
\echo '== the source identifier is unique: a direct INSERT of an identifier already present is refused'
BEGIN;
INSERT INTO transaction (source_transaction_id, customer_id, store_id, channel_id, occurred_at, total)
VALUES ('TXN-00000001', '00000000-0000-0000-0000-000000000001', 1, 1, now() - interval '1 day', 10.00);
ROLLBACK;
\echo '== the source identifier is mandatory: a NULL is refused, not defaulted'
BEGIN;
INSERT INTO transaction (source_transaction_id, customer_id, store_id, channel_id, occurred_at, total)
VALUES (NULL, '00000000-0000-0000-0000-000000000001', 1, 1, now() - interval '1 day', 10.00);
ROLLBACK;
\echo '== the surrogate key is generated by the database and cannot be supplied'
BEGIN;
INSERT INTO transaction (transaction_id, source_transaction_id, customer_id, store_id, channel_id, occurred_at, total)
VALUES (777777, 'EV443-SURROGATE', '00000000-0000-0000-0000-000000000001', 1, 1, now() - interval '1 day', 10.00);
ROLLBACK;
\echo '== a file re-sent in full: load 3 re-sent the two accepted rows of load 1, and every row was rejected as a duplicate'
SELECT l.load_id, l.filename, l.received_count, l.accepted_count, l.rejected_count, r.line_number, r.reason
  FROM sales_load l JOIN sales_load_rejection r USING (load_id) WHERE l.load_id = 3 ORDER BY r.line_number;
\echo '== and no second copy exists: transactions per source identifier (any identifier with more than one transaction)'
SELECT source_transaction_id, count(*) FROM transaction GROUP BY 1 HAVING count(*) > 1;
\echo '== a row whose identifier is absent is rejected with that reason (load 2, line 7)'
SELECT load_id, line_number, reason FROM sales_load_rejection WHERE reason LIKE 'source transaction id%' ORDER BY line_number;
```

</details>

<details><summary><code>f8_02.sql</code></summary>

```sql
\pset pager off
\echo '== each accepted transaction: its stored total against the sum of its lines (quantity x unit price)'
SELECT t.source_transaction_id, t.total, sum(l.quantity * l.unit_price) AS sum_of_lines, t.total = sum(l.quantity * l.unit_price) AS equal
  FROM transaction t JOIN transaction_line l USING (transaction_id)
 WHERE t.source_transaction_id LIKE 'EV443-%' GROUP BY t.transaction_id, t.source_transaction_id, t.total ORDER BY t.transaction_id;
\echo '== and across every transaction in the database (seed and imported): totals that differ from their lines'
SELECT count(*) AS transactions, count(*) FILTER (WHERE t.total <> l.amount) AS totals_that_differ
  FROM transaction t JOIN (SELECT transaction_id, sum(quantity * unit_price) AS amount FROM transaction_line GROUP BY 1) l USING (transaction_id);
\echo '== a rejected row leaves nothing behind: the source identifiers of rows rejected in load 2 that exist as transactions'
SELECT t.source_transaction_id FROM transaction t WHERE t.source_transaction_id IN ('EV443-0013','EV443-0014','EV443-0015','EV443-0016','EV443-0017','EV443-0018','EV443-0019','EV443-0020','EV443-0021');
\echo '== stock is taken by accepted rows only: store 1 product 1 (seed 12) sold 2 in load 1; store 1 product 2 (seed 13) untouched, the 500-unit row was refused; store 2 product 2 (seed 14) sold 1'
SELECT store_id, product_id, quantity_on_hand FROM inventory WHERE (store_id, product_id) IN ((1, 1), (1, 2), (2, 2)) ORDER BY 1, 2;
\echo '== the load records: received = accepted + rejected, and the stored rejection rows match the rejected count'
SELECT l.load_id, l.filename, l.received_count AS received, l.accepted_count AS accepted, l.rejected_count AS rejected,
       l.received_count = l.accepted_count + l.rejected_count AS reconciles,
       (SELECT count(*) FROM sales_load_rejection r WHERE r.load_id = l.load_id) AS rejection_rows
  FROM sales_load l ORDER BY l.load_id;
```

</details>

<details><summary><code>f8_roles.py</code> (needs the sign-in helper <code>mq.py</code>, listed in <a href="f7-04-f7-05-migration-and-matrix.md">F7-04 and F7-05</a>)</summary>

```python
from mq import Client
ROLES = {"ADMIN": "admin@mosaiq-demo.com", "ANALYST": "user14@mosaiq-demo.com", "STORE_MANAGER": "user15@mosaiq-demo.com",
         "MARKETING": "user10@mosaiq-demo.com", "INVENTORY_PLANNER": "user11@mosaiq-demo.com",
         "AUDITOR": "user12@mosaiq-demo.com", "CUSTOMER": "user13@mosaiq-demo.com"}
anon = Client()
s, h, _ = anon.get("/admin/sales-import/")
print("anonymous    GET /admin/sales-import/ ->", s, h.get("Location"))
for role, email in ROLES.items():
    c = Client(); c.login(email)
    g = c.get("/admin/sales-import/")[0]
    g2 = c.get("/admin/sales-import/1")[0]
    g3 = c.get("/admin/sales-import/1/rejections.csv")[0]
    p = c.post("/admin/sales-import/", {}, token_from="/")[0]
    print(f"{role:18} GET index {g}  GET report {g2}  GET rejections.csv {g3}  POST upload {p}")
c = Client(); c.login("admin@mosaiq-demo.com")
s, h, t = c.request("POST", "/admin/sales-import/", {})
print("ADMIN POST without a CSRF token ->", s)
s, h, t = c.request("POST", "/admin/sales-import/", {"csrf_token": "forged"})
print("ADMIN POST with a forged CSRF token ->", s)
```

</details>
