# ADR-0042 — A bench-only Rust build of the products reads is compared with the Flask service under one load

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The team chose to build some services in Rust, and to compare Rust with Flask.
Those are two different goals, and this record keeps them apart.

**Some services in Rust** is met by production services.
[ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)
makes six of the fifteen services Rust-only: stores, stock,
customer-preferences, segments, check-ins and audit-trail. They have no Flask
counterpart, so none of them can be compared with Flask. Each does different
work from every Flask service, and a difference in their numbers would say
nothing about the languages.

**A comparison** needs both languages doing the same work: the same contract,
the same queries, the same data and the same limits. The earlier draft of this
record got that by making the Rust build a service of its own,
catalog-service-rs on port 5103. It was routed, deployed and counted toward
ADR-0016's six to ten. The instructor's requirements, recorded in
[ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md),
rule that out:

- **Never mix endpoints.** Two deployables answering `/v1/products` means two
  services exposing the same resource. The registry of
  [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)
  would have to register `products` twice, or carry an exception.
- **One business rule per service.** A second service implementing the product
  master is not a second rule. Counting it would be the filler the instructor
  excluded.

So the Rust build can exist only as a benchmark artifact. It is something the
team runs to measure, not something the platform runs.

Benchmarks are also easy to get wrong:

- **Cache.** With caching on, Redis answers and hides both builds.
- **Unequal resources.** Different memory or CPU limits measure the limits.
- **Shared bottlenecks.** Both builds verify the same token, ask the same Redis
  whether the session is live, and query the same PostgreSQL. Those costs are
  common to both and can dominate.
- **A busy host.** The e2-standard-2 instance has 2 vCPU and 8 GB, and runs the
  web, PostgreSQL and fifteen services.
- **Noise.** One run proves little.

What is uncertain is how much difference survives the shared path. The record
is built so that "no measurable difference" is a valid, reportable result.

## Decision

`bench/products-service-rs/` is an axum crate that implements the read
operations of `contracts/products/openapi.yaml`, at the same `info.version`:
`GET /v1/products` with `q`, `sku`, `category_id`, `active`, `page` and `size`,
and `GET /v1/products/{product_id}`. It returns the same JSON, the same XML
under `products.xsd`, the same status codes and the same error envelope. It
implements no write operation. It connects as `svc_products_bench`, which holds
SELECT on `public.product` and nothing else. It runs only under
`docker compose --profile bench`, on port 5103, off the instance. It is never
routed, never deployed, never registered and never counted as a service, so no
endpoint is ever served by two deployables. It is compared with products-service
under the fairness rules, scenarios and metrics below, and the result is a
report in `docs/evidence/`.

### Why it is a benchmark artifact and not a microservice

| Property | products-service | `bench/products-service-rs` |
|---|---|---|
| Directory | `services/products/` | `bench/products-service-rs/` |
| In `contracts/registry.yaml` | yes: `products` | no |
| NGINX `/api/products/` | yes, port 5003 | never. No include names port 5103 |
| Deployed by the per-service workflow ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)) | yes | no. The workflow enumerates `services/*/` only |
| Image | pushed to GHCR | built locally for a run, never pushed |
| PostgreSQL role | `svc_products` | `svc_products_bench`, SELECT on `public.product` only |
| Counted by [ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md) | yes | no |
| Writes | the product master's rule | none. Write methods answer 405 |

The crate follows the same code rules as a service
([ADR-0045](0045-services-never-import-the-web-system.md)): its own
`Cargo.lock`, no Cargo workspace, `kit-rs` pinned by exact tag, and no import
or copy of `web/`. It verifies tokens with the kit and signs none
([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).

### Where it runs

`bench/compose.yaml` puts every container in the `bench` profile, so a plain
`docker compose up` starts nothing. The profile runs:
- PostgreSQL, pinned to the instance's major version (18,
  [`docs/infra.md`](../infra.md)), built by the three scripts in order, plus
  `bench/fixtures/products-10k.sql`;
- `redis-state`, the same image as `deploy/infra/compose.yaml`;
- auth-service at its released tag, which signs the token the load uses;
- products-service at its released GHCR tag, on 5003;
- the twin, built from the commit under test, on 5103.

Every port binds 127.0.0.1. The fixture is a fixed
`INSERT ... SELECT ... FROM generate_series(...)` statement that adds 10,000
products, with deterministic SKUs, spread over the seeded categories. It is
applied only to the bench database. The runs happen on one developer machine,
never on the instance. The report records the machine's CPU, memory and
operating system.

### Fairness rules

