# PostgreSQL access and application privileges

F1-04 (#52) and F1-05 (#53) use the single-VM topology already required by
[`scope.md`](../../docs/scope.md) §3. Flask reaches PostgreSQL over loopback.
Workstation access, when needed, goes through SSH to that same loopback
listener. No database port needs to be opened in GCP or firewalld.

## The listener and authentication settings

On the PGDG PostgreSQL 18 installation, `postgresql.conf` and `pg_hba.conf` are
in `/var/lib/pgsql/18/data/`. The stock `postgresql.conf` ships every setting
this story is about commented out, so the running values were inherited
defaults and nothing recorded a decision. [`mosaiq.conf`](mosaiq.conf) states
them, and is applied as a drop-in rather than by editing the packaged file:

```conf
listen_addresses = 'localhost'
port = 5432
password_encryption = 'scram-sha-256'
```

The values are the same ones PostgreSQL defaults to. Writing them down is the
point: reading the configuration now shows a choice, and a package upgrade
cannot move the listener without the change appearing in this repository.

### Applying it, once per instance

```sh
sudo cp -p /var/lib/pgsql/18/data/postgresql.conf \
  /var/lib/pgsql/18/data/postgresql.conf.bak-$(date -u +%Y%m%dT%H%M%SZ)
sudo install -d -o postgres -g postgres -m 750 /var/lib/pgsql/18/data/conf.d
sudo install -o postgres -g postgres -m 600 deploy/postgresql/mosaiq.conf \
  /var/lib/pgsql/18/data/conf.d/mosaiq.conf
sudo restorecon -Rv /var/lib/pgsql/18/data/conf.d
printf "\ninclude_dir = 'conf.d'\n" \
  | sudo tee -a /var/lib/pgsql/18/data/postgresql.conf
sudo systemctl reload postgresql-18
```

SELinux is enforcing on this CentOS instance, so a file copied out of `/tmp`
carries the wrong label until `restorecon` runs and PostgreSQL cannot read it.
Append the `include_dir` line only once; it is already present on the instance.

### Why two of the three still report `default`

`listen_addresses` and `port` are postmaster-context settings. A reload does
not re-evaluate them, so `pg_settings.source` keeps reporting `default` for
those two until the server is next restarted, while `password_encryption`
switches to `configuration file` immediately. Because the drop-in states the
values already in effect, nothing is pending — `SELECT count(*) FROM
pg_settings WHERE pending_restart` returns `0` — and no restart is owed.

Confirm what the next start will read, without touching the running server:

```sh
for p in listen_addresses port password_encryption; do
  printf '%-20s %s\n' "$p" \
    "$(sudo -u postgres /usr/pgsql-18/bin/postgres -D /var/lib/pgsql/18/data -C $p)"
done
```

That also parses the whole configuration, so it is the check that a restart
would succeed. Run it after any edit here.

`localhost` resolves to `127.0.0.1` and `::1` on this VM. Confirm the actual
listeners, including both address families, rather than inferring them from
the configuration text alone.

The installed `pg_hba.conf` permits only local sockets and the two exact
loopback addresses:

```conf
local   all             all                             peer
host    all             all     127.0.0.1/32             scram-sha-256
host    all             all     ::1/128                  scram-sha-256
local   replication     all                             peer
host    replication     all     127.0.0.1/32             scram-sha-256
host    replication     all     ::1/128                  scram-sha-256
```

`all` allows a role to attempt authentication; its database privileges still
control what it may do. `retail_app` has no replication privilege. The local
`peer` rule preserves administration through `sudo -u postgres psql`.
Unlisted source addresses have no matching rule and are refused, as defined
by [PostgreSQL's HBA rules](https://www.postgresql.org/docs/18/auth-pg-hba-conf.html).

Read-only checks on the VM:

```sh
sudo -u postgres psql -X -d retail \
  -c 'SHOW listen_addresses' -c 'SHOW port' \
  -c 'TABLE pg_hba_file_rules'
sudo ss -lntp 'sport = :5432'
```

The HBA view must contain no parse errors or non-loopback TCP allowances.
From a workstation, the following must time out or be refused:

```sh
PGCONNECT_TIMEOUT=5 psql -X -w -h 34.51.123.31 -U retail_app -d retail
```

To test HBA independently of the external firewall, run this on the Linux VM.
It binds an unlisted loopback source (`127.0.0.2`) and sends only a PostgreSQL
startup packet, with no password or SQL:

```sh
python3 - <<'PY'
import socket
import struct

with socket.socket() as connection:
    connection.settimeout(5)
    connection.bind(('127.0.0.2', 0))
    connection.connect(('127.0.0.1', 5432))
    body = struct.pack('!I', 196608) + b'user\0retail_app\0database\0retail\0\0'
    connection.sendall(struct.pack('!I', len(body) + 4) + body)
    with connection.makefile('rb') as response:
        kind = response.read(1)
        length = struct.unpack('!I', response.read(4))[0]
        error = response.read(length - 4)
    assert kind == b'E' and b'C28000\0' in error, repr(error)
    assert b'no pg_hba.conf entry' in error and b'127.0.0.2' in error, repr(error)
    print('PASS: unlisted source 127.0.0.2 refused by HBA (SQLSTATE 28000)')
PY
```

For authorized workstation access, keep the tunnel in a separate terminal:

```sh
gcloud compute ssh mosaiq-deployment-vm --project=iac-dev-01 \
  --zone=northamerica-south1-a -- -N -L 127.0.0.1:55432:127.0.0.1:5432
psql -X -W -h 127.0.0.1 -p 55432 -U retail_app -d retail
```

Use the instance credential through the password prompt; the local development
password from `.env.example` does not apply to the VM. SSH controls who can
reach the tunnel, and PostgreSQL still requires SCRAM authentication.

### The server runs confined (#694)

PGDG installs the server as `/usr/pgsql-18/bin/postgres`. The targeted
policy's rule names `/usr/bin/postgres`, so the PGDG binary is labelled
`bin_t`, and systemd starts it as `unconfined_service_t`: enforcing, but
outside the policy. One local file-context rule gives it `postgresql_exec_t`,
and it starts as `postgresql_t`, the domain the policy writes for it. The data
directory (`postgresql_db_t`), the socket directory (`postgresql_var_run_t`)
and PGDG's libraries (`lib_t`) need no change of their own. `restorecon` also
relabels the server's logs as `postgresql_log_t`.

```sh
sudo semanage fcontext -a -t postgresql_exec_t '/usr/pgsql-[0-9]+/bin/postgres'
sudo restorecon -v /usr/pgsql-18/bin/postgres
sudo restorecon -Rv /var/lib/pgsql
sudo systemctl restart postgresql-18
ps -eZ | awk '$NF == "postgres" {print $1}' | sort -u   # system_u:system_r:postgresql_t:s0
```

- **The rule names any major version,** so `rpm` labels a new one's binary the
  same way when it installs it.
- **The first time on a running instance, start permissive.** Run
  `sudo semanage permissive -a postgresql_t` before the restart. The policy
  then logs what it would refuse instead of refusing it. Exercise the server,
  read `/var/log/audit/audit.log*` for `postgresql_t`, then run
  `sudo semanage permissive -d postgresql_t`, which needs no restart.
- **The restart costs a second or less.**
  - The web opens a connection per request, so it is unaffected.
  - A Python service's pool discards a dead connection after one failed
    request. A Rust service's pool recycles closed connections.
  - On 2026-10-23 the restart took 243 ms, and all fifteen services answered
    ready within 1 s ([evidence](../../docs/evidence/694-postgresql-confined.md)).
- **One denial is expected.** `deploy/check-services.sh`, check 6, tries
  `peer` from a container UID that no host account has. The policy then
  refuses the server's NSS lookup of that UID through GCP OS Login, over HTTP
  on port 80, and the lookup fails as before. One `name_connect` AVC for
  `postgresql_t` per run is that check working, not a missing permission.
- **Rollback:**

  ```sh
  sudo semanage fcontext -d '/usr/pgsql-[0-9]+/bin/postgres'
  sudo restorecon -v /usr/pgsql-18/bin/postgres
  sudo systemctl restart postgresql-18
  ```

  The server is then unconfined again. Containers cannot reach it until
  `deploy/selinux/mosaiq_svc_pgsocket.te` version 1.1 is loaded. That version
  is in commit `6b2d86f`.

### The service roles (ADR-0040, F18-01)

Each service container connects through the read-only mount of
`/run/postgresql` as its own role, with the password in its
`/etc/mosaiq/svc/<name>.env`. It runs as UID 10001, which maps to no host
account, so `peer` would refuse it. Each role therefore gets one
`local retail <role> scram-sha-256` line, placed **above** `local all all peer`
(the first matching line wins):

```conf
local   retail          svc_audit_trail                         scram-sha-256
local   retail          svc_auth                                scram-sha-256
local   retail          svc_campaigns                           scram-sha-256
local   retail          svc_categories                          scram-sha-256
local   retail          svc_checkins                            scram-sha-256
local   retail          svc_customers                           scram-sha-256
local   retail          svc_engagement                          scram-sha-256
local   retail          svc_preferences                         scram-sha-256
local   retail          svc_products                            scram-sha-256
local   retail          svc_recommendations                     scram-sha-256
local   retail          svc_sales                               scram-sha-256
local   retail          svc_segments                            scram-sha-256
local   retail          svc_stock                               scram-sha-256
local   retail          svc_stores                              scram-sha-256
local   retail          svc_users                               scram-sha-256
local   all             all                                     peer
```

These are the fifteen roles `sql/00_create_database.sql` creates, the second
wave's `svc_campaigns` and `svc_audit_trail` included. There is no `host` line
for them, and no line at all for `svc_products_bench`: the bench never runs on
the instance. `listen_addresses` stays `localhost`, and `mosaiq.conf` does not
change.

Apply them with `sudo systemctl reload postgresql-18`, then confirm that
`TABLE pg_hba_file_rules` shows no `error` and that every role's
`line_number` is smaller than the `peer` line's. `deploy/check-services.sh` checks the same thing on
every run, and prints nothing when it holds.

## Verify the restricted role

`sql/00_create_database.sql` creates `retail_app` separately from the owner.
Its default grants give tables DML privileges and sequences `USAGE, SELECT`.
The instance URL lives in `/etc/mosaiq/mosaiq.env` (`0640 root:mosaiq`).
`.env.example` documents the same role with a safe local password.

After the three SQL scripts have run, execute only the opt-in verification
section of `sql/01_schema.sql`, connecting **directly as `retail_app`**:

```sh
sed -n '/^-- BEGIN APPLICATION ROLE VERIFICATION/,$p' sql/01_schema.sql |
  psql -X -W -h localhost -U retail_app -d retail \
    -v ON_ERROR_STOP=1 -v verify_app_role=1
```

Do not rerun the full schema against a populated database. The verification
section lives there to keep all table DDL, including the rejected `DROP`, in
the required file. CI extracts and runs the same section after a clean seed.
It fails unless the role lacks administrative flags, memberships, object
ownership, persistent `CREATE` and `TRUNCATE`, while retaining DML on ordinary
application tables and only `SELECT`/`INSERT` on the append-only audit,
assignment and exposure tables. It also attempts `DROP TABLE inventory`,
rewrites of each append-only table, and a control-group exposure; each must be
refused before it exercises all four DML operations on an audited category.

Every probe rolls back, including an unexpectedly successful `DROP`. Category
ID `32767` must be unused; a collision fails instead of modifying an existing
category. Audit sequence values consumed by the three writes are not reclaimed.
The check uses a two-second lock timeout and a ten-second statement timeout.

Results and review status: [acceptance evidence](../../docs/evidence/f1-04-f1-05-postgresql-access.md).
