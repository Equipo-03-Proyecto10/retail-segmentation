# F1-04 / F1-05 — Database access and the application role

Evidence for [#52](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/52)
and [#53](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/53).
Two claims, both about the same instance: PostgreSQL is reachable only over
loopback, and the role the application connects as can move rows but cannot
touch the schema.

The procedure is [`deploy/postgresql/README.md`](../../deploy/postgresql/README.md).
Every check below is read-only or rolls back; the probes that write run inside a
transaction that ends in `ROLLBACK`.

## How this run was produced

`mosaiq-deployment-vm` (CentOS Stream 10, project `iac-dev-01`, zone
`northamerica-south1-a`), PostgreSQL 18.6 from PGDG, on 2026-09-07 UTC. Checks
on the instance ran over `gcloud compute ssh`; the two rejection checks ran from
a workstation and from an unlisted loopback address respectively.

One change was applied to the instance for this story:
[`deploy/postgresql/mosaiq.conf`](../../deploy/postgresql/mosaiq.conf) was
installed as a `conf.d` drop-in and PostgreSQL was reloaded. Nothing else on the
instance was modified. `postgresql.conf` was backed up first, and the only edit
to it was one appended `include_dir = 'conf.d'` line.

---

# F1-04 — controlled remote access

## The settings are stated, not inherited

The stock `postgresql.conf` ships these three commented out, so before this
story the running values were defaults that nobody had chosen and the
repository recorded no decision. They are now written in a drop-in that is
under version control:

```conf
listen_addresses = 'localhost'          # resolves to 127.0.0.1 and ::1
port = 5432
password_encryption = 'scram-sha-256'
```

After the reload:

```
        name         |    setting    |       source       |                sourcefile                 | sourceline | pending_restart
---------------------+---------------+--------------------+-------------------------------------------+------------+-----------------
 listen_addresses    | localhost     | default            |                                           |            | f
 password_encryption | scram-sha-256 | configuration file | /var/lib/pgsql/18/data/conf.d/mosaiq.conf |         22 | f
 port                | 5432          | default            |                                           |            | f
```

Two of the three still say `default`, and that is expected rather than a
failure. `listen_addresses` and `port` are postmaster-context settings: a reload
does not re-evaluate them, so their recorded source only changes when the server
next restarts. `password_encryption` is not, so it switched immediately and
proves the drop-in is being read. Because the file states the values already in
effect, nothing is pending:

```
SELECT count(*) FROM pg_settings WHERE pending_restart;  -->  0
```

What the next start will read, obtained without touching the running server —
this parses the whole configuration, so it is also the check that a restart
would succeed:

```
listen_addresses     localhost
port                 5432
password_encryption  scram-sha-256
```

## Only the intended addresses are permitted

`pg_hba.conf`, as PostgreSQL itself parses it. Six rules, no parse errors, and
no TCP rule outside the two loopback addresses:

```
 n | type  |   database    | user_name |  address  |                 netmask                 |  auth_method  | error
---+-------+---------------+-----------+-----------+-----------------------------------------+---------------+-------
 1 | local | {all}         | {all}     |           |                                         | peer          |
 2 | host  | {all}         | {all}     | 127.0.0.1 | 255.255.255.255                         | scram-sha-256 |
 3 | host  | {all}         | {all}     | ::1       | ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff | scram-sha-256 |
 4 | local | {replication} | {all}     |           |                                         | peer          |
 5 | host  | {replication} | {all}     | 127.0.0.1 | 255.255.255.255                         | scram-sha-256 |
 6 | host  | {replication} | {all}     | ::1       | ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff | scram-sha-256 |
```

The `error` column is empty on every row, which is the part worth reading: a
malformed rule is skipped rather than refused, so a silent parse error would
leave a policy that looks stricter on paper than the one in force.

`all` in the `database` and `user_name` columns lets a role *attempt*
authentication; what it may then do is a matter of privileges, evidenced under
F1-05 below. `retail_app` has no replication privilege, so rules 4–6 are not
reachable by it. The `local` `peer` rules are what keeps `sudo -u postgres psql`
working for administration.

And the sockets actually bound, which is the claim the configuration text can
only imply:

```
State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
LISTEN 0      200        127.0.0.1:5432      0.0.0.0:*    users:(("postgres",pid=1063,fd=8))
LISTEN 0      200            [::1]:5432         [::]:*    users:(("postgres",pid=1063,fd=7))
```

Both address families, neither of them a public interface.

## A connection from an unlisted address is refused

Two different layers refuse it, and it is worth separating them because only
one of the two is this story's work.

**From a workstation, to the instance's public address.** The GCP firewall
permits `tcp:22`, `tcp:80` and `tcp:443` only (`docs/infra.md`), so the packets
never arrive:

```
$ psycopg.connect(host="34.51.123.31", port=5432, user="retail_app", ...)
refused: connection timeout expired
```

That is the firewall's doing, not PostgreSQL's. On its own it would still be
true if `listen_addresses` were `*` — which is exactly why the second check
exists.

**From an unlisted source address, on the instance itself.** Binding
`127.0.0.2` and sending nothing but a startup packet — no password, no SQL —
isolates the HBA decision from the firewall:

```
response type: E (ErrorResponse)
  severity: FATAL
  SQLSTATE: 28000
   message: no pg_hba.conf entry for host "127.0.0.2", user "retail_app", database "retail", no encryption
```

`127.0.0.2` is a loopback address, so it reached the server and the server
refused it. This is the check that would fail if the HBA policy were widened,
and it fails independently of anything GCP is configured to do.

## The access path that does work

`deploy/postgresql/README.md` documents SSH forwarding as the way a person
reaches the database, so that path is exercised too rather than only described.
Through the tunnel, using a deliberately wrong password so the real credential
stays on the instance:

```
$ gcloud compute ssh mosaiq-deployment-vm ... -- -N -L 127.0.0.1:55432:127.0.0.1:5432
$ psycopg.connect(host="127.0.0.1", port=55432, user="retail_app", password="not-the-real-password", ...)
connection failed: FATAL:  password authentication failed for user "retail_app"
```

PostgreSQL answered, which proves the tunnel reaches the loopback listener, and
it refused, which proves SSH access alone is not database access — SCRAM is
still enforced on top of it.

---

# F1-05 — the application role has least privilege

## What the role is

`retail_app` holds no administrative flag, and is not the owner of anything:

```
  rolname   | rolsuper | rolcreatedb | rolcreaterole | rolreplication | rolbypassrls | rolcanlogin
------------+----------+-------------+---------------+----------------+--------------+-------------
 postgres   | t        | t           | t             | t              | t            | t
 retail_app | f        | f           | f             | f              | f            | t

 tableowner | tables
------------+--------
 postgres   |     19

           privileges           | tables
--------------------------------+--------
 DELETE, INSERT, SELECT, UPDATE |     19

 db_create | schema_create | schema_usage
-----------+---------------+--------------
 f         | f             | t
```

All 19 tables are owned by `postgres`; `retail_app` has the four DML privileges
on all 19 and nothing else — no `TRUNCATE`, no `CREATE` on the database or the
schema. It can use the schema and change rows in it, which is the whole of what
the application does.

## `DROP TABLE` is refused, reads and writes succeed

Both criteria are checked by the same opt-in section at the end of
[`sql/01_schema.sql`](../../sql/01_schema.sql), which must be run connected
directly as `retail_app`. Against the live instance, over both loopback address
families:

```
('retail_app', 'retail_app', IPv4Address('127.0.0.1'), 5432)
PASS: restricted role, no ownership or CREATE, DML on all tables
PASS: DROP TABLE inventory refused (SQLSTATE 42501)
PASS: SELECT, INSERT, UPDATE, DELETE and audit sequence access
PASS: probe rows rolled back
('retail_app', 'retail_app', IPv6Address('::1'), 5432)
PASS: restricted role, no ownership or CREATE, DML on all tables
PASS: DROP TABLE inventory refused (SQLSTATE 42501)
PASS: SELECT, INSERT, UPDATE, DELETE and audit sequence access
PASS: probe rows rolled back
```

`42501` is `insufficient_privilege`, raised by PostgreSQL — the `DROP` is
attempted for real and caught, not asserted about. The write probe is not a bare
`INSERT`: it inserts, updates, reads its own update back, deletes, and then
requires that the audit trigger recorded all three actions, because the trigger
writes to `audit_log` through a sequence and a role that could write rows but
not use that sequence would fail here rather than in production.

The last line matters as much as the others. The probes run inside a
transaction that ends in `ROLLBACK`, so the check leaves the database as it
found it — confirmed by re-reading the probe row after the transaction closed.
Had the `DROP` unexpectedly succeeded, it would have rolled back too.

## `.env.example` documents the restricted role

```
# PostgreSQL. The application connects as a restricted role, never as the role
# that owns the schema — see docs/backlog.md F1-05.
# On the VM use localhost:5432 and retail_app as below, with its real password.
# PostgreSQL stays on loopback; workstation access uses an SSH tunnel, not a
# public database listener. Verification: deploy/postgresql/README.md.
DATABASE_URL=postgresql://retail_app:retail_app@localhost:5432/retail
```

The role named is `retail_app`, not the owner. The password shown is the local
development default that `sql/00_create_database.sql` falls back to; the
instance uses a different one, held in `/etc/mosaiq/mosaiq.env` (`0640
root:mosaiq`) and never written to the repository.

---

## The three scripts still run clean from empty

Definition of Done item 3, re-checked because this story appended to
`sql/01_schema.sql`. A throwaway cluster on the instance, initialised empty:

```
00_create_database.sql   exit 0
01_schema.sql            exit 0
02_seed_30_per_table.sql exit 0

19 tables in public
0 rows with the probe category_id
```

The second pair of lines is the point of running it this way: a plain schema
run did **not** execute the verification section, because it is guarded by a
psql variable that only the verification path sets. The section is inside
`01_schema.sql` rather than in a file of its own so that all table DDL — the
rejected `DROP` included — stays in the one file the repository allows it in.

Running the section explicitly against that same fresh database, as `retail_app`:

```
BEGIN
SET
SET
NOTICE:  PASS: restricted role, no ownership or CREATE, DML on all tables
NOTICE:  PASS: DROP TABLE inventory refused (SQLSTATE 42501)
NOTICE:  PASS: SELECT, INSERT, UPDATE, DELETE and audit sequence access
DO
ROLLBACK
exit 0
```

Afterwards `inventory` still existed and the probe row was gone. CI runs this
same extraction after its clean seed, so a future grant that quietly widens the
application role's privileges fails the build rather than waiting to be noticed
on the instance.

## The instance after the change

```
postgresql-18   active
mosaiq          active
nginx           active
HTTPS status: 200
live database reachable, 19 tables
```

## Not evidenced here

- **A restart of PostgreSQL.** The drop-in states the values already in effect,
  so no restart was owed and none was taken; the running server therefore still
  attributes `listen_addresses` and `port` to `default`. The configuration was
  parsed with `postgres -C` instead, which is what a restart would read.
- **The instance's real `retail_app` password.** Every check here either ran on
  the instance using the credential already in `/etc/mosaiq/mosaiq.env`, or used
  a wrong password deliberately. Rotation is not part of these two stories.
- **Backups, and PostgreSQL's own TLS.** Connections are loopback-only, so
  `ssl = off` is not a gap for this topology; a change that moved the database
  off this instance would make it one.
