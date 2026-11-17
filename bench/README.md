# The products reads bench

[ADR-0042](../docs/adr/0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md) compares Flask and Rust on identical work. `products-service-rs/` is a Rust build of products-service's read operations: `GET /v1/products`, `GET /v1/products/{product_id}`, and, since [ADR-0078](../docs/adr/0078-the-bench-twin-serves-every-read-of-the-products-contract-and-the-load-comparison-stays-as-it-was.md), `GET /v1/products/{product_id}/details` and `GET /v1/products/{product_id}/relations`. It runs behind exactly `contracts/products/` and answers with the same JSON, XML, status codes and error envelope. The load comparison is unchanged: its four scenarios (list, search, filter, detail) use the first two reads; the other two are served so that both builds pass the same conformance suite, and are not benchmarked.

**It is a benchmark artifact, not a service** (accepted exception E-22):
- It is never registered in `contracts/registry.yaml`, never routed by NGINX, never deployed, and never counted toward ADR-0043's services.
- Its image is never pushed.
- It implements no write. The contract's four writes (`createProduct`, `updateProduct`, `updateProductDetails`, `replaceProductRelations`) go through the kit's usual checks (token, role, media type, size, body) and then answer 405; any other method is the router's 405.
- It connects as `svc_products_bench`, which may read `public.product` and the products boundary's four tables (`products.product_status`, `products.product_attribute`, `products.product_barcode`, `products.product_relation`) and nothing else. `PRODUCTS_PHOTO_BASE_URL` has the same value in `env/products-bench.env` and `env/products.env`, so both builds answer the same `photo_url`.
- Its Redis user (`redis/products-bench.acl`) may only `EXISTS` a session's liveness key.
- Like products-service in the bench, it has no cache and no rate limiter. Like every service, it verifies the RS256 token and the session's liveness on every request, and signs nothing.

## What is here

