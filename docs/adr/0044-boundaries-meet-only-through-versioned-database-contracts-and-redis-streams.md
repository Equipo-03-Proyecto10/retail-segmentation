# ADR-0044 — Boundaries meet only through versioned database contracts and Redis Streams relayed from each owner's outbox

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

Fifteen services share one PostgreSQL database on one VM
([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)).
That is a course constraint, not a preference. The instructor also requires the
services to be totally independent: each one is built, versioned, deployed and
rolled back on its own. `AGENTS.md` already forbids services calling each other
over HTTP. Each boundary still needs other boundaries' facts:
- recommendations-service applies RN-40 using facts owned by seven boundaries:
  segments, sales, products, categories, preferences, stock and customers;
- sales-service checks the customer's registration date and the product's
  status, and must consume stock **in the same transaction** as the sale
  (RN-14, [ADR-0031](0031-sales-ingestion-decrements-inventory-atomically.md));
- auth-service must learn when an account is deactivated or changes role;
- caches must learn when the row behind them changes.

Two facts constrain how they can learn it:
- **The frozen web writes many of the same tables:** `app_user`, `product`,
  `category`, `campaign`, `transaction`, `transaction_line`, and `inventory`
  through its CSV path. It writes `store`, `channel` and the segmentation
  tables alone. It may not be rewired, and it connects to no Redis (Q-12). Any
  change signal that depends on the writer's code misses every web write.
- **`pg_notify` is not durable, and not private.** A notification sent while
  the listener is disconnected is lost. PostgreSQL has no privilege on
  `LISTEN`, so any role can listen to any channel.

The team decided on 2026-10-13 that communication is hybrid. Reads go through
versioned database contracts. **Every event between services travels on Redis
Streams.** `pg_notify` is only an owner's private wake-up.

What is uncertain is how a reviewer weighs the shared database. A view in
another boundary's schema is a coupling, even when it is versioned. The claim
of this record is narrower: the coupling is one published surface per
producer, with a version number and an exact grant list. It is not every
table, and it is not a network call.

## Decision

Boundaries meet in exactly two ways:
- **Reads** go through objects the producer publishes in its own
  `api_<boundary>` schema, named `*_v1`.
- **Events** go on Redis Streams. The owner's relay publishes them from a
  transactional outbox, which a SECURITY DEFINER trigger fills for every
  writer, the frozen web included.

There is one synchronous exception, made inside the database and never over
HTTP: `api_stock.consume_v1`, which sales-service calls inside its own
transaction. Nothing else crosses a boundary:
- no service holds a privilege on another boundary's base table;
- no service calls another over HTTP;
- no service LISTENs on another boundary's channel;
- no event carries state, only keys that the consumer re-reads through the
  producer's views.

A breaking change ships `_v2` beside `_v1`, and `stream:<producer>.v2.<event>`
beside the v1 stream.

### Reads: the published database contracts

| Object | Owner | v1 columns or signature | Consumed by |
|---|---|---|---|
| `api_users.credential_v1` | users | `user_id`, `email_lower`, `password_hash`, `is_active`, `role_code` | auth only |
| `api_products.product_ref_v1` | products | `product_id`, `sku`, `name`, `category_id`, `list_price`, `is_active` | sales, recommendations, engagement |
| `api_categories.category_tree_v1` | categories | `category_id`, `parent_category_id`, `name` | recommendations |
| `api_stores.store_directory_v1` | stores | `store_id`, `name`, `city`, `state`, `is_active`, `latitude`, `longitude`, `geofence_radius_m` | check-ins, engagement |
| `api_stock.stock_level_v1` | stock | `store_id`, `product_id`, `quantity_on_hand`, `updated_at` | recommendations |
| `api_stock.consume_v1(smallint, int, int)` | stock | `(store_id, product_id, quantity)` returns the remaining `int` | sales |
| `api_customers.customer_ref_v1` | customers | `customer_id`, `registered_on` | sales, customer-preferences, segments, recommendations, engagement |
| `api_preferences.interest_category_v1` | customer-preferences | `customer_id`, `category_id` | recommendations |
| `api_sales.purchase_line_v1` | sales | `transaction_id`, `customer_id`, `store_id`, `channel_id`, `occurred_at`, `product_id`, `quantity`, `unit_price` | recommendations |
| `api_segments.current_label_v1` | segments | `customer_id`, `state`, `label_code`, `since`, `run_id` | recommendations |

How the views work:
- A view keeps the default `security_invoker = off`, so it reads the base
  tables with its owner's rights. A consumer holds USAGE on the `api_*` schema
  and SELECT on the view, and nothing on the table.