1. **Conformance first.** `bench/run.sh` runs the shared black-box suite and
   the products read cases against both base URLs. It also checks that a fixed
   sample of requests returns equal parsed JSON, and XML that is equal after
   `xmllint --c14n`. If either build fails, nothing is measured.
2. **No cache, no rate limit.** Both run with `CACHE_ENABLED=false` and
   `RATE_LIMIT_ENABLED=false`. products-service does not start its consumer of
   `stream:products.v1.changed`, which exists only to clear its cache.
3. **Identical limits.** Both containers get products-service's production
   limits: `mem_limit` 160 MB and `cpus` 0.5. Each keeps its production
   concurrency: one gunicorn worker with four threads for Flask, two Tokio
   workers for Rust, and a pool of at most three connections for each. This is
   a judgement call. It compares what would be deployed, not a matched thread
   count, and the report says so.
4. **Same data, same path.** Both builds use the same database and the same
   token. Both run RS256 verification and `EXISTS pub:auth:v1:live:{sid}` on
   every request.
5. **Same order.** In each repetition the builds alternate, and the starting
   build alternates between repetitions, so drift on the machine spreads
   across both. k6 runs outside the CPUs the two containers are pinned to with
   `cpuset`.

### k6 scenarios

| Scenario | Request |
|---|---|
| list | `GET /v1/products?page=N&size=20`, `N` drawn over the fixture's pages |
| search | `GET /v1/products?q=T&size=20`, `T` drawn from a fixed term list |
| filter | `GET /v1/products?category_id=C&active=true&size=20`, `C` drawn from the seeded categories |
| detail | `GET /v1/products/{product_id}`, ids drawn from seed and fixture, plus 5% unknown ids (404) |

Each scenario runs with `Accept: application/json` and with
`Accept: application/xml`: eight scenarios per build. One calibration run per
build and format uses a `ramping-arrival-rate` to find the saturation rate,
where p95 passes 250 ms or errors pass 1%. Each measured run is a
`constant-arrival-rate` at 70% of the lower of the two saturation rates, so
both builds see the same offered load. It has 30 s of warm-up, which is
discarded, then 2 minutes measured. Each of the 16 combinations of build and
scenario is repeated 5 times. The scripts live in `bench/k6/`.

### Metrics and report

| Metric | Source |
|---|---|
| p50, p95, p99 latency | k6 `http_req_duration`, per scenario and format |
| Throughput (RPS) and saturation rate | k6 `http_reqs`; the calibration run |
| Error rate | k6 `http_req_failed` plus failed checks on status and `Content-Type` |
| RSS at idle and under load | `docker stats`, sampled every second; median and maximum |
| Image size | `docker image inspect` |
| Cold start | From `docker compose up` of the container to the first 200 on `/health/ready`, median of 5 |

