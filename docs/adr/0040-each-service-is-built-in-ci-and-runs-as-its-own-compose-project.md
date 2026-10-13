# ADR-0040 — Each service is built in CI and runs as its own compose project on the instance

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** [ADR-0015](0015-containers-are-a-development-path-only.md)
**Superseded by:** —

---

## Context

[ADR-0015](0015-containers-are-a-development-path-only.md) keeps every
container runtime off the instance. That was right while the delivered system
was one Flask application. It cannot hold for this stage: the course requires
each microservice in its own container, and
[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
already named ADR-0015 as the record to supersede once deployment was decided.

The instructor's first binding requirement is total independence. Each
service must be built, versioned, deployed, scaled and rolled back on its own
([ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)).
A single Compose file for every service, which this record proposed before,
fails that: one `up` re-evaluates every service in the file, and one bad
change blocks every deploy behind it.

These constraints do not move:

- **One e2-standard-2: 2 vCPU, 8 GB, a 50 GB disk** (`docs/infra.md`). C-5 puts
  everything on it, and C-6 forbids managed databases. There is no Kubernetes.
- **Ingress is the GCP firewall only.** It admits `tcp:22`, and `tcp:443` from
  Cloudflare's ranges. firewalld keeps `eth0` in the `trusted` zone, so it is
  not a second layer (`docs/infra.md`).
- **PostgreSQL listens on loopback only.** `deploy/postgresql/mosaiq.conf` sets
  `listen_addresses = 'localhost'`, and `pg_hba.conf` has no non-loopback TCP
  rule. A container's `127.0.0.1` is its own, not the host's, so a container
  cannot reach that listener as it is configured. ADR-0015 recorded the same
  obstacle.
- **The web system is frozen.** It stays under `mosaiq.service`, deployed by
  `deploy/deploy.sh` from `main`
  ([ADR-0011](0011-one-environment-deployed-from-main.md)).
- **SELinux is enforcing** on the CentOS Stream 10 instance
  (`deploy/postgresql/README.md`).

Two things are uncertain. No memory measurement of the instance under this
load exists yet, so every figure below is an estimate until the Phase 18
evidence (epic #404) replaces it. It is also unverified whether a container
may connect to the host's PostgreSQL socket under the enforcing SELinux
policy. That depends on how Docker's SELinux support is configured on the
instance.

## Decision

Every service is its own deployable unit, from source to running container.
CI builds `services/<name>/` into an immutable image
`ghcr.io/<org>/mosaiq-<name>:<semver>` and pushes it to GHCR. The instance
only pulls. On the instance, each service runs as its own Compose project,
`mosaiq-<name>`, under the systemd template `mosaiq-svc@<name>.service`.
Redis and MongoDB run in a separate project, `mosaiq-infra`. All projects
join one external Docker network, `mosaiq`. Service ports bind `127.0.0.1`
only, behind NGINX. Services reach PostgreSQL through its Unix socket
directory, mounted into the container, so `listen_addresses` does not change.
A per-service workflow deploys a service when its `VERSION` bump merges to
`main`. It rolls back only that service, and installs that service's NGINX
include and Redis ACL. The web, its unit and `deploy/deploy.sh` are not
touched.

### Build

`services/<name>/` holds everything that service needs to ship:

| Path | What it is |
|---|---|
| `Dockerfile` | The build context is `services/<name>/` and nothing above it ([ADR-0045](0045-services-never-import-the-web-system.md)) |
| `VERSION` | semver. CI fails a pull request that changes `services/<name>/**` without bumping it |
| `uv.lock` or `Cargo.lock` | Its own lock file. There is no shared Cargo workspace |
| `compose.yaml` | `name: mosaiq-<name>`, one container, its `mem_limit` and `cpus`, the external `mosaiq` network, ports on `127.0.0.1` only, `restart: unless-stopped`, and no `depends_on` |
| `nginx/<name>.conf` | One `location /api/<name>/` with `proxy_pass http://127.0.0.1:<port>/;`, the port registered in `contracts/registry.yaml`. The trailing slash strips the prefix, so the container sees `/v1/...` |
| `redis/<name>.acl` | Its ACL user line ([ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)) |
| `tests/` | Unit, golden and conformance tests |

`<org>` is `equipo-03-proyecto10`, the organization's name in lower case,
because GHCR requires lower-case image names.

- **Pull requests.** A path-filtered matrix over `services/*/` builds each
  changed service's image, runs its lint, unit and golden tests, and runs the
  shared black-box suite against the built image. Nothing is pushed. A failure
  blocks only that service.
- **Merge to `main` with a bumped `VERSION`.** The release job builds the
  image once and runs the black-box suite against that exact image. It labels
  the image with the commit SHA (`org.opencontainers.image.revision`) and
  pushes it as `:<VERSION>`. It refuses to overwrite a tag that already exists
  in GHCR. The deploy job runs only after the push.

Pushing with the workflow's `GITHUB_TOKEN` needs `permissions: packages:
write` in the workflow. The organization must also allow this repository to
publish container packages, and only an organization owner can grant that.
The packages are private, so the instance pulls with a personal access token
(classic) that has the `read:packages` scope and nothing else. The token is
stored root-only on the instance by `docker login ghcr.io`. It is never in
the repository or in `.env.example`.

### Run

- **Infrastructure.** `deploy/infra/compose.yaml`, project `mosaiq-infra`,
  runs `redis-state`, `redis-cache` and `mongodb` under
  `mosaiq-infra.service`. Images are pinned to exact versions. Nothing is
  published to the host
  ([ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md),
  [ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)).
  The unit's `ExecStartPre` creates the `mosaiq` network if it is missing.
  Every project declares that network `external`, the infrastructure project
  included, so no `down` ever removes it.
- **Services.** `mosaiq-svc@<name>.service` is `Type=oneshot` with
  `RemainAfterExit=yes`. It runs
  `docker compose -p mosaiq-%i up -d --no-deps` in
  `/opt/mosaiq/services/%i/current`, and `down` on stop. It is ordered
  `After=` `docker.service`, `mosaiq-infra.service` and
  `postgresql-18.service`, and only `Wants=` them. It does not `Requires=`
  them, so restarting the infrastructure never restarts a service, and the
  reverse is also true.
- **Releases.** `/opt/mosaiq/services/<name>/releases/<semver>/` holds that
  release's `compose.yaml`, NGINX include, ACL line and `release.env`
  (`IMAGE_TAG=<semver>`). `current` is a symlink to one of them. Secrets live
  in `/etc/mosaiq/svc/<name>.env` (`0600 root`), outside the repository, and
  are passed with `--env-file`.
- **PostgreSQL.** Each container mounts the host's socket directory,
  `/run/postgresql`, read-only, as a directory, so the mount survives a
  PostgreSQL restart. It connects as its own `svc_*` role:
  `postgresql://svc_<role>@/retail?host=/run/postgresql`. `pg_hba.conf` gains
  one line per role:

  ```conf
  local   retail   svc_sales   scram-sha-256
  ```

  These lines go **above** the existing `local all all peer` line. HBA is
  first match, so a `svc_*` line below it would never be reached. The
  connection would be refused as a peer mismatch. No `host` line is added,
  `listen_addresses` stays `'localhost'`, and `deploy/postgresql/mosaiq.conf`
  does not change. `svc_products_bench` gets no line on the instance
  ([ADR-0042](0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md)).
  If SELinux refuses the socket, the fix is a local policy module. The fix is
  never a `:z` relabel, because that relabels the host's own socket
  directory.
- **Connections.** Each service pool holds at most 3 connections, so 45 in
  all. Add one wake-up connection per outbox owner
  ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md))
  and the web's two gunicorn workers. That stays under PostgreSQL's default
  `max_connections` of 100, so that setting is not changed either.
