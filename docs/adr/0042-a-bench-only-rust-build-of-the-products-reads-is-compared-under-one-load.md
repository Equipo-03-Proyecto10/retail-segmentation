# ADR-0042 — A bench-only Rust build of the products reads is compared with the Flask service under one load

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The team chose to build some services in Rust, and to compare Rust with Flask. Those are two
different goals, and this record keeps them apart.

**Some services in Rust** is met by production services: [ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) makes six of the fifteen
Rust-only (stores, stock, customer-preferences, segments, check-ins and audit-trail). None has a
Flask counterpart doing the same work, so none can be compared with Flask.

**A comparison** needs both languages doing the same work: the same contract, queries, data and
limits. An earlier draft made the Rust build a routed service, catalog-service-rs on port 5103,
counted toward ADR-0016's six to ten. The instructor's requirements ([ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)) rule that out:
- **Never mix endpoints.** Two deployables answering `/v1/products` expose one resource twice, and
  the registry of [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) would register `products` twice.
- **One business rule per service.** A second implementation of the product master is not a second
  rule; counting it would be the filler the instructor excluded.

So the Rust build can exist only as a benchmark artifact: something the team runs to measure, not
something the platform runs. Benchmarks are also easy to get wrong:
- **Cache.** With caching on, Redis answers and hides both builds.
- **Unequal resources.** Different memory or CPU limits measure the limits.
- **Shared bottlenecks.** Token checks, Redis and PostgreSQL are common to both and can dominate.
- **A busy host.** The e2-standard-2 (2 vCPU, 8 GB) runs the web, PostgreSQL and fifteen services.
- **Noise.** One run proves little.

Uncertain: how much difference survives the shared path; "no measurable difference" is valid.

## Decision

`bench/products-service-rs/` is an axum crate that implements the read operations of
`contracts/products/openapi.yaml`, at the same `info.version`: `GET /v1/products` with `q`, `sku`,
`category_id`, `active`, `ids` (at most 50), `page` and `size`, and `GET /v1/products/{product_id}`.
It returns the same JSON, the same XML under `products.xsd`, the same status codes and the same
error envelope. It implements no write; write methods answer 405. It connects as
`svc_products_bench`, which holds SELECT on `public.product` and nothing else. It runs only under
`docker compose --profile bench`, on port 5103, off the instance. It is never routed, deployed,
registered or counted as a service, and its image is never pushed, so no endpoint is ever served by
two deployables. It is compared with products-service under the rules below.

The crate follows the same code rules as a service ([ADR-0045](0045-services-never-import-the-web-system.md)): its own `Cargo.lock`, no Cargo
workspace, `kit-rs` pinned by exact tag, and no import or copy of `web/`. It verifies tokens with
the kit and signs none ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).

**Where it runs.** Only in the `bench` profile of `bench/compose.yaml`: PostgreSQL 18, the
instance's major ([`docs/infra.md`](../infra.md)), built by the three scripts plus
`bench/fixtures/products-10k.sql` (10,000 deterministic products from `generate_series`, bench
database only); `redis-state`; auth-service and products-service at their released tags; and the
twin, built from the commit under test, on 5103. Every port binds loopback. The runs happen on one
developer machine, recorded in the report.

**Fairness rules.**
1. **Conformance first.** `bench/run.sh` runs the shared black-box suite and the products read cases
   against both base URLs, and checks a fixed sample for equal parsed JSON and
   `xmllint --c14n`-equal XML. If either build fails, nothing is measured.
2. **No cache, no rate limit.** Both run with `CACHE_ENABLED=false` and `RATE_LIMIT_ENABLED=false`,
   and products-service does not start its cache-clearing consumer of `stream:products.v1.changed`.
3. **Identical limits.** Both get products-service's production limits (`mem_limit` 160 MB, `cpus`
   0.5) and production concurrency: one gunicorn worker with four threads, two Tokio workers, at
   most three pooled connections each. This compares deployments, a judgement call.
