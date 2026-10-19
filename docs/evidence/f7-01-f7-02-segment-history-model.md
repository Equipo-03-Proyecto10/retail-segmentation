# F7-01 and F7-02 — The stable label vocabulary and the segment history

Evidence for F7-01 (#203) and F7-02 (#204), which were closed with no file here.
F7-01 adds the ordered label vocabulary the database constrains every
assignment to. F7-02 replaces `customer.current_segment_id` with durable runs
and one history row per customer per run, with a single open row per customer.

Nothing in the application was changed to produce this evidence, and nothing
here is a screen: both stories are schema and write-path stories. Their effect
on a page is shown in [F7-03](f7-03-run-history.md) (the run list and detail)
and [F7-06 and F7-07](f7-06-f7-07-migration-explanation-and-timeline.md) (the
customer timeline, which reads the open history row).

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`.

```text
docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up --build -d
```

That is PostgreSQL 16.14 (the `postgres:16` image) created from an empty volume,
the web application under gunicorn, and NGINX 1.27.5 in front of it. The clean run
of the three scripts in order, each under `ON_ERROR_STOP=1`, is the init log of
a disposable container started from the same image, the same `docker/initdb.sh`
and the same three scripts (it was removed afterwards; it logged no `ERROR` and
seeded 30 runs, 6 labels and 30 customers):

```text
$ docker run -d --name ev443-clean -e POSTGRES_PASSWORD=postgres -e APP_DB_PASSWORD=retail_app \
      -v ./sql:/opt/sql:ro -v ./docker/initdb.sh:/docker-entrypoint-initdb.d/initdb.sh:ro postgres:16
$ docker logs ev443-clean 2>&1 | grep '^initdb: '
initdb: 00_create_database.sql
initdb: 01_schema.sql
initdb: 02_seed_30_per_table.sql
initdb: done — retail is created, schemad and seeded.
```

What was added to the stack's throwaway database, and nothing else:

| What | How | Why |
|---|---|---|
| Customers 31 *Evidence Newcomer* and 32 *Evidence No Sales* | `INSERT INTO customer` in `psql` | A customer scored for the first time, and one with no sales at all |
| One sale for customer 31 | The sales CSV import page (see [F8-01 and F8-02](f8-01-f8-02-sales-import.md)) | The sale goes through the real ingestion path |
| Runs 31 to 34 | The **Segment run** page as the administrator: 31 RFM_RULES over 180 days, 32 K-means (k = 6, seed 17), 33 RFM_RULES over 14 days, 34 K-means (k = 6, seed 17) again | Runs 1 to 30 are the seed; these add an executor, a K-means run and a run with unassigned customers |
| `scratch443`, a second database in the same container | `pg_dump retail \| psql scratch443` | The two tests that must commit (two concurrent writers, a forced failure) run there, never on `retail` |

Output below is verbatim from `psql`, with blank lines and the `BEGIN` and
`ROLLBACK` echoes removed. The scripts are at the end of the file.

## F7-01 — the label vocabulary (#203)

| Acceptance criterion | Proof |
|---|---|
| An empty PostgreSQL, the three scripts in order, then the vocabulary table exists with a unique code and a unique ordinal position | The init log above, then `\d segment_label` below: `label_code` is the primary key and `ordinal_position` has its own unique constraint |
| A label outside the vocabulary is refused by the database, not only by the application | The foreign key refusing `VIP` for `retail_app`, below |
| The seed loads the vocabulary, and the table is in `sql/seed-exempt.txt` with a reason if its real domain is smaller than thirty | Six rows below; `segment_label` is named in `sql/seed-exempt.txt` with the reason quoted below |
| The 4NF argument in `docs/data-model.md` covers the table | See the last paragraph of this section |

```text
$ psql -h 127.0.0.1 -U retail_app -d retail -f f7_01.sql
== the vocabulary table, its keys and its seed
                        Table "public.segment_label"
      Column      |          Type          | Collation | Nullable | Default 
------------------+------------------------+-----------+----------+---------
 label_code       | character varying(40)  |           | not null | 
 ordinal_position | smallint               |           | not null | 
 name             | character varying(80)  |           | not null | 
 description      | character varying(255) |           |          | 
Indexes:
    "segment_label_pkey" PRIMARY KEY, btree (label_code)
    "segment_label_ordinal_position_key" UNIQUE CONSTRAINT, btree (ordinal_position)
Check constraints:
    "segment_label_ordinal_position_check" CHECK (ordinal_position > 0)
Referenced by:
    TABLE "campaign" CONSTRAINT "campaign_label_code_fkey" FOREIGN KEY (label_code) REFERENCES segment_label(label_code) ON DELETE RESTRICT
    TABLE "customer_segment_history" CONSTRAINT "customer_segment_history_label_code_fkey" FOREIGN KEY (label_code) REFERENCES segment_label(label_code) ON DELETE RESTRICT
    TABLE "segment" CONSTRAINT "segment_label_code_fkey" FOREIGN KEY (label_code) REFERENCES segment_label(label_code) ON DELETE RESTRICT
 label_code  | ordinal_position |    name     
-------------+------------------+-------------
 CHAMPION    |                1 | Champion
 LOYAL       |                2 | Loyal
 POTENTIAL   |                3 | Potential
 AT_RISK     |                4 | At risk
 HIBERNATING |                5 | Hibernating
 LOST        |                6 | Lost
(6 rows)
== a duplicate ordinal position is refused
ERROR:  duplicate key value violates unique constraint "segment_label_ordinal_position_key"
DETAIL:  Key (ordinal_position)=(1) already exists.
== a duplicate label code is refused
ERROR:  duplicate key value violates unique constraint "segment_label_pkey"
DETAIL:  Key (label_code)=(CHAMPION) already exists.
== a label outside the vocabulary cannot be written to history (as retail_app, not only by the application)
INSERT 0 1
ERROR:  insert or update on table "customer_segment_history" violates foreign key constraint "customer_segment_history_label_code_fkey"
DETAIL:  Key (label_code)=(VIP) is not present in table "segment_label".
== the identical insert with a label that is in the vocabulary is accepted
INSERT 0 1
INSERT 0 1
  ?column?   
-------------
 inserted: 1
(1 row)
```

Every write in that script ran inside `BEGIN … ROLLBACK`. The refused insert
used a closed 1990 interval so that only the label could fail; with an open
interval the history table's overlap constraint answers first (see F7-02).

```text
$ sed -n '/Six ordered/,/^segment_label/p' sql/seed-exempt.txt
# Six ordered business labels, best to worst — ADR-0018. A seventh would be a
# business decision about how many segments the company recognizes, not seed
# data; padding this table would misrepresent the vocabulary as larger than
# it is.
segment_label
```

The normalization decision is in [`../data-model.md`](../data-model.md): the
`segment_label` entry of the data dictionary (§3) records why the table has a
single-column natural key (`label_code` is what every consumer reads and
compares, per ADR-0018) and why `ordinal_position` is unique on its own, and
§4's delete rules record `RESTRICT` on the vocabulary. §2.4 argues the run and
history split (F7-02, below). I read the vocabulary as trivially in 4NF, since
it has one key and no multivalued fact, but §2.4 does not say that sentence about
`segment_label` in particular; a reviewer who wants it written there should say
so.

## F7-02 — runs and assignment history (#204)

| Acceptance criterion | Proof |
|---|---|
| `segmentation_run` and `customer_segment_history` exist and `customer.current_segment_id` does not | Catalog queries below |
| A second run closes the previous open row, and exactly one open row remains | Customer 10 across runs 32, 33 and 34, and a count over all 32 customers, below |
| Two concurrent writers: the partial unique index refuses the second | Two `psql` sessions on `scratch443`, below. With the schema as committed the overlap constraint added later by #350 answers first; the partial index refuses once it is the only guard |
| A run that fails partway leaves neither the run row nor any assignment | A real run forced to fail after its assignments were written, below |
| A customer with no sales in the window is recorded as unassigned | Run 33's 17 unassigned rows, below |
| Current segment is read from the open history row, and `grep -rn 'current_segment_id' sql/ web/ tests/` prints nothing | The grep below; the page that shows it is the customer timeline in [F7-07](f7-06-f7-07-migration-explanation-and-timeline.md) |

```text
$ psql -U postgres -d retail -f f7_02.sql
== the tables exist and customer.current_segment_id does not
 segmentation_run | customer_segment_history 
------------------+--------------------------
 segmentation_run | customer_segment_history
(1 row)
 current_segment_id_columns 
----------------------------
                          0
(1 row)
== a second run closes the previous open row: customer 10 across runs 32, 33 and 34
 run_id | label_code |          valid_from           |           valid_to            
--------+------------+-------------------------------+-------------------------------
     32 | CHAMPION   | 2026-10-19 01:38:57.728148+00 | 2026-10-19 01:39:03.670265+00
     33 | AT_RISK    | 2026-10-19 01:39:03.670265+00 | 2026-10-19 01:41:31.169159+00
     34 | CHAMPION   | 2026-10-19 01:41:31.169159+00 | 
(3 rows)
== every customer has exactly one open row (customers whose count of open rows is not 1)
 customer_id | open_rows 
-------------+-----------
(0 rows)
 customers | with_exactly_one_open_row 
-----------+---------------------------
        32 |                        32
(1 row)
== each run closed the previous rows at the instant it opened its own (valid_to of run N = valid_from of run N+1)
 gaps_or_overlaps_between_consecutive_runs 
-------------------------------------------
                                         0
(1 row)
== the partial unique index refuses a second open row (single session)
Index "public.ux_customer_segment_history_open"
   Column    | Type | Key? | Definition  
-------------+------+------+-------------
 customer_id | uuid | yes  | customer_id
unique, btree, for table "public.customer_segment_history", predicate (valid_to IS NULL)
INSERT 0 1
ERROR:  conflicting key value violates exclusion constraint "ex_customer_segment_history_no_overlap"
DETAIL:  Key (customer_id, tstzrange(valid_from, valid_to))=(00000000-0000-0000-0000-000000000010, ["2026-10-19 14:09:09.09787+00",)) conflicts with existing key (customer_id, tstzrange(valid_from, valid_to))=(00000000-0000-0000-0000-000000000010, ["2026-10-19 01:41:31.169159+00",)).
== a customer with no sales in the window is recorded as unassigned, not skipped (run 33, 14-day window)
 customers_in_run | unassigned_rows | unassigned_with_no_values 
------------------+-----------------+---------------------------
               32 |              17 |                        17
(1 row)
 customers_in_the_database | customer_count_recorded_on_run_33 
---------------------------+-----------------------------------
                        32 |                                32
(1 row)
```

```text
$ grep -rn current_segment_id sql/ web/ tests/
(no output; exit status 1)
```

The single-session insert of a second open row was refused by
`ex_customer_segment_history_no_overlap`, not by the partial index, for the
reason given under the concurrency test below.

**Two concurrent writers.** A copy of the database, a probe customer with no
history, and two sessions as `retail_app`. Session A opens a run and an open row
and holds its transaction for three seconds. Session B starts one second later
and tries the same. `\timing` on B shows it waiting about two seconds, for A to
commit, and then being refused. The two sessions' output interleaves; the
timestamps order it.

```text
$ ./conc.sh      (schema as committed, on a fresh copy of the database)
A  14:09:37.276 opens a run and an open row for the probe customer
A  14:09:40.287 committed
Time: 0.189 ms
B  14:09:38.260 tries to open a second open row for the same customer
Time: 0.657 ms
Time: 0.648 ms
ERROR:  conflicting key value violates exclusion constraint "ex_customer_segment_history_no_overlap"
DETAIL:  Key (customer_id, tstzrange(valid_from, valid_to))=(00000000-0000-0000-0000-000000000033, ["2026-10-19 14:09:38.260313+00",)) conflicts with existing key (customer_id, tstzrange(valid_from, valid_to))=(00000000-0000-0000-0000-000000000033, ["2026-10-19 14:09:37.276132+00",)).
Time: 2025.538 ms
Time: 0.211 ms
Time: 0.095 ms
open rows for the probe customer afterwards: 1
```

PostgreSQL checks a table's indexes in the order they were created, and the
overlap constraint `ex_customer_segment_history_no_overlap` (added by #350,
ADR-0029, after this story) is older than `ux_customer_segment_history_open`
(their index OIDs, in that order, are 26736 and 26805 on this copy), so it names the
refusal here. On an earlier copy of the database where the overlap constraint had
been dropped and added back, so that it was the newer index, the partial index
answered instead. The acceptance criterion names the partial index, so the test
was repeated with the overlap constraint dropped:

```text
$ psql -d scratch443 -c "ALTER TABLE customer_segment_history DROP CONSTRAINT ex_customer_segment_history_no_overlap"
$ ./conc.sh
Time: 0.133 ms
B  14:09:41.872 tries to open a second open row for the same customer
Time: 0.488 ms
Time: 0.656 ms
ERROR:  duplicate key value violates unique constraint "ux_customer_segment_history_open"
DETAIL:  Key (customer_id)=(00000000-0000-0000-0000-000000000033) already exists.
Time: 2000.533 ms
Time: 0.174 ms
Time: 0.141 ms
A  14:09:40.861 opens a run and an open row for the probe customer
A  14:09:43.874 committed
open rows for the probe customer afterwards: 1
```

(The constraint was added back afterwards; `scratch443` is dropped at the end.)
So the second writer is always refused by the database after waiting for the
first; which of the two guards says so depends on index order, and the partial
index is the one that matters if the overlap constraint is ever removed.

**A run that fails partway.** `segmentation.run` against `scratch443`, with
`insert_assignments` replaced by a wrapper that performs the real insert and
then raises. By then the previous open rows had been closed, so unchanged counts
also show those closes were rolled back:

```text
$ python f7_rollback.py
before:  runs=36 history_rows=1028 open_rows=32 last_run=48
run raised: simulated failure after the assignments were written
after:   runs=36 history_rows=1028 open_rows=32 last_run=48
unchanged: True
a successful run afterwards: run_id=51, runs=37 history_rows=1061 open_rows=33
```

`last_run` is the largest run id still present. The next run is numbered later
than that plus one: identity values taken by rolled-back inserts (the refused
writer's run row above, and the failed attempt here) are not given back.

## Tests

```text
$ pytest tests/test_seed_consistency.py tests/test_history_protection.py tests/test_segments_db.py \
         tests/test_segments_pipeline_db.py tests/test_segmentation_pipeline.py tests/test_segment_run.py
114 passed in 0.76s
```

The whole suite, `pytest -q` on the repository, was
`3040 passed, 14 skipped in 51.95s`. The criteria map to named tests:
`test_a_failed_write_rolls_back_the_run_with_its_assignments`,
`test_every_customer_in_the_run_has_their_open_row_closed`,
`test_an_unassigned_customer_is_a_valid_assignment`,
`test_one_customers_intervals_cannot_overlap`,
`test_a_history_row_can_only_be_closed_once` and
`test_a_run_is_insert_only_for_the_application_role`.

## What was not run

- The tests marked `postgres` (`tests/test_check_grants.py`,
  `tests/test_create_database_script.py`, `tests/test_outbox_template.py`,
  `tests/test_sales_namespace_guard.py`) were skipped: they need an empty
  scratch cluster and a database nothing else has touched, which CI provides
  and this run did not.
- The two-session test and the forced failure ran on a copy, `scratch443`, of
  the database after the runs above. Nothing was run on the Compute Engine
  instance.
- The failure of a run was simulated by raising after the writes; no genuine
  database fault was provoked.

## The scripts

<details><summary><code>f7_01.sql</code></summary>

```sql
\pset pager off
\echo '== the vocabulary table, its keys and its seed'
\d segment_label
SELECT label_code, ordinal_position, name FROM segment_label ORDER BY ordinal_position;
\echo
\echo '== a duplicate ordinal position is refused'
BEGIN;
INSERT INTO segment_label (label_code, ordinal_position, name) VALUES ('VIP', 1, 'Very important');
ROLLBACK;
\echo '== a duplicate label code is refused'
BEGIN;
INSERT INTO segment_label (label_code, ordinal_position, name) VALUES ('CHAMPION', 99, 'Champion again');
ROLLBACK;
\echo '== a label outside the vocabulary cannot be written to history (as retail_app, not only by the application)'
BEGIN;
INSERT INTO segmentation_run (method, window_days) VALUES ('RFM_RULES', 180);
INSERT INTO customer_segment_history (customer_id, run_id, label_code, valid_from, valid_to)
SELECT '00000000-0000-0000-0000-000000000032', max(run_id), 'VIP', timestamptz '1990-01-01', timestamptz '1990-01-02' FROM segmentation_run;
ROLLBACK;
\echo '== the identical insert with a label that is in the vocabulary is accepted'
BEGIN;
INSERT INTO segmentation_run (method, window_days) VALUES ('RFM_RULES', 180);
INSERT INTO customer_segment_history (customer_id, run_id, label_code, valid_from, valid_to)
SELECT '00000000-0000-0000-0000-000000000032', max(run_id), 'LOST', timestamptz '1990-01-01', timestamptz '1990-01-02' FROM segmentation_run;
SELECT 'inserted: ' || count(*) FROM customer_segment_history WHERE label_code = 'LOST' AND valid_from = timestamptz '1990-01-01';
ROLLBACK;
```

</details>

<details><summary><code>f7_02.sql</code></summary>

```sql
\pset pager off
\echo '== the tables exist and customer.current_segment_id does not'
SELECT to_regclass('public.segmentation_run') AS segmentation_run, to_regclass('public.customer_segment_history') AS customer_segment_history;
SELECT count(*) AS current_segment_id_columns FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'customer' AND column_name = 'current_segment_id';
\echo
\echo '== a second run closes the previous open row: customer 10 across runs 32, 33 and 34'
SELECT h.run_id, h.label_code, h.valid_from, h.valid_to
  FROM customer_segment_history h
 WHERE h.customer_id = '00000000-0000-0000-0000-000000000010' AND h.run_id IN (32, 33, 34)
 ORDER BY h.run_id;
\echo '== every customer has exactly one open row (customers whose count of open rows is not 1)'
SELECT c.customer_id, count(h.history_id) FILTER (WHERE h.valid_to IS NULL) AS open_rows
  FROM customer c LEFT JOIN customer_segment_history h USING (customer_id)
 GROUP BY c.customer_id HAVING count(h.history_id) FILTER (WHERE h.valid_to IS NULL) <> 1;
SELECT count(*) AS customers, count(*) FILTER (WHERE open_rows = 1) AS with_exactly_one_open_row FROM (
  SELECT c.customer_id, count(h.history_id) FILTER (WHERE h.valid_to IS NULL) AS open_rows
    FROM customer c LEFT JOIN customer_segment_history h USING (customer_id) GROUP BY c.customer_id) x;
\echo '== each run closed the previous rows at the instant it opened its own (valid_to of run N = valid_from of run N+1)'
SELECT count(*) AS gaps_or_overlaps_between_consecutive_runs FROM (
  SELECT h.customer_id, h.valid_to, lead(h.valid_from) OVER (PARTITION BY h.customer_id ORDER BY h.valid_from) AS next_from
    FROM customer_segment_history h) x
 WHERE x.valid_to IS NOT NULL AND x.valid_to IS DISTINCT FROM x.next_from;
\echo '== the partial unique index refuses a second open row (single session)'
\d ux_customer_segment_history_open
BEGIN;
INSERT INTO segmentation_run (method, window_days) VALUES ('RFM_RULES', 180);
INSERT INTO customer_segment_history (customer_id, run_id, label_code)
SELECT '00000000-0000-0000-0000-000000000010', max(run_id), 'LOST' FROM segmentation_run;
ROLLBACK;
\echo '== a customer with no sales in the window is recorded as unassigned, not skipped (run 33, 14-day window)'
SELECT count(*) AS customers_in_run, count(*) FILTER (WHERE label_code IS NULL) AS unassigned_rows,
       count(*) FILTER (WHERE label_code IS NULL AND recency_last_purchase_at IS NULL AND frequency_count IS NULL AND monetary_total IS NULL AND r_score IS NULL) AS unassigned_with_no_values
  FROM customer_segment_history WHERE run_id = 33;
SELECT (SELECT count(*) FROM customer) AS customers_in_the_database, (SELECT customer_count FROM segmentation_run WHERE run_id = 33) AS customer_count_recorded_on_run_33;
```

</details>

<details><summary><code>conc.sh</code> and the two sessions</summary>

```sh
#!/bin/sh
# usage: conc.sh   (runs against database scratch443 inside ev443-db-1 as retail_app)
RUN='docker exec -i -e PGPASSWORD=retail_app ev443-db-1 psql -h 127.0.0.1 -U retail_app -d scratch443 -At'
D=<scripts>/sql
docker exec -i ev443-db-1 psql -q -U postgres -d scratch443 < $D/conc_prep.sql
$RUN < $D/conc_a.sql 2>&1 | grep -v '^$' &
sleep 1
$RUN < $D/conc_b.sql 2>&1 | grep -v '^$\|^BEGIN\|^ROLLBACK\|^INSERT'
wait
docker exec ev443-db-1 psql -U postgres -d scratch443 -Atc "select 'open rows for the probe customer afterwards: ' || count(*) from customer_segment_history where customer_id='00000000-0000-0000-0000-000000000033' and valid_to is null"
docker exec ev443-db-1 psql -U postgres -d scratch443 -Atc "delete from customer_segment_history where customer_id='00000000-0000-0000-0000-000000000033'" >/dev/null
```

```sql
-- conc_prep.sql
INSERT INTO customer (customer_id, name, email, registration_channel_id, registered_on)
VALUES ('00000000-0000-0000-0000-000000000033', 'Concurrency Probe', 'concurrency.probe@example.com', 2, '2026-09-01')
ON CONFLICT DO NOTHING;
DELETE FROM customer_segment_history WHERE customer_id = '00000000-0000-0000-0000-000000000033';
-- conc_a.sql
BEGIN;
SELECT 'A  ' || to_char(clock_timestamp(), 'HH24:MI:SS.MS') || ' opens a run and an open row for the probe customer' AS step;
INSERT INTO segmentation_run (method, window_days) VALUES ('RFM_RULES', 180);
INSERT INTO customer_segment_history (customer_id, run_id, label_code)
SELECT '00000000-0000-0000-0000-000000000033', max(run_id), 'LOYAL' FROM segmentation_run;
SELECT pg_sleep(3);
COMMIT;
SELECT 'A  ' || to_char(clock_timestamp(), 'HH24:MI:SS.MS') || ' committed' AS step;
-- conc_b.sql
\set ON_ERROR_STOP off
\timing on
BEGIN;
SELECT 'B  ' || to_char(clock_timestamp(), 'HH24:MI:SS.MS') || ' tries to open a second open row for the same customer' AS step;
INSERT INTO segmentation_run (method, window_days) VALUES ('RFM_RULES', 180);
INSERT INTO customer_segment_history (customer_id, run_id, label_code)
SELECT '00000000-0000-0000-0000-000000000033', max(run_id), 'LOST' FROM segmentation_run;
SELECT 'B  ' || to_char(clock_timestamp(), 'HH24:MI:SS.MS') || ' continued after the statement above' AS step;
ROLLBACK;
```

</details>

<details><summary><code>f7_rollback.py</code></summary>

```python
"""F7-02: a run that fails partway leaves neither the run row nor any of its assignments behind."""
import sys
sys.path.insert(0, "<repo>")
import psycopg
from web.services import segmentation

URL = "postgresql://retail_app:retail_app@127.0.0.1:5432/scratch443"
conn = psycopg.connect(URL)

def counts():
    with conn.cursor() as cur:
        cur.execute("SELECT (SELECT count(*) FROM segmentation_run), (SELECT count(*) FROM customer_segment_history), "
                    "(SELECT count(*) FROM customer_segment_history WHERE valid_to IS NULL), (SELECT max(run_id) FROM segmentation_run)")
        row = cur.fetchone()
    conn.rollback()
    return row

before = counts()
print("before:  runs=%d history_rows=%d open_rows=%d last_run=%d" % before)

real_insert = segmentation.insert_assignments
def insert_then_fail(connection, run_id, rows, at):
    real_insert(connection, run_id, rows, at=at)          # the assignments really are written ...
    raise RuntimeError("simulated failure after the assignments were written")  # ... then the run fails
segmentation.insert_assignments = insert_then_fail
try:
    segmentation.run(conn, 180)
except RuntimeError as error:
    print("run raised:", error)
finally:
    segmentation.insert_assignments = real_insert
after = counts()
print("after:   runs=%d history_rows=%d open_rows=%d last_run=%d" % after)
print("unchanged:", before == after)

# the same code path, not failing, for contrast
result = segmentation.run(conn, 180)
final = counts()
print("a successful run afterwards: run_id=%s, runs=%d history_rows=%d open_rows=%d" % (result.run_id, final[0], final[1], final[2]))
```

</details>
