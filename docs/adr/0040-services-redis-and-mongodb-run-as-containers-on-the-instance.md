# ADR-0040 — Services, Redis and MongoDB run as containers on the instance, next to the unchanged web system

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** [ADR-0015](0015-containers-are-a-development-path-only.md)
**Superseded by:** —

---

## Context

[ADR-0015](0015-containers-are-a-development-path-only.md) forbids a container
runtime on the instance. That was right for one Flask application, and it is
wrong for this delivery, which requires each microservice in "its own
container". [ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
already named ADR-0015 as the record to supersede once deployment was decided.

ADR-0015 also documented the real obstacle: a container's `127.0.0.1` is not
the host's. The instance's PostgreSQL listens on loopback only
(`deploy/postgresql/mosaiq.conf`), so a containerised service cannot reach it
as configured today.

The constraints that do not move:

- **Everything runs on the team's e2-standard-2** (2 vCPU, 8 GB, 50 GB): C-5,
  and no managed services under C-6.
- **The firewall admits only 22, and 443 from Cloudflare.**
- **The web system stays under systemd and is not touched** (#398).
- **CentOS Stream 10 packages neither Redis (it ships Valkey) nor MongoDB.**

No memory measurement of the instance exists yet.

## Decision

**Runtime.**
- Install Docker Engine and the Compose plugin from Docker's CentOS repository,
  pinned.
- A systemd unit, `mosaiq-services.service`, runs a production Compose file
  holding the services, Redis and MongoDB, with `restart: unless-stopped`.

**The web and PostgreSQL stay where they are.**
- The web keeps its `mosaiq.service` unit.
- PostgreSQL stays on the host and remains the only PostgreSQL.

**Network.**
- **PostgreSQL** additionally listens on the Compose network's gateway address.
  `pg_hba.conf` admits **only the `svc_*` roles from that subnet**, with
  `scram-sha-256`. Nothing about `retail_app`'s access changes.
- **Service ports** are published on `127.0.0.1` only. NGINX proxies
  `/api/<service>/` to them on 443.
- **Redis and MongoDB** publish no port at all.
- The GCP firewall is unchanged.

**Memory.** Every container has a memory limit. The budget is:

| Component | Limit |
|---|---|
| PostgreSQL, the web (2 workers) and NGINX, as today | ≈ 1.5 GB |
| Redis | 384 MB (`maxmemory` 256 MB) |
| MongoDB | 768 MB (`wiredTigerCacheSizeGB` 0.25) |
| Five Flask services, 2 workers each | 256 MB each |
| Three Rust services | 64 MB each |
| Operating system and Docker | ≈ 0.8 GB |
| **Total** | **≈ 4.9 GB, leaving about 3 GB of headroom** |

F18-01 records the measured figures. If they exceed this budget, the record is
revisited.

**Deployment** from `main` builds the service images on the instance, starts
them, waits for `/health/ready`, and restores the previous images on failure.
It never runs SQL, exactly like `deploy/deploy.sh`.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep ADR-0015 and run services as systemd units with virtualenvs | Fails "its own container", a stated requirement. Redis and MongoDB would also need non-distribution packages on CentOS 10 |
| Podman and podman-compose (CentOS's default) | Workable, but CI and every developer use Docker Compose. Podman-compose's handling of `depends_on` health conditions has differed from Docker's, and the delivery should not debug two runtimes |
| Move the web and PostgreSQL into containers too | Contradicts #398 (the web is not touched) and risks the graded, running system for no requirement |
| Containers on host networking (`network_mode: host`) | Avoids the PostgreSQL change, but publishes Redis and MongoDB on the host's loopback to every process, and loses per-container network isolation |
| A second VM, GKE or Cloud Run | C-5 and C-8 name one machine, Kubernetes is out of scope, and it adds cost |

## Consequences

**What this makes easy.**
- Development, CI and the instance run the same images.
- Each service is restarted, limited and logged independently.
- Redis and MongoDB come from the official images.

**What this makes hard.**
- **The instance now has two process managers,** systemd for the web and
  Docker for the services. An operator must know which is which;
  `deploy/README.md` says so.
- **PostgreSQL gains a second listen address,** a change to a hardened
  configuration. pg_hba must stay narrow, and F18-01 verifies that the
  address is not reachable from outside.
- **Memory is the binding constraint on 8 GB.** Benchmarking (ADR-0042) must
  not run on the instance during the demonstration.
- **ADR-0015's compliance check now fails by design.** This record's check
  replaces it.

**What must now be true elsewhere.**
- `deploy/README.md` and `deploy/postgresql/` document the runtime, the unit,
  the listen address and pg_hba.
- `.env.example` documents every new variable.
- The `deploy.yml` workflow gains the services step (F18-03).
- `docs/infra.md` records the measured memory.
- ADR-0015's status line reads "Superseded by ADR-0040".

## Compliance

```bash
# On the instance: the web still runs under systemd, services under Compose,
# nothing but NGINX listens publicly, and Redis/MongoDB publish nothing.
systemctl is-active mosaiq mosaiq-services
docker compose -f /opt/mosaiq/compose.services.prod.yaml ps --format '{{.Name}} {{.Publishers}}'
ss -ltnp | awk '$4 !~ /^(127\.0\.0\.1|\[::1\]|172\.)/ {print}'   # expect only :22 and :443
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'    # every container under its limit
```
