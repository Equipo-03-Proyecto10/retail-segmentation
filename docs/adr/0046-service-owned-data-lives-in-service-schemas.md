# ADR-0046 — Service-owned data lives in service schemas

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The distributed stage adds tables the first delivery never had. Some hold a
service's own facts: a store's location, staff check-ins and their reviews,
and the provenance of a phone sale. The others are one transactional outbox
per event producer
([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).
They must live somewhere in the one `retail` database
([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)),
and the obvious place, `public`, is the wrong one for two reasons the
repository already enforces:

- **`public` hands every new table to the web.** `sql/00_create_database.sql`
  runs, before the schema exists:

  ```sql
  ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO retail_app;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO retail_app;
  ```

  Any table the scripts create in `public` is therefore readable and writable
  by the frozen web the moment it exists. Keeping a service table from the web
  would take a `REVOKE` after every `CREATE`. Forgetting one fails silently,
  because nothing errors when the web gains a privilege it never uses.
- **CI requires the opposite.** The step "Application role can write data but
  cannot drop a table" runs the `APPLICATION ROLE VERIFICATION` block of
  `sql/01_schema.sql` as `retail_app`. That block raises
  `Application table privileges differ from the DML-only policy` for any
  ordinary table in `public` on which `retail_app` lacks SELECT, INSERT,
  UPDATE or DELETE. The only exceptions are seven named tables, each exempted by
  an ADR (ADR-0024, ADR-0026, ADR-0027 and ADR-0029). A service table in `public` without web DML fails CI, unless
  the web's privilege policy grows an exception for every service table.

Three more facts shape the answer:

- The CI 30-row loop reads `pg_tables WHERE schemaname = 'public'` and counts
  `public."$tbl"`. `sql/seed-exempt.txt` is matched by exact, bare table
  names. A table outside `public` is invisible to both today.
- `fn_audit` records `TG_TABLE_NAME` as the entity, without its schema. Two
  audited tables with the same name in different schemas would be
  indistinguishable in `audit_log`.
- Every one of the 27 tables in `public` is read or written by the frozen web.
  None can move without a web change.

What is uncertain is how a reviewer reads a model that spans several schemas.
The 4NF justification is a graded deliverable, and it now covers relations the
web never touches.

## Decision

Every table owned by a service that the first delivery did not already have
lives in a schema named for its boundary, never in `public`. Those schemas are
`stores`, `checkins` and `sales`, plus one `<boundary>_events` schema per
outbox owner. `public` keeps exactly the 27 legacy tables, with their columns
and constraints unchanged.
`retail_app` gets no USAGE on any schema except `public`, so the web cannot even
name a service table. All DDL stays in `sql/01_schema.sql`, with one section per
boundary, and all seed rows stay in `sql/02_seed_30_per_table.sql`. The 30-row
rule applies to every schema. A table exempt from it is named in
`sql/seed-exempt.txt` by its schema-qualified name, with its reason.

### The tables

| Table | Owner (writer) | Seed | Columns (key first) |
|---|---|---|---|
| `stores.store_location` | stores-service (`svc_stores`) | 30 rows, one per seeded store | `store_id` PK, FK `public.store` ON DELETE CASCADE; `latitude` (−90 to 90); `longitude` (−180 to 180); `geofence_radius_m` (25 to 2000); `set_by` FK `public.app_user`; `set_at` |
| `checkins.store_check_in` | check-ins-service (`svc_checkins`) | 30 rows | `check_in_id` uuid PK; `client_check_in_id` uuid UNIQUE; `user_id` FK `public.app_user` ON DELETE RESTRICT; `store_id` FK `public.store` ON DELETE RESTRICT; `latitude`, `longitude`, `accuracy_m` (null when there is no fix); `distance_m` (null when there is no fix or no location); `reason` in `within_geofence`, `outside_geofence`, `low_accuracy`, `store_location_unknown`, `no_fix`; `occurred_at`; `recorded_at` |
| `checkins.check_in_review` | check-ins-service (`svc_checkins`), append-only | 30 rows | `review_id` PK; `check_in_id` FK `checkins.store_check_in` ON DELETE RESTRICT; `verified`; `reason` (free text, required); `reviewed_by` FK `public.app_user` ON DELETE RESTRICT; `reviewed_at` |
| `sales.sale_capture` | sales-service (`svc_sales`) | Exempt: runtime state | `transaction_id` PK, FK `public.transaction` ON DELETE CASCADE; `request_sha256` (32 bytes); `recorded_by` FK `public.app_user` ON DELETE RESTRICT; `captured_at` |
| `<boundary>_events.outbox`, for `users`, `products`, `categories`, `stores`, `stock`, `preferences`, `sales` and `segments` | that boundary's `definer_<boundary>` inserts, through the capture trigger; its `svc_*` role relays and purges | Exempt: runtime state | `event_id` uuid PK; `stream`; `event_type`; `entity_id`; `occurred_at`; `relayed_at` (ADR-0044) |

`stores.store_location` is audited by `fn_audit`, as `store` is (RN-28). Its
entity name in `audit_log` is `store_location`.

### Normalization notes

Each relation is in BCNF, and none holds two independent multivalued
dependencies, so each is in 4NF. The reasons are:

- **`stores.store_location`.** Its only determinant is `store_id`. It is a 1:1
  vertical split of `store`, kept separate for three reasons:
  - it has a different writer: stores-service sets it, and only the web writes
    `store`;
  - it is optional: a store with no location has no row, rather than five
    NULL columns;
  - it leaves `public.store` exactly as the frozen web knows it.

  Joining on `store_id` restores the combined relation, so the split is
  lossless.
- **`checkins.store_check_in`.** It has two candidate keys, `check_in_id` and
  the client's `client_check_in_id`, and every other attribute depends on
  each of them.
  - **Verified is not stored.** An automatic check-in is verified exactly when
    `reason = 'within_geofence'`. Storing a `verified` column as well would add
    the transitive dependency `check_in_id → reason → verified`. It is derived
    on read.
  - **`distance_m` and `reason` are not redundant.** They are computed at
    `recorded_at` from the store location in force at that moment. That
    location can change later, so neither value can be derived from the
    current data.
  - **There is no current-status column.** A check-in's effective verdict is
    its latest review if it has one, and its automatic `reason` otherwise.
    Storing that verdict would mean updating a row whose history is meant to
    be append-only.
- **`checkins.check_in_review`.** A check-in has zero or more reviews. That is
  a multivalued fact of the check-in, independent of its own attributes, so
  it is its own relation. Folded into `store_check_in`, it would either allow
  only one review, losing the append-only history, or repeat the check-in's
  facts once per review. `review_id` determines everything in a review. A
  review's `verified` is the reviewer's decision, not a derived value.
- **`sales.sale_capture`.** Its only determinant is `transaction_id`. It is a
  1:1 vertical split of `transaction`, and it exists only for phone sales. It
  is kept separate for three reasons:
  - it has a different writer: only `svc_sales` writes it;
  - it is optional: CSV sales have no row;
  - it holds provenance, not facts of the sale.

  The `Idempotency-Key` is not a column here. It is already the suffix of the
  unique `transaction.source_transaction_id`
  ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)),
  and storing it again would put one fact in two tables.
