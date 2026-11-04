# deploy/infra — the infrastructure project

Compose project `mosaiq-infra` (ADR-0040): the two Redis instances of
[ADR-0037](../../docs/adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md),
`redis-state` and `redis-cache`, and the MongoDB server of
[ADR-0038](../../docs/adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md),
`mongodb`. The specifications are
[`docs/datastores/redis-design.md`](../../docs/datastores/redis-design.md)
(keys, TTLs, ACL rules and failure behaviour) and
[`docs/datastores/mongodb-design.md`](../../docs/datastores/mongodb-design.md)
(the one database, its two collections and who may reach them).

| File | What it is |
|---|---|
| `compose.yaml` | All three: pinned images, no published port, the `mosaiq` network, `mem_limit` 96 MB, 160 MB and 512 MB, and the Redis AOF and MongoDB data volumes |
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

   MongoDB's root account, the same way, in a file that compose reads for
   every command of this project, so it must exist before the first one. The
   password goes through a pipe, never an argv. `MOSAIQ_MONGO_ROOT_ENV` points
   elsewhere for a test.

   ```bash
   install -d -m 0700 /etc/mosaiq/mongodb
   ( umask 077
     { echo MONGO_INITDB_ROOT_USERNAME=root
       printf MONGO_INITDB_ROOT_PASSWORD=
       head -c 24 /dev/urandom | base64 | tr -d '/+=\n'; echo
     } > /etc/mosaiq/mongodb/root.env )
   ```

3. The aclfile must exist before the instances start, because each loads it at
   boot:

   ```bash
   REDIS_OPS_PASSWORD=… deploy/infra/redis/assemble-acl.sh --write-only
   ```

   With no service installed, it holds only `ops` and the disabled `default`
   user (off, with the rules AOF replay needs, ADR-0056). It lives in `/etc/mosaiq/redis/users.acl`, a directory that both
   containers mount read-only. `MOSAIQ_REDIS_ACL_DIR` points elsewhere for a
   test.

4. Start the project:

   ```bash
   docker compose -p mosaiq-infra -f deploy/infra/compose.yaml up -d
   ```

   On its first start into an empty volume, `mongodb` creates the root user
   from that file, on a server that listens only inside the container, then
   starts the real server with `--auth`. Later starts read the file but change
   nothing: the user lives in the volume.

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

## MongoDB

The server holds no application database until engagement-service's
`services/engagement/mongo/init.js` (F14-24) creates `engagement` and its user
`svc_engagement`. An administrator runs that file as root before the
engagement version that needs it, as a schema section is applied before its
service (ADR-0040). No service signs in as root.

Sign in as root from inside the container, so the password stays in the
container's environment and never reaches an argv:

```bash
docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -e HOME=/tmp mongodb \
  mongosh --quiet --norc --eval 'db.getSiblingDB("admin").auth(
    process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD)' --shell
```

`HOME=/tmp` keeps mongosh's history and logs out of the data volume.

Applying engagement's file, as root, with the password it reads from the
environment (it exits non-zero when the variable is unset, and a second run
changes nothing):

```bash
docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T -e HOME=/tmp \
  -e MONGO_ENGAGEMENT_PASSWORD mongodb mongosh --quiet --norc --eval \
  "db.getSiblingDB('admin').auth(process.env.MONGO_INITDB_ROOT_USERNAME,
    process.env.MONGO_INITDB_ROOT_PASSWORD); $(cat services/engagement/mongo/init.js)"
```

`mongo/seed.js` loads the demonstration events the same way. The maintenance
command that rebuilds the rollup runs in the service's own container; its
service sheet, `docs/services/engagement.md`, has it under Operations.

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

For MongoDB it checks that the server publishes no port either, and that a
client on the `mosaiq` network without credentials has `listDatabases`
refused, while root sees authorization on, a 0.25 GB cache and no application
database.

## Changing an image

A tag and digest in `compose.yaml` change only in a pull request of their
own. To bump them:
1. Pick the newest patch at least two weeks old: `7.4.<patch>-alpine` for
   Redis, `7.0.<patch>` for MongoDB.
2. Take its index digest from Docker Hub (the `Docker-Content-Digest` of the
   tag's manifest list).
3. Set the `image:` lines to it: both Redis lines, or the one MongoDB line.