- The view's column list is the whole exposure. `credential_v1` is the only
  one that exposes a credential, and only auth may read it.
- Consumers name the columns they read. A new column is therefore not a
  breaking change
  ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
- Each object's contract is `contracts/db/<schema>.<object>.v1.md`. The exact
  grants are in `contracts/db/grants.csv`
  ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)).

### The one synchronous call: `api_stock.consume_v1`

A sale and its stock movement must commit or roll back together (RN-14,
ADR-0031). Two transactions cannot give that guarantee without a saga, and a
saga would leave a window in which stock is sold twice or a sale exists without
its decrement.

So stock-service publishes its write as a database function:
- `consume_v1` is plpgsql and SECURITY DEFINER.
- It is owned by the NOLOGIN role `definer_stock`, which holds UPDATE on
  `inventory.quantity_on_hand` and `updated_at` only.
- It pins `SET search_path = public, pg_temp`.
- EXECUTE is revoked from PUBLIC and granted to `svc_sales` only.

It runs the same conditional `UPDATE ... WHERE quantity_on_hand >= $3
RETURNING` as `decrement_stock` in `web/db/inventory.py`. When no row matches,
a `SELECT ... FOR UPDATE` tells a missing row from insufficient stock, and it
raises SQLSTATE 23514 with constraint `no_stock_record` or `stock_unavailable`.
Because it runs inside sales-service's transaction, a failure on any line rolls
back the header, the lines and every earlier decrement. How sales-service uses
it is [ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md).
This is the only cross-boundary write and the only synchronous dependency
between two services. A second one needs its own record.

### Events: a transactional outbox per owner, relayed to Redis Streams

**Capture.** `sql/01_schema.sql` defines an AFTER INSERT/UPDATE/DELETE row
trigger on each table whose changes another service must learn about. The
streams and their sources are listed in
[ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md).

The trigger function:
- is SECURITY DEFINER, owned by `definer_<boundary>`, with a pinned
  `search_path` and EXECUTE revoked from PUBLIC;
- inserts one row into the owner's outbox, `<boundary>_events.outbox`;
- calls `pg_notify('<boundary>_outbox', '')`.

Because the function runs as its definer, the writer needs no privilege on the
outbox. `retail_app` writes `product` exactly as it does today, and the event
appears: web edits, CSV ingestion and hard deletes all produce events, with no
web code change and no Redis in the web. The row is part of the writer's
transaction. If the write rolls back, the event never existed.

```sql
CREATE TABLE products_events.outbox (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream      text NOT NULL,          -- 'stream:products.v1.changed'
    event_type  text NOT NULL,          -- TG_OP: INSERT, UPDATE or DELETE
    entity_id   text NOT NULL,          -- the changed row's key, as text
    occurred_at timestamptz NOT NULL DEFAULT now(),
    relayed_at  timestamptz
);
CREATE INDEX ix_products_outbox_pending
    ON products_events.outbox (occurred_at) WHERE relayed_at IS NULL;
```

The row carries **keys only**, never personal data or credentials:
- `entity_id` is the primary key of the changed row.
- For `inventory` it is `store_id:product_id`.
- For a preference row it is the `customer_id`.
- For `stores.store_location` it is the `store_id`.
- For the users stream it is the `user_id`, whatever changed.

The capture for `stream:users.v1.access_changed` fires only on UPDATE OF
`is_active`, `role_id`, and on DELETE. Outbox tables are seed-exempt runtime
state.

**Relay.** The owner relays its own outbox, through the kit's helper
([ADR-0045](0045-services-never-import-the-web-system.md)):

1. Wait for a notification on its own `<boundary>_outbox` channel, for at
   most 5 s.
2. In one transaction:

   ```sql
   SELECT event_id, stream, event_type, entity_id, occurred_at
     FROM products_events.outbox
    WHERE relayed_at IS NULL
    ORDER BY occurred_at
    LIMIT 100
      FOR UPDATE SKIP LOCKED;
   ```

3. For each row, `XADD <stream> MAXLEN ~ 10000 * event_id <id> v 1 event_type
   <type> entity_id <key> occurred_at <rfc3339>` on `redis-state`.
4. Run `WAITAOF 1 0 2000`, so the entries are on disk before the outbox says
   so.
5. `UPDATE ... SET relayed_at = now() WHERE event_id = ANY($1)`, then COMMIT.
6. Once an hour, delete relayed rows older than 7 days.

How the relay behaves:
- It never keeps a high-water mark; it reads only `relayed_at IS NULL`. A
  transaction that started early and committed late is relayed on the next
  pass, not skipped.