- **Scaling** is per service: threads, `mem_limit` and `cpus` in its own
  `compose.yaml`. A second replica takes the next port in the service's range
  and joins that service's NGINX upstream. Any replica counts against the
  budget below.

### Deploy and roll back

`.github/workflows/deploy-service.yml` runs on a push to `main` that changes
`services/*/VERSION`. It builds its matrix from the changed files, so it never
lists services. It authenticates the way `deploy.yml` does, through Workload
Identity Federation, and runs `deploy/deploy-service.sh <name> <version>` over
SSH. Its concurrency group is `deploy-service-<name>`, so one service's
deploy never waits for another's. The script:

1. Refuses to start if `/etc/mosaiq/svc/<name>.env` is missing. It records the
   release that `current` points to.
2. Stages `releases/<version>/` from the merged commit.
3. Installs the ACL line and runs `ACL LOAD` on both Redis instances, as
   ADR-0037 describes. This step comes first because the new version must
   have its permissions when it starts.
4. Runs `docker compose -p mosaiq-<name> pull`, then `up -d --no-deps`.
5. Polls `http://127.0.0.1:<port>/health/ready` for 60 s.
6. Installs `nginx/<name>.conf` as `/etc/nginx/mosaiq-api.d/<name>.conf`,
   then runs `nginx -t` and `systemctl reload nginx`. A route appears only
   once something healthy answers it.
