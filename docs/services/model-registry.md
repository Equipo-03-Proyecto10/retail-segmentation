# model-registry

> **Status:** Stood up (phase 22, third; F22-11, #806): it registers, lists and reads versions and lists their publications, over `models.*`. Since F22-12 (#808) it also trains K-means: it starts a training with stated parameters and registers the result as a `DRAFT`. The contract was published by F22-10 (#805) and extended with drift reports and alerts by F23-08 (#820). Publication and rollback (F22-13, #809) and the drift operations (F23-09, #821) are marked planned in the contract (ADR-0073) and implemented by their own stories. ADR-0059 places it; ADR-0063, ADR-0064 and ADR-0065 decide how it behaves.
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
| `mem_limit` | 128 MB (ADR-0059's first cut, on the F19-08 measurement in [`f19-08-memory.md`](../evidence/f19-08-memory.md)) |
| Used by | Electron: the K-means configuration module (F28-05) and the drift analysis module (F28-07). rfm-engine and analytics-kpis read the published version. |
| Governing ADRs | [ADR-0059](../adr/0059-four-new-services-join-the-fifteen-and-six-capabilities-fold-into-the-rule-they-extend.md), [ADR-0063](../adr/0063-segments-change-on-events-and-on-a-schedule-scored-against-the-published-version.md), [ADR-0064](../adr/0064-a-model-version-is-immutable-and-a-change-of-version-is-never-reported-as-behaviour.md), [ADR-0065](../adr/0065-drift-is-claimed-in-three-separate-kinds-and-concept-drift-only-from-a-predictive-outcome.md), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- A version holds its method (`RFM_RULES` or `KMEANS`), definition, window, seed, frozen scoring (quintile boundaries, or centroids and normalization bounds), dataset fingerprint and origin (ADR-0052), metrics, reference distribution and owner. None of that changes after creation, and no role edits or deletes a version.
- Exactly one version is `PUBLISHED` at a time. Publishing a `DRAFT` retires the previous version; a rollback publishes a `RETIRED` version again. Both append a row to `models.model_publication` with the version, the one it replaced, the author, the time and the reason. History is never rewritten.
- After a publication or rollback, every customer is scored under the published version in a full run whose changed rows carry the cause `VERSION_CHANGE` (ADR-0063, ADR-0064).
- A training fits K-means over the net purchases of a window with a recorded seed, and registers its result as a `DRAFT`. It publishes nothing. `POST /v1/model-trainings` stores the request as a `RUNNING` row and answers 201 at once; the fit runs after the answer, on a thread of the service, and the row ends `COMPLETED` with the version it registered or `FAILED` with a `failure_code`. One training runs at a time. The fit is the service's own code (ADR-0021), pinned to the web's results by golden fixtures (ADR-0045, E-17): customers are the window's net purchases (ADR-0062) as recency, frequency and monetary value, scaled min-max; centroids are seeded k-means++ from the seed; clusters are named by proportional rank (ADR-0030, RN-38) and `cluster_index` is that rank, best first. The same data, configuration and seed give the same fit (RN-58). The version records the dataset fingerprint and origin, the parameters, the seed, inertia and silhouette (sampled above 2,000 customers, as the web does), the frozen centroids and normalization bounds, and the reference distribution; its `stability` is null, because no repeated-seed study runs in the service.
- A training ends `FAILED` rather than half registered: with fewer customers than clusters (`too_few_customers`), past `TRAINING_TIMEOUT_SECONDS` (`timed_out`, default 900), when the sales view cannot be read (`source_unavailable`), when the label vocabulary is empty (`label_vocabulary_unavailable`), when the name was taken meanwhile (`model_version_name_taken`), when the service restarted (`interrupted`) or on any other error (`internal_error`). A `RUNNING` row past its deadline is closed as `timed_out` by the next request, so a died worker never holds the slot.
- A run the web started is registered as a version, and published, when no version matches its method and parameters (ADR-0064). Its source is `WEB_RUN` and it has no owner.
- Drift reports and alerts, judged against a version's reference distribution, belong to this rule (ADR-0059). A **report** is one comparison of one version with its reference, of kind `DATA_DRIFT` (the distribution of the segments, and of R, F and M, against the reference: PSI, and the chi-square on the segment shares, with the thresholds of ADR-0065) or `OUTCOME_TEST` (the repurchase relation of a matured cohort). It carries the statistic, the reference, the current window, the threshold and the model version. The monitor writes `DATA_DRIFT` reports on its own schedule, one per window (`DRIFT_INTERVAL_SECONDS` is how often it tries; a window ends at 03:00 UTC, so a repeated pass changes nothing), under the advisory lock 301001 so one instance runs it (ADR-0088). It judges the published version on the segment shares, R, F and M; categories and channels are not variables (ADR-0065). The window is every scored customer's current profile, read in one repeatable-read snapshot, with R aged to the window's end; a label the reference does not name is kept in a bin of its own. A variable is `ALARM_PENDING` the first window its alarm holds and `ALARM` the second, counting only the report one day earlier as the window before; every report at `ALARM` raises an alert, the first with the second alarming window. The alert names the variable, the change and the version, and suggests a review. A pass writes its report and its audit event, a `CALCULATION_RUN` of `DRIFT`, in a read-committed transaction after the snapshot, and a pass that cannot be audited commits no report. No data-drift report says "concept drift": only an outcome test may. The **outcome test** (F23-11, ADR-0089) runs in the same pass. It judges the published version's repurchase relation on matured cohorts. The reference cohort is at the first 03:00 UTC after the version's latest publication or rollback, and a current cohort comes every 30 days after it, tested once its outcome window is in the past. A cohort's customers and their segments are read from `api_segments.assignment_interval_v1`, and their repurchases from `api_sales.net_purchase_line_v1`. Each segment is compared with its own reference rate by a two-proportion z-test with Holm's correction, one row per segment in `models.drift_outcome_segment`. A segment that passes in two consecutive cohorts raises the one alert that claims `CONCEPT_DRIFT`; a change in how many customers each segment holds cannot. A window with fewer than 200 customers is `INSUFFICIENT_DATA`, and one with a publication or rollback after its start is `NOT_COMPARED`, even one published after the window's end: a change of version is never reported as behaviour (ADR-0064).
- An **alert** is raised by a report when a variable holds its alarm in two consecutive windows (watch is displayed and never alerted), or when a segment passes the outcome test in two consecutive matured cohorts. It says which variable changed, by how much and what review it suggests, and its `claim` is the only wording a client may use: `DISTRIBUTION_CHANGE`, or, on an outcome test alone, with the relation, horizon and cohort named beside it, `CONCEPT_DRIFT` (ADR-0065). A customer's migration between segments and a customer's own consumption shift (RN-36, RN-50) are not drift and are never in a report.

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
| GET | `/v1/drift-reports` | Drift reports, newest window first (`kind`, `model_version_id`, `comparison`, `page`, `size`). Planned (ADR-0073) | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/drift-reports/{report_id}` | One report: per variable its state, statistics against thresholds and every bin's reference and current share; or the outcome test's relation, horizon, cohort and per-segment rates. Planned | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/drift-alerts` | Drift alerts, newest first (`kind`, `model_version_id`, `variable`, `page`, `size`). Planned | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/drift-alerts/{alert_id}` | One alert with its variable, version, statistics and bins (or outcome test) and suggested review. Planned | ADMIN, ANALYST, MARKETING, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`. The contract is [`contracts/model-registry/`](../../contracts/model-registry/openapi.yaml).

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `models.model_version`, `models.model_version_feature` | Tables (`models`) | S, insert only | Immutable: a trigger refuses every DELETE and every change but `state`, `published_at` and `retired_at`, which F22-13 moves with a publication. Exempt from the 30-row seed (two versions), with its reason in `sql/seed-exempt.txt` |
| `models.model_publication` | Table (`models`) | R until F22-13 | Append-only; one seeded row, the first publication. Exempt |
| `models.run_version` | Table (`models`) | R until the stream consumer (ADR-0064) | Links each segmentation run to its version; the seed links all 31 runs. `definer_models` adds the link of each run `api_segments.apply_assignment_v1` records, through `api_models.link_run_version_v1` (ADR-0092), and model-registry's section backfills the runs recorded before it, idempotently |
| `models.model_training`, `models.model_training_feature`, `models.model_training_cluster` | Tables (`models`) | S | F22-12. A training's request and outcome, the variables it fitted, and the size of each cluster by rank. Insert, plus UPDATE of the outcome columns while `RUNNING`; a trigger refuses every other change and every DELETE; one unique partial index allows one `RUNNING` row. Cluster sizes are kept but not served: adding them to a v1 response type would break the XML policy of the contract check. Exempt from the 30-row seed, with its reason in `sql/seed-exempt.txt` |
| `models.drift_report`, `models.drift_variable`, `models.drift_statistic`, `models.drift_bin`, `models.drift_alert` | Tables (`models`) | S, insert only | F23-09 (ADR-0065, ADR-0088). A report is one pass over one window of the published version; its variables, their statistics and bins, and the alerts it raised hang from it. A trigger refuses every UPDATE and DELETE. The reference, the thresholds, a bin's contribution and an alert's text are derived at read, not stored. Exempt from the 30-row seed, with its reason in `sql/seed-exempt.txt`: the seed holds one honest `INSUFFICIENT_DATA` report |
| `models_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_models`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_models.published_version_v1` | rfm-engine, analytics-kpis |
| Publishes | `api_models.run_version_v1` | segments, recommendations, analytics-kpis |
| Publishes | `api_models.link_run_version_v1` (ADR-0092, #1208) | segments (`definer_segments`, from `api_segments.apply_assignment_v1`, in the transaction that records a run of rfm-engine's changes) |
| Consumes | `api_rfm.current_profile_v1` | rfm-engine (the window of the drift monitor, read in one repeatable-read snapshot: every scored customer, F23-09) |
| Consumes | `api_sales.net_purchase_line_v1` (F21-02, #770) | sales (the net purchases a training fits, read once in a repeatable-read snapshot and streamed) |
| Consumes | `api_segments.label_v1` (F22-12) | segments (the label vocabulary and its order, so a training never copies the labels) |
| Consumes | `api_segments.assignment_interval_v1` (F23-11) | segments (each customer's segment at a cohort's as-of date, for the outcome test; ADR-0089) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:models.v1.publication_recorded` | captured from `models.model_publication` | Tells rfm-engine a version was published or restored |
| Consumes | `stream:segments.v1.run_recorded` | group `model-registry` | Registers and publishes the version of a run the web started, linking it in `models.run_version` (ADR-0064). Its group is added to that stream's contract when the service is implemented |

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
| 404 | `drift_report_not_found` | The drift report does not exist |
| 404 | `drift_alert_not_found` | The drift alert does not exist |
| 409 | `model_version_name_taken` | `POST /v1/model-versions` or `POST /v1/model-trainings` names an existing version |
| 409 | `version_not_draft` | A publication of a version that is not `DRAFT` |
| 409 | `version_not_retired` | A rollback to a version that is not `RETIRED` |
| 409 | `publication_conflict` | Another publication committed first; the partial unique index refused a second `PUBLISHED` row, and the service maps it to this 409, never a 500 |
| 409 | `training_in_progress` | A training is already running |

A training's own failures are not HTTP errors: they are the `failure_code` of a `FAILED` training (see the Business rule).
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
- A test scans templates, API schemas and report generators for "concept drift" and fails unless it appears on an outcome-test result that names its relation, horizon and cohort; a window with a change of version inside it is not compared, and fewer than 200 customers is "insufficient data", never a number (ADR-0065).
- [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) Compliance (registry: one rule, RN ids resolve), and ADR-0035 (resources are unique).

## Backlog

F22-10 contract (#805), F22-11 stand-up (#806), F22-12 training (#808; [evidence](../evidence/f22-12-kmeans-training.md)), F22-13 publication and rollback; F23-08 drift contract (#820), F23-09 data-drift monitor (#821), F23-11 outcome test (#823); consumed by F28-05, F28-07, F22-15 and F23. Issue numbers live in [`docs/backlog.md`](../backlog.md).
