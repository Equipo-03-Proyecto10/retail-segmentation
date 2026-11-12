# experimentation

> **Status:** Contract published (phase 25, third; F25-06, #837). Not yet implemented: the service is stood up by F25-07 (#838), and every operation is marked `x-mosaiq-status: planned` until then (ADR-0073). ADR-0059 places it; ADR-0068 and ADR-0069 decide how it behaves.
> **One rule:** An experiment's frame and arms are fixed before measurement, assignment, exposure and conversion are separate append-only facts, a sale is attributed at most once, and an effect is reported only against a control, with its uncertainty (RN-23, RN-24, RN-25, RN-27, RN-42, RN-48, RN-49, RN-67)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5020 |
| Public path | `/api/experimentation/v1/` |
| Wave | third |
| PostgreSQL role | `svc_experiments`, schemas `experiments` and `api_experiments` |
| Redis ACL user | `svc_experiments`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-experimentation` / `ghcr.io/equipo-03-proyecto10/mosaiq-experimentation` |
| `mem_limit` | 160 MB (ADR-0059) |
| Used by | Electron: the A/B experiment module (F28-10, #879). Android: one customer's arm, before an offer is shown (F27-07, #861). analytics-kpis reads its published views. |
| Governing ADRs | [ADR-0059](../adr/0059-four-new-services-join-the-fifteen-and-six-capabilities-fold-into-the-rule-they-extend.md), [ADR-0068](../adr/0068-an-experiment-is-planned-and-frozen-before-assignment-and-claims-an-effect-only-against-a-control.md), [ADR-0069](../adr/0069-a-sale-is-attributed-to-one-campaign-at-most-and-only-the-difference-against-control-is-incremental.md), [ADR-0053](../adr/0053-experiments-are-measured-on-conversion-only.md), [ADR-0050](../adr/0050-experiment-conversions-are-append-only-for-the-application-role.md), [ADR-0062](../adr/0062-a-reversal-is-a-recorded-fact-and-every-reader-counts-net-units.md), [ADR-0075](../adr/0075-engagement-relays-interaction-facts-on-a-redis-stream-from-an-outbox-kept-on-the-event-document.md), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- A plan is frozen before anyone is assigned (ADR-0068). One transaction stores the frame (the eligible customers at one instant of the PostgreSQL clock), the primary metric (`CONVERSION` or `REVENUE_PER_CUSTOMER`), the estimator, alpha, power, the minimum detectable effect, the baseline, the customers per arm, the allocation, the conversion window and a salt. Nothing in it changes afterwards, and a plan whose frame cannot supply the customers per arm is stored `UNDERPOWERED` and is assigned only on a recorded acknowledgement.
- An assignment is a function of the plan: a customer's key is `HMAC-SHA-256` of the published salt over the experiment and the customer, the frame is ordered by that key and dealt round-robin, so arms differ by at most one and anyone can recompute them. The whole frame is assigned once and never rewritten (RN-23, RN-42). A customer is assigned in at most one experiment whose period overlaps.
- Assignment, exposure and conversion are separate append-only facts (RN-27, ADR-0050). The control is never exposed. Exposures are not written here: they are `CAMPAIGN_EXPOSURE` notices of engagement, projected into this service's own schema from its stream (ADR-0075), and only counted.
- The primary analysis is intent to treat over every assigned customer, net of reversals (ADR-0062), read once at the analysis instant (assignment instant plus the window plus seven days). A later read is labelled as of its time and never replaces the stored primary result.
- A sale is attributed to at most one campaign (ADR-0069): a conversion of a treatment assignment, with a recorded exposure before the sale, inside the experiment's own window. The latest exposure holds it, and the holder is never reassigned or reopened.
- Attribution and effect are two measurements named apart. Only a treatment's difference against a control, with its interval, may be called an effect or incremental. An experiment with no control compares two offers and states a difference between treatments and no effect. A non-significant result of an underpowered experiment is inconclusive.

## Endpoints

Every business operation below is `planned`. Reads are for the roles that read campaigns; the writes are for the roles that write them (see the [service operation policy](../requirements.md#service-operation-policy-distributed-stage)).

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/experiments` | Experiments (`campaign_id`, `state`, `data_origin`, `page`, `size`) | ADMIN, ANALYST, MARKETING, AUDITOR |
| POST | `/v1/experiments` | Plan an experiment and freeze the plan; 201 `PLANNED` | ADMIN, MARKETING |
| GET | `/v1/experiments/{experiment_id}` | One experiment with its arms, design values, frame, salt, assignment summary and flags | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/experiments/{experiment_id}/conversions` | The experiment's conversions (`customer_id`, `arm_id`, `counts`) | ADMIN, ANALYST, MARKETING, AUDITOR |
| POST | `/v1/experiments/{experiment_id}/conversions` | Evaluate conversions: record the qualifying sales not recorded yet; 200 | ADMIN, MARKETING |
| GET | `/v1/experiment-assignments` | Assignments (`experiment_id`, `campaign_id`, `customer_id`, `arm_id`, `arm_kind`) | ADMIN, ANALYST, MARKETING, AUDITOR |
| POST | `/v1/experiment-assignments` | Assign an experiment's whole frame, once, with the stored checks; 201 | ADMIN, MARKETING |
| GET | `/v1/experiment-assignments/current` | One customer's arm in one campaign (`customer_id`, `campaign_id`): the floor's read | ADMIN, STORE_MANAGER, SALES_ASSOCIATE |
| GET | `/v1/experiment-results/{experiment_id}` | The result: arm counts, differences with intervals, assumptions, design values, checks, attribution (`basis` `PRIMARY` or `RE_READ`) | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/sale-attributions` | Attributions (`experiment_id`, `campaign_id`, `customer_id`, `sale_id`, `counts`) | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/sale-attributions/{attribution_id}` | One attribution | ADMIN, ANALYST, MARKETING, AUDITOR |

Arms are part of an experiment, fixed at planning, and have no endpoint of their own. There is no operation to edit or delete an experiment, an assignment, a conversion or an attribution; the absent routes answer 405. Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`. The contract is [`contracts/experimentation/`](../../contracts/experimentation/openapi.yaml). It adds the four resources of ADR-0059, and no other service's registered resource name appears in its paths.

The result names attribution and effect in separate sections. Its `comparisons` carry each difference with `lower`, `upper`, the test and the confidence level; its `attribution` carries only `ATTRIBUTION`-labelled figures, none of which is called incremental, uplift or caused.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `experiment`, `experiment_group`, `experiment_assignment`, `experiment_exposure`, `experiment_conversion` | Tables the web writes today | W until F25-07 | The stand-up story takes ownership and grants the role (#838). This contract moves none of them. Experiments assigned under the web's earlier shuffle are served as assignment rule `V0`: readable, with no frame, salt or key, and not called verified |
| The plan (frame, salt, design values), the stored checks, the exposure projection, the attributions and the stored primary results | Tables (`experiments`) | S | Named and justified (4NF) by their own stories: F25-08 (#839), F25-09 (#840), F25-10 (#841), F25-12 (#843), F25-13 (#844). The contract fixes only what the endpoints serve |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_experiments.exposure_v1` | analytics-kpis. Required by its contract, not contracted here |
| Publishes | `api_experiments.campaign_effect_v1` | analytics-kpis. Required by its contract, not contracted here |
| Consumes | `api_segments.current_label_v1` | segments (the label a customer holds at the frame instant) |
| Consumes | `api_models.published_version_v1` | model-registry (the model version published at the frame instant) |
| Consumes | `api_sales.net_purchase_line_v1` | sales (conversions and revenue, net of reversals) |
| Consumes | The campaign's audience, eligibility and exclusions as a versioned view | campaigns (F25-03, F25-04): required, not contracted yet |
| Consumes | `PERSONALIZATION` and `MARKETING_CONTACT` decisions and channel restrictions as of an instant | customer-preferences (F21-17): required, not contracted yet |

The contract invents no column of a view that is not contracted. Each producer's story fixes its columns and adds the grant row for `svc_experiments` to `contracts/db/grants.csv`; the two views this service publishes are fixed by the stories that implement them.

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Consumes | `stream:sales.v1.reversed` | group `experimentation` (declared by that stream's contract) | Re-reads the conversions of that customer's sale net of the reversal. Conversions stay append-only (ADR-0050) |
| Consumes | `stream:engagement.v1.interaction_recorded` | group `experimentation` (ADR-0075) | Projects `CAMPAIGN_EXPOSURE` notices by campaign and customer into its own schema, ignoring the other kinds, and applies a `VOIDED` notice. The stream's own contract follows in the story that implements ADR-0075 |
| Produces | — | — | The service publishes no stream. analytics-kpis reads its `api_experiments` views |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `experiments:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 404 | `experiment_not_found` | The experiment does not exist |
| 404 | `assignment_not_found` | `GET /v1/experiment-assignments/current` finds the customer in no assigned experiment of that campaign |
| 404 | `sale_attribution_not_found` | The attribution does not exist |
| 409 | `campaign_closed` | A plan names a finished or cancelled campaign (RN-49) |
| 409 | `already_assigned` | The experiment is already assigned |
| 409 | `underpowered_not_acknowledged` | An `UNDERPOWERED` plan is assigned without `acknowledge_underpowered` |
| 409 | `empty_frame` | The frame holds no customer |
| 409 | `assignment_conflict` | The database refused a second assignment of a customer in an experiment (RN-23); mapped to this 409, never a 500 |
| 409 | `experiment_not_assigned` | Conversions are evaluated, or a result is read, for an experiment that is not assigned |
| 422 | `invalid_query` | A filter is malformed, or `customer_id` or `campaign_id` is missing on `current` |
| 422 | `invalid_body` | A plan breaks its rules: `control_count`, `treatment_count`, `unknown_campaign` or `out_of_range` |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

None is added by this contract. Every foreign id is a query or body field (`campaign_id`, `customer_id`, `sale_id`), and each path key is this service's own identity. The stand-up story (#838) joins [E-09](./README.md#e-09--the-frozen-web-is-a-legacy-co-writer-of-some-tables), and the story that ports the web's two-proportion arithmetic joins [E-17](./README.md#e-17--rules-ported-from-the-web-exist-twice).

## Failure behaviour

| Dependency down | Effect |
|---|---|
| PostgreSQL unreachable | `/health/ready` reports not ready, and every operation answers 503 `database_unavailable`, never 500. `/health/live` stays 200 |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed. No stream entry is read until it returns, and nothing is lost: the entries wait in the stream |
| engagement's relay stalled | `exposure_count` and `exposed` lag. Exposure is descriptive and decides nothing in an effect, so no result is delayed or changed by it |
| sales' relay stalled | A reversal is read late. A result is always computed from the net view as of its `as_of`, so a late notice never makes a figure net of less than the view holds |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- A test over the OpenAPI and the XSD fails if any attribution field says `incremental` or `uplift`, or if a field that carries an effect has no interval (ADR-0069). It is `tests/test_experimentation_contract.py`, and it runs now.
- The implementing stories provide the checks ADR-0068 and ADR-0069 name: a golden fixture that recomputes every stored assignment; arms differing by at most one; no effect field for an experiment without a control; a stored primary result that a second read never rewrites; one holder per sale; and an attributed total that never exceeds net sales.
- [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) Compliance (registry: one rule, RN ids resolve), and ADR-0035 (resources are unique).

## Backlog

F25-06 contract (#837), F25-07 stand-up (#838), F25-08 stable and verifiable assignment (#839), F25-09 sample size (#840), F25-10 real exposures (#841), F25-11 conversions (#842), F25-12 attribution (#843), F25-13 effect with its uncertainty (#844), F25-14 validation (#845); consumed by F27-07 (#861) and F28-10 (#879). Issue numbers live in [`docs/backlog.md`](../backlog.md).
