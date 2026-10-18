# deploy/templates/service — one service's project

The files a service's implementation story copies into `services/<name>/`
(ADR-0040, F13-08). They live here because ADR-0035's order check refuses a
`services/<name>/` directory before that service's contract merges.

| File | Becomes | What it fixes |
|---|---|---|
| `compose.yaml` | `services/<name>/compose.yaml` | Project `mosaiq-<name>`, one container from GHCR, ports on `127.0.0.1` only, `mem_limit` and `cpus`, no `depends_on`, UID 10001 with no capabilities and a read-only root, the socket, JWKS and env-file mounts, the external `mosaiq` network |
| `nginx.conf` | `services/<name>/nginx/<name>.conf` | `location /api/<name>/`, the overwritten forwarded headers, `client_max_body_size 64k` and the 413 (and auth's 429) in the shared error envelope |
| `Dockerfile.python`, `Dockerfile.rust` | `services/<name>/Dockerfile` | The service's own build context, contracts copied from inside it, UID 10001 |
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
default (Flask 160 MB at 0.5 CPU, Rust 48 MB at 0.25). Then run
`bash deploy/templates/service/check.sh` and expect no output.

## Where each mount points

| Variable | Instance (default) |
|---|---|
| `IMAGE_TAG` | the service's `VERSION`, set by its unit |
| `MOSAIQ_SVC_ENV_DIR` | `/etc/mosaiq/svc` |
| `MOSAIQ_PG_SOCKET` | `/run/postgresql` |
| `MOSAIQ_JWKS_FILE` | `/etc/mosaiq/keys/jwks.json` |
| `MOSAIQ_AUTH_KEY_DIR` (auth) | `/etc/mosaiq/keys` |

Each variable exists so a local stack can point the mount at a stand-in.

The units, `deploy/deploy-service.sh` and the NGINX include directory on the
instance belong to F18-01 to F18-03.
