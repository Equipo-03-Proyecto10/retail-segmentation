# Infrastructure

Record of the provisioned GCP resources. Updated as later F1 stories add
firewall rules, PostgreSQL, and the reverse proxy.

## Compute Engine instance

| Field | Value |
|---|---|
| Name | `mosaiq-deployment-vm` |
| Project | `iac-dev-01` |
| Zone | `northamerica-south1-a` (Querétaro, Mexico — closest GCP region to the team) |
| Machine type | `e2-standard-2` (2 vCPU, 8 GB RAM) |
| Boot disk | 50 GB, `pd-balanced` |
| Image | `centos-stream-10` (project `centos-cloud`) |

Created for `F1-01`.

## Network access

The instance has the `mosaiq-server` network tag. Its ingress policy uses
higher-priority exceptions for the two required public ports followed by an
explicit deny rule:

| Rule | Priority | Source | Action |
|---|---:|---|---|
| `mosaiq-allow-ssh` | 900 | `0.0.0.0/0` | Allow `tcp:22` |
| `mosaiq-allow-http` | 900 | `0.0.0.0/0` | Allow `tcp:80` |
| `mosaiq-deny-other-ingress` | 1000 | `0.0.0.0/0` | Deny all other ingress |

The project default network is shared with the terminated `webdev-centos`
instance, so its pre-existing default rules were not changed. For
`mosaiq-deployment-vm`, the rules above take precedence over those default
rules, which have priority 65534. This limits effective ingress to SSH and
HTTP without changing the other instance's policy.

## SSH access

OS Login is enabled on `mosaiq-deployment-vm` through the instance metadata
value `enable-oslogin=TRUE`. It is not enabled project-wide. Each team member
has `roles/compute.osAdminLogin` on project `iac-dev-01` and
`roles/iam.serviceAccountUser` on the instance's attached service account.

| Team account | Configuration | Shell verification |
|---|---|---|
| `cloudcompute97@gmail.com` | Configured | Verified on 2026-09-03 |
| `maximiliano.rubio@udem.edu` | Configured | Verified by member on 2026-09-03 |
| `estefania.najera@udem.edu` | Configured | Verified by member on 2026-09-03 |
| `raquel.delagarzav@udem.edu` | Configured | Verified by member on 2026-09-03 |

Each member verifies their own identity and shell with:

```sh
gcloud auth login MEMBER_EMAIL
gcloud config set project iac-dev-01
gcloud compute ssh mosaiq-deployment-vm \
  --zone=northamerica-south1-a \
  --command="whoami && hostname"
```

The expected hostname is `mosaiq-deployment-vm`. Every member completed the
personal verification for `F1-02` on 2026-09-03.

## PostgreSQL

Installed from the official PGDG repository, not the CentOS default packages
(`postgresql1*` from AppStream) and not a managed service (Cloud SQL,
AlloyDB) — see constraint C-6 in [`scope.md`](scope.md).

| Field | Value |
|---|---|
| Repository | `pgdg-redhat-repo` (`https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/`) |
| Package | `postgresql18-server` |
| Version | PostgreSQL 18.6 |
| Data directory | `/var/lib/pgsql/18/data/` |
| Service | `postgresql-18.service`, `enabled` (starts on boot), running under `systemd` as the `postgres` OS user |
| Listener | `127.0.0.1:5432` and `[::1]:5432` only — no external interface bound |

Verified for `F1-03` on 2026-09-05:

```
$ psql --version
psql (PostgreSQL) 18.6

$ systemctl is-enabled postgresql-18
enabled
```

### Database, schema and the application role

Provisioned on 2026-09-06, when `F6-02` needed a database to start against.
`postgresql18-contrib` had to be installed first — `sql/00_create_database.sql`
creates the `pg_trgm` extension and the base install does not ship it.

The three committed scripts ran in order against the empty server, unmodified:

```
psql -v app_password=… -f sql/00_create_database.sql
psql -d retail -f sql/01_schema.sql
psql -d retail -f sql/02_seed_30_per_table.sql
```

| Field | Value |
|---|---|
| Database | `retail`, `UTF8`, `template0` |
| Extensions | `pg_trgm` |
| Application role | `retail_app`, `LOGIN`, password generated on the instance |
| Its privileges | `SELECT, INSERT, UPDATE, DELETE` only — verified against `information_schema.table_privileges`. No `CREATE`, no `ALTER`, no ownership |
| Seed | 19 tables loaded; `transaction_line` 600, `transaction` 300, `inventory` 150 rows |

