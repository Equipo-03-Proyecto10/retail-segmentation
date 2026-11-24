### Per step (steady window)

| Phase | Users | Requests | req/s | Mean ms | p50 ms | p95 ms | p99 ms | Max ms | Errors | Sustained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| step-1 | 10 | 245 | 4.9 | 34 | 24 | 126 | 195 | 222 | 0 (0.00%) | yes |
| step-2 | 20 | 493 | 9.8 | 25 | 19 | 50 | 160 | 201 | 0 (0.00%) | yes |
| step-3 | 40 | 984 | 20.0 | 21 | 13 | 51 | 153 | 216 | 0 (0.00%) | yes |
| step-4 | 80 | 2,001 | 39.7 | 17 | 11 | 37 | 150 | 382 | 0 (0.00%) | yes |
| step-5 | 120 | 2,997 | 59.5 | 17 | 10 | 43 | 172 | 292 | 0 (0.00%) | yes |
| step-6 | 160 | 3,891 | 78.8 | 20 | 9 | 94 | 204 | 356 | 0 (0.00%) | yes |
| recovery | 10 | 846 | 5.0 | 35 | 26 | 81 | 248 | 280 | 1 (0.12%) | yes |

### Saturation

Every step was sustained, up to 160 users: the platform did not saturate within this ramp.

### Recovery after the drop

Baseline: step-1's p95, 126 ms; threshold 151 ms with no failure, in 10-second windows from the drop.

| Window (s after drop) | Requests | p95 ms | Errors | Recovered |
|---|---:|---:|---:|---|
| 0–10 | 48 | 55 | 0 | yes |
| 10–20 | 53 | 60 | 0 | yes |
| 20–30 | 49 | 228 | 0 | no |
| 30–40 | 50 | 198 | 0 | no |
| 40–50 | 44 | 64 | 0 | yes |
| 50–60 | 51 | 55 | 0 | yes |
| 60–70 | 46 | 123 | 0 | yes |
| 70–80 | 51 | 68 | 0 | yes |
| 80–90 | 51 | 226 | 0 | no |
| 90–100 | 51 | 80 | 0 | yes |
| 100–110 | 50 | 65 | 0 | yes |
| 110–120 | 52 | 60 | 0 | yes |
| 120–130 | 52 | 71 | 0 | yes |
| 130–140 | 47 | 220 | 1 | no |
| 140–150 | 49 | 90 | 0 | yes |
| 150–160 | 49 | 71 | 0 | yes |
| 160–170 | 52 | 75 | 0 | yes |
| 170–180 | 49 | 80 | 0 | yes |

Recovered **10 s** after the drop.

### The 5 slowest endpoints (whole run, by p95)

| Endpoint | Requests | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|
| `GET /segmentation-dashboard/` | 430 | 173 | 245 | 302 | 0 (0.00%) |
| `GET /catalog/customers?q=` | 846 | 24 | 63 | 145 | 0 (0.00%) |
| `GET /catalog/customers/{id}` | 1,278 | 27 | 60 | 134 | 0 (0.00%) |
| `GET /catalog/segments` | 867 | 22 | 55 | 109 | 0 (0.00%) |
| `GET /catalog/customers` | 1,704 | 22 | 53 | 112 | 0 (0.00%) |

### Per user class (whole run)

| Class | Requests | req/s | Mean ms | p95 ms | p99 ms | Errors |
|---|---:|---:|---:|---:|---:|---:|
| desktop-xml | 2,565 | 4.7 | 7 | 18 | 29 | 0 (0.00%) |
| floor-android | 3,786 | 7.0 | 10 | 25 | 47 | 0 (0.00%) |
| ops-admin | 1,302 | 2.4 | 10 | 27 | 94 | 2 (0.15%) |
| web-analyst | 5,978 | 11.1 | 34 | 155 | 211 | 0 (0.00%) |

### Errors

| Endpoint | Error | Count |
|---|---|---:|
| `GET console /status.json` | 0 | 2 |
