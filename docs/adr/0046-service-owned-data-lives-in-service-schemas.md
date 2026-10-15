# ADR-0046 — Service-owned data lives in service schemas

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The distributed stage adds tables the first delivery never had: a store's location, staff
check-ins and their reviews, a phone sale's provenance, and one outbox per event producer
([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).
They must live in the one `retail` database (ADR-0034), and `public` is the wrong place for two
reasons the repository already enforces:

- **`public` hands every new table to the web.** `sql/00_create_database.sql` runs
  `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO retail_app`,
  so the frozen web could write any new table there; a forgotten `REVOKE` fails silently.
- **CI requires the opposite.** The `APPLICATION ROLE VERIFICATION` block, run as `retail_app`, fails
  on any ordinary `public` table without web DML, except seven tables each exempted by an ADR.

Also: the CI 30-row loop reads only `public`, and `sql/seed-exempt.txt` holds bare names; `fn_audit`
records `TG_TABLE_NAME` without its schema; every `public` table is the web's, the 27 legacy ones and
any the analytics gaps of #405 add, such as AN-04's notification table. What is uncertain is
how a reviewer reads a model spanning schemas, as the graded 4NF argument now covers more relations.

## Decision

Every table owned by a service that the first delivery did not already have lives in a schema named
for its boundary, never in `public`: `stores`, `checkins` and `sales`, plus one `<suffix>_events`
schema per outbox owner (the eight listed in ADR-0044). `public` holds only web tables: the 27 legacy
tables, unchanged, plus any web-only table the analytics gaps of #405 add (AN-04's notification
table). No `svc_*` role holds any privilege on a `public` table outside the legacy 27. `retail_app`
gets no USAGE on any schema except `public`, so the web cannot even name a service table. All DDL
stays in `sql/01_schema.sql`, one section per boundary, and all seed rows in
`sql/02_seed_30_per_table.sql`. The 30-row rule applies to every schema; an exempt table is named in
`sql/seed-exempt.txt` by its schema-qualified name, with its reason.

### The tables

| Table | Owner (writer) | Seed | Columns (key first) |
|---|---|---|---|
| `stores.store_location` | stores-service (`svc_stores`) | 30 rows, one per seeded store | `store_id` PK, FK `public.store` ON DELETE CASCADE; `latitude` (−90 to 90); `longitude` (−180 to 180); `geofence_radius_m` (25 to 2000); `set_by` FK `public.app_user`; `set_at` |
| `checkins.store_check_in` | check-ins-service (`svc_checkins`) | 30 rows | `check_in_id` uuid PK; `client_check_in_id` uuid UNIQUE; `user_id` FK `public.app_user` ON DELETE RESTRICT; `store_id` FK `public.store` ON DELETE RESTRICT; `latitude`, `longitude`, `accuracy_m` (null when there is no fix); `distance_m` (null when there is no fix or no location); `reason` in `within_geofence`, `outside_geofence`, `low_accuracy`, `store_location_unknown`, `no_fix`; `occurred_at`; `recorded_at` |
| `checkins.check_in_review` | check-ins-service (`svc_checkins`), append-only | 30 rows | `review_id` PK; `check_in_id` FK `checkins.store_check_in` ON DELETE RESTRICT; `verified`; `reason` (free text, required); `reviewed_by` FK `public.app_user` ON DELETE RESTRICT; `reviewed_at` |
| `sales.sale_capture` | sales-service (`svc_sales`) | Exempt: runtime state | `transaction_id` PK, FK `public.transaction` ON DELETE CASCADE; `request_sha256` (32 bytes); `recorded_by` FK `public.app_user` ON DELETE RESTRICT; `captured_at` |
| `<suffix>_events.outbox`, for the eight outbox owners (ADR-0044) | `definer_<suffix>` (`definer_stock` for stock) inserts through the capture trigger; the boundary's `svc_*` role relays and purges | Exempt: runtime state | `event_id` uuid PK; `stream`; `event_type`; `entity_id`; `occurred_at`; `relayed_at` (ADR-0044) |

`stores.store_location` is audited by `fn_audit`, as `store` is (RN-28), under the entity `store_location`.

### Normalization notes

