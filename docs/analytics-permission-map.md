# Analytics permission map (F4-07, #202)

Every Delivery 2 analytics surface, mapped to a permission already declared
in `web/middleware/authz.py`. One new permission was needed; everything else
reuses an existing declaration, per ADR-0007.

| Phase | Surface | Permission | Profiles |
|---|---|---|---|
| 7 | Segment assignment history / migration (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 7 | Run RFM_RULES / KMEANS | `segment_run.execute` (existing) | ADMIN |
| 8 | Sales CSV ingestion | `sales_ingest.execute` (/admin/sales-import/) | ADMIN |
| 8 | Consumption profile (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 9 | Model comparison, parameters and quality measures (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 10 | Recommendations (read) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 11 | Campaigns and experiments (assignment, exposure, conversion) | `campaign.read` / `campaign.write` (existing) | same as campaigns today |
| 11 | Experiment arm detail and bulk exposure (`/experiments/<id>/groups/<group_id>`) | `campaign.read` / `campaign.write` | same as campaigns today |
| 12 | Segment / RFM / migration dashboards | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| 12 | Revenue dashboard | `report.read` (existing) | everyone who already holds `report.read` |
| 12 | Experiment dashboard | `campaign.read` (existing) | same as campaigns today |
| 12 | Experiment report and its CSV export (`/experiment-report/`) | `campaign.read` (existing) | same as campaigns today |
| 12 | Consumption-shift and recommendation reports (`/consumption-reports/`) | `segment.read` (existing) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |

`ADMIN` remains the only administrative profile. No new profile is created.

`STORE_MANAGER` was added to the `segment.read` rows by #280, so that the store
manager F10-02 is written for can open it:
[ADR-0023](adr/0023-store-managers-read-segments-and-customers.md).
