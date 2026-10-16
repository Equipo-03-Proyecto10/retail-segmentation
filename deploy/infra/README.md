# deploy/infra — the infrastructure project

Compose project `mosaiq-infra` (ADR-0040): the two Redis instances of
[ADR-0037](../../docs/adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md),
`redis-state` and `redis-cache`. MongoDB joins this project with F13-06.
[`docs/datastores/redis-design.md`](../../docs/datastores/redis-design.md) is
the specification: keys, TTLs, ACL rules and failure behaviour.

| File | What it is |
|---|---|
| `compose.yaml` | Both instances: the pinned image, no published port, the `mosaiq` network, `mem_limit` 96 MB and 160 MB, the AOF volume |
| `redis/redis-state.conf` | 64 MB, `noeviction`, AOF `everysec`, no RDB |
| `redis/redis-cache.conf` | 128 MB, `allkeys-lru`, no persistence |
| `redis/assemble-acl.sh` | Builds the one aclfile both instances load, and loads it with no restart |
| `check-redis.sh` | ADR-0037's checks, as `ops`; prints nothing when they hold |
| `tests/acl/` | The fixture lines CI loads; see its README |

On the instance, F18-01's `mosaiq-infra.service` runs this project. These
steps are what that unit and each service's deploy job do.

## First start on a host

1. The network every project joins, once:

   ```bash
   docker network create mosaiq
   ```

2. The `ops` password: generate it once, and keep it only on the host and out
   of the repository. The scripts read it from `REDIS_OPS_PASSWORD`.

3. The aclfile must exist before the instances start, because each loads it at
   boot:

   ```bash
   REDIS_OPS_PASSWORD=… deploy/infra/redis/assemble-acl.sh --write-only
   ```

   With no service installed, it holds only `ops` and the disabled `default`
   user. It lives in `/etc/mosaiq/redis/users.acl`, a directory that both
   containers mount read-only. `MOSAIQ_REDIS_ACL_DIR` points elsewhere for a
   test.

4. Start both instances:

   ```bash
   docker compose -p mosaiq-infra -f deploy/infra/compose.yaml up -d
   ```

## A service's ACL line

A service commits `services/<name>/redis/<name>.acl`, one
`user svc_<suffix> on {{password}} …` line built from the rules in the
specification. Its password, `REDIS_<SUFFIX>_PASSWORD`, goes in
`/etc/mosaiq/svc/<name>.env`.

The deploy job then runs:

```bash
REDIS_OPS_PASSWORD=… deploy/infra/redis/assemble-acl.sh
```

The script:
1. renders every installed line with its hashed password;
2. writes the aclfile under a `flock`;
3. runs `ACL LOAD` on `redis-state`, then on `redis-cache`, with no restart.

If either instance refuses the file, the previous file is restored and
reloaded, both instances keep their previous users, and the script exits 1.
One bad line therefore blocks every ACL change until it is fixed.

## Checks

```bash
REDIS_OPS_PASSWORD=… deploy/infra/check-redis.sh
```

It prints nothing when every check in the specification holds. It reads
`contracts/streams/` to know which user may `XADD` to which stream;
`STREAMS_DIR` overrides that.

Both scripts reach the instances with
`docker compose … exec -T redis-cli --user ops`. The password travels in
`REDISCLI_AUTH`, never in an argv. `REDIS_STATE_CLI` and `REDIS_CACHE_CLI`
replace that command, for a test against other servers.

In CI, `.github/workflows/infra.yml` runs on any change under `deploy/infra/`.
It loads `tests/acl/` and runs the checks, then three negative tests:
- an over-broad line must fail the checks;
- an invalid line must change nothing;
- a `WAITAOF`-confirmed key must survive `docker compose restart redis-state`.

## Changing the image

The tag and digest in `compose.yaml` change only in a pull request of their
own. To bump them:
1. Pick the newest `7.4.<patch>-alpine` at least two weeks old.
2. Take its index digest from Docker Hub (the `Docker-Content-Digest` of the
   tag's manifest list).
3. Set both `image:` lines to it.
