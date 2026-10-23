# ADR-0055 — An ops console supervises the services through their meta routes, a host snapshot and a read-only Redis user

**Status:** Accepted (2026-10-22)
**Owner:** Max
**Issue:** #676
**Supersedes:** —
**Superseded by:** —

---

## Context

Fifteen services run as fifteen Compose projects ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)).
Knowing whether they are all up today takes fifteen `curl`s, a `docker ps` and a
`redis-cli` session per instance. The team wants one page that answers "is
everything healthy, and if not, what".

The forces:
- **It is not a microservice.** A service implements exactly one business rule
  and is registered in `contracts/registry.yaml` ([ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)).
  A monitor has no rule, owns no data and serves no client. AGENTS.md requires an
  ADR for a deployable unit of any other kind; this is that record.
- **Services never call each other over HTTP** ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).
  The console is not a service, but it must not become a back door around that
  rule.
- **Memory is tight.** One e2-standard-2 carries PostgreSQL, two Redis, MongoDB,
  the web and fifteen containers; Phase 18 has not yet measured them.
- **Ingress is 22 and Cloudflare's 443 only** (`docs/infra.md`). A new public
  surface needs its own authentication story.
- **Containers are hardened** (UID 10001, no capabilities, read-only root). The
  Docker socket is root on the host.

Uncertain: whether a single 10 s poll of fifteen health routes is noticeable in
the services' request logs (it should not be: 30 requests per poll, no
database work beyond the readiness check).

## Decision

A small Flask application, **ops-console**, lives in `ops/console/` (not
`services/`), builds and versions alone like a service (own `uv.lock`, semver
`VERSION`, image `ghcr.io/equipo-03-proyecto10/mosaiq-ops-console`, Compose
project `mosaiq-ops-console`, the ADR-0040 hardening) and binds
`127.0.0.1:5090`, reached only through an SSH tunnel, with no NGINX include. It
reads three sources and nothing else: each registered service's unauthenticated
meta routes (`/health/live`, `/health/ready`, `/openapi.yaml`) over the `mosaiq`
network; a JSON snapshot of `docker ps`/`docker stats` that a host systemd timer
writes to `/run/mosaiq/ops/containers.json` and the container mounts read-only;
and `INFO` and `XINFO GROUPS` on both Redis instances as `ops_console`, a user
that may run those, `PING` and `SCAN`, and nothing that writes. It holds the
last hour of probes in memory and renders one server-side page plus
`/status.json`. No service calls it, it calls no `/v1` route, and it is not in
`contracts/registry.yaml`.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Prometheus, Grafana, blackbox, cAdvisor and the Redis and PostgreSQL exporters | Five more containers and roughly 400 MB of RAM on an 8 GB machine whose service budget is still unmeasured; and the services expose no `/metrics`, so most of the stack would sit idle |
| Register it as a sixteenth service | It has no business rule; registering one would dilute ADR-0043's "one rule per service" for a tool |
| Mount `/var/run/docker.sock` into the console | The socket is root on the host, which undoes every line of the ADR-0040 hardening |
| Count outbox backlogs in PostgreSQL | Needs reads of every boundary's tables, which ADR-0044 forbids to anything but the owner; stream lag in Redis shows the same symptom from the consumer side |
| Publish it under `/ops/` behind NGINX | A public page listing every service, version and failure is reconnaissance for an attacker; the tunnel costs one command |
| The existing `ops` Redis user | It can `ACL LOAD` and `CONFIG GET`; a dashboard that holds those powers is a bigger target than it needs to be |

## Consequences

**What this makes easy.** One page shows which services are live and ready,
which version of each contract is serving, which container is restarting or near
its `mem_limit`, and which consumer group is falling behind on which stream.
`/status.json` gives the same for a script.

**What this makes hard.** History is one hour and lives in memory; a restart of
the console forgets it, and there is no alerting. The host timer is one more
unit to install by hand. `deploy/infra/redis/assemble-acl.sh` now accepts an
`ops_` user from `ops/*/redis/*.acl` beside the `svc_` users, so the shape of
the aclfile is no longer services only.

**What must now be true elsewhere.**
- `/health/live` and `/health/ready` stay unauthenticated in every contract.
- `docs/infra.md` lists port 5090, the two units and the tunnel command.
- `/etc/mosaiq/svc/ops-console.env` holds `REDIS_OPS_CONSOLE_PASSWORD`, read by
  `assemble-acl.sh` the same way as the services' passwords.
- `deploy/infra/check-redis.sh` checks `svc_*` users only; `ops_console` is
  outside its scope and its line is the whole of its permission.
