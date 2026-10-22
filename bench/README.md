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

The k6 load scripts and the report are #455.

## Run it

auth and products run at their released GHCR tags, 0.1.0 by default (`AUTH_TAG`, `PRODUCTS_TAG`). Those packages are private: sign in with `docker login ghcr.io`, or build each one locally under the same tag:

```bash
.github/scripts/stage-contracts.sh products
docker build -t ghcr.io/equipo-03-proyecto10/mosaiq-products:0.1.0 services/products
```

Do the same for `auth`. Then, from the repository root:

```bash
bash bench/conformance.sh
```

It brings the `bench` profile up and runs the suite against both builds, then compares 20 requests in each format. It stops at the first difference and leaves the stack running for the load runs. Afterwards:

```bash
docker compose -f bench/compose.yaml --profile bench down -v
```

## The twin's own checks

```bash
cd bench/products-service-rs
cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```

The database tests need `TEST_DATABASE_URL`, the owner's URL to a database built by the three scripts; they connect as `svc_products_bench`. `.github/workflows/bench.yml` runs these, the shared conformance suite against the twin's image, and ADR-0042's compliance greps on every pull request that touches `bench/` or `contracts/products/`. It is not a required check, and it pushes nothing.
