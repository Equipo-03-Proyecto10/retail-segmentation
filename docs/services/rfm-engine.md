# rfm-engine

> **Status:** Contract published (phase 22, third; F22-03, #777). Not yet implemented: the service is stood up by F22-04 (#778). ADR-0059 places it; ADR-0063 and ADR-0064 decide how it behaves.
> **One rule:** A customer's R, F and M are a function of their accepted, unreversed purchases under one rule-set version, change only for the customers an event touched, and equal a full recalculation of that version at the same instant (RN-65)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5016 |
| Public path | `/api/rfm-engine/v1/` |
| Wave | third |
| PostgreSQL role | `svc_rfm`, schemas `rfm`, `rfm_events` and `api_rfm` |
| Redis ACL user | `svc_rfm`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-rfm-engine` / `ghcr.io/equipo-03-proyecto10/mosaiq-rfm-engine` |
| `mem_limit` | 160 MB (ADR-0059) |
| Used by | Electron: customer exploration (F28-03) and the RFM configuration module (F28-04). model-registry and analytics-kpis read its current profiles. |
| Governing ADRs | [ADR-0059](../adr/0059-four-new-services-join-the-fifteen-and-six-capabilities-fold-into-the-rule-they-extend.md), [ADR-0062](../adr/0062-a-reversal-is-a-recorded-fact-and-every-reader-counts-net-units.md), [ADR-0063](../adr/0063-segments-change-on-events-and-on-a-schedule-scored-against-the-published-version.md), [ADR-0064](../adr/0064-a-model-version-is-immutable-and-a-change-of-version-is-never-reported-as-behaviour.md), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- A profile is computed from the customer's net purchases (accepted, less reversals) in the rule set's window ending at the instant it is processed. R is whole days since the latest net purchase, F the number of net purchases, M the sum of net amounts. Each is scored 1 to 5 against the boundaries frozen in the rule set, never against the population, and the scores select a label from the rule set's non-overlapping bands, or its fallback label when none matches (RN-46). A customer with no net purchase in the window is unassigned (RN-21).
- An event is a trigger and not a delta: the customer is recomputed from net purchases, so a replay or a reordering cannot change a result. Only the customer an event touched, or whose `next_change_at` has passed, is recomputed.
- A verification recomputes everyone at one instant and writes no profile. It reports every customer whose raw R, F and M or label differ from the profile held incrementally. Only the administrator starts one on demand (RN-05).
- A rule set is immutable from creation. Publishing and rolling back belong to model-registry (ADR-0064); `published` here only mirrors that.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/rfm-profiles` | Stored profiles (`state`, `label_code`, `rule_set_id`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/rfm-profiles/{customer_id}` | One customer's profile: `scored`, `unassigned` or `pending` | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/rfm-rule-sets` | Rule sets (`published`, `page`, `size`) | ADMIN, ANALYST, MARKETING, AUDITOR |
| POST | `/v1/rfm-rule-sets` | Create an immutable rule set; 201 | ADMIN |
| GET | `/v1/rfm-rule-sets/{rule_set_id}` | One rule set with its frozen boundaries and bands | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/rfm-recalculations` | Verification recalculations (`state`, `trigger`, `rule_set_id`, `page`, `size`) | ADMIN, ANALYST, AUDITOR |
| POST | `/v1/rfm-recalculations` | Start a verification, on the named or the published rule set; 201 `RUNNING` | ADMIN |
| GET | `/v1/rfm-recalculations/{recalculation_id}` | One recalculation and its outcome | ADMIN, ANALYST, AUDITOR |
| GET | `/v1/rfm-recalculations/{recalculation_id}/differences` | The customers it found different | ADMIN, ANALYST, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`. The contract is [`contracts/rfm-engine/`](../../contracts/rfm-engine/openapi.yaml).

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `rfm.customer_profile`, `rfm.rule_set`, `rfm.recalculation`, `rfm.recalculation_difference` | Tables (`rfm`) | S | Named by the schema story; the contract fixes only what the endpoints serve |
| `rfm_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_rfm`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_rfm.current_profile_v1` | model-registry, analytics-kpis |
| Consumes | `api_customers.customer_ref_v1` | customers (a 404 for an unknown customer; the rows of `pending`) |
| Consumes | `api_sales.purchase_line_v1` | sales (finds the customer of a sale) |
| Consumes | The net view of `api_sales` (F21-02, #770) | sales (net purchases) |
| Consumes | model-registry's published-version view (F22-10, #805) | model-registry (which rule set is published) |
| Calls | `api_segments.apply_assignment_v1` (ADR-0063) | segments (applies the label, in the same transaction as the profile) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:rfm.v1.profile_changed` | captured from `rfm.customer_profile` | Tells model-registry and analytics-kpis a customer's profile changed |
| Consumes | `stream:sales.v1.recorded` | group `rfm-engine` | Recomputes the customer of the sale |
| Consumes | `stream:sales.v1.reversed` | group `rfm-engine` | Recomputes the customer of the reversed sale |
| Consumes | model-registry's publication stream | group `rfm-engine` | Recomputes everyone under the new version, recorded as a change of version |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `rfm:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 404 | `customer_not_found` | The customer is absent from `api_customers.customer_ref_v1`. A customer with no profile is a 200 answer, `pending` |
| 404 | `rule_set_not_found` | The rule set does not exist |
| 404 | `recalculation_not_found` | The recalculation does not exist |
| 409 | `rule_set_name_taken` | `POST /v1/rfm-rule-sets` names an existing rule set |
| 409 | `recalculation_in_progress` | A recalculation is already running |
| 409 | `no_published_rule_set` | `POST /v1/rfm-recalculations` names no rule set and none is published |
| 409 | `recalculation_not_finished` | The differences of a recalculation that is `RUNNING` or `FAILED` |
| 422 | `invalid_query` | A filter is malformed |
| 422 | `invalid_body` | A rule set breaks its rules: `not_monotonic`, `band_range`, `bands_overlap` |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-03 — a foreign id keys a resource that is its own identity

- **What:** `/v1/rfm-profiles/{customer_id}` carries another boundary's id.
- **Why it is acceptable:** A profile exists once per customer, so the id is the profile's own identity.
- **Guard:** No path contains another service's registered resource name; the registry check enforces it.
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md). Register: [E-03](./README.md#e-03--a-foreign-id-keys-a-resource-that-is-its-own-identity).

The call to `api_segments.apply_assignment_v1` (ADR-0063) is a synchronous cross-boundary write inside the database, the kind E-05 registers. Its register entry is amended by the story that publishes the function (F22-14, #810), not by this contract.

## Failure behaviour

| Dependency down | Effect |
|---|---|
| PostgreSQL unreachable | `/health/ready` reports not ready, and every operation answers 503 `database_unavailable`, never 500. `/health/live` stays 200 |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed. No stream entry is read until it returns, and nothing is lost: the entries wait in the stream |
| `redis-state` full or down | The relay cannot `XADD`; unrelayed outbox rows wait until there is room |
| Any other service down | No effect: services never call each other over HTTP. A stalled sales relay delays recomputation and the daily pass still catches time-driven changes |

## Compliance

- A test recomputes the same events twice and out of order and gets the same profile; another proves one sale writes one customer's profile (ADR-0063).
- The verification reports zero differences over a scripted history of sales, reversals, late sales and redeliveries (F22-08).
- [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) Compliance (registry: one rule, RN ids resolve), and ADR-0035 (resources are unique).

## Backlog

F22-03 contract (#777), F22-04 stand-up (#778), F22-05 to F22-08 and F22-09 implementation; consumed by F28-03, F28-04. Issue numbers live in [`docs/backlog.md`](../backlog.md).
