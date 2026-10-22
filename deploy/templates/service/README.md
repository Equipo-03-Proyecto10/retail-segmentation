# deploy/templates/service — one service's project

The files a service's implementation story copies into `services/<name>/`
(ADR-0040, F13-08). They live here because ADR-0035's order check refuses a
`services/<name>/` directory before that service's contract merges.

| File | Becomes | What it fixes |
|---|---|---|
| `compose.yaml` | `services/<name>/compose.yaml` | Project `mosaiq-<name>`, one container from GHCR, ports on `127.0.0.1` only, `mem_limit` and `cpus`, no `depends_on`, UID 10001 with no capabilities and a read-only root, the socket, JWKS and env-file mounts, the external `mosaiq` network |
| `nginx.conf` | `services/<name>/nginx/<name>.conf` | `location /api/<name>/`, the overwritten forwarded headers, `client_max_body_size 64k` and the 413 (and auth's 429) in the shared error envelope |
| `Dockerfile.python`, `Dockerfile.rust` | `services/<name>/Dockerfile` | The service's own build context, contracts copied from inside it, UID 10001; for Rust, libxml2 for kit-rs's XML validation (headers and `pkg-config` to build, the library to run) |
| `render.py` | — | Writes the three files for one service |
| `check.sh` | — | ADR-0040's compose and NGINX loop; prints nothing when they hold |

Lines marked `#auth# ` are auth's alone: its private key mount and its sign-in
limit. `render.py` keeps them for `auth` and drops them for everyone else.

## Starting a service

From the repository root, with the port from `contracts/registry.yaml`:

```bash
python3 deploy/templates/service/render.py products 5003 python services/products
```

`--mem-limit` sets a line of ADR-0040's memory table that is not the kind's
default (Flask 160 MB at 0.5 CPU, Rust 48 MB at 0.25). Then, in the same pull
request:
1. append the service's line to `include` in the local stack file at the
   repository root;
2. commit `docker/distributed/svc/<name>.env` with safe local values (see its
   README);
3. run `bash deploy/templates/service/check.sh` and expect no output.

## What CI runs on it

`.github/workflows/services.yml` (F13-09) builds the service once its
`Dockerfile` exists. It runs on any change under `services/<name>/` or
`contracts/<name>/`, and on any change to `contracts/common/v1/` or
`tests/conformance/`:
1. `VERSION` is semver, and a pull request that changes `services/<name>/`
   bumps it.
2. With `uv.lock`: `uv sync --frozen`, `black --check`, `ruff check` and
   `pytest`. With `Cargo.lock`: `cargo fmt --check`, `cargo clippy
   --all-targets -- -D warnings` and `cargo test`. The toolchain is the
   Dockerfile's `rust:` tag, or `rust-toolchain.toml`. Tests may read the
   seeded database as its owner through `TEST_DATABASE_URL`.
3. The contracts are staged by `.github/scripts/stage-contracts.sh <name>`,
   the image is built from `services/<name>/`, and the shared conformance
   suite runs against it:

   ```bash
   bash tests/conformance/run.sh mosaiq-<name>:ci services/<name> <port>
   ```

   The suite needs `redis/<name>.acl`. When `mongo/init.js` exists, it also
   starts MongoDB and passes `MONGO_URI`.

A merge to `main` that changes `VERSION` builds the image once, runs the
suite against it and pushes
`ghcr.io/equipo-03-proyecto10/mosaiq-<name>:<VERSION>`. It never overwrites a
tag. `.github/scripts/guards.sh` checks the repository rules of ADR-0035 to
ADR-0046 on every pull request.

## Where each mount points

| Variable | Instance (default) | Local stack |
|---|---|---|
| `IMAGE_TAG` | the service's `VERSION`, set by its unit | `local` |
| `MOSAIQ_SVC_ENV_DIR` | `/etc/mosaiq/svc` | `docker/distributed/svc` |
| `MOSAIQ_PG_SOCKET` | `/run/postgresql` | named volume `mosaiq-pgsocket` |
| `MOSAIQ_JWKS_FILE` | `/etc/mosaiq/keys/jwks.json` | `docker/distributed/keys/jwks.json`, made by `docker/distributed/auth-keys.sh` |
| `MOSAIQ_AUTH_KEY_DIR` (auth) | `/etc/mosaiq/keys` | `docker/distributed/keys` |
| `AUTH_KID` (auth) | the unit's, from `/etc/mosaiq/svc/auth.env` | `local-dev` |

`jwks.dev.json` holds a public key whose private half was discarded: it lets a
service start and refuse every token. CI's template job still mounts it. The
local stack uses auth-service's own throwaway pair instead
(`docker/distributed/auth-keys.sh`, git-ignored), so the tokens auth signs are
the ones the other services accept. The rotation runbook is
[`deploy/auth-keys.md`](../../auth-keys.md).

The units, `deploy/deploy-service.sh` and the NGINX include directory on the
instance belong to F18-01 to F18-03.
