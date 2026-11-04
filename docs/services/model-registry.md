# model-registry

> **Status:** Contract published (phase 22, third; F22-10, #805). Not yet implemented: the service is stood up by F22-11 (#806). ADR-0059 places it; ADR-0063 and ADR-0064 decide how it behaves.
> **One rule:** A model version is immutable once published, carries its dataset, parameters, metrics and reference distribution, changes state only by an explicit publication or rollback, and every drift alert is judged against that reference (RN-66)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5019 |
| Public path | `/api/model-registry/v1/` |
| Wave | third |
| PostgreSQL role | `svc_models`, schemas `models`, `models_events` and `api_models` |
| Redis ACL user | `svc_models`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-model-registry` / `ghcr.io/equipo-03-proyecto10/mosaiq-model-registry` |
| `mem_limit` | 160 MB (ADR-0059) |
| Used by | Electron: the K-means configuration module (F28-05). rfm-engine and analytics-kpis read the published version. |
| Governing ADRs | [ADR-0059](../adr/0059-four-new-services-join-the-fifteen-and-six-capabilities-fold-into-the-rule-they-extend.md), [ADR-0063](../adr/0063-segments-change-on-events-and-on-a-schedule-scored-against-the-published-version.md), [ADR-0064](../adr/0064-a-model-version-is-immutable-and-a-change-of-version-is-never-reported-as-behaviour.md), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- A version holds its method (`RFM_RULES` or `KMEANS`), definition, window, seed, frozen scoring (quintile boundaries, or centroids and normalization bounds), dataset fingerprint and origin (ADR-0052), metrics, reference distribution and owner. None of that changes after creation, and no role edits or deletes a version.
- Exactly one version is `PUBLISHED` at a time. Publishing a `DRAFT` retires the previous version; a rollback publishes a `RETIRED` version again. Both append a row to `models.model_publication` with the version, the one it replaced, the author, the time and the reason. History is never rewritten.
- After a publication or rollback, every customer is scored under the published version in a full run whose changed rows carry the cause `VERSION_CHANGE` (ADR-0063, ADR-0064).
- A training fits K-means over the net purchases of a window with a recorded seed, and registers its result as a `DRAFT`. It publishes nothing.
- A run the web started is registered as a version, and published, when no version matches its method and parameters (ADR-0064). Its source is `WEB_RUN` and it has no owner.
- Drift reports and alerts, judged against a version's reference distribution, belong to this rule and are specified by their own contract story (F23). `drift-reports` and `drift-alerts` are reserved for this service by ADR-0059 and are not yet in the registry.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/model-versions` | Versions (`method`, `state`, `page`, `size`); `state=PUBLISHED` is the current one | ADMIN, ANALYST, MARKETING, AUDITOR |
| POST | `/v1/model-versions` | Register an immutable `DRAFT`; 201 | ADMIN |
| GET | `/v1/model-versions/{version_id}` | One version with its scoring, dataset, metrics and reference distribution | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/model-versions/{version_id}/publications` | The publications and rollbacks of that version | ADMIN, ANALYST, MARKETING, AUDITOR |
| POST | `/v1/model-versions/{version_id}/publications` | Publish a `DRAFT`, with a reason; 201 | ADMIN |
| POST | `/v1/model-versions/{version_id}/rollbacks` | Publish a `RETIRED` version again, with a reason; 201 | ADMIN |
| GET | `/v1/model-trainings` | Training runs (`method`, `state`, `page`, `size`) | ADMIN, ANALYST, MARKETING, AUDITOR |
| POST | `/v1/model-trainings` | Start a K-means training; 201 `RUNNING` | ADMIN |
| GET | `/v1/model-trainings/{training_id}` | One training and the version it registered | ADMIN, ANALYST, MARKETING, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`. The contract is [`contracts/model-registry/`](../../contracts/model-registry/openapi.yaml).

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `models.model_version`, `models.model_publication`, `models.model_training`, `models.run_version` | Tables (`models`) | S | Named by the schema story; the contract fixes only what the endpoints serve |
| `models_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_models`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_models.published_version_v1` | rfm-engine, analytics-kpis |
| Consumes | `api_rfm.current_profile_v1` | rfm-engine (current distribution sample for drift, F23-09) |
| Consumes | `api_sales.purchase_line_v1` and the net view of `api_sales` (F21-02, #770) | sales (the net purchases a training fits) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:models.v1.publication_recorded` | captured from `models.model_publication` | Tells rfm-engine a version was published or restored |
| Consumes | `stream:segments.v1.run_recorded` | group `model-registry` | Registers and publishes the version of a run the web started, linking it in `models.run_version` (ADR-0064). Its group is added to that stream's contract when the service is implemented |
| Consumes | `stream:rfm.v1.profile_changed` | group `model-registry` | Marks the customer's row in the drift sample stale (F23-09) |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `models:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 404 | `model_version_not_found` | The version does not exist |
| 404 | `model_training_not_found` | The training does not exist |
| 409 | `model_version_name_taken` | `POST /v1/model-versions` or `POST /v1/model-trainings` names an existing version |
| 409 | `version_not_draft` | A publication of a version that is not `DRAFT` |
| 409 | `version_not_retired` | A rollback to a version that is not `RETIRED` |
| 409 | `publication_conflict` | Another publication committed first; the partial unique index refused a second `PUBLISHED` row, and the service maps it to this 409, never a 500 |
| 409 | `training_in_progress` | A training is already running |
| 422 | `invalid_query` | A filter is malformed |
| 422 | `invalid_body` | A version breaks its rules: `method_mismatch`, `not_monotonic`, `distribution_mismatch` |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

None. The path `/v1/model-versions/{version_id}/publications` nests a publication under the version it concerns, a resource of this service, so it carries no other boundary's id.

## Failure behaviour

| Dependency down | Effect |
|---|---|
| PostgreSQL unreachable | `/health/ready` reports not ready, and every operation answers 503 `database_unavailable`, never 500. `/health/live` stays 200 |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed. No stream entry is read until it returns, and nothing is lost: the entries wait in the stream |
| `redis-state` full or down | The relay cannot `XADD`; unrelayed outbox rows wait until there is room |
| Any other service down | No effect: services never call each other over HTTP. A stalled segments relay delays the registration of a web run's version; the version is read as pending until then (ADR-0064) |

## Compliance

- A test tries to edit and delete a published version as each role, and the partial unique index refuses a second `PUBLISHED` row (ADR-0064).
- A test publishes and rolls back, then compares `customer_segment_history`: rows that existed are identical and only new rows appear.
- [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) Compliance (registry: one rule, RN ids resolve), and ADR-0035 (resources are unique).

## Backlog

F22-10 contract (#805), F22-11 stand-up (#806), F22-12 training, F22-13 publication and rollback; consumed by F28-05, F22-15 and F23. Issue numbers live in [`docs/backlog.md`](../backlog.md).
