# ADR-0042 — Flask and Rust are compared on one contract under one load

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The team wants some services in Rust, "to compare" them with Flask. A
comparison is only worth reporting if the two sides do the same work. A Rust
inventory service and a Flask catalog service differ in queries, payloads and
cache behaviour, so any difference in their numbers says nothing about the
languages.

Three Rust services are planned
([ADR-0034](0034-eight-services-share-one-postgresql-each-the-sole-service-writer-of-its-tables.md)):

- inventory-service and segments-service, which exist only in Rust and are
  read-only;
- catalog-service-rs, a twin of the Flask catalog-service.

The twin is what makes a measured comparison possible.

Benchmarks on shared hardware are easy to get wrong:

- **Warm versus cold cache.** Redis can dominate the result and hide both
  languages.
- **Unequal concurrency.** Two Gunicorn workers against a multi-threaded Tokio
  runtime is not a like-for-like setup.
- **Shared bottlenecks.** The same PostgreSQL behind both services caps both.
- **A busy host.** The 8 GB instance also serves the demonstration.

## Decision

catalog-service-rs implements the catalog contract's **read** operations
(`GET /v1/products`, `/v1/products/{id}`, `/v1/categories`) in Rust (axum). The
responses are byte-equivalent: same JSON and XML, same status codes, same
errors. **The same contract test suite**, parameterized by base URL, must pass
against both implementations. The twin writes nothing, and answers 405 to write
methods.

The comparison is a k6 benchmark committed in `bench/catalog/`:

- **Request mix:** the same one against both services (list, detail and
  categories, in JSON and XML).
- **Resources:** both containers get the same `--cpus` and `--memory` limits on
  the same host. The documented concurrency settings are Gunicorn workers ×
  threads for Flask and Tokio worker threads for Rust, chosen to use the same
  CPU allotment.
- **Repetitions:** three runs with the cache flushed and three warm, reporting
  medians.
- **Report:** `docs/evidence/f15-02-flask-vs-rust.md` gives p50/p95/p99, RPS
  and error rate, RSS at idle and under load, image size and cold-start time,
  together with the raw k6 summaries and an explicit threats-to-validity
  section.
- **Where it runs:** never on the instance during the demonstration.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Compare different Rust and Flask services by their own metrics | Different work produces different numbers, whatever the language. The result could not be defended |
| Twin the whole catalog service, writes included | Two writers of `product` breaks ADR-0034's one-service-writer rule. The comparison does not need writes, which are rarer and dominated by PostgreSQL |
| Twin a write-heavy service such as sales | sales-service imports the Python ingestion rules (ADR-0039). A Rust twin would be a second implementation of those rules, which ADR-0020 forbids |
| Micro-benchmarks inside each language | They do not measure what the delivery is about: a service under HTTP load with authentication, Redis and PostgreSQL in the path |
| wrk or oha instead of k6 | Both are fine load generators. k6 scripts the request mix, thresholds and JSON summaries in one committed file, and the team can read its JavaScript |

## Consequences

**What this makes easy.**
- The comparison is reproducible from the repository, and fair by
  construction.
- The shared contract test suite also proves that the Rust kit (F13-04)
  matches the Python kit (F13-03) on every negative case.

**What this makes hard.**
- **Two implementations of the catalog reads must stay identical.** A contract
  change touches both, and CI runs the suite against both.
- **Any Rust advantage will be partly masked.** Authentication, the Redis
  lookup and PostgreSQL are shared. The report must say so rather than present
  a single "Rust is N× faster" figure.

**What must now be true elsewhere.**
- The catalog contract tags its read operations (F14-05).
- `svc_catalog_rs` exists with SELECT only (F13-07).
- The Redis user for the twin has read-only access to `cache:catalog:*`
  (ADR-0037).
- The twin counts as one service toward the six-to-ten limit.

## Compliance

```bash
# The same contract suite passes against both implementations.
CATALOG_BASE_URL=http://127.0.0.1:5003 pytest services/catalog-service/tests/contract
CATALOG_BASE_URL=http://127.0.0.1:5103 pytest services/catalog-service/tests/contract
# The twin writes nothing.
grep -rnE 'INSERT|UPDATE|DELETE' services/catalog-service-rs/src/ && echo "twin writes" || true
```
