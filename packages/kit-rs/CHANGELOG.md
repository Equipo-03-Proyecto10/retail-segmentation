# Changelog

## 0.1.0 - 2026-10-18

- Add contract-driven axum routing and JSON/XML handling, with XML validated
  against the service XSD by libxml2 after a quick-xml safety screen.
- Add RS256 verification, fail-closed liveness and fixed-window limits.
- Add the PostgreSQL pool, audit attribution, outbox relay and stream consumer
  helpers.
- Add the example service and its multi-stage image, which runs as UID 10001.
