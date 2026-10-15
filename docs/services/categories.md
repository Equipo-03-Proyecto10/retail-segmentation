# categories

> **Status:** Planned (phase 14, core). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** The category tree has no cycles, and a category with products, children or customer interests is never deleted through services (RN-33, RN-06, RN-07)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5004 |
| Public path | `/api/categories/v1/` |
| Wave | core |
| PostgreSQL role | `svc_categories` |
| Redis ACL user | `svc_categories`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-categories` / `ghcr.io/equipo-03-proyecto10/mosaiq-categories` |
| Used by | Android: category names for the interest picker (F17-04). Electron: category maintenance (F16-02). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- The tree is acyclic (`trg_category_no_cycle`, RN-33; 409 `category_cycle`). A category with products (RN-06) or children (RN-07) is refused by `ON DELETE RESTRICT`.
- A category that customers name as an interest is refused by `trg_category_delete_guard` for `svc_*` sessions (E-13). The web's own cascade is unchanged.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/categories` | List (`parent_id`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |
| GET | `/v1/categories/{category_id}` | One category | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |
| GET | `/v1/categories/{category_id}/descendants` | The subtree | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |
| POST | `/v1/categories` | Create | ADMIN |
| PATCH | `/v1/categories/{category_id}` | Edit; a cycle is 409 | ADMIN |
| DELETE | `/v1/categories/{category_id}` | Delete; refused while products, children or interests exist | ADMIN |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `category` | Table (`public`) | S+W: SELECT, INSERT, UPDATE, DELETE (every delete passes the guard) | The web is a legacy co-writer (E-09) |
| `categories_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_categories`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_categories.category_tree_v1` | recommendations |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:categories.v1.changed` | captured from `category` | Own cache and recommendations' cache |
| Consumes | `stream:categories.v1.changed` | group `categories` | Invalidates its own tree cache |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| cache | `categories:cache:*` | 15 min | Tree reads, invalidated on change |
| state | `categories:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 409 | `category_cycle` | The edit would make a category its own ancestor or parent |
| 409 | `category_has_products` | Delete with products (RN-06) |
| 409 | `category_has_children` | Delete with children (RN-07) |
| 409 | `category_has_customer_interest` | Delete while a customer names it as an interest (E-13) |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-09 — the frozen web is a legacy co-writer of some tables

- **What:** The web still writes `app_user`, `product`, `category`, `campaign`, `transaction`, `transaction_line` and, through its CSV path, `inventory`. "Only service writer" is not "only writer".
- **Why it is acceptable:** The web is frozen, yet a phone sale must appear in it and its edits must keep working. Services therefore write the same `public` tables the web reads.
- **Guard:** Rules both enforce live in the schema (CHECKs, unique indexes, triggers) or are pinned by golden fixtures (E-17). The owner's outbox trigger captures web writes too, so no writer is invisible.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Context); [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Events). Register: [E-09](./README.md#e-09--the-frozen-web-is-a-legacy-co-writer-of-some-tables).

### E-12 — audit_log is the one table every writer shares

- **What:** Every audited write, from any service or the web, lands in `audit_log` through `fn_audit`. audit-trail reads it.
- **Why it is acceptable:** The audit is a cross-cutting append-only record (RN-28 to RN-30). One writer function keeps it uniform and attributable, and services pass the actor with `set_config('mosaiq.user_id', <sub>, true)`.
- **Guard:** `fn_audit` is SECURITY DEFINER, owned by `definer_audit`. No `svc_*` role writes the table, and only `svc_audit_trail` reads it.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Compliance); [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) (The tables). Register: [E-12](./README.md#e-12--audit_log-is-the-one-table-every-writer-shares).

### E-13 — a definer guard reads rows its service cannot read

- **What:** Deleting a category is refused when customers name it as an interest, a fact owned by customer-preferences. Activating a campaign is refused while an attached experiment is not ready (RN-24), a fact held in the web-only `experiment` and `experiment_group` tables.
- **Why it is acceptable:** `customer_interest_category` cascades on a category delete. Without the guard, a categories delete would remove another boundary's rows. The refusal protects customer-preferences' data without writing it. The web refuses to activate a campaign whose experiment has no treatment arm; without the guard, campaigns would activate it, because `svc_campaigns` may not read the experiment tables.
- **Guard:** `trg_category_delete_guard` and `trg_campaign_activation_guard` read with `definer_guard` (SELECT on `customer_interest_category`, `experiment` and `experiment_group` only), and neither service gains a privilege on those tables. The delete guard applies to `svc_*` sessions and the activation guard to `svc_campaigns` sessions only, so the web's cascade and its own activation check are unchanged.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Ownership and grants (no cascade, activation guard)); [ADR-0045](../adr/0045-services-never-import-the-web-system.md) (How a shared rule is carried). Register: [E-13](./README.md#e-13--a-definer-guard-reads-rows-its-service-cannot-read).

### E-19 — one PostgreSQL, one DDL file, one redis-state, one MongoDB

- **What:** All services share one PostgreSQL database, one DDL file, one `redis-state`, one `redis-cache` and one MongoDB server. Independence is by role, schema and prefix, not by instance.
- **Why it is acceptable:** The course requires one DDL file that runs clean from empty on one 8 GB VM, with no managed database, and the web must see what services write.
- **Guard:** One `svc_*` role per service, one ACL user per service limited to its prefixes, one MongoDB user on one database, and a memory budget CI checks. Each boundary's schema section is idempotent and deploys expand-before-deploy.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Alternatives considered); [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) (Instances); [ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) (Decision). Register: [E-19](./README.md#e-19--one-postgresql-one-ddl-file-one-redis-state-one-mongodb).

### E-20 — the liveness key and the JWKS file are read by every service

- **What:** Every service reads `pub:auth:v1:live:{sid}` and the mounted `/run/mosaiq/jwks.json`, both published by auth.
- **Why it is acceptable:** Authentication must be checked on every request, locally, without HTTP between services.
- **Guard:** The Redis selector allows `EXISTS` on that one family and nothing else of `auth:*`. Both are versioned contracts (`contracts/auth/redis-liveness.v1.md`). Redis failure fails closed.
- **Decided in:** [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) (Verification in every service); [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) (ACLs). Register: [E-20](./README.md#e-20--the-liveness-key-and-the-jwks-file-are-read-by-every-service).

### E-21 — a mechanism-only kit is shared by every service

- **What:** Python services pin `mosaiq-kit` and Rust services pin `kit-rs` by tag: token verification, negotiation, errors, pagination, health, relay and consumer helpers.
- **Why it is acceptable:** Re-implementing security and negotiation fifteen times would be worse for independence and safety than one pinned, versioned mechanism.
- **Guard:** A kit holds no permission names, no rule and no Redis key except the liveness family. Each service pins an exact tag and upgrades in its own pull request, and path dependencies are rejected.
- **Decided in:** [ADR-0045](../adr/0045-services-never-import-the-web-system.md) (Decision). Register: [E-21](./README.md#e-21--a-mechanism-only-kit-is-shared-by-every-service).

## Failure behaviour

| Dependency down | Effect |
|---|---|
| PostgreSQL unreachable | `/health/ready` reports not ready, and every operation that reads or writes PostgreSQL answers 503 `database_unavailable`, never 500 (ADR-0035). `/health/live` stays 200 |
| `redis-cache` down | Reads fall through to PostgreSQL; same answer, slower |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| `redis-state` full or down | The relay cannot `XADD`; unrelayed outbox rows wait until there is room, and consumers' caches expire by TTL |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (the guard holds for services only: as `svc_categories` the delete fails; as `retail_app` it still cascades).
- [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) Compliance (outbox capture, stream ownership).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-07 contract, F14-08 implementation; consumed by F17-04, F16-02. Issue numbers live in [`docs/backlog.md`](../backlog.md).
