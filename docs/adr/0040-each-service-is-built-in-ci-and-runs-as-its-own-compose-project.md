# ADR-0040 — Each service is built in CI and runs as its own compose project on the instance

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** [ADR-0015](0015-containers-are-a-development-path-only.md)
**Superseded by:** —

---

## Context

[ADR-0015](0015-containers-are-a-development-path-only.md) keeps every container runtime off the instance, which was right for one Flask
application. The course now requires each microservice in its own container, and
[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md) named ADR-0015 as the record to supersede. Total independence
([ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)) also rules out one Compose file for every service: one
`up` re-evaluates all of them, and one bad change blocks every deploy. These constraints do not move:
- **One e2-standard-2** (2 vCPU, 8 GB, a 50 GB disk; `docs/infra.md`): C-5 and C-6 allow no second machine, managed database or Kubernetes.
- **Ingress is the GCP firewall only:** `tcp:22`, and `tcp:443` from Cloudflare's ranges.
- **PostgreSQL listens on loopback only** (`listen_addresses = 'localhost'`), which a container's own `127.0.0.1` cannot reach.
- **The web system is frozen** under `mosaiq.service`, deployed by `deploy/deploy.sh` from `main` (ADR-0011).
- **SELinux is enforcing** on the CentOS Stream 10 instance (`deploy/postgresql/README.md`).

