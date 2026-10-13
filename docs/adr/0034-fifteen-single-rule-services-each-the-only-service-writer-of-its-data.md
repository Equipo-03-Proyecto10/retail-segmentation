# ADR-0034 — Fifteen single-rule services, each the only service that writes its data

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)
records the instructor's requirements for the distributed stage: at least ten
microservices, each implementing one business rule, totally independent, and
never exposing another service's endpoints. The team plans fifteen: thirteen
core and two in a second wave. This record names them and decides who may
write which data.

The earlier draft of this record proposed eight boundaries that imported the
web's modules and read each other's base tables. It fails all four
requirements. A catalog service held two rules (products and categories), a
customers service held three tables with two independent rules, and a grant on
another boundary's base table couples two services to one column layout.

Four facts constrain the answer:

1. **The web system is frozen.** It reads and writes PostgreSQL directly as
   `retail_app`, gets no Redis and consumes no service (Q-12 in
   [`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)).
   Step 5 of the end-to-end demonstration requires that a sale recorded from
   the phone appear in the web with no web change. So the services write the
   same `public` tables the web reads, in the one database `retail`.
2. **The web stays a writer.** `web/db/` writes `app_user`, `product`,
   `category`, `campaign`, `inventory` (the CSV path), `transaction`,
   `transaction_line`, `store`, `channel`, `role` and the segmentation tables.
   It never writes `customer` or either preference table (`web/db/customers.py`
   only reads). "Only service writer" therefore cannot mean "only writer".
3. **Several tables have no service writer at all.** `store`, `channel`,
   `role` and the five segmentation tables are written only by the web.
   Services still need to read them. Without an owner, any service could be
   granted SELECT on them, and nobody would know who breaks when they change.
4. **One VM, one PostgreSQL, one DDL file** (`sql/01_schema.sql`). A database
   per service is not available, so isolation has to come from database roles:
   a service cannot write what it does not own because PostgreSQL refuses it,
   not because its code is careful.

Two parts are judgement calls. Making a service the **read owner** of a table
it never writes is a convention that the grant query below enforces. It is not
a law of the domain. Whether the instructor counts a read-only boundary as
implementing a rule is open (Q-13, see ADR-0043).

## Decision

The distributed stage has **fifteen service boundaries**, listed in the
registry below. Each boundary implements exactly one rule and connects to
PostgreSQL as its own LOGIN role, `svc_<boundary>`. That role holds exactly
the privileges in the ownership matrix below and nothing else. Every base
table that any service touches has **exactly one** `svc_*` role with any
privilege on it. That role is the table's only service writer (S), or its
only service reader when the web is the sole writer (R). A boundary reads
another boundary's data only through the producer's versioned `api_*` objects
([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)),
never through a base-table grant. Writes that must happen with elevated
rights, such as consuming stock, writing the audit log, guarding a delete or
filling an outbox, run in SECURITY DEFINER functions owned by NOLOGIN
`definer_*` roles. No service holds DELETE on `customer`, `product`,
`app_user`, `campaign` or `store`. The exact matrix is committed as
`contracts/db/grants.csv`, and CI compares it with the database in both
directions.

### Registry

| # | Service | Lang | Port | Role | One rule | Top-level resources |
|---|---|---|---|---|---|---|
| 1 | auth | Flask | 5001 | `svc_auth` | A principal is authenticated only by a valid credential of an active account, and every token stays usable only while its session is live (RN-03) | `sessions`, `jwks.json` |
| 2 | users | Flask | 5002 | `svc_users` | Accounts exist under the single-administrator rule: never two, never zero, and moved only by an explicit transfer. Email is unique case-insensitively, and accounts are deactivated, never deleted (RN-01, RN-02, RN-04) | `users`, `roles`, `administrator-transfers` |
| 3 | products | Flask | 5003 | `svc_products` | Product master: unique SKU, price never negative, exactly one existing category, retired by deactivation (RN-10, RN-11) | `products` |
| 4 | categories | Flask | 5004 | `svc_categories` | The category tree has no cycles. A category with products, children or customer interests is never deleted through services (RN-33, RN-06, RN-07) | `categories` |
| 5 | stores | Rust | 5005 | `svc_stores` | Each store has at most one location, with valid coordinates and a 25–2000 m geofence, set by ADMIN only | `stores` |
| 6 | stock | Rust | 5006 | `svc_stock` | Stock is never negative and is consumed only by an accepted sale, atomically with it (RN-14, [ADR-0031](0031-sales-ingestion-decrements-inventory-atomically.md)) | `stock-levels` |
| 7 | customers | Flask | 5007 | `svc_customers` | A customer is registered once, with a valid unique email when given, a known channel and a registration date not in the future, and is never deleted through services (RN-08) | `customers` |
| 8 | customer-preferences | Rust | 5008 | `svc_preferences` | Preferred channels and interest categories are two independent multivalued facts, each replaced atomically as a whole set | `preference-profiles` |
| 9 | sales | Flask | 5009 | `svc_sales` | A sale is recorded exactly once, all or nothing, and consumes its stock in the same transaction. Lines are dated not in the future, on or after registration, for active products, with positive quantities and positive two-decimal prices (RN-11 to RN-13, RN-37 on accepted sales) | `sales`, `channels` |
| 10 | segments | Rust | 5010 | `svc_segments` | A customer has at most one current segment, named by its stable label code. Unassigned states are reported as they are (RN-20, RN-21, [ADR-0018](0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md)) | `segments`, `segment-assignments` |
| 11 | recommendations | Flask | 5011 | `svc_recommendations` | Recommend active, in-stock products not already bought, matching a stated signal, with reasons, or report why nothing qualifies (RN-40) | `recommendations` |
| 12 | engagement | Flask + MongoDB | 5012 | `svc_engagement` | In-store engagement (`QR_SCAN`, `RECO_CLICK`) is recorded once per client event, attributed, voided rather than deleted, and summarized | `engagement-events`, `engagement-summaries` |
| 13 | check-ins | Rust | 5013 | `svc_checkins` | A staff check-in is verified only when the server-computed distance lies within the store's geofence at acceptable accuracy. Overrides are append-only reviews | `check-ins` |
| 14 | campaigns *(second wave)* | Flask | 5014 | `svc_campaigns` | The campaign state machine: DRAFT→ACTIVE/CANCELLED, ACTIVE→FINISHED/CANCELLED. Edits only in DRAFT, and the end date never before the start (RN-15, RN-16, RN-22, RN-31, RN-32) | `campaigns` |
| 15 | audit-trail *(second wave)* | Rust | 5015 | `svc_audit_trail` | The audit trail is append-only, attributed, free of credentials, and readable by ADMIN and AUDITOR only (RN-28, RN-29, RN-30) | `audit-entries` |

The registry is committed as `contracts/registry.yaml`. Its format, and the
rule that no two services share a resource, belong to
[ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).
The bench-only Rust build of the products reads is not a service. It is never
routed or counted, and it connects as `svc_products_bench`, which holds SELECT
on `public.product` and nothing else
([ADR-0042](0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md)).

### Ownership and grants

**S** means the only service writer. **W** means the frozen web also writes
the table. **R** means read-only, through the boundary's one declared read
owner, while the web is the only writer. A table that does not appear here
gets no `svc_*` privilege at all.

| Boundary | Base tables (privileges of its `svc_*` role) | Consumes | Publishes |
|---|---|---|---|
| auth | none. Its state is the `auth:*` Redis prefix ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)) | `api_users.credential_v1` | — |
| users | `app_user` **S+W**: SELECT, INSERT, UPDATE of `name`, `email`, `role_id`, `is_active`, `password_hash`; no DELETE. `role` **R**: SELECT | — | `api_users.credential_v1` (to `svc_auth` only) |
| products | `product` **S+W**: SELECT, INSERT, UPDATE of `name`, `category_id`, `list_price`, `is_active`; no DELETE. `image_path` stays web-managed | — | `api_products.product_ref_v1` |
| categories | `category` **S+W**: SELECT, INSERT, UPDATE, DELETE. Every delete passes `trg_category_delete_guard` | — | `api_categories.category_tree_v1` |
| stores | `store` **R**: SELECT. `stores.store_location` **S** (new, 1:1 with `store`, 30 seed rows): SELECT, INSERT, UPDATE, DELETE | — | `api_stores.store_directory_v1` |
| stock | `inventory` **R**: SELECT. The only service write is `api_stock.consume_v1`, which runs as `definer_stock`. The web's CSV path is **W** | — | `api_stock.stock_level_v1`; `api_stock.consume_v1(smallint, int, int)`, EXECUTE to `svc_sales` only |
| customers | `customer` **S** (the web only reads it): SELECT, INSERT, UPDATE; no DELETE | — | `api_customers.customer_ref_v1` |
| customer-preferences | `customer_preferred_channel`, `customer_interest_category` **S**: SELECT, INSERT, DELETE (a whole-set replace) | `api_customers.customer_ref_v1` | `api_preferences.interest_category_v1` |
| sales | `transaction`, `transaction_line` **S+W**: SELECT, INSERT. `sales.sale_capture` **S** (new, seed-exempt runtime state): SELECT, INSERT. `channel` **R**: SELECT | `api_products.product_ref_v1`, `api_customers.customer_ref_v1`, EXECUTE on `api_stock.consume_v1` | `api_sales.purchase_line_v1` |
| segments | `segment_label`, `segment`, `segment_rule`, `segmentation_run`, `customer_segment_history` **R**: SELECT. The web's administrator run (RN-05) is their only writer | `api_customers.customer_ref_v1` | `api_segments.current_label_v1` |
| recommendations | none | `api_products.product_ref_v1`, `api_categories.category_tree_v1`, `api_stock.stock_level_v1`, `api_customers.customer_ref_v1`, `api_preferences.interest_category_v1`, `api_sales.purchase_line_v1`, `api_segments.current_label_v1` | — |
| engagement | none. Its data is the MongoDB database `engagement` ([ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)) | `api_products.product_ref_v1`, `api_stores.store_directory_v1`, `api_customers.customer_ref_v1` | — |
| check-ins | `checkins.store_check_in`, `checkins.check_in_review` **S** (new, 30 seed rows each): SELECT, INSERT. Reviews are append-only | `api_stores.store_directory_v1` | — |
| campaigns | `campaign` **S+W**: SELECT, INSERT, UPDATE; no DELETE. It allocates `campaign_id` under the web's advisory lock key 221001 (`web/db/campaigns.py`) and retries on 23505 | — | — |
| audit-trail | `audit_log` **R**: SELECT. Only `fn_audit` writes it, running as `definer_audit` | — | — |

Further rules of the matrix:

- **Consuming** an `api_*` object means USAGE on its `api_<boundary>` schema
  plus SELECT on that view, or EXECUTE on that function. Nothing else
  crosses a boundary. A producer also holds SELECT on its own published
  objects, so that its readiness check and contract tests read what its
  consumers read.
- **Outboxes.** Each boundary that publishes a stream
  ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md))
  owns `<boundary>_events.outbox`. Those boundaries are users, products,
  categories, stores, stock, customer-preferences, sales and segments. Its
  `svc_*` role holds SELECT, UPDATE of `relayed_at` and DELETE (the 7-day
  purge) on the outbox. Its `definer_<boundary>` role holds INSERT, through
  the capture trigger. Outboxes are seed-exempt runtime state.
- **Web-only tables.** `app_session`, `sales_load`, `sales_load_rejection`,
  `experiment`, `experiment_group`, `experiment_assignment`,
  `experiment_exposure` and `experiment_conversion` stay the web's. No `svc_*`
  role holds any privilege on them.
- **No cascade from a service.** Foreign keys may cross boundaries, because
  PostgreSQL runs the RI check as the referenced table's owner. A service gets
  23503 and maps it to a 422 by constraint name. It never needs a grant on the
  referenced table. Because no service holds DELETE on `customer`, `product`,
  `app_user`, `campaign` or `store`, no service path can cascade into another
  boundary's rows. The one boundary that deletes a shared master,
  categories, is stopped by `trg_category_delete_guard`, which refuses a
  `svc_*` delete of a category that customers name as an interest
  (`category_has_customer_interest`). RN-06 and RN-07 already refuse the
  other two cases through `ON DELETE RESTRICT`.
- **Roles.** `sql/00_create_database.sql` creates every role idempotently,
  the same way it creates `retail_app`, with passwords from psql variables
  and local defaults in `.env.example`:
  - the 15 LOGIN roles in the registry;
  - `svc_products_bench`;
  - the NOLOGIN definer roles: `definer_stock` (`consume_v1`, which needs
    SELECT and UPDATE of `quantity_on_hand` and `updated_at` on `inventory`,
    plus the stock outbox), `definer_audit` (`fn_audit`, which needs INSERT
    on `audit_log`), `definer_guard` (`trg_category_delete_guard`, which needs
    SELECT on `customer_interest_category`), and one `definer_<boundary>` for
    each of the other seven outbox owners.

  Every SECURITY DEFINER function is owned by a `definer_*` role, pins
  `search_path`, and has EXECUTE revoked from PUBLIC. No `svc_*` role owns
  any schema, relation or function.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| One database per service | The web would not see a phone sale unless it were rewired to call a service, and it is frozen. The course also requires one DDL file that runs clean from empty, on one 8 GB VM with no managed database |
| One shared role for every service, as `retail_app` is for the web | "One writer per table" would be a convention only. A defect in a read-only boundary could rewrite segment history, and no query could show who owns what |
| Grant each consumer SELECT on the producer's base tables (the earlier draft) | Two roles would hold privileges on one table, so neither ownership nor the blast radius of a column change could be checked. Every consumer would be coupled to the producer's physical layout. ADR-0044 replaces this with versioned `api_*` objects |
| Leave the web-written tables (`store`, `channel`, `role`, segmentation) without a service owner, and grant SELECT to whoever needs it | Several services would read `store` directly. A change to it would break readers nobody had listed. A declared read owner gives one service to change, and one contract (`store_directory_v1`) for everyone else |
| Give the master-data owners DELETE, as the web has | `customer` cascades into both preference tables, and `product` and `store` cascade into `inventory`. A service delete would remove another boundary's rows. Deactivation (RN-04, products' `is_active`) is the domain's answer, and the web keeps its own legacy deletes |
| Fewer, larger services (one catalog for products and categories, one customers service for profiles and preferences) | Each would hold two rules, which the instructor forbids (ADR-0043). Products and categories also differ in their delete rule: products are never deleted, categories are deleted under a guard |

## Consequences

**What this makes easy.**
- Ownership is a query. One CSV says what every service may touch, and CI
  fails on any extra or missing grant.
- The demonstration needs no web change. A phone sale lands in
  `transaction`, which `web/db/customer_timeline.py` already reads.
- A defect in one service cannot write another boundary's data, because
  PostgreSQL refuses the write.
- A new service is new rows in the matrix, not a change to anyone else's
  grants. The exception is a producer granting it one of its `api_*` objects.

**What this makes hard.**
- **Many roles to keep straight.** There are 16 LOGIN roles and 10 definer
  roles. `contracts/db/grants.csv` is maintained by hand in step with
  `sql/01_schema.sql`, and every boundary change edits both.
- **"Only service writer" is not "only writer".** The web co-writes
  `app_user`, `product`, `category`, `campaign`, `transaction`,
  `transaction_line` and `inventory`. Concurrent edits are arbitrated by
  PostgreSQL constraints and row locks, not by the boundary. Each rule the
  web also enforces has two implementations, the web's and the service's,
  and only database invariants and golden fixtures keep them in step
  ([ADR-0045](0045-services-never-import-the-web-system.md)).
- **Read owners cannot fix their own data.** stores, segments, and sales for
  `channel` serve tables only the web writes. A wrong store name or a missing
  segmentation run is fixed in the web, not through the service that
  publishes it.
- **The web still cascades.** Deleting a category in the web still removes
  customers' interests. Deleting a store in the web still removes its location
  and stock rows. Services learn of these only through the owner's stream,
  not by refusal.
- **One PostgreSQL is a shared failure and a shared schema.** All fifteen
  services and the web stop together when it stops. A schema change ships
  expand-before-deploy
  ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)).
- **Read-only boundaries carry a risk.** segments, audit-trail, the reading
  side of stock, and recommendations write nothing in PostgreSQL. If the
  instructor does not count them (Q-13), the count still holds only because
  of the margin ADR-0043 records.

**What must now be true elsewhere.**
- `sql/00_create_database.sql` creates the roles above, and `.env.example`
  documents a local default password for each LOGIN role.
- `sql/01_schema.sql` has one section per boundary: its tables, then its
  `api_*` objects, then its triggers, then its grants. The new tables live in
  service schemas
  ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)). `fn_audit`
  becomes SECURITY DEFINER, owned by `definer_audit`, and every definer
  function has EXECUTE revoked from PUBLIC.
- `contracts/db/grants.csv` lists every (role, object, privilege) row, with
  the header `role,object,privilege`. An object is written `schema.relation`,
  `schema.relation(column)` for a column-only grant,
  `schema.function(argtypes)` as `regprocedure` prints it, or a bare schema
  name for USAGE on a schema other than `public`.
- `sql/seed-exempt.txt` gains schema-qualified lines for `sales.sale_capture`
  and every outbox. The CI 30-row loop extends from `public` to the service
  schemas. `stores.store_location`, `checkins.store_check_in` and
  `checkins.check_in_review` ship with 30 seed rows each.
- `docs/data-model.md` records the 4NF justification of each new table.
- Every service write transaction sets
  `set_config('mosaiq.user_id', <sub>, true)`, so `fn_audit` attributes the
  change to the real actor.
- users-service maps the 23505 from `ux_app_user_single_administrator` to a
  409 `single_administrator`, never a 500 (ADR-0036).
- Each container reaches PostgreSQL through the mounted socket, with one
  `pg_hba` `local` line per `svc_*` role (ADR-0040).
- `AGENTS.md` states this ownership rule, and drops the earlier text that
  had services reuse `web/services/` and `web/db/`.

## Compliance

Every check runs in CI after the three scripts have run clean from empty.

**The grant matrix is exact.** The database is compared with
`contracts/db/grants.csv` in both directions. Any `<` line is a missing
grant, and any `>` line is an extra one:

```bash
psql -X -v ON_ERROR_STOP=1 --csv -t -d retail > /tmp/grants.actual.csv <<'SQL'
WITH svc AS (
  SELECT oid, rolname FROM pg_roles WHERE rolname LIKE 'svc\_%'
), rel AS (
  SELECT c.oid, n.nspname || '.' || c.relname AS object
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p', 'v', 'm')
    AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg\_%'
)
SELECT s.rolname, r.object, p.priv
FROM svc s CROSS JOIN rel r
CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',
                        'TRUNCATE','REFERENCES','TRIGGER']) AS p(priv)