| Path | What it is |
|---|---|
| `products-service-rs/` | The axum crate on kit-rs `v0.1.0`: its own `Cargo.lock`, no workspace, a Dockerfile whose context is its own directory |
| `redis/products-bench.acl` | `svc_products_bench`'s one ACL line: the liveness selector only |
| `fixtures/products-10k.sql` | 10,000 deterministic products from `generate_series`, loaded after the three scripts, bench database only |
| `compose.yaml` | PostgreSQL 18 (the instance's major), redis-state on the infra's pinned image, auth and products at their GHCR tags, and the twin on `127.0.0.1:5103`, all in profile `bench` |
| `env/` | The bench's local, throwaway configuration. Caches, rate limits and stream workers are off, per ADR-0042's fairness rules |
| `keys.sh` | A throwaway signing key for the bench, written to `bench/keys/` (git-ignored) |
| `conformance.sh` | Fairness rule 1: both builds pass the shared conformance suite and the products read cases (`tests/`), then answer a fixed sample identically (`compare.py`) |
| `compose.pinned.yaml` | What `run.sh` layers over `compose.yaml`: `cpuset` pinning and the products flags |
| `k6/` | `list.js`, `search.js`, `filter.js`, `detail.js` and their shared `lib.js` |
| `mint_token.py` | Mints the one-hour access token for a k6 run, as the conformance suite does |
| `run.sh` | The whole benchmark: conformance, fairness checks, cold start, calibration passes, measured runs with CPU, RSS and PSS |
| `aggregate.py` | Turns the raw k6 JSON summaries and samples into the report's tables |
| `results/` | The raw k6 JSON summaries, samples and facts of the reported runs, committed: `2026-10-22-pi5` (F15-02) and `2026-11-*-gha-x86` (F15-03) |

The reports are [`docs/evidence/products-reads-flask-vs-rust.md`](../docs/evidence/products-reads-flask-vs-rust.md) (F15-02, a Raspberry Pi 5 with the memory limit not enforced, one calibration, RSS only) and [`docs/evidence/products-reads-flask-vs-rust-x86.md`](../docs/evidence/products-reads-flask-vs-rust-x86.md) (F15-03, the limit enforced, CPU and PSS, five calibration passes).

## Run it

auth and products run at their released GHCR tags (`AUTH_TAG`, `PRODUCTS_TAG`). `conformance.sh` on its own defaults to 0.1.0; `run.sh` sets both from `services/auth/VERSION` and `services/products/VERSION`. Those packages are private: sign in with `docker login ghcr.io`, or build each one locally under the tag it will run as:

```bash
export AUTH_TAG=$(cat services/auth/VERSION) PRODUCTS_TAG=$(cat services/products/VERSION)
.github/scripts/stage-contracts.sh products
docker build -t ghcr.io/equipo-03-proyecto10/mosaiq-products:"$PRODUCTS_TAG" services/products
```

Do the same for `auth`, under `"$AUTH_TAG"`. Then, from the repository root:

```bash
bash bench/conformance.sh
```

It brings the `bench` profile up and runs the suite against both builds, then compares 30 requests in each format (the four reads, with unknown ids and refusals). It stops at the first difference and leaves the stack running for the load runs. Afterwards:

```bash
docker compose -f bench/compose.yaml --profile bench down -v
```

## Run the benchmark

The machine must be a developer machine or a CI runner, never the instance. It needs Linux with Docker and **cgroup v2** (CPU use and the limits are read from each container's cgroup), `xmllint` (`libxml2-utils`), the repository's `.venv` (the conformance suite needs pytest), and root for PSS (below). k6 runs as `grafana/k6` in Docker, pinned by digest to the reported run's image, k6 v2.3.0 (`K6_IMAGE` overrides it). Four CPUs are assumed: CPU 0 is left to the system, the two builds share CPU 1 (only one is ever under load), PostgreSQL, Redis and auth share CPU 2, and k6 has CPU 3 (`BENCH_APP_CPUS`, `BENCH_BACKING_CPUS`, `BENCH_K6_CPUS`).

```bash
source .venv/bin/activate
bash bench/run.sh                                  # 5 repetitions, 30 s warm-up, 2 min measured, 5 calibration passes
python3 bench/aggregate.py report bench/results/<run>   # the report's tables
```

`run.sh` does, in order, and stops at the first failure:
1. **Conformance** (`conformance.sh`) against both base URLs, printing its output. If either build fails, nothing is measured.
2. **Fairness settings**, applied by the script (`compose.pinned.yaml` and `compose.yaml`) and read back from the running containers **and from their cgroups**: `mem_limit` 160 MB (`memory.max` must read 167772160 for both builds), `cpus` 0.5 (`cpu.max` 50000 100000), `cpuset` (the init process's `Cpus_allowed_list`), and `CACHE_ENABLED`, `RATE_LIMIT_ENABLED`, `STREAM_CONSUMER_ENABLED`, `OUTBOX_RELAY_ENABLED` all `false`. The image runs one gunicorn worker with four threads; the twin has two Tokio workers and kit-rs's three pooled connections. The read-back goes to `facts.txt`.
3. **Cold start** (`BENCH_COLDSTART_RUNS` restarts per build, container start to the first `200`) and idle RSS and PSS.
4. **Calibration**, per build, format and scenario, repeated `BENCH_CAL_PASSES` times (5); the builds alternate between passes.
5. **Measured runs**: `BENCH_REPS` repetitions; within a repetition each scenario and format runs both builds, and the starting build alternates between repetitions (Flask first in odd ones). Beside each run, a sampler records the container's CPU use, RSS and PSS (below).
6. **cgroup read-back after the run**, into `cgroup-end.txt`: `memory.max`, `memory.peak`, `memory.events` (`max` and `oom_kill` count the times the limit was reached and the kills), `memory.swap.current`.

`run.sh` stops if the containers do not report the 160 MB memory limit in both Docker's configuration and their cgroup. A kernel without the memory cgroup (Raspberry Pi OS by default) discards it; `BENCH_ALLOW_NO_MEMLIMIT=1` accepts that, and the run records `memory_limit=not-enforced`. A comparison that is meant to hold ADR-0042's limit never sets it.

Everything is overridable by environment variable (`BENCH_REPS`, `BENCH_WARMUP_SECONDS`, `BENCH_MEASURE_SECONDS`, `BENCH_RATE_FRACTION`, `BENCH_CAL_RATES`, `BENCH_CAL_STEP_SECONDS`, `BENCH_CAL_PASSES`, `BENCH_COLDSTART_RUNS`, `BENCH_SAMPLE_INTERVAL_SECONDS`, `BENCH_SAMPLE_MARGIN_SECONDS`, `BENCH_SETTLE_SECONDS`, `BENCH_SCENARIOS`, `BENCH_FORMATS`, `BENCH_OUT`, `BENCH_PRIV`, `BENCH_NO_BUILD`; see the head of `run.sh`). A short smoke run: `BENCH_REPS=1 BENCH_CAL_PASSES=1 BENCH_COLDSTART_RUNS=1 BENCH_WARMUP_SECONDS=5 BENCH_MEASURE_SECONDS=20 BENCH_CAL_STEP_SECONDS=5 BENCH_OUT=bench/results/smoke bash bench/run.sh`. Running again with the same `BENCH_OUT` resumes, skipping what has a summary; it refuses to resume with other repetition, timing, pass or calibration settings, which `aggregate.py` reads back from `facts.txt`. A summary is kept only when k6 finished and the build was still running afterwards, and a build that does not answer within `BENCH_COLDSTART_TIMEOUT_SECONDS` (120) of a restart stops the run.

### What is measured, and how

| Measure | Source | Note |
|---|---|---|
| Latency percentiles, throughput, errors | k6's summary of the measured phase | The same arrival rate for both builds |
| CPU use | The container's cgroup `cpu.stat` (`usage_usec`, user and system), read at the two edges of a window inside the measured phase | Exact over the window, kernel time included, and the same counter that carries `nr_throttled` and `throttled_usec`, which say how often the 0.5 CPU quota held the build back. Reported as a percentage of one CPU (so at most 50), per request, and as throttled periods. `docker stats` was not used: it is a sampled rate with a window of its own, relative to all the host's CPUs, and could not be aligned to the measured phase |
| RSS | `docker top` (`rss`), summed over the container's processes | Counts a page shared between two processes twice |
| PSS | `/proc/<pid>/smaps_rollup` (`Pss`), summed over the same processes | Splits a shared page between the processes that map it, so the gunicorn master and its worker do not count the interpreter twice. Reading another user's process needs root: `BENCH_PRIV` is `sudo -n` unless the script runs as root (set it empty if you are root, or to another command) |
| Memory limit | `memory.max` of the container's cgroup, and Docker's `HostConfig.Memory` | Both must read 160 MB |

RSS and PSS are sampled every `BENCH_SAMPLE_INTERVAL_SECONDS` inside the window, and the run keeps the peak of each. The window opens `BENCH_SAMPLE_MARGIN_SECONDS` after the warm-up ends and closes that long before the phase does: the sampler starts with `docker run`, k6 a moment later, and the margin keeps the window inside the measured phase whatever that lag was.

### The scenarios

Each of `k6/list.js`, `search.js`, `filter.js` and `detail.js` runs once with `Accept: application/json` and once with `Accept: application/xml` (`FORMAT`), and checks the status and the format (`Content-Type` and the body's first character). `list` pages through the 10,000 products 20 at a time; `search` sends `q`; `filter` sends `category_id` and `active`, 50 rows; `detail` reads one id, 5% of them unknown, whose `404` counts as expected.

### The saturation criterion

Calibration offers a ramp of constant arrival rates (`BENCH_CAL_RATES`, `BENCH_CAL_STEP_SECONDS` each), one k6 scenario per step so each step is summarised alone. A step is **sustained** when, over that step:
- at most 1% of its arrivals were dropped (k6 had no free virtual user, so the build was not keeping up);
- at most 1% of requests failed and at most 1% of checks failed;
- p99 latency was at most 1,000 ms.

A pass's **saturation rate** is the highest rate of the unbroken run of sustained steps from the lowest step up. The ramp runs `BENCH_CAL_PASSES` times (five) per build, format and scenario, and the saturation column is the **median of the passes with their range**. Each measured run is a constant arrival rate at 70% of the lower of the two builds' median saturation rates for that scenario and format, so both builds are offered the same work and neither is driven into its overload. The result is only as fine as the steps.

### Reading the tables

Every cell is the median of the repetitions (the saturation column, of the calibration passes) with the min-max range in parentheses. A difference is reported only when the two builds' ranges do not overlap; ranges that overlap are reported as "no difference", which is a valid result (ADR-0042). Each cell has two tables: latency, throughput, errors and saturation; then CPU, throttling, RSS and PSS under load.

A results directory holds one run, or one run per subdirectory (the CI job below writes one per scenario and format), and `aggregate.py report` reads either. A run holds `facts.txt`, `conformance.txt`, `rates.txt`, `cgroup-end.txt`, `calibration/cal<k>-<build>-<format>-<scenario>.json`, `runs/rep<n>-<build>-<format>-<scenario>.json` with its `.sample`, and `coldstart/`. A run from before F15-03 (`results/2026-10-22-pi5`) has one calibration, `.rss` files and no CPU or PSS, and still reports.

## The full comparison in CI

The reported run of F15-03 (#751) is `.github/workflows/bench.yml`, on a GitHub-hosted `ubuntu-24.04` runner: x86-64, 4 vCPU, cgroup v2 with the memory controller, so ADR-0042's 160 MB is enforced and read back. This WSL development machine has no Docker, and the Raspberry Pi of F15-02 boots without the memory cgroup.
- `images` builds auth, products and the twin from the commit under test, once, and every shard loads those images.
- `bench` is a matrix, one shard per scenario and format, each on its own runner with both builds in alternating order, because five calibration passes and five repetitions do not fit one runner in an hour. A shard runs `bench/run.sh` with 15 s of warm-up, 60 s measured and 5 s calibration steps (ADR-0042's 30 s, 2 min and 10 s would take hours; the report says so), `BENCH_NO_BUILD=1`, and never `BENCH_ALLOW_NO_MEMLIMIT`. It fails if the limit is not enforced.
- `report` gathers every shard's raw results and `aggregate.py`'s tables into one artifact, `bench-results`, and the job summary.

It takes about 40 minutes, so it is not run for every bench pull request: it runs on `workflow_dispatch` (Actions > Bench > Run workflow, with the settings as inputs, once the workflow is on the default branch), and on a pull request whose head branch name contains `bench-rerun`. A later push to such a branch that changes only `bench/results/`, `docs/` or the README does not repeat it. It is never a required check and never blocks a release. Download the results with `gh run download <run-id> -n bench-results`, and commit them under `bench/results/<date>-gha-x86/`.

## The twin's own checks

```bash
cd bench/products-service-rs
cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```

The database tests need `TEST_DATABASE_URL`, the owner's URL to a database built by the three scripts; they connect as `svc_products_bench`. `.github/workflows/bench.yml` runs these, the shared conformance suite against the twin's image, and ADR-0042's compliance greps on every pull request that touches `bench/` or `contracts/products/`. It is not a required check, and it pushes nothing.