Two things are uncertain: every memory figure is an estimate until Phase 18 (epic #404) measures it, and whether the enforcing SELinux
policy lets a container use the host's PostgreSQL socket is unverified.

## Decision

Every service is its own deployable unit, from source to running container. CI builds `services/<name>/` into an immutable image
`ghcr.io/<org>/mosaiq-<name>:<semver>` and pushes it to GHCR; the instance only pulls. There, each service runs as its own Compose project,
`mosaiq-<name>`, under the systemd template `mosaiq-svc@<name>.service`, in a non-root container with no capabilities. Redis and MongoDB run
in a separate project, `mosaiq-infra`, and all projects join one external Docker network, `mosaiq`. Service ports bind `127.0.0.1` only,
behind NGINX. Services reach PostgreSQL through its Unix socket directory, mounted into the container, so `listen_addresses` does not change.
A per-service workflow deploys a service when its `VERSION` bump merges to `main`, installs its NGINX include and Redis ACL, and rolls back
only that service. The web, its unit and `deploy/deploy.sh` are not touched.

### Build

`services/<name>/` holds what the service ships:
- `Dockerfile`: the build context is `services/<name>/` and nothing above it ([ADR-0045](0045-services-never-import-the-web-system.md)).
- `VERSION`: semver; CI fails a pull request that changes `services/<name>/**` without bumping it.
- `uv.lock` or `Cargo.lock`: its own lock file; there is no shared Cargo workspace.
- `compose.yaml`: project `mosaiq-<name>`, one container, `mem_limit` and `cpus`, the external `mosaiq` network, ports on
  `127.0.0.1:<port>` only, `restart: unless-stopped`, no `depends_on`, and the hardening under Run.
- `nginx/<name>.conf`: one `location /api/<name>/` with `proxy_pass http://127.0.0.1:<port>/;`, `proxy_set_header X-Real-IP $remote_addr;`,
  `proxy_set_header X-Forwarded-For $remote_addr;` (overwritten, never appended; `$remote_addr` is restored from Cloudflare by
  `cloudflare-real-ip.conf`) and `client_max_body_size 64k;`. auth's also applies a `limit_req` zone to `POST /api/auth/v1/sessions`, as the
  web limits `/login`.
- `redis/<name>.acl`: its ACL user line (ADR-0037).
- `tests/`: unit, golden and conformance tests.

`<org>` is `equipo-03-proyecto10`, in lower case as GHCR requires. A pull request builds each changed service's image and runs its tests and
the black-box suite, pushing nothing. A merge to `main` with a bumped `VERSION` builds the image once, runs the black-box suite against it,
labels it with the commit SHA and pushes `:<VERSION>`, never overwriting a tag. Pushing needs `packages: write` and an organization owner's
consent; the instance pulls with a classic `read:packages` token kept root-only by `docker login` (`deploy/README.md`). Before
`docker build`, CI copies `contracts/<name>/` and `contracts/common/v1/` into `services/<name>/contracts/` (git-ignored); the Dockerfile
copies them from inside its context, so the context stays `services/<name>/` (ADR-0045), and the black-box suite proves the served
`/openapi.yaml` and `/schema.xsd` equal the repository files.

### Run

- **Units.** `deploy/infra/compose.yaml` (project `mosaiq-infra`, unit `mosaiq-infra.service`) runs `redis-state`, `redis-cache` and
  `mongodb` at pinned images, publishing nothing, and creates the `external` `mosaiq` network. `mosaiq-svc@<name>.service` runs
  `docker compose -p mosaiq-%i up -d --no-deps` in `/opt/mosaiq/services/%i/current`, and only `Wants=` Docker, `mosaiq-infra` and
  `postgresql-18`, so no restart cascades. Each release lives in `releases/<semver>/`, and secrets in `/etc/mosaiq/svc/<name>.env` (`0600`).
- **Hardening.** Every service container runs as `user: "10001:10001"`, a UID no host account uses, with `cap_drop: [ALL]`,
  `security_opt: ["no-new-privileges:true"]` and `read_only: true` plus a tmpfs `/tmp`. Peer authentication on the mounted socket
  therefore maps the container to no host user, and it cannot become `postgres`.
- **PostgreSQL.** Each container mounts `/run/postgresql` read-only, as a directory, and connects as
  `postgresql://svc_<suffix>@/retail?host=/run/postgresql`. `pg_hba.conf` gains one `local retail svc_<suffix> scram-sha-256` line per role
  **above** `local all all peer`, because HBA is first match. No `host` line is added, `listen_addresses` stays `'localhost'`,
  `deploy/postgresql/mosaiq.conf` does not change, and `svc_products_bench` gets no line (ADR-0042). If SELinux refuses the socket, the fix
  is a local policy module, never a `:z` relabel.
- **Keys.** `/etc/mosaiq/keys/auth-<kid>.pem` (0400, owned by UID 10001) is bind-mounted read-only into auth only; the persistent
  `/etc/mosaiq/keys/jwks.json` (0444) is mounted read-only at `/run/mosaiq/jwks.json` in every service (ADR-0036).
- **Connections.** Pools of at most 3 per service (45), one wake-up connection per outbox owner (8, ADR-0044) and the web's 2 workers stay
  under the default `max_connections` of 100, which is not changed. **Scaling** is per service, in its own `compose.yaml`.

### Deploy and roll back

`.github/workflows/deploy-service.yml` runs on a merged `services/*/VERSION` bump, builds its matrix from the changed files (so it lists no
service) and runs `deploy/deploy-service.sh <name> <version>`, one concurrency group per service. The script installs the ACL line and runs
`ACL LOAD` on both Redis instances, runs `up -d --no-deps`, polls `/health/ready` for 60 s, then installs the NGINX include, runs `nginx -t`
and reloads, under a host `flock` for the shared `aclfile` and NGINX. On any failure it restores only this service's previous release.

### Schema changes: expand before deploy

Each boundary section of `sql/01_schema.sql` sits between `-- BEGIN BOUNDARY <name>` and `-- END BOUNDARY <name>` and is idempotent
(`CREATE ... IF NOT EXISTS`, `CREATE OR REPLACE` for views, functions and triggers, constraints only in guarded DO blocks, re-runnable
grants), so an operator applies the committed section to the live database as `postgres` before the service version that needs it merges.
The three scripts still run clean from empty, and CI re-applies every section. The deploy runs no SQL; `/health/ready` answers 503
`schema_not_ready` until the section is applied, so an early deploy is rolled back.

### Memory budget

CI sums every `mem_limit` in `deploy/infra/compose.yaml` and `services/*/compose.yaml`, and fails a pull request above **3,000 MB**.

| Container | `mem_limit` |
|---|---|
| auth | 384 MB (argon2id, at most 2 verifies at once) |
| users | 224 MB (argon2id, 1 at a time) |
| recommendations | 192 MB |
| products, categories, customers, sales, engagement, campaigns *(second wave)*: Flask, 1 worker × 4 threads, `cpus` 0.5 | 6 × 160 = 960 MB |
| segments | 64 MB |
| stores, stock, customer-preferences, check-ins, audit-trail *(second wave)*: Rust, 2 tokio workers, `cpus` 0.25 | 5 × 48 = 240 MB |
| `redis-state` | 96 MB (`maxmemory` 64 MB) |
| `redis-cache` | 160 MB (`maxmemory` 128 MB) |
| `mongodb` | 512 MB (`wiredTigerCacheSizeGB` 0.25) |
| **Containers** | **2,832 MB** (2,624 MB without the second wave; 168 MB under the gate) |

The host takes about 2,690 MB (OS 450, Docker 200, PostgreSQL 1,700, web 300, NGINX 40), so the VM commits about 5.5 GB of 8 GB; the
swapfile is a cushion, and the `cpus` caps oversubscribe on purpose. If memory is short, drop the second wave, then lower the Flask limits
to 128 MB after measuring.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| One Compose file for every service, Redis and MongoDB, under one unit (this record's earlier text) | Services deploy in lock step. One `up` re-evaluates every service, a broken file blocks every service's deploy, and a rollback means "the previous file", not "the previous version of this one service". That fails the independence requirement directly |
| Build images on the instance at deploy time | A Rust release link takes 1–2 GB and would compete with the running stack on 8 GB. It puts toolchains on the instance, and it runs an image that CI never tested |
| Podman with podman-compose or Quadlet units (CentOS's default) | Workable, and Quadlet fits systemd well. But CI and every developer run Docker Compose, and `mem_limit`, external networks and `--no-deps` would need to behave the same in two runtimes. The delivery should not debug both |
| PostgreSQL also listening on the Docker bridge gateway, with a `host` rule for that subnet | It changes a hardened `listen_addresses` and adds a TCP rule for an address range that Docker allocates and can renumber. The socket keeps the listener exactly as F1-04 left it |
| Containers on host networking (`network_mode: host`) | It reaches the loopback PostgreSQL without a socket mount. But it puts every container on the host's network namespace and port space, with no isolation between services. Redis and MongoDB would be on the host's loopback for every process |
| A second VM, GKE or Cloud Run | C-5 and C-8 name one machine. Kubernetes is out of scope, and each option adds cost and a second place to deploy |

## Consequences

**What this makes easy.**
- A service is released, deployed and rolled back alone; a rollback switches to an image tag already on disk, with no build and no network.
- The image that passed the black-box suite in CI is the image that runs; the instance has no build toolchains.
- Redeploying a service never restarts Redis, MongoDB, PostgreSQL, NGINX or the web, and PostgreSQL's listener stays as hardened today.
- Adding a service touches its own directory, its registry line, its schema section and its ADR, plus its lines in the shared files
  ADR-0043 lists. Nothing in `deploy/` or `.github/` lists services.

**What this makes hard.**
- **Two process managers.** The web runs under systemd, each service as a systemd template over Docker: sixteen more units to know.
- **GHCR has a person attached.** The read token is one member's classic PAT; if it lapses, deploys fail, and so do rollbacks to a tag no
  longer on disk. Someone owns its rotation.
- **Schema changes are manual, ordered and idempotent.** Every boundary section is written to run twice, and one that is not re-runnable
  fails CI. Removing anything from a published object takes two releases.
- **The `pg_hba` order is a trap.** A `svc_*` line below `local all all peer` fails as a peer mismatch, not as a missing rule.
- **The SELinux socket path is unverified.** If the policy refuses it, a local policy module must come before the first service.
- **168 MB of headroom.** Any growth or replica needs a cut or a new record, and the figures are estimates until measured.
- **Shared surfaces remain.** NGINX, the `aclfile` and the infrastructure project serve every service; a hand edit that skips the job can
  break `/api/` for all, and a down `redis-state` makes every service answer 503.
- **ADR-0015's compliance check now fails by design;** this record's check replaces it.

**What must now be true elsewhere.**
- Epic #404 adds `deploy/infra/compose.yaml`, `deploy/systemd/mosaiq-infra.service`, `deploy/systemd/mosaiq-svc@.service`,
  `deploy/deploy-service.sh` and `.github/workflows/deploy-service.yml`, and extends `ci.yml` with the build matrix, release job and memory gate.
- `deploy/nginx/mosaiq.conf` gains `include /etc/nginx/mosaiq-api.d/*.conf;` in its 443 `server` block and declares the sign-in `limit_req`
  zone once in its http context ([ADR-0009](0009-nginx-as-the-reverse-proxy.md)).
- `deploy/postgresql/README.md` documents the `svc_*` lines; `deploy/README.md` covers both process managers, the GHCR token and the key paths.
- `sql/01_schema.sql` keeps one marked, idempotent section per boundary ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md));
  every `/health/ready` answers `schema_not_ready`, and the registry carries every port.
- `.env.example` documents each service's socket-form `DATABASE_URL`; `.gitignore` lists `services/*/contracts/`; `docs/infra.md` records
  the Docker version, the measured `docker stats` and the swapfile.
- On acceptance, ADR-0015's status line becomes `Superseded by ADR-0040` in the same pull request, and [`README.md`](README.md) follows.

## Compliance

In CI, from the repository root, this must print nothing:

```bash
for d in services/*/; do
  n=$(basename "$d"); f="$d/compose.yaml"; c="$d/nginx/$n.conf"; port=$(yq -r ".services[] | select(.name == \"$n\") | .port" contracts/registry.yaml)
  [ "$(yq -r '.name' "$f")" = "mosaiq-$n" ] || echo "FAIL: $f is not project mosaiq-$n"
  yq -e '.services[] | select(has("depends_on"))' "$f" >/dev/null 2>&1 && echo "FAIL: $f has depends_on"
  yq -r '.services[].ports[]?' "$f" | grep -v "^127\.0\.0\.1:$port:" | sed "s|^|FAIL: $f publishes |"
  yq -e '.services[] | select((.user // "0" | test("^(0|root)(:|$)")) or ((.cap_drop // []) | index("ALL") | not) or .read_only != true
    or ((.security_opt // []) | any(test("no-new-privileges")) | not))' "$f" >/dev/null 2>&1 && echo "FAIL: $f is not hardened"
  { grep -q "proxy_pass http://127\.0\.0\.1:$port/;" "$c" && grep -q 'X-Real-IP \$remote_addr' "$c"; } || echo "FAIL: $c"
done
for b in $(sed -n 's/^-- BEGIN BOUNDARY \(.*\)$/\1/p' sql/01_schema.sql); do
  sed -n "/^-- BEGIN BOUNDARY $b\$/,/^-- END BOUNDARY $b\$/p" sql/01_schema.sql \
    | psql -X -q -v ON_ERROR_STOP=1 -d retail >/dev/null || echo "FAIL: boundary section $b is not re-runnable"
done
total=0; for v in $(yq -r '.services[].mem_limit' deploy/infra/compose.yaml services/*/compose.yaml); do
  case "$v" in *m) total=$((total + ${v%m})) ;; *g) total=$((total + ${v%g} * 1024)) ;; *) echo "FAIL: mem_limit '$v'" ;; esac; done
[ "$total" -le 3000 ] || echo "FAIL: mem_limit sums to ${total} MB, above 3,000 MB"
grep -nE 'docker compose' .github/workflows/deploy-service.yml deploy/deploy-service.sh | grep -v -- '-p mosaiq-'
grep -nE 'docker (compose .*)?build|--build' deploy/deploy-service.sh .github/workflows/deploy-service.yml
grep -rnE 'mosaiq-(auth|users|products|categories|stores|stock|customers|customer-preferences|sales|segments|recommendations|engagement|check-ins|campaigns|audit-trail)\b' .github/ deploy/
grep -nE 'docker|ghcr|services/' deploy/deploy.sh .github/workflows/deploy.yml
```

On the instance, `deploy/check-services.sh` fails on any output. It checks that:
- the only non-loopback listeners are sshd :22 and NGINX :80 (its redirect, closed at the GCP firewall) and :443;
- every container has a `mem_limit` and none was OOM-killed;
- every image comes from `ghcr.io/equipo-03-proyecto10/mosaiq-*`, `redis:` or `mongo:`;
- each `/opt/mosaiq/services/<name>` has an active `mosaiq-svc@<name>` and a project `mosaiq-<name>`, and `mosaiq` and `mosaiq-infra` are active;
- every `mosaiq-*` service container runs as a non-root user with `CapDrop` `ALL`, and the `.pem` mount appears only in `mosaiq-auth`;
- `pg_hba`'s `svc_*` lines are `local` `scram-sha-256` lines for `retail` above the peer line, one per LOGIN role, none for
  `svc_products_bench`, and `listen_addresses` is `localhost`;
- a container run with the service user, `cap_drop: [ALL]` and the socket mount cannot connect as `postgres`.

A rollback is rehearsed once per service before the demonstration: a release whose `/health/ready` cannot pass must leave the previous tag
serving and the other projects' containers untouched (`docker ps --format '{{.Names}} {{.RunningFor}}'`).