`docs/evidence/products-reads-flask-vs-rust.md` reports the median and the
min–max range over the 5 repetitions for each cell. It keeps the raw
`--summary-export` files next to it, records the image digests and kit tags,
and ends with a threats-to-validity section. A difference inside the range of
the repetitions is reported as no difference. The report gives no single
"N× faster" figure.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Run the Rust build as a routed, counted service (the earlier draft's catalog-service-rs) | Two deployables would serve `/v1/products`, which "never mix endpoints" forbids. The registry would hold `products` twice. A second implementation of the same rule is not a second rule, so counting it toward ten is filler |
| Compare a Rust production service with a different Flask service, for example stores with products | Different work produces different numbers, whatever the language. The result could not be defended |
| Twin a Rust production service in Flask instead, for example stores | `store` has a handful of rows and one location each, so its reads measure almost nothing. products has the largest read table, the QR-detail hot path on the phone, and a contract with search and filters |
| Twin the writes too | It would make a second writer of `product`, which [ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) forbids, and port the product master's write rules twice. Writes are rarer, and PostgreSQL dominates their cost |
| Benchmark on the instance | It shares 2 vCPU and 8 GB with the web, PostgreSQL and fifteen services, and would perturb the demonstration. The memory budget of [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) has no room for it |
| Benchmark with caching on | Redis answers, and the comparison measures Redis |
| wrk or oha instead of k6 | Both are good load generators. k6 scripts the scenario mix, arrival rates, checks and JSON summaries in committed files the team can read |

## Consequences

**What this makes easy.**
- "Some services in Rust" holds whatever happens to the benchmark. If epic #401
  is cut, nothing in production changes.
- The comparison is reproducible from the repository, and fair by
  construction: same contract, same data, same limits, conformance first.
- No endpoint is served by two deployables, and the registry and service count
  need no exception.
- The conformance step doubles as a cross-check that `kit-rs` and `kit-py`
  behave the same on the negative cases.

**What this makes hard.**
- **A crate that nobody deploys still has to be kept.** It needs `cargo fmt`,
  clippy and `cargo test` like any Rust crate. A change to the products read
  contract must reach it before the next run. Its CI job runs on changes to
  `bench/` and `contracts/products/`, but it is not a required check for
  products-service. Making it one would let a benchmark block a service
  release, against the independence requirement. The cost is that the twin can
  be stale between runs, and `bench/run.sh` refuses to measure until it passes
  conformance again.
- **The shared path masks any language difference.** Token verification, the
  Redis round trip and the PostgreSQL query are the same work in both. The
  report must say so, and a small or zero difference is a legitimate result.
- **Production concurrency settings are not a matched experiment.** One worker
  with four threads against two Tokio workers compares deployments, not
  runtimes at equal parallelism. That is the question the team is asking, and
  it is listed as a threat to validity.
- **Off-instance numbers do not transfer.** A developer machine is not an
  e2-standard-2. The report compares the two builds with each other, never
  with the instance.
- **The bench database matches the instance's major version, but CI's does
  not.** `.github/workflows/ci.yml` and the development `compose.yaml` use
  `postgres:16`, while the instance runs PostgreSQL 18.6. The bench pins 18,
  so its query plans resemble the instance's.

**What must now be true elsewhere.**
- `sql/00_create_database.sql` creates `svc_products_bench` idempotently, and
  `sql/01_schema.sql` grants it SELECT on `public.product` only.
  [ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)'s
  one-role-per-table check maps it to the products boundary.
  `contracts/db/grants.csv` lists that one row.
- `contracts/products/openapi.yaml` marks its read operations, so the
  conformance cases that apply to the twin can be selected.
  `contracts/registry.yaml` has no entry for the twin
  ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
- [ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)
  counts services from the registry and `services/`, so the twin is never
  counted.
- [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md):
  the deploy workflow and the memory budget cover `services/*/` and
  `deploy/infra/` only. Neither mentions `bench/`.
- `.env.example` documents the bench role's password with a local default.
- Epic #401 carries the stories for the crate, the fixture, the k6 scripts and
  the report. [`docs/roadmap.md`](../roadmap.md) describes the twin as a
  benchmark artifact, not a service.

## Compliance

CI runs these from the repository root, and each must print nothing:

```bash
# Never registered, routed or deployed.
grep -rnE '5103|products-service-rs' contracts/registry.yaml services/*/nginx/ deploy/ \
  .github/workflows/deploy-service.yml

# Never pushed: no workflow tags or pushes the twin's image.
grep -rnE 'products-service-rs' .github/workflows/ | grep -iE 'ghcr|push'

# Every bench container is in the bench profile, and 5103 binds loopback only.
yq -r '.services | to_entries[] | select((.value.profiles // []) | index("bench") | not) | .key' bench/compose.yaml
yq -r '.services["products-service-rs"].ports[]' bench/compose.yaml | grep -vx '127.0.0.1:5103:5103'

# The twin serves reads only.
grep -rnE '\.(post|put|patch|delete)\(' bench/products-service-rs/src/

# Production Rust is exactly the six Rust-only services.
[ "$(ls services/*/Cargo.toml | cut -d/ -f2 | sort | tr '\n' ' ')" = \
  "audit-trail check-ins customer-preferences segments stock stores " ] \
  || echo "FAIL: the Rust production services are not the six of ADR-0034"
```

```bash
# The crate passes the Rust gates.
(cd bench/products-service-rs && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test)
```

```sql
-- svc_products_bench reaches exactly one relation, with SELECT only. Expected: one row, public.product.
SELECT c.oid::regclass AS relation
FROM pg_class c
WHERE c.relkind IN ('r', 'p', 'v', 'm')
  AND c.relnamespace NOT IN ('pg_catalog'::regnamespace, 'information_schema'::regnamespace)
  AND (has_table_privilege('svc_products_bench', c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
       OR has_any_column_privilege('svc_products_bench', c.oid, 'SELECT, INSERT, UPDATE'));
SELECT has_table_privilege('svc_products_bench', 'public.product', 'INSERT, UPDATE, DELETE, TRUNCATE');  -- false
```

A benchmark run is valid only if `bench/run.sh` printed a passing conformance
step for both base URLs before the first k6 run. The report quotes that output.
A reviewer of the report checks that it gives p50, p95 and p99, RPS, error
rate, RSS at idle and under load, image size and cold start, for both builds
and both formats, over 5 repetitions each.