7. Points `current` at the new release.

Steps 3 and 6 take a host `flock`, because the `aclfile` and NGINX are shared.
If any step fails, the script restores only this service's previous release:
its image tag, which is still on disk, its ACL line and its include. It
then checks that release's health. It never runs SQL, never builds an image
and never touches another project. `deploy/deploy.sh` and `deploy.yml` stay as
they are and never move a service image.

### Schema changes: expand before deploy

A service never ships ahead of its database. Its boundary's section of
`sql/01_schema.sql` merges first, and an operator applies that section to the
live database as `postgres`. A service version that needs it merges after.
The deploy job runs no SQL. `/health/ready` checks the service's grants and
views, and answers 503 `schema_not_ready` when they are missing, so a service
deployed too early fails step 5 and is rolled back. A published `*_v1` object
never changes incompatibly. A breaking change ships `_v2` beside it, and `_v1`
is dropped only after its last consumer has moved.

### Memory budget

Every container has a `mem_limit`. CI sums `mem_limit` across
`deploy/infra/compose.yaml` and `services/*/compose.yaml`, and fails the
pull request above **3,000 MB**.

| Container | `mem_limit` | Notes |
|---|---|---|
| auth | 384 MB | gunicorn 1 worker × 4 threads; argon2id needs 64 MiB per verify, so at most 2 run at once |
| users | 224 MB | argon2id on create and reset, 1 at a time |
| recommendations | 192 MB | |
| products, categories, customers, sales, engagement | 160 MB each, 800 MB | |
| campaigns *(second wave)* | 160 MB | |
| **Flask, 9 services** | **1,760 MB** | `cpus` 0.5 each |
| stores, stock, customer-preferences, check-ins | 48 MB each, 192 MB | tokio, 2 workers |
| audit-trail *(second wave)* | 48 MB | |
| segments | 64 MB | |
| **Rust, 6 services** | **304 MB** | `cpus` 0.25 each |
| `redis-state` | 96 MB | `maxmemory` 64 MB |
| `redis-cache` | 160 MB | `maxmemory` 128 MB |
| `mongodb` | 512 MB | `wiredTigerCacheSizeGB` 0.25 |
| **Containers** | **2,832 MB** | 2,624 MB without the second wave; 168 MB under the gate |

The host itself is estimated at about 2,690 MB:

| Host component | Estimate |
|---|---|
| The OS, sshd and journald | about 450 MB |
| Docker and containerd | about 200 MB |
| PostgreSQL with the added backends | about 1,700 MB |
| The web's gunicorn, 2 workers | about 300 MB |
| NGINX | about 40 MB |

The whole VM commits about 5.5 GB of 8 GB. The rest is page cache and room
for spikes. A 2 GB swapfile is a cushion against an OOM kill, not capacity.
The `cpus` caps are limits, not reservations. They oversubscribe the 2 vCPU
on purpose, for a stack that is mostly idle.

