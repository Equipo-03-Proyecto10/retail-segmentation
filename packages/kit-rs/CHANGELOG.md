# Changelog

## 0.1.1 - 2026-10-20

- Refuse a request-body number that neither a 64-bit integer nor a finite
  `f64` holds, such as `1e400`, `-1e400`, `1e309` or a 400-digit integer, with
  422 `invalid_body` and an `out_of_range` detail at its path. jsonschema 0.30
  panicked comparing such a number against `minimum` or `maximum`, and the
  connection dropped (#583).

## 0.1.0 - 2026-10-18

- Add contract-driven axum routing and JSON/XML handling, with XML validated
  against the service XSD by libxml2 after a quick-xml safety screen.
- Add RS256 verification, fail-closed liveness and fixed-window limits.
- Add the PostgreSQL pool, audit attribution, outbox relay and stream consumer
  helpers.
- Add the example service and its multi-stage image, which runs as UID 10001.