- **The outboxes.** `event_id` determines every other column. There is one
  outbox per owner, not one shared table. A shared outbox would have eight
  writers and eight relays, against the one-writer-per-table rule of
  ADR-0034.

### Schemas and privileges

- Each schema is created in `sql/01_schema.sql` and owned by the role that runs
  the scripts, as the legacy tables are. No `svc_*` LOGIN role owns a schema
  or a table.
- USAGE goes to the boundary's own `svc_*` role. On a `<boundary>_events`
  schema it also goes to that boundary's `definer_<boundary>`, or to
  `definer_stock` for the stock outbox. Nobody else gets USAGE.
  Other boundaries read only through `api_*` objects (ADR-0044), and those
  schemas hold views and functions, never tables.
- PostgreSQL gives PUBLIC no USAGE on a new schema, and nothing in
  `00_create_database.sql` adds default privileges outside `public`. Leaving
  `retail_app` out needs no `REVOKE`. It is the default.
- A table that `fn_audit` audits has a name that no other schema reuses.

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
- The web's isolation is enforced by PostgreSQL, not by convention.
  `retail_app` gets "permission denied for schema" on any service table.
- `public` stays the legacy model. The existing verification block, the 27-table
  list and the web's code all hold unchanged.
- A table's schema names its owning boundary, so a reviewer reads ownership
  from the name before opening `contracts/db/grants.csv`.
- `deploy/check-schema-drift.sh` already dumps every schema
  (`pg_dump --schema-only` with no `-n`), so drift in a service table on the
  instance is caught with no change to the script.

**What this makes hard.**
- **Every query must be schema-qualified.** A service role's default
  `search_path` is `"$user", public`, so an unqualified `store_location` fails
  with "relation does not exist". That error is loud, but it can surface late.
- **Two exemption formats.** `sql/seed-exempt.txt` keeps bare names for
  `public` and uses `schema.table` for the rest. The CI loop handles both. A
  bare `outbox` line exempts nothing, because the eight outboxes are listed
  one by one.