If measured memory is short, the cuts come in this order:
1. Drop the second wave (campaigns, audit-trail).
2. Lower the Flask limits to 128 MB, after measuring them.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| One Compose file for every service, Redis and MongoDB, under one unit (this record's earlier text) | Services deploy in lock step. One `up` re-evaluates every service, a broken file blocks every service's deploy, and a rollback means "the previous file", not "the previous version of this one service". That fails the independence requirement directly |
| Build images on the instance at deploy time | A Rust release link takes 1–2 GB and would compete with the running stack on 8 GB. It puts toolchains on the instance, and it runs an image that CI never tested |
| Podman with podman-compose or Quadlet units (CentOS's default) | Workable, and Quadlet fits systemd well. But CI and every developer run Docker Compose, and `mem_limit`, external networks and `--no-deps` would need to behave the same in two runtimes. The delivery should not debug both |
| PostgreSQL also listening on the Docker bridge gateway, with a `host` rule for that subnet (this record's earlier text) | It changes a hardened `listen_addresses` and adds a TCP rule for an address range that Docker allocates and can renumber. The socket keeps the listener exactly as F1-04 left it |
| Containers on host networking (`network_mode: host`) | It reaches the loopback PostgreSQL without a socket mount. But it puts every container on the host's network namespace and port space, with no isolation between services. Redis and MongoDB would be on the host's loopback for every process |
| A second VM, GKE or Cloud Run | C-5 and C-8 name one machine. Kubernetes is out of scope, and each option adds cost and a second place to deploy |

## Consequences

**What this makes easy.**
- A service is released, deployed and rolled back alone, and a failure blocks
  only that service. A rollback switches to an image tag already on disk. It
  needs no build and no network.
- The image that ran the black-box suite in CI is the image that runs on the
  instance. The instance has no compilers or build toolchains.
- Redeploying a service never restarts Redis, MongoDB, PostgreSQL, NGINX or
  the web.
- Adding a service touches its own directory, its registry line, its schema
  section and its ADR. Nothing in `deploy/` or `.github/` lists services.
- PostgreSQL's listener and its TCP rules are exactly as hardened today.

**What this makes hard.**
- **Two process managers.** The web runs under systemd. Each service is a
  systemd template instance over Docker. An operator must know which is
  which, and there are now sixteen more units.
- **GHCR is a new dependency with a person attached.** The organization owner
  must allow package publishing. The instance's read token is a classic PAT,
  tied to one member's account. If it expires or that member leaves, deploys
  fail, and so do rollbacks to a tag no longer on disk. Someone has to own
  its rotation.
- **Schema changes are manual and ordered.** An operator applies a boundary's
  section before its service merges. Each section must be written so that it
  applies to a populated database, which the three scripts are not required
  to do today. Removing anything from a published object takes two
  releases.
- **The `pg_hba` order is a trap.** A `svc_*` line placed below
  `local all all peer` fails as a peer mismatch, not as a missing rule.
- **The SELinux socket path is unverified.** If the policy refuses it, a local
  policy module has to be written before the first service runs.
- **168 MB of headroom.** Any service that grows, or any added replica, needs
  a cut or a new record. The figures are estimates until they are measured.
- **Shared surfaces remain.** NGINX, the `aclfile` and the infrastructure
  project serve every service. A bad include fails `nginx -t` and aborts that
  deploy, but a hand edit that skips the job can still break `/api/` for
  everyone. If `redis-state` goes down, every service answers 503
  (ADR-0037).
- **ADR-0015's compliance check now fails by design.** This record's check
  replaces it.

**What must now be true elsewhere.**
- Epic #404 adds `deploy/infra/compose.yaml`,
  `deploy/systemd/mosaiq-infra.service`, `deploy/systemd/mosaiq-svc@.service`,
  `deploy/deploy-service.sh` and `.github/workflows/deploy-service.yml`. It
  also extends `ci.yml` with the per-service build matrix, the release job and
  the memory gate.
- The 443 `server` block in `deploy/nginx/mosaiq.conf` gains
  `include /etc/nginx/mosaiq-api.d/*.conf;`. That file is still installed by
  hand, as F6-06 left it ([ADR-0009](0009-nginx-as-the-reverse-proxy.md)).
- `deploy/postgresql/README.md` documents the `svc_*` `local` lines and where
  they go. `deploy/postgresql/mosaiq.conf` does not change. `deploy/README.md`
  describes both process managers and the GHCR token.
- `sql/01_schema.sql` keeps one marked section per boundary, applicable on its
  own to the live database
  ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md),
  [ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)).
- Every service implements `/health/ready` with the `schema_not_ready`
  answer, and the registry carries every port
  ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
- `.env.example` documents each service's socket-form `DATABASE_URL` with a
  local default. CI and laptops may point it at a throwaway PostgreSQL over
  TCP instead.
- `docs/infra.md` records the Docker version, the measured `docker stats` and
  the swapfile.
- When this record is accepted, ADR-0015's status line becomes
  `Superseded by ADR-0040` in the same pull request, and the index in
  [`README.md`](README.md) follows.

## Compliance

In CI, from the repository root, each block must print nothing:

```bash
# Every service project is named for its directory, has no depends_on, binds
# 127.0.0.1 only, and its port and NGINX include match the registry.
for d in services/*/; do
  n=$(basename "$d"); f="$d/compose.yaml"
  port=$(yq -r ".services[] | select(.name == \"$n\") | .port" contracts/registry.yaml)
  [ "$(yq -r '.name' "$f")" = "mosaiq-$n" ] || echo "FAIL: $f is not project mosaiq-$n"
  yq -e '.services[] | select(has("depends_on"))' "$f" >/dev/null 2>&1 && echo "FAIL: $f has depends_on"
  yq -r '.services[].ports[]?' "$f" | grep -v "^127\.0\.0\.1:$port:" | sed "s|^|FAIL: $f publishes |"
  grep -q "proxy_pass http://127\.0\.0\.1:$port/;" "$d/nginx/$n.conf" || echo "FAIL: $n include does not proxy to $port"
done

# Redis and MongoDB are defined only in the infrastructure project.
grep -lE '^\s*image:\s*\S*(redis|mongo)' services/*/compose.yaml

# The memory gate: every container has a limit, and the sum is <= 3,000 MB.
total=0
for f in deploy/infra/compose.yaml services/*/compose.yaml; do
  for v in $(yq -r '.services[].mem_limit' "$f"); do
    case "$v" in
      *m) total=$(( total + ${v%m} )) ;;
      *g) total=$(( total + ${v%g} * 1024 )) ;;
      *)  echo "FAIL: $f has mem_limit '$v'" ;;
    esac
  done
done
[ "$total" -le 3000 ] || echo "FAIL: mem_limit sums to ${total} MB, above 3,000 MB"

# The deploy path runs compose only per project, never builds, and lists no
# service by name. The web's deploy never touches a container.
grep -nE 'docker compose' .github/workflows/deploy-service.yml deploy/deploy-service.sh | grep -v -- '-p mosaiq-'
grep -nE 'docker (compose .*)?build|--build' deploy/deploy-service.sh .github/workflows/deploy-service.yml
grep -rnE 'mosaiq-(auth|users|products|categories|stores|stock|customers|customer-preferences|sales|segments|recommendations|engagement|check-ins|campaigns|audit-trail)\b' .github/ deploy/
grep -nE 'docker|ghcr|services/' deploy/deploy.sh .github/workflows/deploy.yml
```

On the instance:

```bash
# Nothing new listens publicly. Only sshd and NGINX's :22, :80 and :443 bind
# a non-loopback address; :80 is NGINX's redirect, closed at the GCP firewall.
sudo ss -ltnH | awk '{print $4}' | grep -vE '^(127\.0\.0\.1|\[::1\]):' | grep -vE ':(22|80|443)$'

# Every container has a limit, none was OOM-killed, none is near its limit,
# and every image came from a registry.
docker ps -q | xargs docker inspect \
  --format '{{.Name}} oom={{.State.OOMKilled}} limit={{.HostConfig.Memory}}' \
  | grep -E 'oom=true|limit=0$'
docker stats --no-stream --format '{{.Name}} {{.MemPerc}}' \
  | awk '{ sub("%", "", $2); if ($2 + 0 >= 90) print "WARN: " $1 " at " $2 "%" }'
docker ps --format '{{.Image}}' \
  | grep -vE '^(ghcr\.io/equipo-03-proyecto10/mosaiq-[a-z-]+:[0-9]+\.[0-9]+\.[0-9]+|redis:|mongo:)'

# Each service runs as its own project and unit, and the web is unchanged.
for d in /opt/mosaiq/services/*/; do
  n=$(basename "$d")
  systemctl is-active --quiet "mosaiq-svc@$n" || echo "FAIL: mosaiq-svc@$n"
  docker compose ls --format json | jq -e --arg p "mosaiq-$n" 'any(.[]; .Name == $p)' >/dev/null \
    || echo "FAIL: no project mosaiq-$n"
done
for u in mosaiq mosaiq-infra; do systemctl is-active --quiet "$u" || echo "FAIL: $u"; done
```

The PostgreSQL rules: the query must return no rows.

```sql
-- sudo -u postgres psql -X -d retail
WITH peer AS (
  SELECT min(line_number) AS ln FROM pg_hba_file_rules
   WHERE type = 'local' AND user_name = '{all}' AND auth_method = 'peer'),
svc AS (
  SELECT r.line_number, r.type, r.database, r.auth_method, u.name
    FROM pg_hba_file_rules r CROSS JOIN LATERAL unnest(r.user_name) AS u(name)
   WHERE u.name LIKE 'svc\_%')
SELECT 'not a local scram rule for retail, or below peer', name
  FROM svc, peer
 WHERE type <> 'local' OR auth_method <> 'scram-sha-256'
    OR database <> '{retail}' OR line_number > peer.ln
UNION ALL
SELECT 'bench role admitted on the instance', name FROM svc
 WHERE name = 'svc_products_bench'
UNION ALL
SELECT 'missing or duplicated', rolname FROM pg_roles
 WHERE rolcanlogin AND rolname LIKE 'svc\_%' AND rolname <> 'svc_products_bench'
   AND (SELECT count(*) FROM svc WHERE svc.name = rolname) <> 1
UNION ALL
SELECT 'listen_addresses changed', current_setting('listen_addresses')
 WHERE current_setting('listen_addresses') <> 'localhost';
```

A rollback is rehearsed once per service before the demonstration. Run
`deploy-service.sh` with a release whose `/health/ready` cannot pass. The
previous tag must be serving again, and the other projects' container start
times must be unchanged (`docker ps --format '{{.Names}} {{.RunningFor}}'`).
