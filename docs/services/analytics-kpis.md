# analytics-kpis

> **Status:** Stood up (phase 26, third; F26-02, #847): it serves `GET /v1/kpi-reports/net-sales` and `GET /v1/kpi-reports/definitions`, since F26-05 (#850) `GET /v1/kpi-reports/demand-coverage`, and since F26-03 (#848) `GET /v1/kpi-reports/spend`, `/retention` and `/migrations`; every other report is marked `x-mosaiq-status: planned` (ADR-0073) until its own story. The contract was published by F26-01 (#846). ADR-0059 places it; ADR-0060 decides who reads which store.
> **One rule:** A KPI is reported only under its published definition, window and data origin, from `api_*` objects, and only to the roles and stores allowed to read it (RN-68)

## Summary

| Field | Value |
|---|---|
| Language | Rust (axum, kit-rs 0.1.5) |
| Port (loopback) | 5022 |
| Public path | `/api/analytics-kpis/v1/` |
| Wave | third |
| PostgreSQL role | `svc_kpis`, schemas `kpis` and `api_kpis` (ADR-0059), created empty; it owns no table |
| Redis ACL user | `svc_kpis`, same user line on `redis-state` and `redis-cache`: session liveness (`EXISTS`) and the rate limit only; no stream, no cache |
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
| GET | `/v1/kpi-reports/definitions` | The catalog of the seven KPIs (served) | ADMIN, ANALYST, MARKETING, AUDITOR, STORE_MANAGER, COMMERCIAL_SUPERVISOR |
| GET | `/v1/kpi-reports/net-sales` | Net amount, units and orders (served, F26-02) | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/spend` | Net amount, active customers, average spend (served, F26-03) | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/retention` | Cohort retention by next bucket (served, F26-03) | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/conversion` | Exposed customers who bought within 7 days (planned) | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/recommendation-performance` | Impressions, clicks, purchases (planned) | the above (store roles narrowed) |
| GET | `/v1/kpi-reports/migrations` | Segment changes within a model version (served, F26-03) | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/kpi-reports/campaign-performance` | Attributed sales (`ATTRIBUTED`) and incremental effect (`INCREMENTAL`, with its 95% interval) per measured experiment of a campaign, never merged (F26-04) | ADMIN, ANALYST, MARKETING, AUDITOR |
| GET | `/v1/kpi-reports/demand-coverage` | Demand by segment and category beside stock on hand, days of cover and units to replenish (served, F26-05) | ADMIN, ANALYST, INVENTORY_PLANNER, AUDITOR, STORE_MANAGER (narrowed) |

Reports take `from` and `to` (required), `group_by` of `segment`, `store`, `channel` and `period`, `granularity`, filters, `page` and `size`; `demand-coverage` instead takes `store_id`, `category_id` and `target_cover_days` (1 to 180, default 14), has no `group_by`, and states its calculation in every response. Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`. The contract is [`contracts/analytics-kpis/`](../../contracts/analytics-kpis/openapi.yaml). It adds one resource, `kpi-reports`, and no registered resource name appears in its paths.

## Spend, retention and migrations (F26-03)

- **Spend.** The net amount, the customers who bought and the average spend per customer, grouped like net sales. A customer is active once per group, so a customer who bought in two stores is active once in each store and once in the total: the stores' customers can add to more than the window's. `average_spend` is absent from a row with no active customer.
- **Retention.** A cohort is the customers whose first net purchase *ever* falls in a bucket (`from` and `to` select cohorts by that instant); retained is those with a net purchase in the next bucket, at any store. By hand: three customers first buy in March, two buy again in April: 3, 2, 0.6667. Rows are always cohorts, so `period` is a dimension whether or not the caller names it. A cohort whose next bucket has not ended at `generated_at` has no retained count and no rate. A customer whose only purchase was reversed in full is in no cohort (ADR-0062).
- **Migrations.** A change of label between two consecutive assignments (`api_segments.assignment_change_v1`). A change whose two sides are in different model versions, or in a version nobody has linked, is methodological: counted in `methodological_changes`, never in `changes` or `customers_migrated` (ADR-0064). A row is keyed by the version entered, so it never mixes versions. For a window with one change per customer, the rows are the cells of `GET /v1/segment-migrations/matrix` (`tests/migrations.rs` computes the matrix by its rule and compares); a customer with two changes in the window is one matrix cell and two changes here. A migration has no store, so the store-scoped roles do not read it.
- **Model versions.** `data_origin.model_version_ids` names the versions behind a figure and `spans_model_versions` marks two or more: for migrations those on either side of a change in the window; for spend and retention grouped or filtered by segment, those of the labels' runs. Net sales does not read them yet.

## Database contracts it consumes

| View | Producer | State |
|---|---|---|
| `api_segments.current_label_v1` | segments | Published; granted to `svc_kpis` by F26-02 |
| `api_sales.net_purchase_line_v1` | sales | Published with `data_origin` (F21-03, ADR-0052); granted to `svc_kpis` by F26-02 |
| `api_users.store_assignment_v1` | users | Published (F19-05); granted to `svc_kpis` by F26-02, read on each request of a store-scoped role |
| `api_stock.stock_level_v1` | stock | Published; read by `demand-coverage` only, granted to `svc_kpis` by F26-05 |
| `api_products.product_ref_v1` | products | Published; read by `demand-coverage` only, for the product's category and `is_active`, granted to `svc_kpis` by F26-05 |
| `api_segments.assignment_change_v1` | segments | Published by F26-03 ([`api_segments.assignment_change.v1.md`](../../contracts/db/api_segments.assignment_change.v1.md)): one row per change of a customer's label between two consecutive assignments, each side named by its run (the model version comes from `api_models.run_version_v1`); read by `migrations` only, granted to `svc_kpis` by F26-03 |
| `api_models.run_version_v1` | model-registry | Published; read by `migrations` for the model version of each side's run, and by `spend` and `retention` through the run of a customer's current label, to name the model versions behind a segment, granted to `svc_kpis` by F26-03 |
<<<<<<< ours
| `api_recommendations.interaction_v1` | recommendations | Contracted (`contracts/db/api_recommendations.interaction.v1.md`, #1197): `RECO_IMPRESSION` and `RECO_CLICK` events that are not voided, kept by recommendations from engagement's stream (ADR-0075); read by `conversion` and `recommendation-performance` |
| `api_experiments.exposure_v1` | experimentation | Required, not contracted: `CAMPAIGN_EXPOSURE` events that are not voided, kept by experimentation from engagement's stream (ADR-0075) |
| `api_recommendations.recommended_purchase_v1` | recommendations | Contracted (`contracts/db/api_recommendations.recommended_purchase.v1.md`, #1197, ADR-0090): net sale lines whose customer clicked that product in the seven days before; read by `recommendation-performance` |
=======
| `api_recommendations.interaction_v1` | recommendations | Required, not contracted: `RECO_IMPRESSION` and `RECO_CLICK` events that are not voided, kept by recommendations from engagement's stream (ADR-0075) |
| `api_experiments.exposure_v1` | experimentation | Contracted (`contracts/db/api_experiments.exposure.v1.md`, #1198): `CAMPAIGN_EXPOSURE` events that are not voided, kept by experimentation from engagement's stream (ADR-0075); read by `conversion` |
| `api_recommendations.recommended_purchase_v1` | recommendations | Required, not contracted: sale lines bought through a recommendation |
>>>>>>> theirs
| `api_experiments.campaign_effect_v1` | experimentation | Contracted (`contracts/db/api_experiments.campaign_effect.v1.md`, F26-04): per measured experiment of a campaign, its window, arm sizes, net attributed amount, effect, interval and the interval's confidence |

engagement publishes no `api_*` view; its facts reach recommendations and experimentation on a Redis stream (ADR-0075), so figures built on them are eventually consistent. The contract invents none of the required views' columns. Each producer's story fixes them, adds its grant row for `svc_kpis` to `contracts/db/grants.csv`, and names analytics-kpis as a consumer.

## Data it owns

None. Redis keys: `pub:auth:v1:live:{sid}` (`EXISTS` only) and the per-service rate limit.

## Service-specific errors

| HTTP | Code | When |
|---|---|---|
| 403 | `forbidden_store` | A store-scoped role names a store it is not assigned to |
| 422 | `invalid_query` | `from` or `to` is missing or malformed (`required`, `invalid_format`), `to` is before `from` (`to_before_from`), the window exceeds 731 days (`window_too_long`), a dimension is not supported by the KPI (`dimension_not_supported`), or `granularity` is given without `period` (`requires_period`) |

## Accepted exceptions

None of its own. It is a read-rule service (E-08).