4. **Same data, same path.** Both use the same database and token, and both run RS256 verification
   and `EXISTS pub:auth:v1:live:{sid}` on every request.
5. **Same order.** The builds alternate within each repetition and the starting build alternates
   between repetitions; k6 runs outside the CPUs the containers are pinned to with `cpuset`.

**Load and report.** k6 runs four scenarios (list, search, filter, and detail with 5% unknown ids),
each in JSON and XML. A ramping calibration finds each build's saturation rate; each measured run is
a constant arrival rate at 70% of the lower one, with 30 s of warm-up then 2 minutes measured,
repeated 5 times with the builds alternating. The report,
`docs/evidence/products-reads-flask-vs-rust.md`, gives p50/p95/p99, RPS and saturation rate, error
rate, RSS at idle and under load, image size and cold start, as the median and min–max range over
the repetitions. It keeps the raw summaries, records the image digests and kit tags, and ends with
threats to validity; a difference inside the repetitions' range is reported as none. The scripts and
the full plan live in `bench/k6/` and `bench/README.md` (epic #401).

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
- "Some services in Rust" holds whatever happens to the benchmark, even if epic #401 is cut.
- The comparison is reproducible and fair by construction; the registry and count need no exception.
- The conformance step also checks that `kit-rs` and `kit-py` agree on the negative cases.

**What this makes hard.**
- **A crate that nobody deploys still has to be kept.** It needs `cargo fmt`, clippy and
  `cargo test`, and must follow every change to the products read contract. Its CI job runs on
  changes to `bench/` and `contracts/products/` but is not a required check for products-service: a
  benchmark must not block a release. The twin can go stale between runs; `bench/run.sh` measures
  only once it conforms.
- **The shared path masks any language difference.** Token verification, the Redis round trip and
  the PostgreSQL query are the same work in both. A small or zero difference is a legitimate result.
- **Production concurrency is not a matched experiment.** One worker with four threads against two
  Tokio workers compares deployments, not runtimes at equal parallelism. That is the team's
  question, and it is listed as a threat to validity.
- **Off-instance numbers do not transfer.** A developer machine is not an e2-standard-2; the report
  compares the builds with each other, never with the instance.
- **The bench matches the instance's PostgreSQL major, but CI does not.** `.github/workflows/ci.yml`
  and the development `compose.yaml` use `postgres:16`, while the instance runs 18.6; the bench pins
  18, so its query plans resemble the instance's.

**What must now be true elsewhere.**
- `sql/00_create_database.sql` creates `svc_products_bench` idempotently, and `sql/01_schema.sql`
  grants it SELECT on `public.product` only. ADR-0034's one-role-per-table check maps it to the
  products boundary, and `contracts/db/grants.csv` lists that one row.
- `contracts/products/openapi.yaml` marks its read operations, `ids` included, so the conformance
  cases for the twin can be selected. The registry has no entry for it (ADR-0035).
- ADR-0043 counts services from the registry and `services/`; ADR-0040's deploy workflow and memory
  budget cover `services/*/` and `deploy/infra/` only.
- `.env.example` documents the bench role's password with a local default. Epic #401 carries the
  stories for the crate, the fixture, the k6 scripts and the report, and [`docs/roadmap.md`](../roadmap.md)
  describes the twin as a benchmark artifact, not a service.

## Compliance

CI runs these from the repository root. Each check must print nothing, and the Rust gates must pass:

```bash
# Never registered, routed or deployed, and never pushed.
grep -rnE '5103|products-service-rs' contracts/registry.yaml services/*/nginx/ deploy/ .github/workflows/deploy-service.yml
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

A run is valid only if `bench/run.sh` printed a passing conformance step for both base URLs before
the first k6 run; the report quotes that output. A reviewer checks that the report gives every
metric above for both builds and both formats, over 5 repetitions.
