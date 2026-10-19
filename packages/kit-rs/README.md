# MOSAIQ Rust kit

`mosaiq-kit` (this crate, released as `kit-rs`) contains the shared transport
and infrastructure mechanisms used by MOSAIQ's Rust services. It behaves like
the Python kit in `packages/kit-py/`: it verifies tokens, checks session
liveness, drives contract-based routes, negotiates JSON and XML, renders common
errors, manages pagination, health, rate limits, database attribution, outbox
relays and stream consumers.

It contains no business rules, role vocabulary or web-system code. This is the
mechanism-only exception E-21 governed by ADR-0045. The two kits agree on the
vectors in `packages/kit-vectors/`.

## Pinning a release

Each service pins the annotated tag as a git dependency and commits its own
`Cargo.lock`:

```toml
[dependencies]
mosaiq-kit = { git = "https://github.com/Equipo-03-Proyecto10/retail-segmentation", tag = "kit-rs/v0.1.0" }
```

Path dependencies on `packages/` are rejected, and the crate has no
`[workspace]` table, so it never shares a lock file with a service. A kit
upgrade is a service pull request with an exact new tag, a refreshed lock and
that service's own `VERSION` bump. The current release is `kit-rs/v0.1.0`,
matching this crate's version.

## Build requirement: libxml2

XML request bodies are validated against the service XSD by libxml2, the
engine lxml uses in kit-py, so both kits report the same `details`
(ADR-0049). Building needs `libxml2-dev` and `pkg-config`; the runtime image
needs `libxml2`. Before libxml2 sees a body, quick-xml has refused any DOCTYPE,
entity declaration, undeclared entity reference or NUL byte, and libxml2 then
parses with network access off and recovery off.

## Staged contracts

A service image stages its contract exactly as kit-py's do:

```text
contracts/
  <service>/openapi.yaml
  <service>/<service>.xsd
  common/v1/common.xsd
```

The kit reads the XML namespace from the service XSD and operation metadata
from the OpenAPI document. A request schema component with no `xml` object
uses its component name as the XML root and the service XSD's target
namespace. Explicit `xml.name` or `xml.namespace` values override those
defaults.

The verifier reads the mounted JWKS on first use, reloads it for a first
unknown key id, rate-limits later unknown-key reloads to one per 30 seconds,
and always honours `SIGHUP` for planned rotation. Verification uses
`jsonwebtoken` with `Validation::new(Algorithm::RS256)` only, checks `iss`,
`aud` = `mosaiq-api` and `exp` with no leeway, and a verified token is never
accepted without a live session in `redis-state`: `EXISTS
pub:auth:v1:live:{sid}` within 200 ms, with no retry.

## axum usage

```rust
use mosaiq_kit::{money, ApiError, Ctx, Kit, KitConfig, Reply, Representation, Value};

async fn get_product(ctx: Ctx) -> Result<Reply, ApiError> {
    let product_id = ctx.path_i64("product_id").unwrap_or_default();
    let connection = ctx.db().connection().await?;
    let row = connection
        .query_opt(
            "SELECT product_id, name, list_price FROM api_products.product_v1 \
             WHERE product_id = $1",
            &[&product_id],
        )
        .await?
        .ok_or_else(|| ApiError::new(404, "product_not_found", "The product does not exist."))?;
    Ok(Representation::new(
        "Product",
        [
            ("product_id", Value::from(row.try_get::<_, i64>(0)?)),
            ("name", Value::from(row.try_get::<_, String>(1)?)),
            ("list_price", money(row.try_get::<_, rust_decimal::Decimal>(2)?)),
        ],
    )
    .into())
}

let router = Kit::new("products", "products", "contracts", KitConfig::from_env("products"))?
    .operation("getProduct", get_product)?
    .ready_check(check_schema)
    .into_router();
axum::serve(listener, router.into_make_service_with_connect_info::<std::net::SocketAddr>()).await?;
```

`operation` registers the path and method from `operationId`, applies the
declared security and `x-mosaiq-roles`, and refuses, at startup, a protected
operation without roles or a request body without an XML root. A request is
handled in kit-py's order: path constraints (404), the 64 KiB limit (413), the
token (401, 503), the role (403), the rate limit (429), `Accept` (406), the
body (415, 400, 422), pagination (422), then the handler. The handler gets a
`Ctx` with the verified `principal`, the parsed `body` (exact numbers), `page`
and `size`, and typed path values. It returns `Representation`, `Collection`,
`Page` or `NoContent`, or an `ApiError` for service-owned errors. A
`tokio_postgres` or pool error converts with `?`: a connection that cannot be
had or is lost mid-request is 503 `database_unavailable`, never 500.

Use `connection.transaction(ctx.principal())` for a request write. It sets the
local audit actor with `set_config('mosaiq.user_id', $1, true)`. Plain
`connection` reads set nothing.

```rust
use mosaiq_kit::{OutboxRelay, StreamConsumer};

let relay = OutboxRelay::new(&database_url, suffix, produced_streams, relay_redis)?;
relay.spawn();

let consumer = StreamConsumer::new(consumer_redis, consumed_stream, handle_event)?;
consumer.spawn();
```

The relay accepts only the service's configured streams and never sets an
audit actor. The consumer always uses `MOSAIQ_SERVICE_NAME` as its group;
callers cannot select another group. Give each helper its own `redis-state`
connection (`service_redis_info` builds the `svc_<suffix>` credentials); a
blocking read holds the consumer's for up to 5 s. Both helpers expose
`run_once()` for deterministic tests.

`configure_logging` installs one stderr line format with the request's
correlation id, actor and client, and redacts bearer tokens, JWTs and
`password=` values.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_ISSUER` | `https://auth.invalid/` | Required token issuer |
| `AUTH_JWKS_PATH` | `/run/mosaiq/jwks.json` | Mounted public JWKS |
| `REDIS_STATE_URL` | `redis://redis-state:6379/0` | Session, limit and stream state |
| `REDIS_<SUFFIX>_PASSWORD` | none | Password for `svc_<suffix>` |
| `RATE_LIMIT_ENABLED` | `true` | Enables fixed-window limits |
| `RATE_LIMIT_REQUESTS` | `120` | Requests allowed per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length |
| `DATABASE_URL` | none | Service role connection URL |
| `DATABASE_POOL_TIMEOUT_SECONDS` | `2` | Maximum pool checkout wait |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `2` | PostgreSQL connection timeout |
| `MOSAIQ_SERVICE_NAME` | none | This service's consumer group |

## Example service

`examples/kit-example.rs` serves the kit's own test contract in
`examples/contracts/kit-example/`, with placeholder roles `ROLE_READER` and
`ROLE_WRITER`. F13-09's conformance suite runs against its image. CI stages the
common schema, then builds it from this directory:

```sh
cp -R ../../contracts/common/v1 examples/contracts/common/
docker build -f examples/Dockerfile -t mosaiq-kit-rs-example .
```

## Tests

The integration tests need the prepared PostgreSQL (the three scripts) and a
Redis 7.4 with AOF on. Without them, each test is skipped; with
`KIT_REQUIRE_SERVICES=1` it fails instead.

```sh
export KIT_TEST_REDIS_URL=redis://:<admin password>@127.0.0.1:6379/0
export KIT_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/retail
cd packages/kit-rs
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
```