WHERE has_table_privilege(s.oid, r.oid, p.priv)
UNION ALL
SELECT s.rolname, r.object || '(' || a.attname || ')', p.priv
FROM svc s CROSS JOIN rel r
JOIN pg_attribute a ON a.attrelid = r.oid AND a.attnum > 0 AND NOT a.attisdropped
CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) AS p(priv)
WHERE has_column_privilege(s.oid, r.oid, a.attnum, p.priv)
  AND NOT has_table_privilege(s.oid, r.oid, p.priv)
UNION ALL
SELECT s.rolname, f.oid::regprocedure::text, 'EXECUTE'
FROM svc s CROSS JOIN pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace
WHERE n.nspname LIKE 'api\_%' AND has_function_privilege(s.oid, f.oid, 'EXECUTE')
UNION ALL
SELECT s.rolname, n.nspname, 'USAGE'
FROM svc s CROSS JOIN pg_namespace n
WHERE n.nspname NOT IN ('public', 'pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\_%'
  AND has_schema_privilege(s.oid, n.oid, 'USAGE');
SQL
diff <(tail -n +2 contracts/db/grants.csv | LC_ALL=C sort) \
     <(LC_ALL=C sort /tmp/grants.actual.csv)
```

**At most one service role per base table.** `svc_products_bench` counts as
products. The query must return no row:

```sql
SELECT n.nspname || '.' || c.relname AS base_table,
       array_agg(DISTINCT replace(r.rolname, 'svc_products_bench', 'svc_products')) AS holders
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN pg_roles r
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\_%'
  AND r.rolname LIKE 'svc\_%'
  AND (has_table_privilege(r.oid, c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR has_any_column_privilege(r.oid, c.oid, 'SELECT, INSERT, UPDATE, REFERENCES'))
GROUP BY 1
HAVING count(DISTINCT replace(r.rolname, 'svc_products_bench', 'svc_products')) > 1;
```

**Roles, web-only tables, deletes, the audit log and definers.** Each query
must return no row:

```sql
-- Exactly the fifteen service LOGIN roles, plus the bench role.
SELECT coalesce(x.name, r.rolname) AS missing_or_extra FROM unnest(ARRAY['svc_auth','svc_users','svc_products','svc_categories',
  'svc_stores','svc_stock','svc_customers','svc_preferences','svc_sales','svc_segments',
  'svc_recommendations','svc_engagement','svc_checkins','svc_campaigns','svc_audit_trail',
  'svc_products_bench']) AS x(name)
FULL JOIN (SELECT rolname FROM pg_roles WHERE rolname LIKE 'svc\_%' AND rolcanlogin) r
  ON r.rolname = x.name
WHERE x.name IS NULL OR r.rolname IS NULL;

-- No service privilege on a web-only table.
SELECT r.rolname, t FROM pg_roles r
CROSS JOIN unnest(ARRAY['app_session','sales_load','sales_load_rejection','experiment',
  'experiment_group','experiment_assignment','experiment_exposure',
  'experiment_conversion']) AS t
WHERE r.rolname LIKE 'svc\_%'
  AND (has_table_privilege(r.oid, ('public.' || t)::regclass,
         'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR has_any_column_privilege(r.oid, ('public.' || t)::regclass,
         'SELECT, INSERT, UPDATE, REFERENCES'));

-- No service holds DELETE or TRUNCATE on the five masters.
SELECT r.rolname, t FROM pg_roles r
CROSS JOIN unnest(ARRAY['public.customer','public.product','public.app_user',
  'public.campaign','public.store']) AS t
WHERE r.rolname LIKE 'svc\_%' AND has_table_privilege(r.oid, t, 'DELETE, TRUNCATE');

-- Only svc_audit_trail reads the audit log, and no service writes it.
SELECT rolname FROM pg_roles
WHERE rolname LIKE 'svc\_%'
  AND (has_table_privilege(oid, 'public.audit_log', 'INSERT, UPDATE, DELETE, TRUNCATE')
       OR (rolname <> 'svc_audit_trail' AND has_table_privilege(oid, 'public.audit_log', 'SELECT')));

-- Every SECURITY DEFINER function is owned by a NOLOGIN, non-superuser
-- definer_* role, pins search_path, and is not executable by PUBLIC.
SELECT f.oid::regprocedure, o.rolname
FROM pg_proc f
JOIN pg_roles o ON o.oid = f.proowner
JOIN pg_namespace n ON n.oid = f.pronamespace
WHERE f.prosecdef
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND (o.rolname NOT LIKE 'definer\_%' OR o.rolcanlogin OR o.rolsuper
       OR NOT EXISTS (SELECT 1 FROM unnest(f.proconfig) AS c WHERE c LIKE 'search\_path=%')
       OR has_function_privilege('public', f.oid, 'EXECUTE'));

-- No service role owns a schema, a relation or a function.
SELECT 'schema', nspname FROM pg_namespace
  WHERE pg_get_userbyid(nspowner) LIKE 'svc\_%'
UNION ALL SELECT 'relation', relname FROM pg_class
  WHERE pg_get_userbyid(relowner) LIKE 'svc\_%'
UNION ALL SELECT 'function', proname FROM pg_proc
  WHERE pg_get_userbyid(proowner) LIKE 'svc\_%';
```

**The category guard holds for services only.** Run as `svc_categories`, a
delete of a category that a customer names as an interest must fail with
constraint `category_has_customer_interest`. The same delete as
`retail_app`, inside a rolled-back transaction, must still cascade as it does
today.
