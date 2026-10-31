# ADR-0044 — Boundaries meet only through versioned database contracts and Redis Streams relayed from each owner's outbox

**Status:** Partially superseded by ADR-0061, ADR-0062 and ADR-0063 (one synchronous cross-boundary call)
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** [ADR-0061](0061-stock-keeps-one-quantity-per-store-and-changes-only-through-a-sale-a-reversal-or-a-recorded-movement.md), [ADR-0062](0062-a-reversal-is-a-recorded-fact-and-every-reader-counts-net-units.md) and [ADR-0063](0063-segments-change-on-events-and-on-a-schedule-scored-against-the-published-version.md), in part

---

## Context

Fifteen services share one PostgreSQL on one VM ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)),
yet must be totally independent, with no HTTP between them. recommendations reads seven boundaries;
sales must consume stock in the sale's own transaction (RN-14, [ADR-0031](0031-sales-ingestion-decrements-inventory-atomically.md));
auth and caches must learn of changes. **The frozen web writes many of the same tables** and has no
Redis (Q-12), so a signal in the writer's code misses web writes; **`pg_notify` is neither durable
nor private**. On 2026-10-13 the team chose a hybrid: reads through versioned database contracts,
**every event between services on Redis Streams**, `pg_notify` only as an owner's private wake-up.
What is uncertain is how a reviewer weighs the shared database.

## Decision

Boundaries meet in exactly two ways. **Reads** go through `*_v1` objects the producer publishes in its
own `api_<suffix>` schema, where `<suffix>` is the boundary's `svc_` role suffix. **Events** go on
Redis Streams, published by the owner's relay from a transactional outbox that a SECURITY DEFINER
trigger fills for every writer, the frozen web included. The one synchronous exception runs inside
the database, never over HTTP: `api_stock.consume_v1`, called inside sales-service's own transaction.
Nothing else crosses a boundary: no service holds a privilege on another boundary's base table,
calls another over HTTP, or LISTENs on another boundary's channel, and no event carries state, only
keys the consumer re-reads through the producer's views.

### Reads: the published database contracts

| Object | Owner | v1 columns or signature | Consumed by |
|---|---|---|---|
| `api_users.credential_v1` | users | `user_id`, `email_lower`, `password_hash`, `is_active`, `role_code` | auth (its only consumer) |
| `api_products.product_ref_v1` | products | `product_id`, `sku`, `name`, `category_id`, `list_price`, `is_active` | sales, recommendations, engagement |
| `api_categories.category_tree_v1` | categories | `category_id`, `parent_category_id`, `name` | recommendations |
| `api_stores.store_directory_v1` | stores | `store_id`, `name`, `city`, `state`, `is_active`, `latitude`, `longitude`, `geofence_radius_m` | check-ins, engagement |
| `api_stock.stock_level_v1` | stock | `store_id`, `product_id`, `quantity_on_hand`, `updated_at` | recommendations |
| `api_stock.consume_v1(smallint, int, int)` | stock | `(store_id, product_id, quantity)` returns the remaining `int` | sales |
| `api_customers.customer_ref_v1` | customers | `customer_id`, `registered_on` | sales, customer-preferences, segments, recommendations, engagement |
| `api_preferences.interest_category_v1` | customer-preferences | `customer_id`, `category_id` | recommendations |
| `api_sales.purchase_line_v1` | sales | `transaction_id`, `customer_id`, `store_id`, `channel_id`, `occurred_at`, `product_id`, `quantity`, `unit_price` | recommendations |
| `api_segments.current_label_v1` | segments | `customer_id`, `state`, `label_code`, `since`, `run_id` | recommendations |

Views keep `security_invoker = off`. A consumer holds USAGE plus SELECT, nothing on the table; a
producer also holds SELECT on its own views. `credential_v1` is the only view that carries a
credential, and auth is its only consumer. Consumers name the columns they read, so a new column
is not breaking. Contracts are `contracts/db/<schema>.<object>.v1.md`; grants are in `grants.csv` (ADR-0034).

### The one synchronous call: `api_stock.consume_v1`