- `SKIP LOCKED` lets a second replica relay at the same time without taking
  the same rows.
- A crash between `XADD` and COMMIT sends the batch again. Delivery is **at
  least once**.
- The seven days of relayed rows are a replay log. After a Redis loss, an
  operator sets `relayed_at = NULL` for the affected window, and the relay
  sends those rows again.

**Consume.** Each consuming service has one consumer group per stream, named
after itself. Each replica is a consumer named after its container hostname.
- On first start it runs `XGROUP CREATE <stream> <group> $ MKSTREAM`.
- On every start it first re-reads its own pending entries (`XREADGROUP ...
  STREAMS <stream> 0`). It then claims, with `XPENDING` and `XCLAIM`, entries
  idle for over 60 s whose consumer is gone. Only then does it read new
  entries with `>`.
- It handles an entry, then runs `XACK`.

Consumers are idempotent by `event_id`. Today every handler deletes keys or
replaces a cache generation, so handling a duplicate is harmless. A future
consumer whose effect is not naturally idempotent records the `event_id` with
that effect, in the same transaction. An entry that fails five deliveries is
logged with its `event_id` and acknowledged. Its outbox row stays available for
replay.

**Payloads carry ids; state is re-read.** A consumer that needs more than the
key reads it through the producer's `api_*` view at handling time. A duplicate,
or an entry handled out of order, therefore converges on the current state. A
deleted row shows up as a re-read that returns nothing.

**`pg_notify` is only a wake-up.** The channel `<boundary>_outbox` carries an
empty payload. Only the owner's relay listens to it. PostgreSQL folds identical
notifications in one transaction into one, so a CSV import of thousands of
sales wakes the relay once. The relay does not depend on the notification: if
one is lost, the 5-second poll finds the rows. No service LISTENs on another
boundary's channel. Inter-service signals go on streams.

### Versioning and order of change

- **Views and functions.** A breaking change to an `api_*` object ships
  `<name>_v2` beside `<name>_v1`. Consumers move in their own pull requests.
  `_v1` is dropped only when `contracts/db/grants.csv` shows no consumer left
  on it.
- **Streams.** A breaking change to a stream ships
  `stream:<producer>.v2.<event>` beside the v1 stream. During the overlap the
  capture trigger writes one outbox row per published version. Every entry
  carries its version in `v`.
- **Expand before deploy.** The boundary's section of `sql/01_schema.sql`
  lands first: the new object, trigger or grant. The service that uses it
  ships after. Nothing is contracted while a consumer still holds a grant on
  it.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| `pg_notify` channels as the inter-service signal, one per owner, LISTENed to by consumers (the reviewed design this record replaces) | The team requires inter-service events on Redis Streams. `pg_notify` is not durable: a revocation sent while auth's listener is reconnecting is lost, and only the 60 s reconcile would catch it. LISTEN has no privilege, so a consumer depending on another boundary's channel cannot be stopped by a grant |
| Full projections: each service keeps its own copy of what it needs, fed only by events, with no cross-boundary database reads | Roughly doubles the plumbing: a projection table, a backfill and a consumer for every fact a service reads. Every read becomes eventually consistent. recommendations alone would copy seven datasets. sales could not check stock and a customer's registration date atomically with the sale |
| Grant each consumer SELECT on the producer's base tables (the first draft of [ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)) | Couples every consumer to the producer's internal layout, so a column rename breaks services nobody tested. Two roles on one table makes ownership uncheckable. Nothing versions the dependency |
| Services call each other over HTTP | Forbidden by `AGENTS.md`. It turns every outage into a cascade, and it cannot make the sale and its stock decrement atomic |
| The writing service runs `XADD` itself after it commits | A crash between COMMIT and `XADD` loses the event. Writes made by the frozen web would produce no event at all, unless the web got Redis and new code, which Q-12 and the freeze forbid |
| Logical decoding (CDC) from the WAL into Redis | Needs `wal_level = logical`, a replication slot and a connector process that the 3,000 MB budget does not have. A stalled slot retains WAL until the disk fills. It is one more deployable unit with no single owner |

## Consequences

**What this makes easy.**
- Web writes produce events with no web change. Deactivating a user in the
  web reaches auth on the same path as deactivating one through
  users-service.
- A consumer depends on a named, versioned surface. A producer can reshape its
  tables freely, provided it keeps its `_v1` objects returning the same
  columns.
- The sale stays atomic with its stock, as ADR-0031 requires, with no saga.
- Every coupling is visible: `contracts/db/grants.csv` lists who reads what,
  and `contracts/streams/` lists who consumes what.
