# Products reads: Flask against its Rust twin, on x86-64 with the 160 MB limit enforced

Evidence for F15-03 (#751), under [ADR-0042](../adr/0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md). It re-runs the comparison of F15-02 (#455, [the first report](products-reads-flask-vs-rust.md), kept as history) under the conditions the instructor asked for. Two builds of the same contract (`contracts/products/`) answer `GET /v1/products` and `GET /v1/products/{product_id}` under the same load: products-service (Flask, gunicorn, 1 worker, 4 threads) and the bench-only twin (axum, 2 Tokio workers, 3 pooled connections). The raw results are in [`bench/results/2026-11-09-gha-x86/`](../../bench/results/2026-11-09-gha-x86/), taken by [GitHub Actions run 42658673709](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/42658673709) (workflow `.github/workflows/bench.yml`); `python3 bench/aggregate.py report bench/results/2026-11-09-gha-x86` reproduces every table below.

## What changed since F15-02

| Gap in the F15-02 report | This run |
|---|---|
| Raspberry Pi 5, aarch64 | GitHub-hosted `ubuntu-24.04` runners: x86-64, 4 vCPU, kernel 7.0.0-1017-azure, Docker 28.0.4 with the systemd cgroup driver on cgroup v2 |
| The 160 MB limit was discarded by the kernel | Enforced and read back from each container's cgroup, for both builds, in all eight shards: `memory.max=167772160` (and `cpu.max=50000 100000`, one CPU allowed). `run.sh` stops otherwise, and `BENCH_ALLOW_NO_MEMLIMIT` was not set |
| One calibration, so the saturation column had no range | Five calibration passes per build, format and scenario; the column is the median with its range |
| RSS only, which counts Flask's shared pages twice | RSS and PSS (`/proc/<pid>/smaps_rollup`) side by side |
| No CPU measure | CPU time from the container's cgroup `cpu.stat`, over a window inside each measured phase, with the quota's throttling |

## How to read this report

Every number is bound to one **scenario** (`list`, `search`, `filter` or `detail`), one **format** (JSON or XML) and one **measure**. Each cell is the median over the 5 repetitions with the min-max range in parentheses (the saturation column: over the 5 calibration passes). **A difference is reported only when the two builds' ranges do not overlap. Where they overlap, this report says "no difference"**, and with 5 repetitions on a shared runner that means only that the ranges overlap, not that a difference was ruled out. No sentence here attributes a result to the implementation language: a measure is a property of that build, in that cell, on that runner. The eight cells ran on eight different runners (see Conditions), so numbers are compared between the two builds of one cell and never between cells.

## Headline

All of this is at an offered rate of 70% of the lower of the two builds' median saturation rates for the cell (so the same work was offered to both), 15 s warm-up then 60 s measured, 5 repetitions.

- **Latency, p50:** the twin's range is below Flask's in all eight cells, without overlap: `list` JSON 3.7 against 2.8 ms, `list` XML 4.9 against 3.4, `search` JSON 4.2 against 3.5, `search` XML 8.9 against 6.6, `filter` JSON 2.2 against 1.3, `filter` XML 2.5 against 0.9, `detail` JSON 0.8 against 0.3, `detail` XML 1.3 against 0.5 ms (medians, Flask then twin).
- **Latency, p95 and p99:** p95 differs in seven cells and **overlaps in `search` JSON** (Flask 8.1 ms, range 6.6 to 10.5; twin 6.3, range 4.0 to 9.2). p99 **overlaps in `list` JSON, `search` JSON and `search` XML**; it differs in `list` XML (25.4 against 17.1 ms), `filter` JSON and XML (4.7 against 3.0; 4.9 against 2.6) and `detail` JSON and XML (5.3 against 1.6; 4.1 against 2.0). One twin run in `search` JSON had a p99 of 163.2 ms and 13 dropped arrivals (repetition 4; the other four runs of that cell had 11.5 to 23.6 ms); the cause was not found.
- **Throughput:** requests per second equal the offered rate for both builds in all eight cells, **no difference** by construction (a constant arrival rate), and the error rate is 0.000% in all 80 runs. Four runs dropped arrivals, listed under the tables: Flask 1 (`filter` JSON), 4 and 10 (`detail` JSON, two runs) and the twin 13 (the `search` JSON run above); none failed a check.
- **Saturation (calibration median, range over 5 passes):** **no difference** in `list` JSON (320, 320 to 320 against the same), `list` XML (240 against 240), `search` JSON (240 against 240) and `search` XML (Flask 120, 120 to 120; twin 160, 120 to 160: the ranges overlap). The twin's range is above Flask's in `filter` JSON (Flask 320, 240 to 320; twin 960), `filter` XML (240 against 1,280), `detail` JSON (640 against 1,920) and `detail` XML (320 against 1,920). In `detail` the twin sustained every step of the ramp in all five passes, so 1,920 is a lower bound. The ramp's steps are coarse (320 to 480 is a 50% step), so "no difference" means no difference at that resolution.
- **CPU, at the same offered rate:** the twin used less CPU than Flask in all eight cells, without overlap, 66% to 85% less CPU per request: `list` JSON 29.2% of one CPU against 6.3% (1,306 against 280 microseconds per request), `list` XML 33.0% against 6.2%, `search` JSON 16.3% against 3.4%, `search` XML 22.0% against 3.8%, `filter` JSON 37.3% against 12.5%, `filter` XML 35.5% against 5.6%, `detail` JSON 29.1% against 5.2%, `detail` XML 25.9% against 3.8%. **No difference** in throttled periods: the 0.5 CPU quota throttled no more than 0.8% of the periods in any run, so it did not bind at these rates.
- **Memory, PSS and RSS under load (peak inside the measured phase):** Flask's is the same in every cell, RSS 118 to 120 MiB and PSS 101 to 102 MiB; RSS counts about 17 MiB of shared pages twice (idle: RSS 115.8, PSS 98.4 MiB). The twin's depends on the scenario: PSS 46 MiB in `detail`, 62 to 76 in `filter`, 112 to 116 in `list`, 121 to 139 in `search`. So the twin's PSS is lower than Flask's in `detail` and `filter`, **higher in `list` and `search`**, in each case without overlap. RSS alone would have hidden that in `list`: it reads 114 against 120 MiB (JSON) and 118.4 against 118.9 (XML), where PSS reads 112 against 102 and 116 against 102.
- **The 160 MB limit:** in the cgroup read-back after each shard's run, `memory.events` shows no `max`, `oom` or `oom_kill` event for any of the 16 containers and `memory.swap.current` was 0, but the twin's `memory.peak` reached **167,460,864 bytes in the `search` XML shard, against a `memory.max` of 167,772,160** (99.8%), and 144.7 MB in `search` JSON and 133 to 136 MB in `list`. The peak covers the whole shard run, calibration steps beyond saturation included.
- **Idle and start:** idle RSS 115.8 MiB (PSS 98.4) for Flask against 34.1 (32.1) for the twin; cold start 5,310 ms (3,640 to 6,387) against 214 ms (179 to 558); image 172.1 MB against 91.5 MB.

## Conditions

One shard per scenario and format, each on its own GitHub-hosted runner with both builds on it, alternating order (Flask first in odd repetitions and calibration passes). Every runner: `ubuntu-24.04`, image `20261101.360.1`, kernel `7.0.0-1017-azure`, 4 vCPU as 2 cores of 2 threads (`cpu_to_core=0,0 1,0 2,1 3,1`, so CPU 0 and 1 share a core, as do 2 and 3), 15 GiB of memory and 3 GiB of swap, Docker 28.0.4 (systemd cgroup driver, cgroup v2), k6 v2.3.0 on `linux/amd64`.

| Shard | CPU of the runner |
|---|---|
| `detail-json`, `search-json` | AMD EPYC 9V45 96-Core |
| `detail-xml`, `list-json` | Intel Xeon Platinum 8573C |
| `filter-json`, `list-xml`, `search-xml` | AMD EPYC 7763 64-Core |
| `filter-xml` | AMD EPYC 9V74 80-Core |

The cgroup read-back, identical for both builds in all eight shards (`facts.txt` of each): `memory.max=167772160 memory.swap.max=167772160 cpu.max=50000 100000`, the init process allowed on CPU 1 only. `memory.swap.max` is the default Docker sets when only `mem_limit` is given (as in products-service's own compose file), and `memory.swap.current` stayed 0.

| Item | Value |
|---|---|
| Source | the merge commit `c381be7ecb22` that GitHub builds for the pull request: develop `ed470029d388` plus this branch at `04ad564ae898` |
| products-service | `ghcr.io/equipo-03-proyecto10/mosaiq-products:0.1.5`, built by the `images` job from `services/products`, image `sha256:c8f7bbaa5cfae13ada157688a1ba6f6e1bd2b496c85e1ed425cb5431eef97f5d` (not pulled from the registry); `kit-py/v0.1.7` |
| Rust twin | `mosaiq-products-service-rs:bench`, image `sha256:d38ec062aafd5aedf982fd241ea7bbf0d062257cb4c6b631e1c55fef59f1b405`, `kit-rs/v0.1.0` |
| Same images everywhere | one `images` job built them and every shard loaded them |
| PostgreSQL | `postgres:18@sha256:8bca9649e52d948114ccc8164b55b7ab26b64e729893e420114bb5c87bf7c7cc`, 10,000 bench products, data on tmpfs |
| Redis | `redis:7.4.11-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` |
| k6 | `grafana/k6@sha256:e66db15b860113878fa74670e31f5e274830b7b6e42c8bff28b2f2d86a257603` |
| Pinning | builds on CPU 1, PostgreSQL, Redis and auth on CPU 2, k6 on CPU 3; each build 0.5 CPU and 160 MB |

## Method

- **Same work for both builds.** The same queries, the same database, the same token path (RS256 verification and the `EXISTS pub:auth:v1:live:{sid}` check on every request) and the same k6 scripts; no cache, no rate limit, no stream worker (ADR-0042's fairness rules, read back by `bench/run.sh`). Each shard first ran the shared conformance suite against both builds and the 20-request identical-answers comparison: `49 passed, 4 skipped` for products-service, `53 passed` for the twin and `compare: 20 requests x 2 formats answered identically` in all eight shards ([`conformance.txt`](../../bench/results/2026-11-09-gha-x86/list-json/conformance.txt) of each).
- **Calibration, five passes.** A ramp of constant-arrival steps of 5 s (10, 20, 40, 60, 80, 120, 160, 240, 320, 480, 640, 960, 1280, 1920 req/s). A step is sustained when at most 1% of arrivals were dropped, at most 1% of requests and checks failed, and p99 was at most 1,000 ms; a pass's saturation is the highest step of the unbroken sustained run from the bottom. The five passes alternate the starting build. The reported saturation is the median of the five with their range, and each cell's offered rate is 70% of the lower of the two builds' medians.
- **Measured runs.** 15 s warm-up then 60 s measured at that constant rate, 5 repetitions, the starting build alternating, 80 runs in all.
- **CPU.** The container's cgroup `cpu.stat` (`usage_usec`, `user_usec`, `system_usec`, `nr_periods`, `nr_throttled`) read at the two edges of a 50 s window inside the measured phase (5 s in from each edge, so that the sampler's start, which precedes k6's, cannot move the window out of the phase). Exact over the window, kernel time included. `docker stats` was not used: it is a sampled rate over its own window and relative to all the host's CPUs. Reported as a percentage of one CPU (at most 50 under the quota), as microseconds per request (the window's CPU time over the requests it held), and as the share of the quota's periods that were throttled.
- **RSS and PSS.** `docker top` (`rss`) and `/proc/<pid>/smaps_rollup` (`Pss`, read with `sudo -n`), summed over every process in the container, sampled every 10 s inside the same window; the run keeps the peak of each. Idle values are taken 10 s after a start.
- **Memory limit.** `memory.max` read from the container's cgroup and compared with Docker's `HostConfig.Memory`; after the run `memory.peak`, `memory.events` and `memory.swap.current` are read again ([`cgroup-end.txt`](../../bench/results/2026-11-09-gha-x86/search-xml/cgroup-end.txt) of each shard).

### Deviations from ADR-0042 and from the F15-02 run

ADR-0042 specifies 30 s of warm-up, 2 minutes measured and 5 repetitions, on one developer machine. Five calibration passes and five repetitions of eight cells at those durations take hours on one runner, and the run had to fit well under an hour, so:

| Item | ADR-0042 and F15-02 | This run | Why |
|---|---|---|---|
| Machine | one developer machine | eight GitHub-hosted runners, one per scenario and format | time; the memory controller and x86-64 |
| Warm-up | 30 s | 15 s | time |
| Measured phase | 120 s | 60 s (50 s window for CPU, RSS and PSS) | time |
| Calibration step | 10 s | 5 s | five passes |
| Cold starts per build | 5 | 3 per shard (24 per build) | time |
| Pause after each k6 run | 5 s | 3 s | time |
| Calibration | once | five passes | the acceptance criterion |

The whole workflow took 33 minutes of wall clock (the images 1 min 34 s, each shard about 28 min). The containers are restarted only for the cold starts, before the calibration: the measured runs run on processes that have already served the calibration's steps, overloaded ones included (see the threats to validity).

## Results

The tables are the output of `python3 bench/aggregate.py report bench/results/2026-11-09-gha-x86`, unedited. RPS is the requests of the measured phase over 60 s; with a constant arrival rate it equals the offered rate unless a build drops arrivals.

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

## Conformance output (fairness rule 1)

Printed by `bench/run.sh` before the first k6 run of each shard (the same in all eight; this is `list-json`):

```
== conformance: products on 127.0.0.1:5003
49 passed, 4 skipped in 6.73s
== conformance: products-service-rs on 127.0.0.1:5103
53 passed in 3.78s
== identical answers
compare: 20 requests x 2 formats answered identically
```

## Reading the numbers against each other

- The only measures where the twin did not come out ahead of Flask, with ranges that do not overlap, are memory in `list` and `search` (PSS 112 to 139 MiB against 101 to 102). Everything else the report measures is either better for the twin (lower p50 everywhere, p95 in seven cells, p99 in five, CPU everywhere and PSS in four cells; higher saturation in four cells) or **no difference** (p99 in three cells, p95 in one, saturation in four, throughput, errors, throttling).
- Arithmetic, not a measurement: CPU per request times a cell's median saturation rate gives the CPU a build would use at that rate if its cost per request did not change. For `filter` it comes to 53% (Flask JSON), 54% (twin JSON), 51% (Flask XML) and 42% (twin XML) of one CPU, close to the 0.5 CPU quota; for `list` and `search` the twin's comes to 5% to 9% of a CPU. This is consistent with the quota limiting `filter` for both builds and with the twin's `list` and `search` saturation being set elsewhere, in the path both builds share (PostgreSQL, Redis, k6). It was not tested: PostgreSQL's CPU was not recorded.
- Against F15-02 (a Raspberry Pi 5, limit not enforced, one calibration): the p50 gaps in `filter` and `detail` are of the same size (twin 59% to 63% lower on `detail`, 41% and 63% on `filter`, against 61% to 62% and 43% and 64% there). The p99 ranges did not overlap in any cell on the Pi and overlap in three here. The twin's memory under load was 62 MiB (RSS) there, in every scenario; here it is scenario dependent. These are two different machines and two methods; no claim is made about which differences are due to which.

## Threats to validity

- **Shared, virtualised runners.** Each shard is a VM on a shared host with neighbours this run cannot see. The five repetitions of a cell ran within about 15 minutes on one VM, so the ranges measure noise inside that window, not between VMs or hours; there is **one runner per cell, never two**. One twin run (`search` JSON, repetition 4: p99 163.2 ms, 13 dropped arrivals) is the visible kind of event; it is inside the cell's range and was not removed.
- **Different hardware per shard.** The eight shards got four CPU models (AMD EPYC 9V45, 9V74, 7763 and Intel Xeon Platinum 8573C). Both builds of a cell shared a runner, so a cell compares the builds fairly, but a cell's absolute numbers are not comparable with another cell's, and the same cell on another runner would give other absolute numbers.
- **Pinning on hyperthreads.** The 4 vCPUs are 2 cores of 2 threads: the builds (CPU 1) share a core with the operating system, Docker and the runner agent (CPU 0), and PostgreSQL, Redis and auth (CPU 2) share a core with k6 (CPU 3). The `docker top`, `sudo` and `cat` of the sampler are not pinned either. This is the same for both builds, but CPU microseconds per request on a thread whose sibling is busy are not those of a dedicated core.
- **Shortened phases** (15 s warm-up, 60 s measured with a 50 s window, calibration steps of 5 s, 3 cold starts per shard instead of ADR-0042's 30 s, 120 s, 10 s and 5). p99 over 60 s of 84 to 448 req/s is 5,000 to 27,000 samples, enough for p99 but short for rare events; a 5 s step offers few requests at low rates and gives a coarser sustained/not-sustained verdict.
- **Memory under load includes what the calibration left behind.** The containers are restarted only before the calibration, so the "under load" RSS and PSS of a measured run are peaks of a process that has already served the calibration's steps beyond saturation (up to 1,920 req/s offered). Flask's are the same in every cell (one worker, four threads bound the work in flight). The twin's rise with the number of steps it was driven past saturation: PSS 46 MiB in `detail` (it sustained every step), 62 and 76 MiB in `filter` (one and two steps beyond), 112 to 116 in `list` (five or six steps beyond) and 121 to 139 in `search` (six to eight). That ordering is **consistent with memory retained after queued work at overload, and it was not tested**: a restart before the measured phase, or a sample before and after the calibration, would separate the two. Until then these figures say what the twin's resident set was in this procedure, not what it holds at 70% load.
- **`memory.peak` is whole-run and charge-based.** It covers the calibration, overload included, and counts pages charged to the cgroup, which differs from RSS and PSS (it excludes shared file pages another cgroup had already loaded, and includes page cache). Flask's `memory.peak` (93 to 95 MB) is below its PSS for that reason. It is evidence about the limit, not a second measure of the resident set.
- **Saturation is a lower bound at the top of the ramp and coarse everywhere.** The twin sustained 1,920 req/s in `detail` in all five passes. Elsewhere the ramp's steps (240, 320, 480, ...) are 33% to 50% apart, so five identical passes mean the same step, not the same rate; the criterion (1% drops, 1% errors, p99 at most 1 s) is a choice. k6 had one hyperthread shared with PostgreSQL, so the highest steps may be limited by it, for both builds.
- **Production concurrency, not equal parallelism.** One gunicorn worker with four threads against two Tokio workers compares deployments, not runtimes at equal parallelism (ADR-0042).
- **The shared path** (RS256 verification, the Redis liveness check, one PostgreSQL) is common to both builds and compresses differences; PostgreSQL's and Redis's CPU were not recorded, so where the builds do not differ this report does not say what limits them.
- **Two search terms match nothing** in the fixture (`organic juice`, `family soap`), as in F15-02: about a quarter of the `search` requests scan the table and return an empty page, for both builds.
- **Images built on the runner**, not pulled from the registry: they are the repository's code at the merge commit above, not necessarily byte-identical to the published images of the same tags. The merge commit includes develop at the time (`ed470029d388`).
- **Small ranges.** Five repetitions make the min-max range a coarse measure of noise; "no difference" means overlapping ranges, and "lower" or "higher" means non-overlapping ranges in a cell, nothing more. No significance test was run.
- **The sampler** starts with `docker run`, a moment before k6; the 5 s margin keeps the window inside the measured phase, but the window's exact position was not checked against k6's own clock.

## Reproduce

```bash
# On a runner or a machine with Docker, cgroup v2 and the memory controller:
bash bench/run.sh                     # ADR-0042's timings: 5 repetitions, 30 s warm-up, 120 s measured, 10 s steps, 5 calibration passes
# What this report ran, one shard (CI runs the eight):
BENCH_SCENARIOS=detail BENCH_FORMATS=json BENCH_WARMUP_SECONDS=15 BENCH_MEASURE_SECONDS=60 \
  BENCH_CAL_STEP_SECONDS=5 BENCH_COLDSTART_RUNS=3 BENCH_SETTLE_SECONDS=3 BENCH_OUT=bench/results/detail-json bash bench/run.sh
python3 bench/aggregate.py report bench/results/2026-11-09-gha-x86
```

The artifact of the run reported here, `bench-results` (the shards and `report.md`), is kept for 30 days: `gh run download 42658673709 -n bench-results`; the files this report reads are committed. In CI: Actions > Bench > Run workflow (once `bench.yml` is on the default branch), or a pull request from a branch whose name contains `bench-rerun`. `bench/README.md` describes the settings.
