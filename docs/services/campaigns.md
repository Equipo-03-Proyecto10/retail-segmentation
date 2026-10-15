# campaigns

> **Status:** Planned (phase 14, second wave). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** A campaign moves only DRAFT→ACTIVE/CANCELLED and ACTIVE→FINISHED/CANCELLED, is edited only in DRAFT, never ends before it starts, and is activated only before it has ended and while every attached experiment is ready (RN-15, RN-16, RN-22, RN-24 (activation), RN-31, RN-32, RN-49 (activation))

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5014 |
| Public path | `/api/campaigns/v1/` |
| Wave | second wave |
| PostgreSQL role | `svc_campaigns` |
| Redis ACL user | `svc_campaigns`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-campaigns` / `ghcr.io/equipo-03-proyecto10/mosaiq-campaigns` |
| Used by | Electron: campaigns (second wave, F16-08). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md), [ADR-0045](../adr/0045-services-never-import-the-web-system.md) |

## Business rule

- Each transition is conditional on the status that was read (`expected_status`), so two administrators cannot both move a campaign. Edits are allowed only in DRAFT, and the end date is never before the start.
- `campaign_id` is allocated as max+1 under the web's advisory lock key 221001, with a retry on 23505 (E-14). A campaign targets a stable segment label code. No DELETE grant.
- Activation is refused for a campaign whose `ends_on` is before PostgreSQL's `CURRENT_DATE`, checked by the service in the same transaction (RN-49), and by `trg_campaign_activation_guard` while an attached experiment has no treatment group, or has no control and other than two treatment groups (RN-24, E-13). The guard reads the web-only experiment tables as `definer_guard` and fires for `svc_campaigns` sessions only.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/campaigns` | List (`status`, `label_code`, `page`, `size`) | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/campaigns/{campaign_id}` | One campaign | ADMIN, ANALYST, MARKETING, AUDITOR |
| POST | `/v1/campaigns` | Create (born DRAFT) | ADMIN, MARKETING |
| PATCH | `/v1/campaigns/{campaign_id}` | Edit (DRAFT only) | ADMIN, MARKETING |
| POST | `/v1/campaigns/{campaign_id}/transitions` | Change status (`to`, `expected_status`) | ADMIN, MARKETING |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `campaign` | Table (`public`) | S+W: SELECT, INSERT, UPDATE; no DELETE (every service activation passes the guard) | Audited (E-12); the web is a legacy co-writer (E-09) |

## Database contracts

None.

## Events

None.

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `campaigns:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 409 | `campaign_not_draft` | An edit outside DRAFT |
| 409 | `illegal_transition` | A move the state machine does not allow |
| 409 | `status_changed` | `expected_status` no longer matches |
| 409 | `campaign_ended` | A move to ACTIVE after `ends_on` (RN-49) |
| 409 | `experiment_not_ready` | A move to ACTIVE while an attached experiment is not ready, refused by `trg_campaign_activation_guard` (RN-24, E-13) |
| 422 | Undecided; the contract story fixes the code | An end date before the start (F14-27 fixes the code) |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-09 — the frozen web is a legacy co-writer of some tables

- **What:** The web still writes `app_user`, `product`, `category`, `campaign`, `transaction`, `transaction_line` and, through its CSV path, `inventory`. "Only service writer" is not "only writer".
- **Why it is acceptable:** The web is frozen, yet a phone sale must appear in it and its edits must keep working. Services therefore write the same `public` tables the web reads.
- **Guard:** Rules both enforce live in the schema (CHECKs, unique indexes, triggers) or are pinned by golden fixtures (E-17). The owner's outbox trigger captures web writes too, so no writer is invisible.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Context); [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Events). Register: [E-09](./README.md#e-09--the-frozen-web-is-a-legacy-co-writer-of-some-tables).

### E-11 — foreign keys cross service boundaries

- **What:** Tables owned by different services reference each other (`customer.channel_id`, `transaction_line.product_id`, `campaign.label_code` to `segment_label`, `checkins.store_check_in` to `store` and `app_user`).
- **Why it is acceptable:** They come from the legacy schema or protect it. The RI check runs as the parent's owner, so the child's service needs no privilege on the parent. Dropping them would weaken the model the web relies on.
- **Guard:** A violation (23503) maps to a 422 by constraint name. No service holds DELETE on a master, so no cascade starts from a service. The category guard covers the remaining case.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Consequences). Register: [E-11](./README.md#e-11--foreign-keys-cross-service-boundaries).

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

### E-14 — campaigns shares the web's id-allocation lock

- **What:** `campaign_id` is allocated as max+1 under `pg_advisory_xact_lock(221001)`, the key the web uses.
- **Why it is acceptable:** Both writers allocate ids the same way. Using the same lock keeps them from drawing the same number.
- **Guard:** The key is recorded under `contracts/db/` next to the table, the service retries on 23505, and the primary key backstops any slip.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Ownership and grants). Register: [E-14](./README.md#e-14--campaigns-shares-the-webs-id-allocation-lock).

### E-17 — rules ported from the web exist twice

- **What:** Four rules exist in the web and in a service: sale validation, the RN-40 recommendation rule, user outcomes and campaign transitions.
- **Why it is acceptable:** A service may not import `web/`, which is frozen, loads Flask and has no version to pin. Carrying a rule twice is the price of independence.
- **Guard:** The database carries what it can (CHECKs, indexes, triggers). The rest is a port pinned by golden fixtures captured once from the web on the seed database, with a drift job. A web bug fix opens an issue to regenerate them.
- **Decided in:** [ADR-0045](../adr/0045-services-never-import-the-web-system.md) (Decision); [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (Context). Register: [E-17](./README.md#e-17--rules-ported-from-the-web-exist-twice).

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
| Second-wave service | If memory runs short it is dropped first (ADR-0043); nothing else depends on it |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (no DELETE on `campaign`; at most one service role on the table; the activation guard holds for services only).
- [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) Compliance (registry marks it `wave: second`).
- Golden fixtures for campaign transitions ([ADR-0045](../adr/0045-services-never-import-the-web-system.md)).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-27 contract, F14-28 implementation; consumed by F16-08. Issue numbers live in [`docs/backlog.md`](../backlog.md).
