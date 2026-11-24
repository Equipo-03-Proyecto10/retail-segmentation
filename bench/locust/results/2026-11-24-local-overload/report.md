### Per step (steady window)

| Phase | Users | Requests | req/s | Mean ms | p50 ms | p95 ms | p99 ms | Max ms | Errors | Sustained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| step-1 | 160 | 3,979 | 79.2 | 23 | 11 | 112 | 235 | 1,098 | 0 (0.00%) | yes |
| step-2 | 240 | 5,863 | 116.7 | 39 | 10 | 204 | 356 | 774 | 3 (0.05%) | yes |
| step-3 | 320 | 7,505 | 149.2 | 133 | 10 | 651 | 878 | 1,239 | 33 (0.44%) | yes |
| step-4 | 400 | 8,097 | 164.2 | 442 | 9 | 2,001 | 2,388 | 2,956 | 107 (1.32%) | no |
| step-5 | 480 | 8,480 | 168.5 | 842 | 10 | 4,919 | 6,493 | 7,074 | 180 (2.12%) | no |
| step-6 | 560 | 9,344 | 185.7 | 972 | 8 | 6,410 | 7,660 | 9,101 | 258 (2.76%) | no |
| recovery | 10 | 838 | 4.9 | 50 | 28 | 86 | 756 | 1,418 | 0 (0.00%) | yes |

### Saturation

Saturation at **400 users** (step-4: p99 2,388 ms > 1,000 ms; errors 1.32% > 1.00%). The last sustained step was **320 users**.

### Recovery after the drop

Baseline: step-1's p95, 112 ms; threshold 135 ms with no failure, in 10-second windows from the drop.

| Window (s after drop) | Requests | p95 ms | Errors | Recovered |
|---|---:|---:|---:|---|
| 0–10 | 39 | 8,096 | 0 | no |
| 10–20 | 51 | 84 | 0 | yes |
| 20–30 | 51 | 69 | 0 | yes |
| 30–40 | 48 | 68 | 0 | yes |
| 40–50 | 50 | 71 | 0 | yes |
| 50–60 | 53 | 70 | 0 | yes |
| 60–70 | 51 | 104 | 0 | yes |
| 70–80 | 46 | 260 | 0 | no |
| 80–90 | 47 | 127 | 0 | yes |
| 90–100 | 51 | 79 | 0 | yes |
| 100–110 | 51 | 65 | 0 | yes |
| 110–120 | 49 | 66 | 0 | yes |
| 120–130 | 52 | 69 | 0 | yes |
| 130–140 | 45 | 96 | 0 | yes |
| 140–150 | 49 | 60 | 0 | yes |
| 150–160 | 47 | 171 | 0 | no |
| 160–170 | 48 | 57 | 0 | yes |
| 170–180 | 49 | 71 | 0 | yes |

Recovered **20 s** after the drop.

### The 5 slowest endpoints (whole run, by p95)

| Endpoint | Requests | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|
| `GET /segmentation-dashboard/` | 1,229 | 1,620 | 6,450 | 7,976 | 0 (0.00%) |
| `GET /catalog/segments/{id}` | 2,429 | 1,329 | 6,052 | 7,444 | 0 (0.00%) |
| `GET /catalog/customers?q=` | 2,459 | 1,389 | 6,037 | 7,422 | 0 (0.00%) |
| `GET /catalog/customers/{id}` | 3,647 | 1,361 | 6,037 | 7,367 | 0 (0.00%) |
| `GET /catalog/customers` | 4,968 | 1,388 | 5,991 | 7,394 | 0 (0.00%) |

### Per user class (whole run)

| Class | Requests | req/s | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|---:|
| desktop-xml | 11,875 | 22.0 | 11 | 21 | 68 | 11 (0.09%) |
| floor-android | 17,794 | 32.9 | 17 | 31 | 313 | 0 (0.00%) |
| ops-admin | 5,961 | 11.0 | 9 | 25 | 96 | 572 (9.60%) |
| web-analyst | 17,281 | 32.0 | 1,384 | 6,031 | 7,424 | 0 (0.00%) |

### Errors

| Endpoint | Error | Count |
|---|---|---:|
| `GET /api/users/v1/users` | 429 rate_limited | 309 |
| `GET /api/audit-trail/v1/audit-entries` | 429 rate_limited | 255 |
| `GET console /status.json` | 0 | 6 |
| `GET /api/analytics-kpis/v1/kpi-reports/net-sales` | 429 rate_limited | 3 |
| `GET console /` | 0 | 2 |
| `GET /api/analytics-kpis/v1/kpi-reports/spend` | 429 rate_limited | 2 |
| `GET /api/analytics-kpis/v1/kpi-reports/retention` | 429 rate_limited | 2 |
| `GET /api/analytics-kpis/v1/kpi-reports/demand-coverage` | 429 rate_limited | 1 |
| `GET /api/analytics-kpis/v1/kpi-reports/migrations` | 429 rate_limited | 1 |
| `GET /api/analytics-kpis/v1/kpi-reports/net-sales (segment, month)` | 429 rate_limited | 1 |
| `GET /api/analytics-kpis/v1/kpi-reports/definitions` | 429 rate_limited | 1 |