So that a sale and its stock commit together with no saga (ADR-0031), `consume_v1` is plpgsql,
SECURITY DEFINER, owned by NOLOGIN `definer_stock` (SELECT on `inventory`, UPDATE of `quantity_on_hand`
and `updated_at`, INSERT on `stock_events.outbox` through the capture trigger), with
`SET search_path = public, pg_temp` and EXECUTE for `svc_sales` only. A NULL or ≤ 0 quantity raises
SQLSTATE 22023 (`invalid_quantity`) before any row is touched. Otherwise it runs the web's conditional
`UPDATE ... WHERE quantity_on_hand >= $3 RETURNING` (`decrement_stock`); when no row matches, a
`SELECT ... FOR UPDATE` tells `no_stock_record` from `stock_unavailable`, raised as SQLSTATE 23514.
Callers cast `$1::smallint` (int4 to int2 is not implicit). It runs inside sales-service's transaction
([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)) and is the only cross-boundary write; a second one needs its own record.

### Events: a transactional outbox per owner, relayed to Redis Streams

**Capture.** An AFTER row trigger on each captured table calls a SECURITY DEFINER function owned by
`definer_<suffix>` (`definer_stock` for stock), with `search_path` pinned and EXECUTE revoked from PUBLIC.
It inserts one row into `<suffix>_events.outbox` and calls `pg_notify('<suffix>_outbox', '')`. The eight
outbox schemas are `users_events`, `products_events`, `categories_events`, `stores_events`, `stock_events`,
`preferences_events`, `sales_events` and `segments_events`; their channels are `users_outbox`, `products_outbox`,
`categories_outbox`, `stores_outbox`, `stock_outbox`, `preferences_outbox`, `sales_outbox` and `segments_outbox`.
The writer needs no outbox privilege, so web edits, CSV ingestion and hard deletes all produce events, and a
rollback removes the event. An outbox has exactly `event_id` (uuid PK, `gen_random_uuid()`), `stream`,
`event_type` (TG_OP), `entity_id`, `occurred_at` (default `now()`) and `relayed_at` (NULL until relayed),
with a partial index on `occurred_at` WHERE `relayed_at IS NULL`. The streams:

| Stream | Producer (captured from) | Consumer groups |
|---|---|---|
| `stream:users.v1.access_changed` | users (`app_user` UPDATE OF `is_active`, `role_id`, `password_hash`, and DELETE) | `auth`: ends every session of that user (liveness key, session hash and refresh family). auth also reconciles live sessions against `api_users.credential_v1` every 60 s |
| `stream:products.v1.changed` | products (`product`) | `products` (its own cache), `recommendations` |
| `stream:categories.v1.changed` | categories (`category`) | `categories` (its own cache), `recommendations` |
| `stream:stores.v1.changed` | stores (`store`, `stores.store_location`) | `stores` (its own cache) |
| `stream:stock.v1.changed` | stock (`inventory`) | `recommendations` |
| `stream:preferences.v1.changed` | customer-preferences (`customer_preferred_channel`, `customer_interest_category`) | `recommendations` |
| `stream:sales.v1.recorded` | sales (`transaction` AFTER INSERT OR UPDATE OF `total`, from the CSV path and the service) | `recommendations` |
| `stream:segments.v1.run_recorded` | segments (`segmentation_run` INSERT) | `segments` (its own cache), `recommendations` |

`entity_id` is the changed row's key: `store_id:product_id` for `inventory`, `customer_id` for a
preference row, `store_id` for `store_location`, `user_id` for the users stream. A service sale adds
one sales outbox row; a CSV row that creates a header adds two (INSERT, then UPDATE OF `total`); an
appended CSV line adds one. Consumers dedupe by `event_id`. Outboxes are seed-exempt runtime state.

**Relay.** The owner relays through the kit's helper ([ADR-0045](0045-services-never-import-the-web-system.md)):
it waits at most 5 s on its own channel, then in one transaction runs
`SELECT ... WHERE relayed_at IS NULL ORDER BY occurred_at LIMIT 100 FOR UPDATE SKIP LOCKED`,
`XADD <stream> MAXLEN ~ 10000 *` on `redis-state` (`event_id`, `v 1`, `event_type`, `entity_id`, `occurred_at`),
`WAITAOF 1 0 2000`, sets `relayed_at` and commits. Hourly it purges relayed rows older than 7 days, the replay
log. No high-water mark; `SKIP LOCKED` allows a second replica; delivery is at least once.

**Consume.** One group per consuming service and one consumer per container, created with
`XGROUP CREATE <stream> <group> $ MKSTREAM`. On start it re-reads its own pending entries, claims
entries idle over 60 s, then reads `>`; it `XACK`s after handling and is idempotent by `event_id`. Five
failed deliveries are logged and acknowledged; the outbox row stays for replay. Payloads carry ids; state
is re-read through the producer's views.