Each relation is in BCNF, and none holds two independent multivalued dependencies, so each is in 4NF:
- **`stores.store_location`:** determinant `store_id`; a lossless 1:1 split of `store`, kept apart
  for a different writer, because it is optional, and to leave `public.store` unchanged.
- **`checkins.store_check_in`:** candidate keys `check_in_id` and `client_check_in_id`. No `verified`
  column (it would depend transitively on `reason`); `distance_m` and `reason` are fixed at
  `recorded_at`; no current-status column, which would update an append-only history.
- **`checkins.check_in_review`:** reviews are a multivalued fact of their own; `review_id` determines a review.
- **`sales.sale_capture`:** determinant `transaction_id`, a 1:1 split for phone sales only. The
  `Idempotency-Key` is not stored, being the suffix of `source_transaction_id`
  ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)).
- **The outboxes:** `event_id` determines every column; one outbox per owner keeps one writer per table.

### Schemas and privileges

Each schema is created in `sql/01_schema.sql` and owned by the role that runs the scripts. USAGE
goes to the boundary's own `svc_*` role, and on a `<suffix>_events` schema also to its definer
(`definer_stock` for stock); nobody else gets it. Other boundaries read only through `api_*`
schemas, which hold views and functions, never tables. PUBLIC gets no USAGE on a new schema, so
leaving `retail_app` out needs no `REVOKE`. A table `fn_audit` audits has a name no schema reuses.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| New tables in `public`, each followed by `REVOKE ALL ... FROM retail_app`, with the CI verification block's exception list extended to match | The safe outcome would depend on remembering a `REVOKE` after every `CREATE`. A forgotten one gives the frozen web full DML on service data, and no check notices. The web's privilege policy, which is the web's own assertion, would also grow an exception for every service table |
| New tables in `public`, leaving the web its default DML | Every new table would become S+W by accident. The web has no code for these tables, but any defect or hand-written query in it could write a check-in or a store location, and "the only service writer" would hold for none of them |
| One schema shared by every service, such as `svc` | Ownership would rest on table grants alone. The schema name would no longer say which boundary a table belongs to, and a USAGE grant could not be read as one boundary's reach. Eight tables named `outbox` would also need eight different names |
| Move every boundary's tables into schemas, legacy ones included (`public.product` to `products.product`) | Breaks the frozen web. It names its tables unqualified and resolves them through `public`. Bridging that with views in `public`, or with a web change, would rewrite a working, graded system for tidiness |
| Change `ALTER DEFAULT PRIVILEGES` so that `public` no longer grants `retail_app` | The 27 legacy tables would then need their grants written out one by one, and the CI verification block would still require web DML on every table in `public`. It changes the web's privilege model in order to add tables the web never uses |
| A separate database per service | Rejected in ADR-0034: one DDL file, one VM, and the web must see a phone sale. Foreign keys to `store`, `app_user` and `transaction` cannot cross databases |

## Consequences

**What this makes easy.**
- PostgreSQL enforces the web's isolation; `public`, the verification block and the web's code hold.
- A table's schema names its owning boundary, and `deploy/check-schema-drift.sh` already dumps every
  schema, so drift in a service table is caught with no change.

**What this makes hard.**
- **Every query must be schema-qualified;** an unqualified `store_location` fails, loudly but late.
- **Two exemption formats:** bare names for `public`, `schema.table` otherwise; a bare `outbox` exempts nothing.
- **The model spans schemas:** `docs/data-model.md`'s diagram, dictionary and 4NF argument cover them.
- **Some service data is invisible to the web, by design:** locations and check-ins show only in clients.
- **The web still cascades into service data:** a web store delete removes its `store_location` row,
  and `web/services/catalog.py` refuses, as "in use", a store with check-ins.
- **The outboxes hold the seed's own events** after a fresh build, published on the relay's first
  start; harmless, since consumers only invalidate caches, and why outboxes are exempt.
- **Seed rows must be plausible:** check-ins by seeded managers or admins, reasons that fit the locations.

**What must now be true elsewhere.**
- `sql/01_schema.sql` keeps the legacy sections as they are and adds one marked, idempotent section
  per boundary (ADR-0040 re-applies them in CI): `CREATE SCHEMA`, tables, `api_<suffix>` objects,
  triggers, grants. The verification block stays last, gaining the USAGE assertion below and
  ADR-0039's namespace probe.
