# kit-rs — `mosaiq-kit` for Rust services

The mechanisms every MOSAIQ Rust service shares, with the behaviour of the
Python kit (`packages/kit-py`), on axum. Mechanism only (exception E-21,
ADR-0045): no business rule, no role code, no Redis key but the session
liveness prefix and the stream prefix.

Parity with kit-py is the contract. Bodies are byte-identical where kit-py's
are deterministic: JSON as `json.dumps(..., separators=(",", ":"))` writes it
(ASCII-escaped, floats as Python's `repr`), XML as lxml's `tostring()`
writes it. `Accept` is negotiated with werkzeug's rules. Each behaviour has a
test that pins kit-py's answer.

## Pinning a release

Each Rust service pins an annotated tag as a git dependency and commits its
own `Cargo.lock`:

```toml
[dependencies]
mosaiq-kit = { git = "https://github.com/Equipo-03-Proyecto10/retail-segmentation", tag = "kit-rs/v0.1.0" }
```

Path dependencies on `packages/` are rejected (ADR-0045). A kit upgrade is a
service pull request with an exact new tag, a refreshed lock and that
service's own `VERSION` bump. The crate is not a workspace member and has no
`[workspace]` table; the toolchain is pinned in `rust-toolchain.toml`.

## Staged contracts

A service image stages its contract exactly as for kit-py:

```text
contracts/
  <service>/openapi.yaml
  <service>/<service>.xsd
  <service>/<service>.v<N>.xsd   (frozen versions, optional)
  common/v1/common.xsd
```

The XML namespace of responses is the service XSD's `targetNamespace`.

## Usage

```rust
use mosaiq_kit::{Kit, configure_logging};

configure_logging("INFO");
let kit = Kit::builder("stores", "stores")
    .contracts_dir("contracts")
    .ready_check(|| async { /* is my schema section applied? */ true })
    .build()?;
let app = kit.routes().into_router();
```

Handlers receive a `Context` (correlation id, negotiated format, path
parameters, the body, validated `page` and `size`) and return a `Reply`:
`Representation`, `Collection`, `Page` or `Reply::NoContent`. They return
`ApiError` for service-owned errors. Use `money()` for amounts: a bare
`Decimal` is a JSON number, as in kit-py.

Every operation runs kit-py's `_dispatch` order: the 64 KiB cap (413), then
negotiation (406), the request media type (415) and pagination (422
`invalid_pagination`), then the handler. An error raised before negotiation
uses the format the client prefers; a later one uses the negotiated format.

## Meta routes

Public, as in kit-py: `GET /health/live` (204), `GET /health/ready` (204, or
503 `schema_not_ready` when a check fails or panics), `GET /openapi.yaml`,
`GET /schema.xsd` and `GET /schema.v<N>.xsd`, each byte-exact.

## Logging

`configure_logging()` installs one stderr subscriber. Every line carries
`correlation=… actor=… client=…` and is redacted after formatting: no bearer
token, JWT or `password=` value reaches a log line.

## Tests

```sh
cd packages/kit-rs
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
```

The tests stage `packages/kit-py/example/contracts/kit-example` with
`contracts/common/v1`, as kit-py's do.
