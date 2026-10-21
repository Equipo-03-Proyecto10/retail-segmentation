# Changelog

## 0.1.2 - 2026-10-21

Version 0.1.1 is the number-panic fix (#583); this release builds on it.

- `OutboxRelay::run_with_reconnect` and `StreamConsumer::run_with_reconnect`
  rebuild the Redis connection after an outage, with backoff from 1 s doubling
  to 30 s, one warning when the outage starts and one info line when delivery
  resumes. A failed batch is rolled back and delivered again, so delivery stays
  at least once. `run_forever` logs once per outage and backs off too, but
  cannot rebuild the connection it was given (#579).
- Read an XML request member that is required and nullable in the OpenAPI, and
  omitted from the body, as `null` (#571).
- Read `xsi:nil="true"` on an XML request element as `null` (#585).
- `/openapi.yaml` and `/schema.xsd` answer 406 `not_acceptable` when `Accept`
  excludes the document's type; no `Accept`, `*/*` and type wildcards still get
  200 (#589).

## 0.1.0 - 2026-10-18

- Add contract-driven axum routing and JSON/XML handling, with XML validated
  against the service XSD by libxml2 after a quick-xml safety screen.
- Add RS256 verification, fail-closed liveness and fixed-window limits.
- Add the PostgreSQL pool, audit attribution, outbox relay and stream consumer
  helpers.
- Add the example service and its multi-stage image, which runs as UID 10001.