- **The model spans schemas.** `docs/data-model.md` must show them in its ER
  diagram, its data dictionary and its 4NF argument. That graded justification
  now covers twelve more relations.
- **Some service data is invisible to the web, by design.** Store locations
  and check-ins appear in no web page. The demonstration shows the web only
  data that lands in `public`, such as a phone sale, and shows the rest in the
  clients.
- **The web still reaches service data through cascades.** Deleting a store in
  the web removes its `stores.store_location` row, because PostgreSQL runs the
  cascade as the table's owner. A store that has check-ins cannot be deleted:
  `web/services/catalog.py` maps the `ForeignKeyViolation` to its existing
  "in use" refusal. Neither needs a web privilege on the service schema, and
  the web cannot avoid either.
- **The outboxes are not empty after a fresh build.** The capture triggers fire
  while `02_seed_30_per_table.sql` runs, so each outbox holds the seed's own
  events. The relay publishes them on its first start. That is harmless,
  because every consumer only invalidates a cache, but it is why the outboxes
  are exempt rather than counted.
- **Seed rows must be plausible.**
  - The 30 check-ins belong to seeded `STORE_MANAGER` or `ADMIN` users, at
    seeded stores.
  - Each check-in's `reason` agrees with its distance and accuracy against the
    seeded location.
  - All 30 reviews are by the one administrator.

**What must now be true elsewhere.**
- `sql/01_schema.sql` keeps the legacy sections as they are and adds one
  section per boundary after them. Each section holds its `CREATE SCHEMA`, its
  tables, its `api_<boundary>` objects, its triggers and its grants, in that
  order (ADR-0034). The `APPLICATION ROLE VERIFICATION` block stays last. It
  gains the USAGE assertion below, and ADR-0039's namespace probe.
- `sql/02_seed_30_per_table.sql` seeds `stores.store_location`,
  `checkins.store_check_in` and `checkins.check_in_review` with
  schema-qualified inserts, 30 rows each.
- `sql/seed-exempt.txt` gains `sales.sale_capture` and the eight
  `<boundary>_events.outbox` names, each under a comment that names it as
  runtime state, as `app_session` and `sales_load` are.
- `.github/workflows/ci.yml` replaces the 30-row loop with the one under
  Compliance.
- `docs/data-model.md` records these normalization notes in §2.4, the columns
  in its data dictionary, and the schemas in §4.
- `contracts/db/grants.csv` lists each schema's USAGE rows (ADR-0034).
- `AGENTS.md` keeps "Schema changes go in `sql/01_schema.sql`". It adds that a
  service's new tables go in its boundary's schema, never in `public`, and
  that a new table in `public` needs an ADR superseding this one.

## Compliance

The 30-row loop in `.github/workflows/ci.yml`. It covers every schema, and it
quotes identifiers taken from the catalog with `format('%I.%I')`:

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

The assertion added to the `APPLICATION ROLE VERIFICATION` block, which CI
runs as `retail_app`:

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

Run as the database owner after the three scripts. Every query must return zero
rows:

```sql
-- 1. public holds exactly the 27 legacy tables.
SELECT count(*) FROM pg_tables WHERE schemaname = 'public' HAVING count(*) <> 27;

-- 2. api_* schemas hold no tables.
SELECT schemaname, tablename FROM pg_tables WHERE schemaname LIKE 'api\_%';

-- 3. No LOGIN role owns a schema or a table.
SELECT n.nspname FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner
 WHERE r.rolcanlogin AND r.rolname LIKE 'svc\_%'
UNION ALL
SELECT c.oid::regclass::text FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner
 WHERE r.rolcanlogin AND r.rolname LIKE 'svc\_%';

-- 4. An audited table's name is unique across schemas, so audit_log.entity
--    stays unambiguous.
SELECT c.relname
  FROM pg_class c
 WHERE c.relkind IN ('r', 'p')
   AND c.relname IN (SELECT tc.relname
                       FROM pg_trigger t
                       JOIN pg_class tc ON tc.oid = t.tgrelid
                       JOIN pg_proc p ON p.oid = t.tgfoid
                      WHERE p.proname = 'fn_audit')
 GROUP BY c.relname
HAVING count(*) > 1;
```

From the repository root, each of these must print nothing:

```bash
# A service table may be exempt from the 30-row rule only as runtime state:
# sales.sale_capture or an outbox. Seeded service tables are never exempt.
grep -vE '^\s*(#|$)' sql/seed-exempt.txt | grep -F . \
  | grep -vxE 'sales\.sale_capture|[a-z]+_events\.outbox'

# No DDL outside sql/01_schema.sql.
grep -rniE '^\s*(create|alter|drop)\s+(table|schema)\b' services/ packages/ bench/ deploy/
```
