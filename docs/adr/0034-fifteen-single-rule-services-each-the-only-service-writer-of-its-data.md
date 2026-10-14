# ADR-0034 — Fifteen single-rule services, each the only service that writes its data

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)
sets the floor of ten single-rule, independent services; the team plans fifteen
(thirteen core, two in a second wave). This record names them and decides who may
write which data. Four facts constrain the answer:

1. **The web is frozen** (Q-12 in [`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)),
   yet a phone sale must appear in it. So services write the `public` tables the web reads.
2. **The web stays a writer** of `app_user`, `product`, `category`, `campaign`, `inventory`,
   `transaction`, `transaction_line`, `store`, `channel`, `role` and segmentation.
3. **Some tables have no service writer** (`store`, `channel`, `role`, segmentation),
   yet services read them; without an owner nobody knows who breaks.
4. **One VM, one PostgreSQL, one DDL file.** Isolation comes from database roles.

Judgement calls: a **read owner** of a table it never writes is a convention the grant
checks enforce; whether a read-only boundary counts as a rule is open (Q-13, ADR-0043).

## Decision

The distributed stage has **fifteen service boundaries** (registry below). Each
implements exactly one rule and connects to PostgreSQL as its own LOGIN role,
`svc_<suffix>`, holding exactly the privileges in the ownership matrix and nothing
else. Every base table any service touches has **exactly one** `svc_*` role with any
privilege on it: its only service writer (S), or its only service reader when the
web is the sole writer (R). A boundary reads another's data only through the
producer's versioned `api_*` objects
([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)),
never through a base-table grant. Writes that need elevated rights (consuming stock,
writing the audit log, guarding a delete, filling an outbox) run in SECURITY DEFINER
functions owned by NOLOGIN `definer_*` roles; each pins `search_path` and has EXECUTE
revoked from PUBLIC. No `svc_*` role owns a schema, relation or function, and none holds
DELETE on `customer`, `product`, `app_user`, `campaign` or `store`. The matrix is committed as
`contracts/db/grants.csv`, which CI compares with the database in both directions.

### Registry

| # | Service | Lang | Port | Role | One rule | Top-level resources |
|---|---|---|---|---|---|---|
| 1 | auth | Flask | 5001 | `svc_auth` | A principal is authenticated only by a valid credential of an active account, and every token stays usable only while its session is live (RN-52; related RN-03) | `sessions`, `jwks.json` |
| 2 | users | Flask | 5002 | `svc_users` | Accounts obey the single-administrator rule (never two, never zero, moved only by an explicit transfer), have case-insensitively unique emails, and are deactivated, never deleted (RN-01, RN-02, RN-04) | `users`, `roles`, `administrator-transfers` |
| 3 | products | Flask | 5003 | `svc_products` | A product has a unique SKU, a price that is never negative and exactly one existing category, and is retired by deactivation (RN-10, RN-11) | `products` |
| 4 | categories | Flask | 5004 | `svc_categories` | The category tree has no cycles, and a category with products, children or customer interests is never deleted through services (RN-33, RN-06, RN-07) | `categories` |
| 5 | stores | Rust | 5005 | `svc_stores` | Each store has at most one location, with valid coordinates and a 25–2000 m geofence, set by ADMIN only (RN-53) | `stores` |
| 6 | stock | Rust | 5006 | `svc_stock` | Stock is never negative and is consumed only by an accepted sale, atomically with it (RN-14, [ADR-0031](0031-sales-ingestion-decrements-inventory-atomically.md)) | `stock-levels` |
| 7 | customers | Flask | 5007 | `svc_customers` | A customer is registered once, with a valid unique email when given, a known channel and a registration date not in the future, and is never deleted through services (RN-08) | `customers` |
| 8 | customer-preferences | Rust | 5008 | `svc_preferences` | A customer's preferred channels and interest categories are each replaced only atomically, as a whole set of existing references (RN-54) | `preference-profiles` |
| 9 | sales | Flask | 5009 | `svc_sales` | A sale is recorded exactly once, all or nothing, consuming its stock in the same transaction, and only with lines dated not in the future and not before the customer's registration, for active products, with positive quantities and positive two-decimal prices (RN-11 to RN-13, RN-37 (accepted sales)) | `sales`, `channels` |
| 10 | segments | Rust | 5010 | `svc_segments` | A customer has at most one current segment, named by its stable label code, and unassigned states are reported as they are (RN-20, RN-21) | `segments`, `segment-assignments` |
| 11 | recommendations | Flask | 5011 | `svc_recommendations` | Recommend active, in-stock products not already bought, matching a stated signal, with reasons, or report why nothing qualifies (RN-40) | `recommendations` |
| 12 | engagement | Flask + MongoDB | 5012 | `svc_engagement` | In-store engagement (`QR_SCAN`, `RECO_CLICK`) is recorded once per client event, attributed, voided rather than deleted, and summarized (RN-55) | `engagement-events`, `engagement-summaries` |
| 13 | check-ins | Rust | 5013 | `svc_checkins` | A staff check-in is verified only when the server-computed distance lies within the store's geofence at acceptable accuracy, and overrides are append-only reviews (RN-56) | `check-ins` |
| 14 | campaigns *(second wave)* | Flask | 5014 | `svc_campaigns` | A campaign moves only DRAFT→ACTIVE/CANCELLED and ACTIVE→FINISHED/CANCELLED, is edited only in DRAFT, and never ends before it starts (RN-15, RN-16, RN-22, RN-31, RN-32) | `campaigns` |
| 15 | audit-trail *(second wave)* | Rust | 5015 | `svc_audit_trail` | The audit trail is append-only, attributed, free of credentials, and readable by ADMIN and AUDITOR only (RN-28, RN-29, RN-30) | `audit-entries` |

The registry is committed as `contracts/registry.yaml`; its format and checks belong
to [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).
sales serves `channels`, the read-only vocabulary its rule validates every sale's
`channel_id` against, written only by the web; ADR-0043 allows such a vocabulary. The
bench-only Rust build of the products reads is never routed or counted, and connects as
`svc_products_bench` with SELECT on `public.product` only ([ADR-0042](0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md)).

### Ownership and grants

**S**: the only service writer. **W**: the frozen web also writes it. **R**: read-only, by
the one declared read owner, while the web is the only writer. Unlisted tables get no `svc_*` privilege.

| Boundary | Base tables (privileges of its `svc_*` role) | Consumes | Publishes |
|---|---|---|---|
| auth | none. Its state is the `auth:*` Redis prefix ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)) | `api_users.credential_v1` | — |
| users | `app_user` **S+W**: SELECT, INSERT, UPDATE of `name`, `email`, `role_id`, `is_active`, `password_hash`; no DELETE. `role` **R**: SELECT | — | `api_users.credential_v1` (auth is its only consumer) |
| products | `product` **S+W**: SELECT, INSERT, UPDATE of `name`, `category_id`, `list_price`, `is_active`; no DELETE. `image_path` stays web-managed | — | `api_products.product_ref_v1` |
| categories | `category` **S+W**: SELECT, INSERT, UPDATE, DELETE. Every delete passes `trg_category_delete_guard` | — | `api_categories.category_tree_v1` |
| stores | `store` **R**: SELECT. `stores.store_location` **S** (new, 1:1 with `store`, 30 seed rows): SELECT, INSERT, UPDATE, DELETE | — | `api_stores.store_directory_v1` |
| stock | `inventory` **R**: SELECT. The only service write is `api_stock.consume_v1`, which runs as `definer_stock`. The web's CSV path is **W** | — | `api_stock.stock_level_v1`; `api_stock.consume_v1(smallint, int, int)`, EXECUTE to `svc_sales` only |
| customers | `customer` **S** (the web only reads it): SELECT, INSERT, UPDATE; no DELETE | — | `api_customers.customer_ref_v1` |
| customer-preferences | `customer_preferred_channel`, `customer_interest_category` **S**: SELECT, INSERT, DELETE: a whole-set replace, serialized per customer by `pg_advisory_xact_lock(290001, hashtext(customer_id::text))` (RN-54) | `api_customers.customer_ref_v1` | `api_preferences.interest_category_v1` |
| sales | `transaction`, `transaction_line` **S+W**: SELECT, INSERT. `sales.sale_capture` **S** (new, seed-exempt runtime state): SELECT, INSERT. `channel` **R**: SELECT | `api_products.product_ref_v1`, `api_customers.customer_ref_v1`, EXECUTE on `api_stock.consume_v1` | `api_sales.purchase_line_v1` |
| segments | `segment_label`, `segment`, `segment_rule`, `segmentation_run`, `customer_segment_history` **R**: SELECT. The web's administrator run (RN-05) is their only writer | `api_customers.customer_ref_v1` | `api_segments.current_label_v1` |
| recommendations | none | `api_products.product_ref_v1`, `api_categories.category_tree_v1`, `api_stock.stock_level_v1`, `api_customers.customer_ref_v1`, `api_preferences.interest_category_v1`, `api_sales.purchase_line_v1`, `api_segments.current_label_v1` | — |
| engagement | none. Its data is the MongoDB database `engagement` ([ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)) | `api_products.product_ref_v1`, `api_stores.store_directory_v1`, `api_customers.customer_ref_v1` | — |
| check-ins | `checkins.store_check_in`, `checkins.check_in_review` **S** (new, 30 seed rows each): SELECT, INSERT. Reviews are append-only | `api_stores.store_directory_v1` | — |
| campaigns | `campaign` **S+W**: SELECT, INSERT, UPDATE; no DELETE. It allocates `campaign_id` under the web's advisory lock key 221001 (`web/db/campaigns.py`) and retries on 23505 | — | — |
| audit-trail | `audit_log` **R**: SELECT. Only `fn_audit` writes it, running as `definer_audit` | — | — |

- **Consuming** is USAGE on the `api_<suffix>` schema plus SELECT on the view, or EXECUTE on the
  function. Producers also hold SELECT on their own `api_*` views. EXECUTE on `consume_v1` goes to `svc_sales` only.
- **Outboxes.** users, products, categories, stores, stock, customer-preferences, sales and segments
  each own `<suffix>_events.outbox` ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)
  names them). The `svc_*` role holds SELECT, UPDATE of `relayed_at`, and DELETE for the 7-day purge;
  `definer_<suffix>` (`definer_stock` for stock) inserts through the capture trigger. Seed-exempt runtime state.
- **Web-only tables** get no `svc_*` privilege: `app_session`, `sales_load`, `sales_load_rejection`,
  `experiment`, `experiment_group`, `experiment_assignment`, `experiment_exposure`, `experiment_conversion`.
- **No cascade from a service:** none deletes a master. A categories delete passes
  `trg_category_delete_guard`, which refuses a category customers name as an interest
  (`category_has_customer_interest`); `ON DELETE RESTRICT` covers RN-06 and RN-07.
- **Roles** (`sql/00_create_database.sql`): the 15 LOGIN roles, `svc_products_bench`, and ten NOLOGIN
  definers: `definer_stock` (SELECT on `inventory`, UPDATE of `quantity_on_hand` and `updated_at`,
  INSERT on `stock_events.outbox`), `definer_audit` (INSERT on `audit_log`), `definer_guard` (SELECT
  on `customer_interest_category`), and `definer_users`, `definer_products`, `definer_categories`,
  `definer_stores`, `definer_preferences`, `definer_sales`, `definer_segments` (INSERT on their outbox).

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
- Ownership is a query: one CSV says what every service may touch, CI fails on any
  drift, and a defect in one service cannot write another boundary's data.
- The demonstration needs no web change: a phone sale shows at once in the reports
  index revenue summary (`web/db/revenue_summary.py`), and its decrement in the stock
  consultation (`web/db/inventory.py`).

**What this makes hard.**
- **Many roles.** 16 LOGIN and 10 definer roles; `grants.csv` is kept by hand in step
  with `sql/01_schema.sql`, and every boundary change edits both.
- **"Only service writer" is not "only writer".** Rules the web also enforces exist twice,
  kept in step by invariants and golden fixtures ([ADR-0045](0045-services-never-import-the-web-system.md)).
- **Read owners cannot fix their own data.** A wrong store name or a missing
  segmentation run is fixed in the web, not through the service that publishes it.
- **Foreign keys cross boundaries** (`customer.channel_id`, `transaction_line.product_id`, `checkins.store_check_in`
  to `store` and `app_user`). Their RI checks run as the parent's owner, so a service needs no privilege on the parent,
  and a violation (23503) maps to a 422 by constraint name. They stay because the legacy schema and the web rely on
  them. No service path cascades: no DELETE on a master, and the category guard.
- **The web still cascades.** A web category or store delete still removes interests,
  locations and stock rows; services learn of it only through streams.
- **One PostgreSQL is a shared failure and a shared schema.** Each boundary section is
  idempotent and ships expand-before-deploy ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)).
- **Read-only boundaries carry a risk.** If Q-13 does not count them, the floor holds only
  through the margin ADR-0043 records.

**What must now be true elsewhere.**
- `sql/00_create_database.sql` creates the roles idempotently; `.env.example` documents
  a local default password for each LOGIN role.
- `sql/01_schema.sql` holds one marked, idempotent section per boundary: tables in
  service schemas ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)),
  `api_*` objects, triggers, grants. `fn_audit` becomes SECURITY DEFINER, owned by `definer_audit`.
- `contracts/db/grants.csv` has the header `role,object,privilege`. An object is
  `schema.relation`, `schema.relation(column)`, `schema.function(argtypes)` as
  `regprocedure` prints it, or a bare schema name for USAGE outside `public`.
- `sql/seed-exempt.txt` and the 30-row seeds follow [ADR-0046](0046-service-owned-data-lives-in-service-schemas.md).
- Every service write transaction sets `set_config('mosaiq.user_id', <sub>, true)`.
- `contracts/db/` records the advisory lock keys 221001 and 290001.

## Compliance

Every check runs in CI after the three scripts have run clean from empty.
**At most one service role per base table** (`svc_products_bench` counts as products); no row:

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

`contracts/db/check_grants.sh` (its SQL is this record's full draft) fails on any output:
1. every `svc_*` privilege (table, column, EXECUTE on `api_*` functions, USAGE on non-`public` schemas) equals `contracts/db/grants.csv`, in both directions;
2. the LOGIN `svc_*` roles are exactly the fifteen plus `svc_products_bench`;
3. no `svc_*` privilege on a web-only table;
4. no `svc_*` DELETE or TRUNCATE on `customer`, `product`, `app_user`, `campaign` or `store`;
5. only `svc_audit_trail` reads `audit_log`, and no `svc_*` role writes it;
6. every SECURITY DEFINER function is owned by a NOLOGIN, non-superuser `definer_*` role, pins `search_path` and is not executable by PUBLIC;
7. no `svc_*` role owns a schema, relation or function.

**The category guard holds for services only.** As `svc_categories`, deleting a category
a customer names as an interest fails with `category_has_customer_interest`; as
`retail_app`, in a rolled-back transaction, it still cascades as it does today.