The listener is unchanged: `127.0.0.1:5432` and `[::1]:5432` only. The
application runs on the same host, so it reaches PostgreSQL over loopback and
nothing needs to be exposed — which is what `F1-04` asks for on this topology.
The credential lives only in `/etc/mosaiq/mosaiq.env` (`0640 root:mosaiq`); it
is not in the repository and not in any shell history.

## Reverse proxy

NGINX in front of the application — decision [ADR-0009](adr/0009-nginx-as-the-reverse-proxy.md),
story `F6-01` (#77). Config and runbook: [`deploy/`](../deploy/README.md).

| Field | Value |
|---|---|
| Package | `nginx` (CentOS Stream 10 AppStream) |
| Config | `/etc/nginx/conf.d/mosaiq.conf`, from `deploy/nginx/mosaiq.conf` |
| Listener | `:80` redirects to HTTPS; `:443 ssl http2` `default_server`, `server_name _` |
| Upstream | `127.0.0.1:8000` (gunicorn — `F6-02`, #78) |
| SELinux | `httpd_can_network_connect` set to `1` |
| TLS | `/etc/nginx/tls/mosaiq.{crt,key}` — Let's Encrypt where the host has a real name, self-signed otherwise (`F6-03`, #79). Depends on Q-2 (`scope.md` §8). Needs GCP rule `mosaiq-allow-https` for `tcp:443`. |
| HSTS | `max-age=31536000` on HTTPS responses |

Applied on `mosaiq-deployment-vm` on 2026-09-06. `nginx/1.26.3`,
`systemctl is-enabled nginx` → `enabled`. The stock `server {}` block in
`/etc/nginx/nginx.conf` is commented out so ours is the only `default_server`;
the untouched original is kept at `/etc/nginx/nginx.conf.orig`.

`F6-01` acceptance checks:

```
curl -sI http://34.51.123.31/          -> HTTP/1.1 301, Location: https://…
curl -skI https://34.51.123.31/        -> HTTP/2 200      (MOSAIQ landing page)
curl -m6 http://34.51.123.31:8000/     -> timed out       (gunicorn is loopback-only)
```

`F6-03` — the certificate is **self-signed**, `CN=34.51.123.31` with
`subjectAltName=IP:34.51.123.31`, valid to 2027-09-06. This is Path B in
[`deploy/README.md`](../deploy/README.md): the instance has no DNS name, and
Let's Encrypt does not issue for a bare IP. A browser therefore warns.
**Q-2 (`scope.md` §8) is still what stands between this and a certificate that
is valid for the published host** — when the assigned hostname is confirmed and
points here, re-issue with certbot and set `server_name`.

## Application service

gunicorn under `systemd` — story `F6-02` (#78), [ADR-0006](adr/0006-run-under-both-systemd-and-docker-compose.md).
Unit and runbook: [`deploy/`](../deploy/README.md).

| Field | Value |
|---|---|
| Unit | `/etc/systemd/system/mosaiq.service`, from `deploy/systemd/mosaiq.service` |
| Type | `notify`, `Restart=on-failure`, `WantedBy=multi-user.target` |
| Runs as | user `mosaiq` (unprivileged), `WorkingDirectory=/opt/mosaiq/current` |
| Binds | `127.0.0.1:8000` — reachable only through NGINX |
| Environment | `/etc/mosaiq/mosaiq.env` (`FLASK_ENV=production`, real secret, `TRUSTED_PROXY_HOPS=1`) |
| Logs | `journald` (`journalctl -u mosaiq`) |

Applied on `mosaiq-deployment-vm` on 2026-09-06. `gunicorn 23.0.0` under
`/opt/mosaiq/venv`, `systemctl is-enabled mosaiq` → `enabled`.

`F6-02` acceptance checks:

```
systemctl show -p MainPID --value mosaiq   -> 104831
sudo kill -9 104831 ; sleep 8
systemctl is-active mosaiq                 -> active     (new MainPID 105042)
curl -skI https://localhost/               -> HTTP/2 200

# after a full reboot, with nobody logged in (`who` -> 0):
systemctl is-active mosaiq                 -> active
systemctl is-active nginx                  -> active
curl -skI https://34.51.123.31/            -> HTTP/2 200
```
