Each cell is the median over the repetitions (or calibration passes), with the min-max range in parentheses.
Latency in milliseconds. Rates in requests per second. Error rate is the share of
requests that were not an expected status (404 is expected for the detail scenario's
unknown ids).

### Machines

| Shard | CPU | cores (threads per core) | kernel | runner image | memory limit | cgroup read-back (Flask) | cgroup read-back (Rust twin) |
|---|---|---|---|---|---|---|---|
| detail-json | AMD EPYC 9V45 96-Core Processor | 4 (2) | Ubuntu 24.04.5 LTS kernel 7.0.0-1017-azure x86_64 | GitHub Actions 1000012880 image=20261101.360.1 | 160m | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 |
| detail-xml | INTEL(R) XEON(R) PLATINUM 8573C | 4 (2) | Ubuntu 24.04.5 LTS kernel 7.0.0-1017-azure x86_64 | GitHub Actions 1000012883 image=20261101.360.1 | 160m | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 |
| filter-json | AMD EPYC 7763 64-Core Processor | 4 (2) | Ubuntu 24.04.5 LTS kernel 7.0.0-1017-azure x86_64 | GitHub Actions 1000012886 image=20261101.360.1 | 160m | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 |
| filter-xml | AMD EPYC 9V74 80-Core Processor | 4 (2) | Ubuntu 24.04.5 LTS kernel 7.0.0-1017-azure x86_64 | GitHub Actions 1000012878 image=20261101.360.1 | 160m | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 |
| list-json | INTEL(R) XEON(R) PLATINUM 8573C | 4 (2) | Ubuntu 24.04.5 LTS kernel 7.0.0-1017-azure x86_64 | GitHub Actions 1000012879 image=20261101.360.1 | 160m | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 |
| list-xml | AMD EPYC 7763 64-Core Processor | 4 (2) | Ubuntu 24.04.5 LTS kernel 7.0.0-1017-azure x86_64 | GitHub Actions 1000012882 image=20261101.360.1 | 160m | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 |
| search-json | AMD EPYC 9V45 96-Core Processor | 4 (2) | Ubuntu 24.04.5 LTS kernel 7.0.0-1017-azure x86_64 | GitHub Actions 1000012885 image=20261101.360.1 | 160m | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 |
| search-xml | AMD EPYC 7763 64-Core Processor | 4 (2) | Ubuntu 24.04.5 LTS kernel 7.0.0-1017-azure x86_64 | GitHub Actions 1000012877 image=20261101.360.1 | 160m | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 | memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000 cpus_allowed=1 |

- products_image: ghcr.io/equipo-03-proyecto10/mosaiq-products:0.1.5 sha256:c8f7bbaa5cfae13ada157688a1ba6f6e1bd2b496c85e1ed425cb5431eef97f5d
- twin_image: mosaiq-products-service-rs:bench sha256:d38ec062aafd5aedf982fd241ea7bbf0d062257cb4c6b631e1c55fef59f1b405
- k6_image_digest: grafana/k6@sha256:e66db15b860113878fa74670e31f5e274830b7b6e42c8bff28b2f2d86a257603
- postgres: postgres:18 sha256:8bca9649e52d948114ccc8164b55b7ab26b64e729893e420114bb5c87bf7c7cc
- commit: c381be7ecb224085220dfb6d7bc455b205b55b2c
- dirty: no

### Summary: medians, Flask / Rust twin

`=` marks a measure whose Flask and Rust ranges overlap over the repetitions (the calibration passes for saturation): no difference. The tables below give every range.

