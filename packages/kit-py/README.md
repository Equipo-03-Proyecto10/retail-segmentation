# MOSAIQ Python kit

`mosaiq-kit` contains the shared transport and infrastructure mechanisms used
by MOSAIQ Flask services. It verifies tokens, checks session liveness, drives
contract-based routes, negotiates JSON and XML, renders common errors, manages
pagination, health, rate limits, database attribution, outbox relays and stream
consumers.

It contains no business rules, role vocabulary or web-system code. This is the
mechanism-only exception E-21 governed by ADR-0045.

## Pinning a release

Each service pins the annotated kit tag as a git dependency and commits its own
`uv.lock`:

```toml
dependencies = [
  "mosaiq-kit @ git+https://github.com/Equipo-03-Proyecto10/retail-segmentation@kit-py/v0.1.1#subdirectory=packages/kit-py",
]
```

Path dependencies on `packages/` are rejected. A kit upgrade is a service pull
request with an exact new tag, a refreshed lock and that service's own
`VERSION` bump. The current release is `kit-py/v0.1.1`, matching this package's
version.

## Staged contracts

A service image stages its contract without changing the repository layout:

```text
contracts/
  <service>/openapi.yaml
  <service>/<service>.xsd
  common/v1/common.xsd
```

The relative common-schema import therefore continues to resolve. The kit
reads the XML namespace from the service XSD and operation metadata from the
OpenAPI document. A request schema component with no `xml` object uses its
component name as the XML root and the service XSD's target namespace. Explicit
`xml.name` or `xml.namespace` values override those defaults.

The verifier reads the mounted JWKS on first use, reloads it for a first unknown
key id, rate-limits later unknown-key reloads to one per 30 seconds, and always
honours `SIGHUP` for planned rotation. Verification is pinned to RS256 and a
verified token is never accepted without a live session in `redis-state`.

## Flask usage

```python
from flask import Flask
from mosaiq_kit import ApiError, Kit, Page, Representation, money

app = Flask(__name__)
kit = Kit(
    app,
    service="products",
    suffix="products",
    contracts_dir="contracts",
    ready_checks=[check_schema],
)

@kit.operation("getProduct")
def get_product(product_id: int):
    with kit.db.connection() as connection:
        row = connection.execute(
            "SELECT product_id, name, list_price FROM api_products.product_v1 "
            "WHERE product_id = %s",
            (product_id,),
        ).fetchone()
    if row is None:
        raise ApiError(404, "product_not_found", "The product does not exist.")
    return Representation(
        "Product",
        {"product_id": row[0], "name": row[1], "list_price": money(row[2])},
    )
```

`@kit.operation` registers the path and method from `operationId`, applies the
declared security and `x-mosaiq-roles`, and exposes the parsed body as
`kit.body`. For paged operations, `kit.page` and `kit.size` hold validated
values. Handlers return `Representation`, `Collection`, `Page` or
`NoContent`. They raise `ApiError` for service-owned errors.

Use `kit.db.transaction()` for a request write. It sets the local audit actor
from the verified token with a bound parameter. Use `kit.db.connection()` for
reads.

```python
from mosaiq_kit import OutboxRelay, StreamConsumer

relay = OutboxRelay(database_url, suffix, produced_streams, redis_state)
relay.start()

consumer = StreamConsumer(redis_state, consumed_stream, handle_event)
consumer.start()
```

The relay accepts only the service's configured streams. The consumer always
uses `MOSAIQ_SERVICE_NAME` as its group; callers cannot select another group.
Both helpers expose `run_once()` for deterministic tests.

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

## Tests

Start from the repository's prepared PostgreSQL and Redis services, then run:

```sh
source /path/to/kitsvc/env.sh
cd packages/kit-py
uv sync --frozen
uv run black --check .
uv run ruff check .
uv run pytest
```
