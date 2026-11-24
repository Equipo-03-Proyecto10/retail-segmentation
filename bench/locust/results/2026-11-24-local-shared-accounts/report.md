### Per step (steady window)

| Phase | Users | Requests | req/s | Mean ms | p50 ms | p95 ms | p99 ms | Max ms | Errors | Sustained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| step-1 | 10 | 252 | 5.0 | 30 | 22 | 67 | 163 | 281 | 0 (0.00%) | yes |
| step-2 | 20 | 495 | 9.8 | 25 | 19 | 55 | 150 | 439 | 0 (0.00%) | yes |
| step-3 | 40 | 973 | 19.3 | 20 | 13 | 44 | 150 | 213 | 117 (12.02%) | no |
| step-4 | 80 | 1,949 | 39.5 | 20 | 12 | 90 | 177 | 402 | 503 (25.81%) | no |
| step-5 | 120 | 2,971 | 59.1 | 33 | 12 | 146 | 713 | 1,139 | 942 (31.71%) | no |
| step-6 | 160 | 3,949 | 78.4 | 20 | 10 | 98 | 196 | 484 | 1,361 (34.46%) | no |
| recovery | 10 | 837 | 4.9 | 32 | 25 | 63 | 191 | 227 | 64 (7.65%) | no |

### Saturation

Saturation at **40 users** (step-3: errors 12.02% > 1.00%). The last sustained step was **20 users**.

### Recovery after the drop

Baseline: step-1's p95, 67 ms; threshold 80 ms with no failure, in 10-second windows from the drop.

| Window (s after drop) | Requests | p95 ms | Errors | Recovered |
|---|---:|---:|---:|---|
| 0–10 | 48 | 85 | 18 | no |
| 10–20 | 49 | 68 | 20 | no |
| 20–30 | 43 | 66 | 16 | no |
| 30–40 | 51 | 183 | 17 | no |
| 40–50 | 48 | 66 | 10 | no |
| 50–60 | 47 | 73 | 0 | yes |
| 60–70 | 53 | 59 | 0 | yes |
| 70–80 | 49 | 54 | 0 | yes |
| 80–90 | 50 | 57 | 0 | yes |
| 90–100 | 52 | 159 | 0 | no |
| 100–110 | 48 | 67 | 0 | yes |
| 110–120 | 50 | 56 | 1 | no |
| 120–130 | 53 | 62 | 0 | yes |
| 130–140 | 49 | 55 | 0 | yes |
| 140–150 | 52 | 56 | 0 | yes |
| 150–160 | 50 | 43 | 0 | yes |
| 160–170 | 46 | 53 | 0 | yes |

Recovered **60 s** after the drop.

### The 5 slowest endpoints (whole run, by p95)

| Endpoint | Requests | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|
| `GET /segmentation-dashboard/` | 428 | 172 | 258 | 413 | 0 (0.00%) |
| `POST /api/auth/v1/sessions/refresh` | 3 | 63 | 151 | 151 | 0 (0.00%) |
| `GET /catalog/customers?q=` | 813 | 32 | 85 | 274 | 0 (0.00%) |
| `GET /catalog/customers/{id}` | 1,227 | 39 | 83 | 570 | 0 (0.00%) |
| `GET /catalog/segments/{id}` | 876 | 33 | 79 | 262 | 0 (0.00%) |

### Per user class (whole run)

| Class | Requests | req/s | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|---:|
| desktop-xml | 2,513 | 4.6 | 7 | 18 | 30 | 1,633 (64.98%) |
| floor-android | 3,783 | 7.0 | 15 | 27 | 152 | 1,808 (47.79%) |
| ops-admin | 1,275 | 2.4 | 16 | 33 | 219 | 2 (0.16%) |
| web-analyst | 5,995 | 11.1 | 42 | 159 | 301 | 0 (0.00%) |

### Errors

| Endpoint | Error | Count |
|---|---|---:|
| `POST /api/sales/v1/sales` | 429 rate_limited | 1,439 |
| `GET /api/analytics-kpis/v1/kpi-reports/net-sales` | 429 rate_limited | 438 |
| `GET /api/analytics-kpis/v1/kpi-reports/spend` | 429 rate_limited | 297 |
| `GET /api/analytics-kpis/v1/kpi-reports/net-sales (segment, month)` | 429 rate_limited | 281 |
| `GET /api/products/v1/products` | 429 rate_limited | 172 |
| `GET /api/analytics-kpis/v1/kpi-reports/migrations` | 429 rate_limited | 165 |
| `GET /api/analytics-kpis/v1/kpi-reports/demand-coverage` | 429 rate_limited | 161 |
| `GET /api/analytics-kpis/v1/kpi-reports/retention` | 429 rate_limited | 152 |
| `GET /api/stock/v1/stock-levels` | 429 rate_limited | 142 |
| `GET /api/analytics-kpis/v1/kpi-reports/definitions` | 429 rate_limited | 139 |
| `POST /api/sales/v1/sales (replay)` | replay answered 429 rate_limited without the header | 55 |
| `GET console /` | 0 | 1 |
| `GET console /status.json` | 0 | 1 |
