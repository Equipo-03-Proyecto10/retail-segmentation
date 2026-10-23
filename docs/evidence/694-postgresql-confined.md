# #694: PostgreSQL runs confined on the instance

F18-01 found the PGDG server running unconfined. Its binary,
`/usr/pgsql-18/bin/postgres`, was labelled `bin_t`, so systemd started it as
`unconfined_service_t` ([f18-01-runtime.md](f18-01-runtime.md)). This records
how it was moved into the policy's own `postgresql_t` domain, on
`mosaiq-deployment-vm` on 2026-10-23, with the web and all fifteen services
running. The procedure is in
[deploy/postgresql/README.md](../../deploy/postgresql/README.md), "The server
runs confined".

All times are UTC.

## What changed on the instance

| Before | After |
|---|---|
| `/usr/pgsql-18/bin/postgres` labelled `bin_t` | `postgresql_exec_t`, by the local rule `semanage fcontext -a -t postgresql_exec_t '/usr/pgsql-[0-9]+/bin/postgres'` |
| The server ran as `system_u:system_r:unconfined_service_t:s0` | All 32 `postgres` processes run as `system_u:system_r:postgresql_t:s0` |
| The server's logs labelled `postgresql_db_t` | `postgresql_log_t`, which the policy's `/var/lib/pgsql/.*\.log` rule names, applied by `restorecon -Rv /var/lib/pgsql` |
| `mosaiq_svc_pgsocket` 1.1 let containers connect to `postgresql_t` and to `unconfined_service_t` | 1.2 names `postgresql_t` only |

Nothing else needed a label. The data directory was already `postgresql_db_t`,
the socket directory `postgresql_var_run_t`, and PGDG's libraries,
`pg_trgm.so` and `btree_gist.so` among them, `lib_t`.

## How it was done

1. **Permissive first.** `semanage permissive -a postgresql_t`. In that state
   the policy logs what it would refuse instead of refusing it, so a missing
   permission could not take the database down.
2. **Relabel and restart.** The label went on with the local rule and
   `restorecon`, then `systemctl restart postgresql-18` ran at 03:18:17.371.
   The restart took **243 ms**.
3. **Exercise the server,** while permissive:
   - the web, sampled every second;
   - all fifteen services' `/health/ready`;
   - `similarity()` from `pg_trgm`, and `btree_gist`'s operator classes;
   - a read of `customer`, a `CHECKPOINT` and `pg_switch_wal()`;
   - `pg_dump --schema-only`, `systemctl reload postgresql-18` and `pg_reload_conf()`;
   - `VACUUM (ANALYZE) customer`, and one timed checkpoint and the autovacuum launcher over the next eight minutes.
4. **Read the audit log.** It held no AVC naming `postgresql_t` between the
   restart and 03:26:31. The PostgreSQL log after the restart held no error
   but the 14 `terminating connection due to administrator command` lines of
   the restart itself.
5. **Enforce.** At 03:26:31, `semanage permissive -d postgresql_t` was run,
   which needs no restart. Then `mosaiq_svc_pgsocket` 1.2 was built and loaded.

## Results

- **The web.** 551 samples from 03:17:46 to 03:28:32, every one of them 200
  with `mosaiq` active. One sample fell inside the restart: PostgreSQL was
  `deactivating`, and the web still answered 200.
- **The services.** All fifteen answered 204 within **1 s** of the restart,
  with no restart of their own.
  - The Python services' pools discarded the dead connections.
  - The Rust services' pools recycled them.
  - The web opens a connection per request.
- **After enforcing and module 1.2:**
  - a new connection from the users container signed in as `svc_users`;
  - `mosaiq-svc@stock`, restarted from scratch, came back ready (204), and all fifteen were ready;
  - `deploy/check-services.sh` printed nothing, and `getenforce` printed `Enforcing`.

## The one denial, and what it explained

After enforcing, the audit log holds exactly one AVC naming `postgresql_t`:

```text
avc:  denied  { name_connect } for  pid=131851 comm="postgres" dest=80
  scontext=system_u:system_r:postgresql_t:s0
  tcontext=system_u:object_r:http_port_t:s0 tclass=tcp_socket permissive=0
proctitle: postgres: postgres retail [local] authentication
```

- **What made it.** It is check 6 of `check-services.sh`: a throwaway
  container with UID 10001 tries to sign in as `postgres` over `peer`.
  - To resolve UID 10001, PostgreSQL calls NSS.
  - On this image, `/etc/nsswitch.conf` reads `passwd: files systemd
    cache_oslogin oslogin`, from `google-compute-engine-oslogin`.
  - For a UID it does not know, the OS Login module asks GCP's metadata
    server, over HTTP on port 80.
- **Why it changes nothing.** The lookup fails, which is the outcome it had
  before: `local user with ID 10001 does not exist`, then `Peer authentication
  failed for user "postgres"`. That refusal is the one check 6 exists to see.
  The service roles sign in through their `scram-sha-256` lines and resolve no
  UID, and the web signs in over TCP. Nothing else asks for this lookup.
- **Why it stays refused.** A database server has no business making HTTP
  requests. No rule was added to allow the connection, and no `dontaudit` rule
  was added to hide it.

The same lookup explains the flood that F18-01 recorded but did not
investigate: 111,496 `name_connect` denials for `auditd_t`.
- `auditd` writes in `log_format = ENRICHED`, so it resolves about nine UID and
  GID fields of each record.
- While the services were refused the socket, every refused retry came from
  UID 10001. Each lookup reached OS Login's HTTP call, which the policy refuses
  to `auditd_t`.
- The ratio fits: about twelve to one against the 9,291 container denials.
- The flood stopped with the container denials, at 01:52:38.

## What was not done

- **No reboot.** The rule is persistent (`semanage fcontext -l -C`), and the
  permissive domain was removed with `semanage`, so both survive one. A
  reboot was not run to show it.
- **Kept unchanged:**
  - the other PGDG binaries (`pg_ctl`, `initdb`, `pg_dump`) keep `bin_t`, because systemd runs only `postgres`;
  - `postgresql-18-check-db-dir`, the unit's `ExecStartPre`, still runs unconfined, as a one-shot check.