| Cell | offered (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | CPU (% of one CPU) | CPU (us per request) | RSS peak (MiB) | PSS peak (MiB) | saturation (req/s) |
|---|---|---|---|---|---|---|---|---|---|
| list JSON | 224 | 3.7 / 2.8 | 8.5 / 5.4 | 19.3 / 15.2 = | 29.2 / 6.3 | 1306 / 280 | 120 / 114 | 102 / 112 | 320 / 320 = |
| list XML | 168 | 4.9 / 3.4 | 11.8 / 6.5 | 25.4 / 17.1 | 33.0 / 6.2 | 1963 / 367 | 119 / 118 | 102 / 116 | 240 / 240 = |
| search JSON | 168 | 4.2 / 3.5 | 8.1 / 6.3 = | 20.2 / 16.6 = | 16.3 / 3.4 | 968 / 204 | 119 / 123 | 102 / 121 | 240 / 240 = |
| search XML | 84 | 8.9 / 6.6 | 15.7 / 11.8 | 29.3 / 21.3 = | 22.0 / 3.8 | 2615 / 447 | 118 / 141 | 101 / 139 | 120 / 160 = |
| filter JSON | 224 | 2.2 / 1.3 | 2.7 / 1.6 | 4.7 / 3.0 | 37.3 / 12.5 | 1666 / 559 | 119 / 78 | 102 / 76 | 320 / 960 |
| filter XML | 168 | 2.5 / 0.9 | 3.0 / 1.2 | 4.9 / 2.6 | 35.5 / 5.6 | 2111 / 332 | 119 / 64 | 101 / 62 | 240 / 1280 |
| detail JSON | 448 | 0.8 / 0.3 | 1.0 / 0.4 | 5.3 / 1.6 | 29.1 / 5.2 | 650 / 117 | 119 / 48 | 102 / 46 | 640 / 1920 |
| detail XML | 224 | 1.3 / 0.5 | 1.7 / 0.8 | 4.1 / 2.0 | 25.9 / 3.8 | 1157 / 169 | 118 / 48 | 101 / 46 | 320 / 1920 |

### list, JSON (offered 224 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 3.7 (3.7-3.7) | 8.5 (8.5-8.8) | 19.3 (16.1-20.2) | 224.0 (224.0-224.0) | 0.000 (0.000-0.000) | 320 (320-320) |
| Rust twin | 5 | 2.8 (2.8-2.8) | 5.4 (5.0-5.6) | 15.2 (13.4-16.2) | 224.0 (224.0-224.0) | 0.000 (0.000-0.000) | 320 (320-320) |
| Difference | | Rust 24% lower | Rust 37% lower | no difference | no difference | no difference | no difference |

| Build | n | CPU (% of one CPU) | CPU (us per request) | throttled periods (%) | RSS peak (MiB) | PSS peak (MiB) |
|---|---|---|---|---|---|---|
| Flask | 5 | 29.2 (29.1-29.5) | 1306 (1298-1318) | 0.0 (0.0-0.0) | 119.7 (119.7-119.7) | 101.9 (101.9-101.9) |
| Rust twin | 5 | 6.3 (6.2-6.3) | 280 (279-282) | 0.0 (0.0-0.0) | 114.0 (113.4-114.0) | 112.0 (111.4-112.0) |
| Difference | | Rust 79% lower | Rust 79% lower | no difference | Rust 5% lower | Rust 10% higher |

### list, XML (offered 168 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 4.9 (4.8-5.0) | 11.8 (11.1-12.2) | 25.4 (22.4-28.8) | 168.0 (168.0-168.0) | 0.000 (0.000-0.000) | 240 (240-240) |
| Rust twin | 5 | 3.4 (3.4-3.6) | 6.5 (6.4-6.7) | 17.1 (13.6-18.7) | 168.0 (168.0-168.0) | 0.000 (0.000-0.000) | 240 (240-240) |
| Difference | | Rust 29% lower | Rust 45% lower | Rust 32% lower | no difference | no difference | no difference |

| Build | n | CPU (% of one CPU) | CPU (us per request) | throttled periods (%) | RSS peak (MiB) | PSS peak (MiB) |
|---|---|---|---|---|---|---|
| Flask | 5 | 33.0 (32.5-34.5) | 1963 (1934-2055) | 0.0 (0.0-0.4) | 118.9 (118.9-118.9) | 101.5 (101.5-101.5) |
| Rust twin | 5 | 6.2 (6.1-6.6) | 367 (364-391) | 0.0 (0.0-0.0) | 118.4 (118.4-118.4) | 116.4 (116.4-116.4) |
| Difference | | Rust 81% lower | Rust 81% lower | no difference | Rust 0.4% lower | Rust 15% higher |

### search, JSON (offered 168 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 4.2 (4.0-4.2) | 8.1 (6.6-10.5) | 20.2 (15.6-26.4) | 168.0 (168.0-168.0) | 0.000 (0.000-0.000) | 240 (240-240) |
| Rust twin | 5 | 3.5 (3.4-3.7) | 6.3 (4.0-9.2) | 16.6 (11.5-163.2) | 168.0 (167.8-168.0) | 0.000 (0.000-0.000) | 240 (240-240) |
| Difference | | Rust 16% lower | no difference | no difference | no difference | no difference | no difference |

| Build | n | CPU (% of one CPU) | CPU (us per request) | throttled periods (%) | RSS peak (MiB) | PSS peak (MiB) |
|---|---|---|---|---|---|---|
| Flask | 5 | 16.3 (13.9-16.6) | 968 (828-986) | 0.0 (0.0-0.0) | 119.4 (119.4-119.4) | 101.9 (101.9-101.9) |
| Rust twin | 5 | 3.4 (3.1-4.1) | 204 (183-244) | 0.0 (0.0-0.0) | 123.3 (123.3-127.3) | 121.3 (121.3-125.3) |
| Difference | | Rust 79% lower | Rust 79% lower | no difference | Rust 3% higher | Rust 19% higher |

### search, XML (offered 84 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 8.9 (8.5-9.1) | 15.7 (14.9-16.6) | 29.3 (24.1-30.7) | 84.0 (84.0-84.0) | 0.000 (0.000-0.000) | 120 (120-120) |
| Rust twin | 5 | 6.6 (6.6-6.9) | 11.8 (10.8-12.5) | 21.3 (18.6-24.6) | 84.0 (84.0-84.0) | 0.000 (0.000-0.000) | 160 (120-160) |
| Difference | | Rust 25% lower | Rust 25% lower | no difference | no difference | no difference | no difference |

| Build | n | CPU (% of one CPU) | CPU (us per request) | throttled periods (%) | RSS peak (MiB) | PSS peak (MiB) |
|---|---|---|---|---|---|---|
| Flask | 5 | 22.0 (20.3-23.2) | 2615 (2414-2767) | 0.0 (0.0-0.0) | 118.3 (118.3-118.3) | 101.2 (101.2-101.2) |
| Rust twin | 5 | 3.8 (3.6-4.2) | 447 (431-500) | 0.0 (0.0-0.0) | 140.8 (140.8-142.6) | 138.7 (138.7-140.5) |
| Difference | | Rust 83% lower | Rust 83% lower | no difference | Rust 19% higher | Rust 37% higher |

### filter, JSON (offered 224 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 2.2 (2.2-2.2) | 2.7 (2.7-2.8) | 4.7 (4.6-5.8) | 224.0 (224.0-224.0) | 0.000 (0.000-0.000) | 320 (240-320) |
| Rust twin | 5 | 1.3 (1.3-1.3) | 1.6 (1.6-1.6) | 3.0 (2.9-3.2) | 224.0 (224.0-224.0) | 0.000 (0.000-0.000) | 960 (960-960) |
| Difference | | Rust 41% lower | Rust 42% lower | Rust 37% lower | no difference | no difference | Rust 200% higher |

| Build | n | CPU (% of one CPU) | CPU (us per request) | throttled periods (%) | RSS peak (MiB) | PSS peak (MiB) |
|---|---|---|---|---|---|---|
| Flask | 5 | 37.3 (37.2-37.4) | 1666 (1659-1669) | 0.0 (0.0-0.8) | 118.9 (118.9-118.9) | 101.6 (101.6-101.6) |
| Rust twin | 5 | 12.5 (12.5-12.6) | 559 (557-564) | 0.0 (0.0-0.0) | 78.2 (78.2-78.2) | 76.2 (76.2-76.2) |
| Difference | | Rust 66% lower | Rust 66% lower | no difference | Rust 34% lower | Rust 25% lower |

### filter, XML (offered 168 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 2.5 (2.5-2.6) | 3.0 (3.0-3.3) | 4.9 (4.8-5.2) | 168.0 (168.0-168.0) | 0.000 (0.000-0.000) | 240 (240-240) |
| Rust twin | 5 | 0.9 (0.9-1.0) | 1.2 (1.2-1.2) | 2.6 (2.5-2.8) | 168.0 (168.0-168.0) | 0.000 (0.000-0.000) | 1280 (1280-1280) |
| Difference | | Rust 63% lower | Rust 59% lower | Rust 46% lower | no difference | no difference | Rust 433% higher |

| Build | n | CPU (% of one CPU) | CPU (us per request) | throttled periods (%) | RSS peak (MiB) | PSS peak (MiB) |
|---|---|---|---|---|---|---|
| Flask | 5 | 35.5 (35.2-37.1) | 2111 (2096-2205) | 0.0 (0.0-0.2) | 119.0 (119.0-119.0) | 101.5 (101.5-101.5) |
| Rust twin | 5 | 5.6 (5.5-5.7) | 332 (326-338) | 0.0 (0.0-0.0) | 64.3 (64.3-64.3) | 62.3 (62.3-62.3) |
| Difference | | Rust 84% lower | Rust 84% lower | no difference | Rust 46% lower | Rust 39% lower |

### detail, JSON (offered 448 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 0.8 (0.8-0.8) | 1.0 (1.0-1.0) | 5.3 (4.6-6.8) | 448.0 (447.9-448.0) | 0.000 (0.000-0.000) | 640 (640-640) |
| Rust twin | 5 | 0.3 (0.3-0.3) | 0.4 (0.4-0.5) | 1.6 (1.5-1.8) | 448.0 (448.0-448.0) | 0.000 (0.000-0.000) | 1920 (1920-1920) |
| Difference | | Rust 59% lower | Rust 56% lower | Rust 70% lower | no difference | no difference | Rust 200% higher |

Rust twin sustained the top of the ramp (1920 req/s) in 5 of 5 passes: its saturation rate is a lower bound.

| Build | n | CPU (% of one CPU) | CPU (us per request) | throttled periods (%) | RSS peak (MiB) | PSS peak (MiB) |
|---|---|---|---|---|---|---|
| Flask | 5 | 29.1 (29.1-29.5) | 650 (649-659) | 0.0 (0.0-0.6) | 119.1 (119.1-119.1) | 101.7 (101.7-101.7) |
| Rust twin | 5 | 5.2 (5.2-5.3) | 117 (117-118) | 0.0 (0.0-0.0) | 47.9 (47.9-47.9) | 45.8 (45.8-45.8) |
| Difference | | Rust 82% lower | Rust 82% lower | no difference | Rust 60% lower | Rust 55% lower |

### detail, XML (offered 224 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 1.3 (1.3-1.3) | 1.7 (1.7-1.7) | 4.1 (4.1-4.4) | 224.0 (224.0-224.0) | 0.000 (0.000-0.000) | 320 (320-320) |
| Rust twin | 5 | 0.5 (0.5-0.5) | 0.8 (0.7-0.8) | 2.0 (1.9-2.2) | 224.0 (224.0-224.0) | 0.000 (0.000-0.000) | 1920 (1920-1920) |
| Difference | | Rust 63% lower | Rust 55% lower | Rust 52% lower | no difference | no difference | Rust 500% higher |

Rust twin sustained the top of the ramp (1920 req/s) in 5 of 5 passes: its saturation rate is a lower bound.

| Build | n | CPU (% of one CPU) | CPU (us per request) | throttled periods (%) | RSS peak (MiB) | PSS peak (MiB) |
|---|---|---|---|---|---|---|
| Flask | 5 | 25.9 (25.7-26.2) | 1157 (1147-1171) | 0.0 (0.0-0.0) | 118.5 (118.5-118.5) | 101.2 (101.2-101.2) |
| Rust twin | 5 | 3.8 (3.8-3.8) | 169 (168-171) | 0.0 (0.0-0.0) | 47.9 (47.9-47.9) | 45.8 (45.8-45.8) |
| Difference | | Rust 85% lower | Rust 85% lower | no difference | Rust 60% lower | Rust 55% lower |

### Dropped arrivals and failed checks in the measured runs

- `rep4-rust-json-search`: 10068 requests, dropped arrivals: 13, failed checks: 0.000%
- `rep3-flask-json-filter`: 13440 requests, dropped arrivals: 1, failed checks: 0.000%
- `rep3-flask-json-detail`: 26877 requests, dropped arrivals: 4, failed checks: 0.000%
- `rep5-flask-json-detail`: 26871 requests, dropped arrivals: 10, failed checks: 0.000%

### Memory, image size and cold start

| Build | RSS idle (MiB) | PSS idle (MiB) | RSS under load (MiB) | PSS under load (MiB) | image (MB) | cold start (ms) |
|---|---|---|---|---|---|---|
| Flask | 115.8 (115.4-118.2) | 98.4 (98.2-101.0) | 119.0 (118.3-119.7) | 101.6 (101.2-101.9) | 172.1 | 5310 (3640-6387) |
| Rust twin | 34.1 (34.0-34.2) | 32.1 (32.0-32.2) | 95.8 (47.9-142.6) | 93.8 (45.8-140.5) | 91.5 | 214 (179-558) |

RSS and PSS are the sums over every process in the container (`docker top`, `/proc/<pid>/smaps_rollup`),
idle after a 10 s settle following a start, and under load the peak of the samples taken inside each measured
phase. The ranges are over every cold start of the build, and over every measured run of the build (all
scenarios, formats and repetitions; every shard).

### Calibration

- list json Flask: saturation per pass [320, 320, 320, 320, 320], median 320, range 320-320; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:5/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- list json Rust twin: saturation per pass [320, 320, 320, 320, 320], median 320, range 320-320; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:5/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- list xml Flask: saturation per pass [240, 240, 240, 240, 240], median 240, range 240-240; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:0/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- list xml Rust twin: saturation per pass [240, 240, 240, 240, 240], median 240, range 240-240; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:0/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- search json Flask: saturation per pass [240, 240, 240, 240, 240], median 240, range 240-240; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:0/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- search json Rust twin: saturation per pass [240, 240, 240, 240, 240], median 240, range 240-240; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:0/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- search xml Flask: saturation per pass [120, 120, 120, 120, 120], median 120, range 120-120; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:0/5, 240:0/5, 320:0/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- search xml Rust twin: saturation per pass [120, 120, 160, 160, 160], median 160, range 120-160; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:3/5, 240:0/5, 320:0/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- filter json Flask: saturation per pass [320, 240, 320, 320, 320], median 320, range 240-320; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:4/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- filter json Rust twin: saturation per pass [960, 960, 960, 960, 960], median 960, range 960-960; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:5/5, 480:5/5, 640:5/5, 960:5/5, 1280:0/5, 1920:0/5
- filter xml Flask: saturation per pass [240, 240, 240, 240, 240], median 240, range 240-240; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:0/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- filter xml Rust twin: saturation per pass [1280, 1280, 1280, 1280, 1280], median 1280, range 1280-1280; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:5/5, 480:5/5, 640:5/5, 960:5/5, 1280:5/5, 1920:0/5
- detail json Flask: saturation per pass [640, 640, 640, 640, 640], median 640, range 640-640; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:5/5, 480:5/5, 640:5/5, 960:0/5, 1280:0/5, 1920:0/5
- detail json Rust twin: saturation per pass [1920, 1920, 1920, 1920, 1920], median 1920, range 1920-1920; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:5/5, 480:5/5, 640:5/5, 960:5/5, 1280:5/5, 1920:5/5
- detail xml Flask: saturation per pass [320, 320, 320, 320, 320], median 320, range 320-320; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:5/5, 480:0/5, 640:0/5, 960:0/5, 1280:0/5, 1920:0/5
- detail xml Rust twin: saturation per pass [1920, 1920, 1920, 1920, 1920], median 1920, range 1920-1920; passes that sustained each step: 10:5/5, 20:5/5, 40:5/5, 60:5/5, 80:5/5, 120:5/5, 160:5/5, 240:5/5, 320:5/5, 480:5/5, 640:5/5, 960:5/5, 1280:5/5, 1920:5/5