- `sql/02_seed_30_per_table.sql` seeds the three tables with 30 schema-qualified rows each.
  `sql/seed-exempt.txt` gains `sales.sale_capture` and the eight `<suffix>_events.outbox` lines, named
  as in ADR-0044, as runtime state. `ci.yml`'s 30-row loop becomes the one below.
- `docs/data-model.md` §2.4 records the full normalization notes (from this record and its full
  draft), the columns and the schemas; `contracts/db/grants.csv` lists the USAGE rows.

## Compliance

The 30-row loop in `.github/workflows/ci.yml` covers every schema and quotes catalog identifiers
with `format('%I.%I')`:
```bash
q() { psql -v ON_ERROR_STOP=1 -d retail -tAc "$1"; }
failed=0
while IFS='|' read -r label rel; do
  [ -z "$label" ] && continue
  if grep -qxF "$label" sql/seed-exempt.txt; then echo "$label: exempt"; continue; fi
  n=$(q "SELECT count(*) FROM $rel;")
  if [ "$n" -lt 30 ]; then echo "$label has $n rows, below the 30-row minimum"; failed=1; fi
done < <(q "SELECT CASE WHEN schemaname = 'public' THEN tablename
                        ELSE schemaname || '.' || tablename END,
                   format('%I.%I', schemaname, tablename)
              FROM pg_tables
             WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
             ORDER BY 1;")
[ "$failed" -eq 0 ]
```
The assertion added to the `APPLICATION ROLE VERIFICATION` block, which CI runs as `retail_app`:
```sql
IF EXISTS (
    SELECT FROM pg_namespace
    WHERE nspname NOT IN ('public', 'information_schema')
      AND nspname NOT LIKE 'pg\_%'
      AND has_schema_privilege(oid, 'USAGE')
) THEN
    RAISE EXCEPTION 'Application role can reach a schema other than public';
END IF;
RAISE NOTICE 'PASS: no service schema is reachable by the application role';
```
As the database owner after the three scripts, every query returns zero rows:
```sql
-- 1. No svc_* role holds a privilege on a public table outside the 27 legacy tables.
SELECT c.relname, r.rolname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN pg_roles r
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p')
  AND r.rolname LIKE 'svc\_%'
  AND c.relname NOT IN ('role', 'channel', 'category', 'store', 'segment_rule', 'segment_label',
        'segment', 'app_user', 'app_session', 'customer', 'customer_preferred_channel',
        'customer_interest_category', 'segmentation_run', 'customer_segment_history', 'product',
        'transaction', 'transaction_line', 'sales_load', 'sales_load_rejection', 'campaign',
        'experiment', 'experiment_group', 'experiment_assignment', 'experiment_exposure',
        'experiment_conversion', 'inventory', 'audit_log')
  AND (has_table_privilege(r.oid, c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR has_any_column_privilege(r.oid, c.oid, 'SELECT, INSERT, UPDATE, REFERENCES'));
-- 2. api_* schemas hold no tables.
SELECT schemaname, tablename FROM pg_tables WHERE schemaname LIKE 'api\_%';
-- 3. An audited table's name is unique across schemas, so audit_log.entity stays unambiguous.
SELECT c.relname FROM pg_class c
 WHERE c.relkind IN ('r', 'p')
   AND c.relname IN (SELECT tc.relname FROM pg_trigger t JOIN pg_class tc ON tc.oid = t.tgrelid
                       JOIN pg_proc p ON p.oid = t.tgfoid WHERE p.proname = 'fn_audit')
 GROUP BY c.relname HAVING count(*) > 1;
```
From the repository root, each of these must print nothing:
```bash
# Only runtime state (sales.sale_capture or an outbox) may be exempt among service tables.
grep -vE '^\s*(#|$)' sql/seed-exempt.txt | grep -F . \
  | grep -vxE 'sales\.sale_capture|[a-z]+_events\.outbox'
# No DDL outside sql/01_schema.sql.
grep -rniE '^\s*(create|alter|drop)\s+(table|schema)\b' services/ packages/ bench/ deploy/
```
