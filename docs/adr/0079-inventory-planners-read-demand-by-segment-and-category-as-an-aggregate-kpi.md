# ADR-0079 — Inventory planners read demand by segment and category as an aggregate report

**Status:** Proposed
**Owner:** Max Rubio (drafted with Claude Code)
**Issue:** #881 (F28-12)
**Supersedes:** — (this record adds a report; the narrowing in `docs/requirements.md` §3 that keeps `INVENTORY_PLANNER` out of customer-named sales rows stays in force for every other report)
**Superseded by:** —

---

## Context

F28-12 asks for demand by segment and category next to availability, for a period and a store, with no customer data in view. The planner's role policy in `docs/requirements.md` §3 keeps `INVENTORY_PLANNER` off every report that names a customer. `net-sales` reads `api_sales.net_purchase_line_v1` and `api_segments.current_label_v1`, and its roles exclude the planner for that reason.

The existing KPIs cannot answer F28-12:

- `analytics-kpis` has no category dimension. `net-sales` groups by `segment`, `store`, `channel` and `period` only.
- No KPI is readable by `INVENTORY_PLANNER`.

Segment is the sensitive part. A count of customers per segment is aggregate, but a segment with one customer reveals that customer's label. This record decides that the planner may see segment-level units, sales and amounts, and that the response never carries a customer identifier or a customer count. It does not decide a minimum cell size for small segments; that is left open (see Consequences).

Adding the figure to the existing KPI enums was considered and rejected for v1. `contracts/README.md` says anything `oasdiff` reports is breaking, and adding a value to `KpiCode` or `Dimension` changes a response type. That would force a `/v2` of the whole service.

## Decision

`analytics-kpis` publishes one new read-only operation, `GET /v1/kpi-reports/demand`, with its own response type `DemandReport` (root element `DemandReport`, XML namespace unchanged). Its dimensions are `DemandDimension`: `segment`, `store`, `category` and `period`. Its rows are `DemandRow`, which carries `category_id` and no channel, campaign or model-version field. It measures three OBSERVED figures per group: `net_units` (count), `net_orders` (count of distinct sales) and `net_amount` (currency). It reads `api_sales.net_purchase_line_v1` (for `product_id`, `net_quantity`, `net_amount`, `store_id` and `transaction_id`), `api_products.product_ref_v1` (for `category_id`) and `api_segments.current_label_v1` (for `segment`). Its roles are `ADMIN`, `ANALYST`, `INVENTORY_PLANNER` and `AUDITOR`, over every store. Existing types, enums and operations are unchanged.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Show category demand only, with no segment | Drops an acceptance criterion of F28-12 ("demand by segment and category") |
| Let the planner read `net-sales` with `group_by=segment` | `net-sales` is closed to the planner by role, and reopening it would open every dimension of the customer-named report at once |
| Add `demand` to `KpiCode` and `category` to `Dimension` | `oasdiff` reports each added value as a breaking response change, and `contracts/README.md` makes that a `/v2` of the whole service |
| Add `category_id` as a required field on the existing `Row` | Breaking for every existing KPI, for the same reason |
| Stitch two existing reports together in the desktop | The desktop would join figures it cannot reconcile, and the join is a business rule that belongs in the contract |

## Consequences

**What this makes easy.** The planner gets demand next to availability through one published, role-gated report. The desktop asks for a report and does not join data itself. No existing contract changes shape, so no client breaks.

**What this makes hard.**

- The planner now reads a segment-level figure, which `docs/requirements.md` §3 had kept out. The exception is written for this report only.
- **The KPI catalog does not list `demand`.** `GET /v1/kpi-reports/definitions` lists the seven v1 KPIs, because the catalog's `kpi_code` is `KpiCode`. The catalog is meant to be the single statement of what a figure means, so this report's definition lives in its operation description and in this record until a `/v2` adds it to the catalog. A reader must not treat the catalog as complete for this service.
- Small segments can still be re-identified from amounts. A minimum cell size would close that, and it is a separate decision.

**What must now be true elsewhere.**
- `contracts/analytics-kpis/` gains the `demand` operation, `DemandReport`, `DemandRow`, `DemandDimension`, and JSON and XML examples. Its version moves to 1.1.0.
- `svc_kpis` (the analytics-kpis PostgreSQL role, ADR-0059) needs `SELECT` on `api_products.product_ref_v1`. `contracts/db/grants.csv` has no `svc_kpis` rows yet, because the service is not stood up (F26-02). The grant is added with that role, not here.
- `docs/requirements.md` §3 states the exception for `demand`.
- `docs/services/analytics-kpis.md` describes the report.

**Open items.**
- A minimum cell size for segment rows.
- Whether the catalog should list `demand` in a future `/v2`.

## Compliance

- `contracts/check.sh` passes against `develop`: the OpenAPI validates, the XML examples validate against the XSD, `oasdiff` reports no breaking change, and the version moves.
- A contract check asserts that no `DemandReport` example contains a `customer_id` or a customer count, and that the `demand` operation's `x-mosaiq-roles` are exactly `ADMIN`, `ANALYST`, `INVENTORY_PLANNER` and `AUDITOR`.