- A Redis loss is recoverable. The outbox holds seven days of events to replay.

**What this makes hard.**
- **The database is still shared runtime.** One PostgreSQL outage stops every
  service. A slow query in a view slows its consumers.
- **Every captured write costs one more INSERT, the web's included.** A CSV
  import of N sales writes N outbox rows. If the outbox insert fails, for
  example on a full disk, the web's write fails with it. That is a new failure
  mode for the frozen web, with no code change to explain it.
- **Caches are eventually consistent.** Relay latency is a notification,
  bounded by the 5 s poll, plus the consumer's turn. Contracts state their
  staleness bound.
- **At least once means idempotent or wrong.** Every consumer has to stay
  safe under duplicates, including ones written later by people who did not
  read this record.
- **`consume_v1` couples sales to stock at transaction level.** A broken
  function stops every phone sale. sales-service cannot deploy around it.
- **recommendations depends on seven contracts.** A careless `_v1` change in
  any of seven boundaries breaks it.
- **The relay holds a transaction open across Redis calls.** That is bounded
  by 100 entries and `WAITAOF`'s 2 s timeout, and locks only outbox rows.
- **The private-channel rule is enforced by grep, not by PostgreSQL.** LISTEN
  cannot be revoked. The empty payload limits what a trespassing listener
  learns to "something changed".

**What must now be true elsewhere.**
- `sql/00_create_database.sql` creates `definer_stock` and one
  `definer_<boundary>` per outbox owner, all NOLOGIN, as
  [ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)
  lists them.
- `sql/01_schema.sql` holds, per boundary: its `api_<boundary>` schema and
  objects, its `<boundary>_events` schema and outbox, its capture triggers, and
  its grants. Outbox and service schemas stay out of `public`
  ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)).
- `sql/seed-exempt.txt` names each outbox, schema-qualified, as runtime state.
- [ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)
  grants `+xadd` only to producers and the group commands only to consumers.
- [ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)
  consumes `stream:users.v1.access_changed` and keeps the 60 s reconcile.
- [ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)
  calls `consume_v1` per line, in ascending `product_id` order, inside the
  sale transaction.
- The kits carry the relay and consumer helpers, and no stream names beyond
  the naming convention
  ([ADR-0045](0045-services-never-import-the-web-system.md)).
- `contracts/db/` documents every `api_*` object and each owner's outbox
  channel. `contracts/streams/` documents every stream
  ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).

## Compliance

Run as the database owner after the three scripts, from an empty database.
Every query must return zero rows.

