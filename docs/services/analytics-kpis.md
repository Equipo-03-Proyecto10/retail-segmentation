# analytics-kpis

> **Status:** Contract published (phase 26, third; F26-01, #846). Not yet implemented: the service is stood up by F26-02 (#847). ADR-0059 places it; ADR-0060 decides who reads which store.
> **One rule:** A KPI is reported only under its published definition, window and data origin, from `api_*` objects, and only to the roles and stores allowed to read it (RN-68)

## Summary

| Field | Value |
|---|---|
| Language | Rust (axum) |
| Port (loopback) | 5022 |
| Public path | `/api/analytics-kpis/v1/` |
| Wave | third |
| PostgreSQL role | `svc_kpis`, schema `api_kpis` (ADR-0059); it owns no table |
| Redis ACL user | `svc_kpis`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-analytics-kpis` / `ghcr.io/equipo-03-proyecto10/mosaiq-analytics-kpis` |
| `mem_limit` | 64 MB (ADR-0059) |
| Used by | Android: the store manager's local indicators (F27). Electron: the executive reports (F28). |
| Governing ADRs | [ADR-0059](../adr/0059-four-new-services-join-the-fifteen-and-six-capabilities-fold-into-the-rule-they-extend.md), [ADR-0060](../adr/0060-four-store-profiles-map-to-four-roles-and-services-read-each-users-stores-per-request.md), [ADR-0062](../adr/0062-a-reversal-is-a-recorded-fact-and-every-reader-counts-net-units.md), [ADR-0064](../adr/0064-a-model-version-is-immutable-and-a-change-of-version-is-never-reported-as-behaviour.md), [ADR-0052](../adr/0052-data-provenance-is-recorded-per-sale-and-per-segmentation-run.md), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- A figure has one definition, one window and one data origin, published at `GET /v1/kpi-reports/definitions`. A report names its `kpi` and `definition_version`, and each measure its `kind` (`OBSERVED`, `ATTRIBUTED`, `INCREMENTAL`) and `unit`.
- It reads `api_*` views only. It holds no privilege on a base table and calls no service. It writes nothing and publishes no stream, so it can be dropped without effect (E-08).
- Sales figures are net of reversals (ADR-0062). A window that holds a change of model version is marked (ADR-0064). A figure that aggregates a seeded sale is marked synthetic (ADR-0052).
- `STORE_MANAGER` and `COMMERCIAL_SUPERVISOR` read only their assigned stores, from `api_users.store_assignment_v1` on each request (ADR-0060). Another store is 403 `forbidden_store`; no store named means the assigned stores. They do not read a KPI whose figure has no store.
- No report carries a customer identifier.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/kpi-reports/definitions` | The catalog of the seven KPIs | ADMIN, ANALYST, MARKETING, AUDITOR, STORE_MANAGER, COMMERCIAL_SUPERVISOR |
| GET | `/v1/kpi-reports/net-sales` | Net amount, units and orders | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/spend` | Net amount, active customers, average spend | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/retention` | Cohort retention by next bucket | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/conversion` | Exposed customers who bought within 7 days | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/recommendation-performance` | Impressions, clicks, purchases | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/migrations` | Segment changes within a model version | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/kpi-reports/campaign-performance` | Attributed sales and incremental effect per campaign | ADMIN, ANALYST, MARKETING, AUDITOR |

Reports take `from` and `to` (required), `group_by` of `segment`, `store`, `channel` and `period`, `granularity`, filters, `page` and `size`. Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`. The contract is [`contracts/analytics-kpis/`](../../contracts/analytics-kpis/openapi.yaml). It adds one resource, `kpi-reports`, and no registered resource name appears in its paths.

## Database contracts it consumes

| View | Producer | State |
|---|---|---|
| `api_segments.current_label_v1` | segments | Published |
| `api_sales.net_purchase_line_v1` | sales | Contracted in the reversal story (F21-03); it must also publish `data_origin` (ADR-0052), an additive column |
| `api_users.store_assignment_v1` | users | Contracted in the store-assignment story (F19-05) |
| `api_segments.assignment_change_v1` | segments | Required, not contracted: a customer's changes of assignment with previous and new label and model version |
| `api_engagement.interaction_v1` | engagement | Required, not contracted: impressions, exposures and `RECO_CLICK` events that are not voided |
| `api_recommendations.recommended_purchase_v1` | recommendations | Required, not contracted: sale lines bought through a recommendation |
| `api_experiments.campaign_effect_v1` | experimentation | Required, not contracted: per measured campaign, its window, arm sizes, attributed amount, effect and interval |

The contract invents none of the required views' columns. Each producer's story fixes them, adds its grant row for `svc_kpis` to `contracts/db/grants.csv`, and names analytics-kpis as a consumer.

## Data it owns

None. Redis keys: `pub:auth:v1:live:{sid}` (`EXISTS` only) and the per-service rate limit.

## Service-specific errors

| HTTP | Code | When |
|---|---|---|
| 403 | `forbidden_store` | A store-scoped role names a store it is not assigned to |
| 422 | `invalid_query` | `from` or `to` is missing or malformed (`required`, `invalid_format`), `to` is before `from` (`to_before_from`), the window exceeds 731 days (`window_too_long`), a dimension is not supported by the KPI (`dimension_not_supported`), or `granularity` is given without `period` (`requires_period`) |

## Accepted exceptions

None of its own. It is a read-rule service (E-08).
