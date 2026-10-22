# Products reads: Flask against its Rust twin

Evidence for F15-02 (#455), under [ADR-0042](../adr/0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md). Two builds of the same contract (`contracts/products/`) answer `GET /v1/products` and `GET /v1/products/{product_id}` under the same load: products-service (Flask, gunicorn, 1 worker, 4 threads) and the bench-only twin (axum, 2 Tokio workers, 3 pooled connections). The raw k6 JSON summaries are in [`bench/results/2026-10-22-pi5/`](../../bench/results/2026-10-22-pi5/); `python3 bench/aggregate.py report bench/results/2026-10-22-pi5` reproduces every table below.

## Headline

- **Latency:** at an offered rate of 70% of the lower saturation rate, the twin was lower than Flask in every scenario and format, and its p50, p95 and p99 ranges did not overlap Flask's. The gap was small on `search` (p50 12 to 17% lower), where PostgreSQL dominates, and large on `detail` (p50 about 61% lower) and `filter` (p50 43 to 64% lower).
- **Saturation:** the twin sustained 1x (`list` JSON, `search`), 1.3x (`list` XML), 3x to 6x (`filter`) and 4x to 6x (`detail`) Flask's rate.
- **Memory:** the twin used 22 MiB idle and 62 MiB under load; Flask used 116 MiB and 120 MiB. Image: 157 MB against 281 MB. Cold start: 0.5 s against 8.6 s.
- **No difference** in requests per second (both builds were offered the same constant rate and sustained it) and in error rate (0 in all 80 measured runs).
- Where the shared path dominates, `search` and `list`, saturation is identical or close: both builds sit on the same PostgreSQL query. The error rate is 0.000% everywhere, so the rows say nothing more than that neither build failed at 70%.

## How to read the tables

Each cell is the median over the 5 repetitions with the min-max range in parentheses. The "Difference" row says **no difference** when the two builds' ranges overlap, and otherwise gives the difference between medians. RPS is the number of requests in the 2-minute measured phase divided by 120 s; with a constant arrival rate it equals the offered rate unless the build drops arrivals, so it is "no difference" by construction here. The saturation rate comes from a single calibration per build, format and scenario and has no range; the "Difference" in that column compares two single numbers and is bounded by the calibration's step size (the ramp has steps of 10 to 1920 req/s, so a 120 against a 160 is one step).

### list, JSON (offered 112 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 7.2 (7.2-7.3) | 13.8 (13.6-16.9) | 23.8 (22.1-41.8) | 112.0 (112.0-112.0) | 0.000 (0.000-0.000) | 160 |
| Rust twin | 5 | 5.7 (5.7-5.8) | 9.4 (9.0-11.1) | 15.9 (15.2-19.8) | 112.0 (112.0-112.0) | 0.000 (0.000-0.000) | 160 |
| Difference | | Rust 21% lower | Rust 32% lower | Rust 33% lower | no difference | no difference | no difference |

### list, XML (offered 84 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 7.9 (7.8-7.9) | 14.8 (14.1-16.1) | 24.8 (20.2-68.4) | 84.0 (84.0-84.0) | 0.000 (0.000-0.000) | 120 |
| Rust twin | 5 | 5.6 (5.6-5.6) | 8.7 (8.3-8.7) | 12.7 (12.6-12.9) | 84.0 (84.0-84.0) | 0.000 (0.000-0.000) | 160 |
| Difference | | Rust 29% lower | Rust 42% lower | Rust 49% lower | no difference | no difference | Rust 33% higher |

### search, JSON (offered 56 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 13.2 (13.1-13.2) | 21.6 (20.6-22.7) | 36.5 (35.9-37.8) | 56.0 (56.0-56.0) | 0.000 (0.000-0.000) | 80 |
| Rust twin | 5 | 11.6 (11.6-11.6) | 17.1 (16.6-17.6) | 29.1 (28.6-30.1) | 56.0 (56.0-56.0) | 0.000 (0.000-0.000) | 80 |
| Difference | | Rust 12% lower | Rust 21% lower | Rust 20% lower | no difference | no difference | no difference |

### search, XML (offered 56 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 13.8 (13.8-13.9) | 23.0 (22.0-24.9) | 38.1 (37.9-39.1) | 56.0 (56.0-56.0) | 0.000 (0.000-0.000) | 80 |
| Rust twin | 5 | 11.5 (11.5-11.5) | 17.0 (16.6-17.9) | 29.1 (28.9-30.4) | 56.0 (56.0-56.0) | 0.000 (0.000-0.000) | 80 |
| Difference | | Rust 17% lower | Rust 26% lower | Rust 24% lower | no difference | no difference | no difference |

### filter, JSON (offered 112 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 3.3 (3.3-3.3) | 6.2 (6.0-6.4) | 10.1 (9.4-11.4) | 112.0 (112.0-112.0) | 0.000 (0.000-0.000) | 160 |
| Rust twin | 5 | 1.9 (1.9-1.9) | 3.7 (3.6-3.8) | 7.0 (6.9-7.2) | 112.0 (112.0-112.0) | 0.000 (0.000-0.000) | 480 |
| Difference | | Rust 43% lower | Rust 40% lower | Rust 30% lower | no difference | no difference | Rust 200% higher |

### filter, XML (offered 56 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 4.9 (4.9-5.0) | 8.3 (8.1-8.9) | 12.1 (11.7-12.8) | 56.0 (56.0-56.0) | 0.000 (0.000-0.000) | 80 |
| Rust twin | 5 | 1.8 (1.8-1.8) | 4.1 (4.0-4.3) | 7.3 (7.2-7.5) | 56.0 (56.0-56.0) | 0.000 (0.000-0.000) | 480 |
| Difference | | Rust 64% lower | Rust 50% lower | Rust 39% lower | no difference | no difference | Rust 500% higher |

### detail, JSON (offered 168 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 2.2 (2.2-2.2) | 5.6 (5.1-5.8) | 15.4 (10.3-22.5) | 168.0 (168.0-168.0) | 0.000 (0.000-0.000) | 240 |
| Rust twin | 5 | 0.8 (0.8-0.9) | 2.4 (2.2-2.5) | 6.0 (5.7-6.2) | 168.0 (168.0-168.0) | 0.000 (0.000-0.000) | 960 |
| Difference | | Rust 61% lower | Rust 58% lower | Rust 61% lower | no difference | no difference | Rust 300% higher |

### detail, XML (offered 112 req/s)

| Build | n | p50 (ms) | p95 (ms) | p99 (ms) | RPS | error rate (%) | saturation (req/s) |
|---|---|---|---|---|---|---|---|
| Flask | 5 | 2.3 (2.3-2.4) | 5.1 (4.7-5.3) | 9.3 (8.1-9.6) | 112.0 (112.0-112.0) | 0.000 (0.000-0.000) | 160 |
| Rust twin | 5 | 0.9 (0.9-0.9) | 2.5 (2.4-2.5) | 6.0 (5.6-6.2) | 112.0 (112.0-112.0) | 0.000 (0.000-0.000) | 960 |
| Difference | | Rust 62% lower | Rust 51% lower | Rust 36% lower | no difference | no difference | Rust 500% higher |

### Memory, image size and cold start

| Build | RSS idle (MiB) | RSS under load (MiB) | image (MB) | cold start (ms) |
|---|---|---|---|---|
| Flask | 116.2 (116.2-116.3) | 120.1 (120.0-120.1) | 281.3 | 8568 (8439-8990) |
| Rust twin | 21.6 (21.6-21.6) | 61.8 (61.8-61.8) | 157.1 | 543 (419-854) |

RSS is the sum over every process in the container (`docker top`), idle after a 10 s settle following a start, and the peak of samples every 10 s during the measured phase. The range under load is over every measured run of the build (all scenarios, formats and repetitions). The cold start is the time from `docker start` to the first `200` on an authenticated `GET /v1/products/100001`, over 5 restarts.

## Method

- Calibration: a ramp of constant-arrival steps of 10 s (10, 20, 40, 60, 80, 120, 160, 240, 320, 480, 640, 960, 1280, 1920 req/s). A step is sustained when at most 1% of arrivals were dropped, at most 1% of requests and checks failed, and p99 was at most 1,000 ms. The saturation rate is the highest step of the unbroken sustained run from the bottom (`bench/README.md`). Each measured run offers 70% of the lower of the two builds' saturation rates for the scenario and format.
- Measured runs: 30 s warm-up then 2 min measured at a constant arrival rate, 5 repetitions. The builds alternate within each repetition, and Flask starts the odd repetitions and the twin the even ones. 80 runs in all.
- Scenarios: `list` (20 rows, pages 1 to 500), `search` (`q`), `filter` (`category_id` and `active`, 50 rows), `detail` (5% unknown ids, whose 404 is expected); each with `Accept: application/json` and `Accept: application/xml`, checking the status and the format.
- Fairness settings applied and read back by `bench/run.sh`: `CACHE_ENABLED`, `RATE_LIMIT_ENABLED`, `STREAM_CONSUMER_ENABLED` and `OUTBOX_RELAY_ENABLED` false for products-service (the twin has no cache and forces its limiter off); 0.5 CPU each; the builds pinned to CPU 1, PostgreSQL, Redis and auth to CPU 2, k6 to CPU 3; one database for both, and tokens minted the same way for both (an ANALYST token signed with the bench key, a fresh one for each k6 run); RS256 verification and the `EXISTS pub:auth:v1:live:{sid}` check on every request.

## Deviation: the memory limit was not enforced

ADR-0042 fixes `mem_limit` at 160 MB. This machine boots with `cgroup_disable=memory` (Raspberry Pi OS default), so Docker printed "Your kernel does not support memory limit capabilities" and discarded the limit for both containers. Both ran without a memory limit, which is symmetric, but it is not the specified configuration. Flask's 120 MiB resident set would have fitted the limit, and the twin's 62 MiB did too, so neither would have been constrained; the numbers above still do not prove that. `run.sh` accepts this only with `BENCH_ALLOW_NO_MEMLIMIT=1` and records `memory_limit=not-enforced` in `facts.txt`. The CPU limit (0.5) and the pinning were enforced and read back.

## Deviation: the saturation rate has a single value

#455 asks for every reported figure, the saturation rate included, as the median and the min-max range over the 5 repetitions. `run.sh` calibrates once per build, format and scenario, before the repetitions, because the measured rate is derived from that calibration. The saturation column therefore holds one value with no range, and its "Difference" compares two single numbers (see "Single calibration" under the threats to validity). A range would need the calibration repeated five times.

## Machine and versions

| Item | Value |
|---|---|
| Machine | Raspberry Pi 5, the developer machine; not the instance |
| CPU | Cortex-A76, 4 cores |
| Memory | 15 GiB |
| OS | Debian GNU/Linux 13 (trixie), kernel 6.18.50+rpt-rpi-2712, aarch64 |
| Docker | 29.8.1 |
| k6 | v2.3.0, `grafana/k6@sha256:e66db15b860113878fa74670e31f5e274830b7b6e42c8bff28b2f2d86a257603` |
| Commit | base `3e483e64cd31e75d6ce2379f1393e3981152d2e7` (develop) plus the #455 commit on `feature/455-products-bench` that adds `bench/run.sh` and the files it uses; `facts.txt` records `dirty=yes` because they were not yet committed when the run started |
| products-service | `ghcr.io/equipo-03-proyecto10/mosaiq-products:0.1.3`, built locally from `services/products` (the GHCR package is private), image `sha256:fc70c186f669b3954badbe8b115cc8a5dbf6d876ecc20b5cab84fea4559a86c7` |
| Rust twin | `mosaiq-products-service-rs:bench`, image `sha256:3b52dab5b0eec612e10c21606f55cac90a3ae8a1e7ad66c33dbcf457a1ecedd3` |
| auth-service | `mosaiq-auth:0.1.1`, built locally |
| Kit tags | `kit-py/v0.1.6` (products-service), `kit-rs/v0.1.0` (twin) |
| PostgreSQL | `postgres:18@sha256:edb0c08dc33befd0262ab3b5cf1c4f3f8cfd8b09e88c05b45adee29d6761ddb6`, 10,000 bench products |
| Redis | `redis:7.4.11-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` |

The full list of settings is in [`facts.txt`](../../bench/results/2026-10-22-pi5/facts.txt), and the calibration steps behind each saturation rate are printed by `aggregate.py report`.

## Conformance output (fairness rule 1)

Quoted from [`conformance.txt`](../../bench/results/2026-10-22-pi5/conformance.txt), printed by `bench/run.sh` before the first k6 run:

```
bench-keys: /home/pi/school/retail-segmentation/bench/keys/jwks.json holds kid bench
== conformance: products on 127.0.0.1:5003
................................................ssss                     [100%]
48 passed, 4 skipped in 12.96s
== conformance: products-service-rs on 127.0.0.1:5103
....................................................                     [100%]
52 passed in 5.65s
== identical answers
compare: 20 requests x 2 formats answered identically
```

## Threats to validity

- **Shared token path.** Both builds verify the same RS256 token and check the same Redis key on every request. That work is common to both and compresses the differences; a language difference can show only in what is left.
- **Shared Redis and PostgreSQL.** One PostgreSQL and one Redis, on a pinned CPU, serve both builds. `search` is dominated by the query, which is why its gap is small and its saturation is identical. The twin's advantage grows where there is less database work (`detail`, `filter`).
- **Production concurrency, not equal parallelism.** One gunicorn worker with four threads against two Tokio workers compares deployments, not runtimes at equal parallelism. Flask's GIL and the twin's second worker are part of the result.
- **Off-instance numbers.** An A76 core at the Pi's clock is not an e2-standard-2 vCPU, and PostgreSQL, Redis, k6 and the builds here do not share cores as they would there. The numbers compare the builds with each other and say nothing about the instance.
- **Memory limit not enforced** (above).
- **Single calibration.** The saturation rates have no repetition range, are quantised by the ramp's steps, and the criterion (1% drops, 1% errors, p99 at most 1 s) is a choice. The 70% load is therefore a 70% of an estimate.
- **Order and thermals.** Alternating the builds mitigates drift, but the Pi was not temperature-controlled and the host also runs other containers (unrelated to this project) which were idle during the run.
- **Load generator.** k6 had one core (CPU 3); calibration steps near the twin's top (up to 1,920 req/s) may be limited by k6 itself, which is why the highest steps show fewer requests than offered.
- **Small ranges.** With five repetitions the min-max range is a coarse measure of noise; "no difference" means the ranges overlap, not that a difference was ruled out.
- **Local images.** products-service and auth were built locally under their release tags because the GHCR packages are private; they are the repository's code at that commit, not necessarily byte-identical to the published images.