**`pg_notify` is only a wake-up** (empty payload); only the owner's relay listens. Identical
notifications within one transaction fold into one. The CSV path commits once per row, so an import
wakes the relay once per row; each wake-up drains up to 100 pending rows, so wake-ups coalesce in
practice, and a lost one is covered by the 5-second poll. No service LISTENs on another boundary's channel.

### Versioning and order of change

A breaking change ships `<name>_v2` beside `<name>_v1`, dropped once `grants.csv` shows no consumer,
and `stream:<producer>.v2.<event>` beside v1, the trigger writing one row per version. **Expand before
deploy:** the boundary's section of `sql/01_schema.sql` lands first. It is idempotent (`IF NOT EXISTS`,
`CREATE OR REPLACE`; ADR-0040 re-applies every section in CI), so an operator applies it to the live
database before the service that uses it ships. Nothing is contracted while a consumer still holds a grant on it.

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
- Web writes produce events with no web change; producers reshape tables freely behind `_v1`; the
  sale stays atomic with its stock; every coupling is listed; a Redis loss is replayable.

**What this makes hard.**
- **Shared runtime:** one PostgreSQL outage stops every service; a slow view slows its consumers.
- **Every captured write costs one more INSERT, the web's included:** a full disk fails the web's write.
- **Caches are eventually consistent** by the relay latency (at most the 5 s poll).
- **At least once means idempotent or wrong,** for every future consumer too.
- **`consume_v1` couples sales to stock:** a broken function stops every phone sale.
- **recommendations depends on seven contracts.**
- **The relay holds a transaction across Redis calls,** bounded by 100 entries and 2 s.
- **The private-channel rule is enforced by grep,** as LISTEN cannot be revoked.

**What must now be true elsewhere.**
- `sql/00_create_database.sql` creates the definer roles (ADR-0034). Each boundary's idempotent section
  holds its `api_<suffix>` schema and objects, its `<suffix>_events` schema and outbox, its capture
  triggers and grants, out of `public` ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)); outboxes are seed-exempt.
- [ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) grants
  `+xadd` only to producers and group commands only to consumers, for exactly these streams. ADR-0036
  consumes the users stream and keeps the 60 s reconcile. ADR-0039 calls `consume_v1` per line, in
  ascending `product_id` order. The kits carry the relay and consumer helpers (ADR-0045). `contracts/db/`
  documents every `api_*` object, outbox channel and advisory lock key; `contracts/streams/`, every stream (ADR-0035).

## Compliance

As the database owner, `contracts/db/check_boundaries.sql` (the full draft's SQL, except that check 2's expected
set is read from `grants.csv`'s `svc_*` rows on `api_*` objects, not the draft's hard-coded pairs) returns no rows: (1) outside
`api_*` schemas, no relation is reachable by two boundaries; (2) cross-boundary access equals the published list in `grants.csv`,
producers' SELECT on their own views excepted, and EXECUTE on `consume_v1` for `svc_sales` only; (3) every outbox
has exactly the six columns; (4) a capture trigger names only its own boundary's streams. `contracts/streams/check_streams.sh` finds every captured stream's contract file.
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
SET ROLE svc_sales; DO $$ DECLARE q int; BEGIN FOREACH q IN ARRAY ARRAY[0, -1] LOOP BEGIN  -- 0 and -1: 22023
  PERFORM api_stock.consume_v1(1::smallint, 1, q); RAISE 'quantity % accepted', q;
  EXCEPTION WHEN invalid_parameter_value THEN NULL; END; END LOOP; END $$; RESET ROLE;
```
```bash
for dir in services/*/; do name=$(basename "$dir")  # 7. No other boundary's outbox channel (own = suffix).
  case "$name" in customer-preferences) own=preferences ;; *) own=${name//-/_} ;; esac
  grep -rhoE '\b[a-z_]+_outbox\b' --exclude-dir=tests "$dir" | sort -u \
    | grep -vx "${own}_outbox" | sed "s|^|FAIL: $name names |"; done
grep -rniE '\bxadd\b' --include='*.py' --include='*.rs' --exclude-dir=tests services/ bench/ \
  && echo "FAIL: direct XADD in a service"  # 8. Only the kit XADDs. Source only: redis/<name>.acl grants +xadd.
```
At runtime, ADR-0037's `ACL DRYRUN` loop checks that only a stream's producer can `XADD` to it,
and a user deactivated through the web's own code path gets 401 within the 5 s poll plus 1 s.
