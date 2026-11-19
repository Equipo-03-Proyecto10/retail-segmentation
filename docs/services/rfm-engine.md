# rfm-engine

> **Status:** Incremental update implemented (phase 22, third; F22-05, #779, and F22-06, #780) on the stand-up of F22-04 (#778): a sale on `stream:sales.v1.recorded` and a reversal on `stream:sales.v1.reversed` each recompute the one customer they name, and `GET /v1/rfm-rule-sets` serves the seeded rule set. The other operations are still `planned` (ADR-0073). The contract was published by F22-03 (#777). ADR-0059 places it; ADR-0063 and ADR-0064 decide how it behaves.
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
| `mem_limit` | 128 MB (ADR-0059's first cut, on the F19-08 measurement in [`f19-08-memory.md`](../evidence/f19-08-memory.md)) |
| Used by | Electron: customer exploration (F28-03) and the RFM configuration module (F28-04). model-registry and analytics-kpis read its current profiles. |
| Governing ADRs | [ADR-0059](../adr/0059-four-new-services-join-the-fifteen-and-six-capabilities-fold-into-the-rule-they-extend.md), [ADR-0062](../adr/0062-a-reversal-is-a-recorded-fact-and-every-reader-counts-net-units.md), [ADR-0063](../adr/0063-segments-change-on-events-and-on-a-schedule-scored-against-the-published-version.md), [ADR-0064](../adr/0064-a-model-version-is-immutable-and-a-change-of-version-is-never-reported-as-behaviour.md), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- A profile is computed from the customer's net purchases (accepted, less reversals) in the rule set's window ending at the instant it is processed. R is whole days since the latest net purchase, F the number of net purchases, M the sum of net amounts. Each is scored 1 to 5 against the boundaries frozen in the rule set, never against the population, and the scores select a label from the rule set's non-overlapping bands, or its fallback label when none matches (RN-46). A customer with no net purchase in the window is unassigned (RN-21).
- A reversal leaves the profile as if the reversed units had never counted (ADR-0062). A whole cancellation, or a return of every unit of a sale, takes that sale out of F and R, so the most recent purchase can move back to the sale before it. A partial return lowers M and leaves F alone, because the sale still has a unit. A customer left with no net purchase in the window is *unassigned* (RN-21), never scored zero. A late sale counts by its `occurred_at` and never rewrites a closed run (ADR-0017), and one outside the window changes nothing. Redelivery is harmless twice over: the consumer drops an `event_id` it handled, and a recomputation that moves no captured value announces nothing.
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
| `rfm.customer_profile` | Table (`rfm`) | S | One row per processed customer; `scored` or `unassigned` (a customer with no row is `pending` in the view). Seed-exempt: the first event, or the daily pass, creates a row. Indexed on `next_change_at` for the pass (F22-07) |
| `rfm.rule_set`, `rfm.rule_set_band` | Tables (`rfm`) | SELECT; the seed inserts the one rule set | The window, the frozen boundaries and the bands. The seed holds the scoring of the web's published rule version (`web-rfm-rules-180d`) with the 30 segment boxes as bands; F22-09 creates the others. `rule_set` is seed-exempt, `rule_set_band` has 30 rows |
| `rfm.recalculation`, `rfm.recalculation_difference` | Tables (`rfm`) | S | Named by the verification story (F22-08) |
| `rfm_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_rfm` through `trg_capture_profile_inserted` and `trg_capture_profile_changed`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_rfm.current_profile_v1` | model-registry (granted), analytics-kpis (granted with its role) |
| Consumes | `api_customers.customer_ref_v1` | customers (a 404 for an unknown customer; the rows of `pending`) |
| Consumes | `api_sales.purchase_line_v1` | sales (finds the customer of a sale) |
| Consumes | `api_sales.net_purchase_line_v1` | sales (net purchases, ADR-0062) |
| Consumes | model-registry's published-version view (F22-13) | model-registry (which rule set is published). Until it exists the rule set in force is `RFM_RULE_SET_ID` (default 1, the seeded one) |
| Calls | `api_segments.apply_assignment_v1` (ADR-0063) | segments (applies the label, in the same transaction as the profile) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:rfm.v1.profile_changed` | captured from `rfm.customer_profile` | Tells model-registry and analytics-kpis a customer's profile changed |
| Consumes | `stream:sales.v1.recorded` | group `rfm-engine` | Recomputes the customer of the sale (F22-05) |
| Consumes | `stream:sales.v1.reversed` | group `rfm-engine` | Recomputes the customer of the reversed sale (F22-06). `entity_id` is `sale_id:reversal_id`; the customer comes from the gross view, which still lists a sale cancelled in full |
| Consumes | model-registry's publication stream | group `rfm-engine` | Recomputes everyone under the new version, recorded as a change of version |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `rfm:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |
| state | `stream:sales.v1.recorded` | the stream's `MAXLEN` | Consumer group `rfm-engine`: `XREADGROUP`, `XACK`, `XGROUP CREATE`, `XPENDING`, `XCLAIM` |
| state | `stream:rfm.v1.profile_changed` | the stream's `MAXLEN` | `XADD` by the outbox relay |

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

## Concurrency and the clock

- One advisory lock, `pg_advisory_xact_lock(295001, hashtext(customer_id::text))`, is taken before a customer's measures are read, so two consumers handling the same customer's events queue and the second reads what the first wrote (`contracts/db/advisory-locks.md`).
- `computed_at` is the instant of the recomputation, `r_days` is measured to it, and `next_change_at` is the earlier of the oldest purchase leaving the window and the recency passing the largest value its score allows. Neither the outbox trigger nor the equality of two profiles looks at them (`rfm.profile_changed.v1.md`).
- The scoring is `app/profile.py`: recency scores 5 up to the first boundary, 4 up to the second and so on, frequency and monetary the other way, a box of scores picks the label (the lowest band first) or the fallback label with `via_fallback` true. The seed derives its boundaries from the web's own scoring of the last rule-based run, so the service scores a seeded customer as the web did at that instant.

## Failure behaviour

| Dependency down | Effect |
|---|---|
| PostgreSQL unreachable | `/health/ready` reports not ready, and every operation answers 503 `database_unavailable`, never 500. `/health/live` stays 200 |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed. No stream entry is read until it returns, and nothing is lost: the entries wait in the stream |
| `redis-state` full or down | The relay cannot `XADD`; unrelayed outbox rows wait until there is room |
| Any other service down | No effect: services never call each other over HTTP. A stalled sales relay delays recomputation and the daily pass still catches time-driven changes |

## Compliance

- A test recomputes the same events twice and out of order and gets the same profile; another proves one sale writes one customer's profile (ADR-0063): every other profile row is locked by a second connection during the recomputation, which would time out if it read one for writing.
- 1,000 sales cost the same with 5,000 more customers: [`f22-05-incremental-cost.md`](../evidence/f22-05-incremental-cost.md).
- The verification reports zero differences over a scripted history of sales, reversals, late sales and redeliveries (F22-08).
- [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) Compliance (registry: one rule, RN ids resolve), and ADR-0035 (resources are unique).

## Backlog

F22-03 contract (#777), F22-04 stand-up (#778), F22-05 incremental update on a sale (#779), F22-06 reversals, late sales and duplicates (#780), F22-07 to F22-08 and F22-09 implementation; consumed by F28-03, F28-04. Issue numbers live in [`docs/backlog.md`](../backlog.md).
