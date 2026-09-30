# Analytics permission map (F4-07, #202)

Every Delivery 2 analytics surface, mapped to a permission already declared
in `web/middleware/authz.py`. One new permission was needed; everything else
reuses an existing declaration, per ADR-0007.

| Phase | Surface | Permission | Profiles |
|---|---|---|---|
| 7 | Segment assignment history / migration (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 7 | Customer segment timeline, as-of lookup and the sales behind a change (`/catalog/customers/<id>`, `/catalog/customers/<id>/segment-changes/<run>`, #337) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 7 | Run RFM_RULES / KMEANS | `segment_run.execute` (existing) | ADMIN |
| 8 | Sales CSV ingestion | `sales_ingest.execute` (/admin/sales-import/) | ADMIN |
| 8 | Consumption profile (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 9 | Model comparison, parameters and quality measures (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 10 | Recommendations (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 11 | Campaigns and experiments (assignment, exposure, conversion) | `campaign.read` / `campaign.write` (existing) | same as campaigns today |
| 11 | Experiment arm detail and bulk exposure (`/experiments/<id>/groups/<group_id>`) | `campaign.read` / `campaign.write` | same as campaigns today |
| 12 | Segment / RFM / migration dashboards | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 12 | Revenue summary by channel and store (`/reports/`) | `report.read` (existing) | everyone who already holds `report.read` |
| 12 | Revenue by stable segment label inside the segmentation dashboard | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 12 | Experiment dashboard | `campaign.read` (existing) | same as campaigns today |
| 12 | Experiment report and its CSV export (`/experiment-report/`) | `campaign.read` (existing) | same as campaigns today |
| 12 | Consumption-shift and recommendation reports (`/consumption-reports/`) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |

`ADMIN` remains the only administrative profile. No new profile is created.

`STORE_MANAGER` was added to the `segment.read` rows by #280, so that the store
manager F10-02 is written for can open it:
[ADR-0023](adr/0023-store-managers-read-segments-and-customers.md).

## Declared permissions without a route

The vocabulary is deliberately a little larger than the current URL map. A
declared permission grants nothing until a route names it; ADR-0007's default
deny still applies.

| Permission | Current state | Why it remains declared |
|---|---|---|
| `sales_ingest.execute` | Used by all four `/admin/sales-import/` routes | The route landed after the permission map, so the earlier no-route finding is no longer true. `tests/test_analytics_authz.py` keeps the mapping explicit. |
| `inventory.write` | No route | Accepted sales decrement stock atomically under ADR-0031. This dormant permission remains for a separate, auditable receipt/transfer/count/adjustment entry point; its implementation is sequenced in `roadmap.md`. |
| `segment.write` | No route | Runs use `segment_run.execute`; changing the stable label vocabulary or rule catalog is withheld until a governance story defines how historical comparability is preserved. |
| `user.self` | No route | `CUSTOMER` is a declared loyalty-customer profile, and the schema has an optional `customer.user_id` ownership link, but the seed and current user workflow never populate it. A self-service story must define that link's lifecycle and populate it before a route can safely narrow a query. |

The last three permissions are therefore dormant capabilities, not hidden
access. Their absence is visible in the route-completeness tests and in the
sequenced follow-up table in [`roadmap.md`](roadmap.md).
