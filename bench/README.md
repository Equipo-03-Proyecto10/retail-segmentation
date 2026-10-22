# The products reads bench

[ADR-0042](../docs/adr/0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md) compares Flask and Rust on identical work. `products-service-rs/` is a Rust build of products-service's two read operations, `GET /v1/products` and `GET /v1/products/{product_id}`. It runs behind exactly `contracts/products/` and answers with the same JSON, XML, status codes and error envelope.

**It is a benchmark artifact, not a service** (accepted exception E-22):
- It is never registered in `contracts/registry.yaml`, never routed by NGINX, never deployed, and never counted toward ADR-0043's services.
- Its image is never pushed.
- It implements no write. The contract's two writes go through the kit's usual checks (token, role, media type, size, body) and then answer 405; any other method is the router's 405.
- It connects as `svc_products_bench`, which may read `public.product` and nothing else.
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
| `run.sh` | The whole benchmark: conformance, fairness checks, cold start, calibration, measured runs |
| `aggregate.py` | Turns the raw k6 JSON summaries into the report's tables |
| `results/` | The raw k6 JSON summaries of the reported run, committed |

The report is [`docs/evidence/products-reads-flask-vs-rust.md`](../docs/evidence/products-reads-flask-vs-rust.md).

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

It brings the `bench` profile up and runs the suite against both builds, then compares 20 requests in each format. It stops at the first difference and leaves the stack running for the load runs. Afterwards:

```bash
docker compose -f bench/compose.yaml --profile bench down -v
```

## Run the benchmark

The machine must be a developer machine, never the instance. It needs Docker, `xmllint` (`libxml2-utils`) and the repository's `.venv` (the conformance suite needs pytest). k6 runs as `grafana/k6` in Docker, pinned by digest to the reported run's image, k6 v2.3.0 (`K6_IMAGE` overrides it). Four CPUs are assumed: CPU 0 is left to the system, the two builds share CPU 1 (only one is ever under load), PostgreSQL, Redis and auth share CPU 2, and k6 has CPU 3 (`BENCH_APP_CPUS`, `BENCH_BACKING_CPUS`, `BENCH_K6_CPUS`).

```bash
source .venv/bin/activate
bash bench/run.sh                                  # 5 repetitions, 30 s warm-up, 2 min measured
python3 bench/aggregate.py report bench/results/<run>   # the report's tables
```

`run.sh` does, in order, and stops at the first failure:
1. **Conformance** (`conformance.sh`) against both base URLs, printing its output. If either build fails, nothing is measured.
2. **Fairness settings**, applied by the script (`compose.pinned.yaml` and `compose.yaml`) and read back from the running containers: `mem_limit` 160 MB, `cpus` 0.5, `cpuset`, and `CACHE_ENABLED`, `RATE_LIMIT_ENABLED`, `STREAM_CONSUMER_ENABLED`, `OUTBOX_RELAY_ENABLED` all `false`. The image runs one gunicorn worker with four threads; the twin has two Tokio workers and kit-rs's three pooled connections.
3. **Cold start** (5 restarts per build: container start to the first `200`) and idle RSS.
4. **Calibration**, per build, format and scenario.
5. **Measured runs**: `BENCH_REPS` repetitions; within a repetition each scenario and format runs both builds, and the starting build alternates between repetitions (Flask first in odd ones).

`run.sh` stops if the containers do not report the 160 MB memory limit. A kernel without the memory cgroup (Raspberry Pi OS by default) discards it; `BENCH_ALLOW_NO_MEMLIMIT=1` accepts that, and the run records `memory_limit=not-enforced`.

Everything is overridable by environment variable (`BENCH_REPS`, `BENCH_WARMUP_SECONDS`, `BENCH_MEASURE_SECONDS`, `BENCH_RATE_FRACTION`, `BENCH_CAL_RATES`, `BENCH_CAL_STEP_SECONDS`, `BENCH_SCENARIOS`, `BENCH_FORMATS`, `BENCH_OUT`; see the head of `run.sh`). A short smoke run: `BENCH_REPS=1 BENCH_WARMUP_SECONDS=5 BENCH_MEASURE_SECONDS=20 BENCH_CAL_STEP_SECONDS=5 BENCH_OUT=bench/results/smoke bash bench/run.sh`. Running again with the same `BENCH_OUT` resumes, skipping what has a summary; it refuses to resume with other repetition, timing or calibration settings, which `aggregate.py` reads back from `facts.txt`. A summary is kept only when k6 finished and the build was still running afterwards, and a build that does not answer within `BENCH_COLDSTART_TIMEOUT_SECONDS` (120) of a restart stops the run.

### The scenarios

Each of `k6/list.js`, `search.js`, `filter.js` and `detail.js` runs once with `Accept: application/json` and once with `Accept: application/xml` (`FORMAT`), and checks the status and the format (`Content-Type` and the body's first character). `list` pages through the 10,000 products 20 at a time; `search` sends `q`; `filter` sends `category_id` and `active`, 50 rows; `detail` reads one id, 5% of them unknown, whose `404` counts as expected.

### The saturation criterion

Calibration offers a ramp of constant arrival rates (`BENCH_CAL_RATES`, 10 s each), one k6 scenario per step so each step is summarised alone. A step is **sustained** when, over that step:
- at most 1% of its arrivals were dropped (k6 had no free virtual user, so the build was not keeping up);
- at most 1% of requests failed and at most 1% of checks failed;
- p99 latency was at most 1,000 ms.

The **saturation rate** is the highest rate of the unbroken run of sustained steps from the lowest step up. Each measured run is a constant arrival rate at 70% of the lower of the two builds' saturation rates for that scenario and format, so both builds are offered the same work and neither is driven into its overload. Calibration runs once per build, format and scenario, so the saturation rate has no range. The result is only as fine as the steps.

### Reading the tables

Every cell is the median of the repetitions with the min–max range in parentheses. A difference is reported only when the two builds' ranges do not overlap; ranges that overlap are reported as "no difference", which is a valid result (ADR-0042).

## The twin's own checks

```bash
cd bench/products-service-rs
cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```

The database tests need `TEST_DATABASE_URL`, the owner's URL to a database built by the three scripts; they connect as `svc_products_bench`. `.github/workflows/bench.yml` runs these, the shared conformance suite against the twin's image, and ADR-0042's compliance greps on every pull request that touches `bench/` or `contracts/products/`. It is not a required check, and it pushes nothing.
