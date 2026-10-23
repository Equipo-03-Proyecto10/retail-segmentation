# F18-01 (#448): the services on the instance

What was installed on the instance, what was checked, and what was not. The
design is
[ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md),
and the procedure is section F18-01 of [deploy/README.md](../../deploy/README.md).
Memory is recorded separately in [f18-01-memory.md](f18-01-memory.md).

## Where it was run

All times are UTC, on 2026-10-23.

| Piece | Here |
|---|---|
| Instance | `mosaiq-deployment-vm`, `e2-standard-2`, CentOS Stream 10, SELinux `Enforcing` |
| Docker | `docker-ce` 29.9.2, `containerd.io` 2.4.2, `docker-compose-plugin` 5.6.1, `container-selinux` 2.248.0, `"selinux-enabled": true` |
| PostgreSQL | 18 from PGDG, on the host, reached through `/run/postgresql` only |
| Release | `main` at `2cd641e` (target `17f2a8b`), rebuilt before this work (PR #689) |
| Infrastructure | `mosaiq-infra`: `redis:7.4.11-alpine` twice (state and cache), `mongo:7.0.41` |
| Services | 13 core services first, then the second wave (campaigns, audit-trail), each pulled from `ghcr.io/equipo-03-proyecto10/mosaiq-<name>:<VERSION>` |

| Service | Version | Service | Version | Service | Version |
|---|---|---|---|---|---|
| auth | 0.1.2 | stores | 0.1.4 | sales | 0.1.1 |
| users | 0.1.5 | stock | 0.1.2 | segments | 0.1.1 |
| products | 0.1.4 | customers | 0.1.5 | recommendations | 0.1.1 |
| categories | 0.1.5 | customer-preferences | 0.1.2 | engagement | 0.1.1 |
| check-ins | 0.1.3 | campaigns | 0.1.2 | audit-trail | 0.1.2 |

## Results

- `deploy/check-services.sh` printed nothing, with 13 services and again with
  all 15. Its six checks cover the listeners, the `mem_limit`s and the OOM
  kills, the image origins, the units and projects, user, `CapDrop` and the
  `.pem`, the `pg_hba` lines with `listen_addresses`, and a service-user
  container refused as `postgres`.
- Every service answered `/health/ready` with 204 on `127.0.0.1:<port>`, all
  15 at 02:03.
- `deploy/infra/check-redis.sh` failed on one line, a defect that predates this
  work. It is described [below](#redis-acl-check).
- The web stayed up throughout. A loop sampled `https://mosaiq.maxthecoder.online/`
  and `systemctl is-active mosaiq` every 5 seconds, from 00:40:25 to 02:06:03.
  All 989 samples were 200 and `active`.
- `getenforce` printed `Enforcing` before, during and after. SELinux was never
  set to permissive, and no `:z` relabel was used.

## SELinux

**What failed.** With Docker's `selinux-enabled` on, every service started but
answered 503 `schema_not_ready`. Their logs showed `connection to server on
socket "/run/postgresql/.s.PGSQL.5432" failed: Permission denied`.

Inside the users container, running as `uid=10001` and labelled
`system_u:system_r:container_t:s0:c152,c604`:

- `ls /run/postgresql/` was refused;
- a raw `connect()` to the socket gave `13 EACCES`.

The same UID on the host listed the directory, which is `755 postgres
postgres`. So the denial came from SELinux, not from file modes. A throwaway
container showed the same thing:

```text
label enabled: ls: /run/postgresql/.s.PGSQL.5432: Permission denied
label --security-opt label=disable: srwxrwxrwx    1 26       tape             0 Oct 23 01:17 /run/postgresql/.s.PGSQL.5432
```

**The two denials.** The first was the socket's directory and file,
`postgresql_var_run_t`. Granting those exposed a second one:

```text
avc:  denied  { connectto } for  comm="gunicorn" path="/run/postgresql/.s.PGSQL.5432"
  scontext=system_u:system_r:container_t:s0:c109,c997
  tcontext=system_u:system_r:unconfined_service_t:s0 tclass=unix_stream_socket permissive=0
```

The PGDG server runs as `unconfined_service_t`, not `postgresql_t`, because
`/usr/pgsql-18/bin/postgres` is labelled `bin_t`. All ten `postgres` processes
showed that domain.

**The fix.** [`deploy/selinux/mosaiq_svc_pgsocket.te`](../../deploy/selinux/mosaiq_svc_pgsocket.te)
v1.1 grants `container_t` four things:

- `getattr search` on the directory;
- `getattr write` on the socket file;
- `connectto` on `postgresql_t`;
- `connectto` on `unconfined_service_t`.

It grants no read of PostgreSQL's data or configuration, and no TCP access. The
module was built with `checkmodule` and `semodule_package` and loaded with
`semodule -i`. `semodule -l` lists `mosaiq_svc_pgsocket`. Then:

- the users container connected as `svc_users`, with its password over
  scram-sha-256;
- the running services reconnected without a restart;
- sales answered 204 a little after the others, also without a restart;
- check 6 of `check-services.sh` now tests `pg_hba` rather than SELinux: the
  socket is reachable, and `postgres` is still refused to a container's UID.

**The audit log.** The container denials ran from 01:33:33 to 01:52:38, the
instant the module's policy loaded, and none came after. Counted straight from
`/var/log/audit/audit.log*`:

| Denial | Count |
|---|---|
| `{ write }` on `postgresql_var_run_t` `sock_file` | 8,106 |
| `{ connectto }` on `unconfined_service_t` `unix_stream_socket` | 1,183 |
| `{ read }` on `postgresql_var_run_t` `dir` (the diagnostic `ls`) | 2 |

The services retry every second, and `auditd` logged 111,496 of its own
`name_connect` denials (`http_port_t`, port 80) in the same window, and none
after it. The cause, found later in #694, is GCP OS Login resolving the
containers' UID 10001 over HTTP
([694-postgresql-confined.md](694-postgresql-confined.md)). The 5 × 8 MB audit log
therefore rotated about every 4.5 minutes, and the denials from before 01:33
were lost. `ausearch -ts boot` found no matches, so the counts above come from
the files.

To see denials the policy hides, `semodule -DB` was run while reproducing, and
`semodule -B` restored it. Neither showed anything beyond the two denials above.
Since then, the only denial on the instance is `systemd-userdbd` mapping an
`etc_t` file, which is CentOS's own and unrelated.

**Still open.** PostgreSQL itself runs unconfined, since PGDG's binaries carry
no `postgresql_exec_t` label. That predates F18-01, and is tracked in #694. It was resolved on the same day: see
[694-postgresql-confined.md](694-postgresql-confined.md).

## Units and restarts

`systemctl list-units 'mosaiq*'` with all 15:

```text
mosaiq-infra.service active exited
mosaiq-svc@audit-trail.service active exited
mosaiq-svc@auth.service active exited
mosaiq-svc@campaigns.service active exited
mosaiq-svc@categories.service active exited
mosaiq-svc@check-ins.service active exited
mosaiq-svc@customer-preferences.service active exited
mosaiq-svc@customers.service active exited
mosaiq-svc@engagement.service active exited
mosaiq-svc@products.service active exited
mosaiq-svc@recommendations.service active exited
mosaiq-svc@sales.service active exited
mosaiq-svc@segments.service active exited
mosaiq-svc@stock.service active exited
mosaiq-svc@stores.service active exited
mosaiq-svc@users.service active exited
mosaiq.service active running
```

- `active exited` is the expected state of a `Type=oneshot` unit with
  `RemainAfterExit=yes`. Docker's restart policy keeps the containers running.
- **Restarting the infrastructure leaves the services alone.** With the 13
  core services running, `systemctl restart mosaiq-infra` moved
  `redis-state`'s `StartedAt` from 01:08:29 to 01:55:09. The `StartedAt` of
  all 13 service containers was unchanged, and all 13 answered 204 afterwards.
  A service unit has only `Wants=` on infra (ADR-0040), so systemd did not
  restart them, and each one's Redis client reconnected by itself.
- The services were started one at a time, auth first. A long `85-start.sh`
  run was detached with `setsid nohup` on the instance, so that an SSH timeout
  could not cut a pull halfway.

## Isolation per container

From `docker inspect` (no environment, no mount sources, no secrets):

| Container | User | `CapDrop` | Read-only | `no-new-privileges` | SELinux label | Memory (bytes) | Ports | `.pem` |
|---|---|---|---|---|---|---|---|---|
| audit-trail | 10001:10001 | ALL | yes | yes | `container_t:s0:c657,c902` | 50331648 | 127.0.0.1:5015 | |
| auth | 10001:10001 | ALL | yes | yes | `container_t:s0:c20,c251` | 402653184 | 127.0.0.1:5001 | yes |
| campaigns | 10001:10001 | ALL | yes | yes | `container_t:s0:c175,c447` | 167772160 | 127.0.0.1:5014 | |
| categories | 10001:10001 | ALL | yes | yes | `container_t:s0:c183,c226` | 167772160 | 127.0.0.1:5004 | |
| check-ins | 10001:10001 | ALL | yes | yes | `container_t:s0:c323,c730` | 50331648 | 127.0.0.1:5013 | |
| customer-preferences | 10001:10001 | ALL | yes | yes | `container_t:s0:c95,c115` | 50331648 | 127.0.0.1:5008 | |
| customers | 10001:10001 | ALL | yes | yes | `container_t:s0:c403,c555` | 167772160 | 127.0.0.1:5007 | |
| engagement | 10001:10001 | ALL | yes | yes | `container_t:s0:c567,c920` | 167772160 | 127.0.0.1:5012 | |
| products | 10001:10001 | ALL | yes | yes | `container_t:s0:c70,c426` | 167772160 | 127.0.0.1:5003 | |
| recommendations | 10001:10001 | ALL | yes | yes | `container_t:s0:c567,c946` | 201326592 | 127.0.0.1:5011 | |
| sales | 10001:10001 | ALL | yes | yes | `container_t:s0:c109,c997` | 167772160 | 127.0.0.1:5009 | |
| segments | 10001:10001 | ALL | yes | yes | `container_t:s0:c723,c996` | 67108864 | 127.0.0.1:5010 | |
| stock | 10001:10001 | ALL | yes | yes | `container_t:s0:c590,c676` | 50331648 | 127.0.0.1:5006 | |
| stores | 10001:10001 | ALL | yes | yes | `container_t:s0:c594,c842` | 50331648 | 127.0.0.1:5005 | |
| users | 10001:10001 | ALL | yes | yes | `container_t:s0:c152,c604` | 234881024 | 127.0.0.1:5002 | |
| infra `mongodb` | 999:999 | ALL | yes | yes | `container_t:s0:c239,c454` | 536870912 | none | |
| infra `redis-cache` | 999:1000 | ALL | yes | yes | `container_t:s0:c927,c1015` | 167772160 | none | |
| infra `redis-state` | 999:1000 | ALL | yes | yes | `container_t:s0:c597,c638` | 100663296 | none | |

Each container has its own pair of MCS categories, so one container cannot
read another's files even under the same type. Only auth mounts
`auth-2026-10.pem`, which is `0400` and owned by UID 10001. Every service
mounts `jwks.json`, which is `0444`. Each service's secrets come from
`/etc/mosaiq/svc/<name>.env`, which is `0600 root` and is read by Docker, not
mounted.

## Network

- **Listeners outside loopback:** `0.0.0.0:22`, `0.0.0.0:80`, `0.0.0.0:443`
  and their `[::]` pairs (sshd and NGINX). Every service port is bound to
  `127.0.0.1`, and the infrastructure publishes none.
- **PostgreSQL:** `listen_addresses` is still `localhost`. `pg_hba.conf` gained
  the 15 `local retail svc_<suffix> scram-sha-256` lines (114 to 128), above
  `local all all peer` (129). It has none for `svc_products_bench`. The old
  file was backed up under `/root/mosaiq-backups/` first.
- **GCP firewall:** unchanged. The instance's tag `mosaiq-server` still gets
  only `mosaiq-allow-ssh` (tcp:22), `mosaiq-allow-https` (tcp:443 from
  Cloudflare's 15 ranges) and `mosaiq-deny-other-ingress`.

## Redis ACL check

`deploy/infra/check-redis.sh` against the running infrastructure:

```text
FAIL: svc_recommendations: MULTI is allowed
check-redis exit=1
```

`services/recommendations/redis/recommendations.acl` grants
`+multi +exec +discard`, because `app/cache.py` writes a cached result with
`pipeline(transaction=True)`.
[ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)
allows `MULTI` to `svc_auth` alone. The ACL is the committed one from #477, so
the defect is older than F18-01, which only ran the check. It is tracked in
#693. It was fixed in recommendations 0.1.2, and with that version on the instance
the check prints nothing ([f18-02-api-edge.md](f18-02-api-edge.md)). No other line of the check failed.

## What was not done

- **No reboot.** Every unit is enabled, but the instance was not rebooted to
  show that they come back at boot. Restarting infra and single services was
  shown instead.
- **No load.** The services were up and idle: no requests beyond readiness
  probes and the outbox relays. Memory under load is not measured
  ([f18-01-memory.md](f18-01-memory.md)).
- **PostgreSQL unconfined.** That is the instance's state before F18-01 (see
  above, and #694).