```sql
-- 1. Outside api_* schemas, no relation is reachable by two boundaries.
--    (Which role it is belongs to contracts/db/grants.csv, ADR-0034.)
SELECT n.nspname, c.relname,
       array_agg(DISTINCT regexp_replace(r.rolname, '_bench$', '')) AS boundaries
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_roles r ON r.rolname LIKE 'svc\_%'
 WHERE c.relkind IN ('r', 'p', 'v', 'm')
   AND n.nspname NOT LIKE 'api\_%'
   AND n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND (has_table_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_any_column_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))
 GROUP BY n.nspname, c.relname
HAVING count(DISTINCT regexp_replace(r.rolname, '_bench$', '')) > 1;

-- 2. Cross-boundary access is exactly the published list. A producer may also
--    read its own views. CI reads the same pairs from contracts/db/grants.csv.
WITH expected(role, obj) AS (VALUES
    ('svc_auth', 'api_users.credential_v1'),
    ('svc_sales', 'api_products.product_ref_v1'),
    ('svc_recommendations', 'api_products.product_ref_v1'),
    ('svc_engagement', 'api_products.product_ref_v1'),
    ('svc_recommendations', 'api_categories.category_tree_v1'),
    ('svc_checkins', 'api_stores.store_directory_v1'),
    ('svc_engagement', 'api_stores.store_directory_v1'),
    ('svc_recommendations', 'api_stock.stock_level_v1'),
    ('svc_sales', 'api_stock.consume_v1'),
    ('svc_sales', 'api_customers.customer_ref_v1'),
    ('svc_preferences', 'api_customers.customer_ref_v1'),
    ('svc_segments', 'api_customers.customer_ref_v1'),
    ('svc_recommendations', 'api_customers.customer_ref_v1'),
    ('svc_engagement', 'api_customers.customer_ref_v1'),
    ('svc_recommendations', 'api_preferences.interest_category_v1'),
    ('svc_recommendations', 'api_sales.purchase_line_v1'),
    ('svc_recommendations', 'api_segments.current_label_v1')
), actual(role, obj) AS (
    SELECT r.rolname, n.nspname || '.' || c.relname
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_roles r ON r.rolname LIKE 'svc\_%'
     WHERE n.nspname LIKE 'api\_%'
       AND has_table_privilege(r.oid, c.oid, 'SELECT')
       AND r.rolname <> 'svc_' || substr(n.nspname, 5)
    UNION
    SELECT r.rolname, n.nspname || '.' || p.proname
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
      JOIN pg_roles r ON r.rolname LIKE 'svc\_%'
     WHERE n.nspname LIKE 'api\_%'
       AND has_function_privilege(r.oid, p.oid, 'EXECUTE')
)
(SELECT 'unexpected' AS problem, role, obj FROM actual
 EXCEPT SELECT 'unexpected', role, obj FROM expected)
UNION ALL
(SELECT 'missing', role, obj FROM expected
 EXCEPT SELECT 'missing', role, obj FROM actual);

-- 3. Every SECURITY DEFINER function is owned by a NOLOGIN definer_* role,
--    pins search_path, and is not executable by PUBLIC.
SELECT p.oid::regprocedure, o.rolname
  FROM pg_proc p
  JOIN pg_roles o ON o.oid = p.proowner
 WHERE p.prosecdef
   AND (o.rolname NOT LIKE 'definer\_%'
        OR o.rolcanlogin
        OR NOT EXISTS (SELECT 1 FROM unnest(coalesce(p.proconfig, '{}')) AS cfg
                        WHERE cfg LIKE 'search_path=%')
        OR has_function_privilege('public', p.oid, 'EXECUTE'));

-- 4. Every outbox has exactly the six key-only columns.
SELECT table_schema,
       string_agg(column_name, ',' ORDER BY ordinal_position) AS columns
  FROM information_schema.columns
 WHERE table_schema LIKE '%\_events' AND table_name = 'outbox'
 GROUP BY table_schema
HAVING string_agg(column_name, ',' ORDER BY ordinal_position)
       <> 'event_id,stream,event_type,entity_id,occurred_at,relayed_at';

-- 5. A capture trigger only names streams its own boundary produces.
SELECT n.nspname, m[1] AS stream
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace,
       regexp_matches(p.prosrc, '''(stream:[a-z_]+\.v[0-9]+\.[a-z_]+)''', 'g') AS m
 WHERE n.nspname LIKE '%\_events'
   AND split_part(substr(m[1], 8), '.', 1) <> regexp_replace(n.nspname, '_events$', '');
```

```sql
-- 6. A web write produces an event, and the web role cannot see the outbox.
BEGIN;
SET LOCAL ROLE retail_app;
UPDATE product SET name = name WHERE product_id = (SELECT min(product_id) FROM product);
RESET ROLE;
SELECT stream, event_type, entity_id
  FROM products_events.outbox
 WHERE relayed_at IS NULL
 ORDER BY occurred_at DESC
 LIMIT 1;                                                         -- stream:products.v1.changed | UPDATE | <that id>
SELECT has_schema_privilege('retail_app', 'products_events', 'USAGE');   -- false
ROLLBACK;
```

```bash
# 7. No service LISTENs to, or names, another boundary's outbox channel.
for dir in services/*/; do
  name=$(basename "$dir")
  case "$name" in customer-preferences) own=preferences ;; *) own=${name//-/_} ;; esac
  grep -rhoE '\b[a-z_]+_outbox\b' --exclude-dir=tests "$dir" | sort -u \
    | grep -vx "${own}_outbox" | sed "s|^|FAIL: $name names |"
done

# 8. Only the kit's relay calls XADD; no service writes a stream directly.
grep -rniE '\bxadd\b' --exclude-dir=tests services/ bench/ && echo "FAIL: direct XADD in a service"

# 9. Every stream a capture trigger names has a contract, so its producer is on record.
for s in $(psql -X -d retail -tAc "SELECT DISTINCT m[1] FROM pg_proc p,
      regexp_matches(p.prosrc, '''(stream:[a-z_]+\.v[0-9]+\.[a-z_]+)''', 'g') AS m"); do
  b=${s#stream:}; v=$(echo "$b" | grep -oE '\.v[0-9]+\.' | tr -d .)
  f="contracts/streams/${b%%.v[0-9]*}.${b#*.v[0-9]*.}.$v.md"
  [ -f "$f" ] || echo "FAIL: $s has no contract ($f)"
done
```

[ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)'s
`ACL DRYRUN` loop checks at runtime that only a stream's producer can `XADD`
to it. The shared conformance suite deactivates a user through the web's own
code path, with `retail_app`. It then expects that user's next service request
to return 401 within the relay's 5-second poll plus one second.
