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

The instance has the `mosaiq-server` network tag. Its ingress policy is two
higher-priority exceptions followed by an explicit deny rule:

| Rule | Priority | Source | Action |
|---|---:|---|---|
| `mosaiq-allow-ssh` | 900 | `0.0.0.0/0` | Allow `tcp:22` |
| `mosaiq-allow-https` | 900 | Cloudflare's published ranges | Allow `tcp:443` |
| `mosaiq-deny-other-ingress` | 1000 | `0.0.0.0/0` | Deny all other ingress |

The project default network is shared with the terminated `webdev-centos`
instance, so its pre-existing default rules were not changed. None of them
reaches this instance: `default-allow-http` and `default-allow-https` sit at
priority 1000 but target the `http-server` and `https-server` tags, which
`mosaiq-deployment-vm` does not carry — it has only `mosaiq-server` — and the
remaining default rules are priority 65534, below `mosaiq-deny-other-ingress`.
Effective ingress is SSH, and HTTPS from a Cloudflare edge.

### Only the Cloudflare edge reaches the origin (F6-03 hardening, #167)

`mosaiq-allow-https` was added for F6-03 as `0.0.0.0/0`. On 2026-09-07 it was
narrowed to Cloudflare's published ranges and `mosaiq-allow-http` was deleted.

The delivery is served proxied through Cloudflare
([ADR-0012](adr/0012-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)),
the edge does "Always Use HTTPS", and the origin presents a Cloudflare Origin CA
pair that only the edge trusts. Nothing needs to reach the box except an edge on
`:443`. Until this was applied, `https://34.51.123.31/` with the published
hostname as SNI answered `HTTP/2 200` from `nginx/1.26.3` — the edge's filtering,
WAF and real-client-IP were all optional for an attacker who knew the address.

Applied with `cloudcompute97@gmail.com`, which holds
`roles/compute.securityAdmin`; the OS-Login `*.udem.edu` accounts carry only
`compute.osAdminLogin` and cannot change firewall rules:

```sh
CF4=$(curl -s https://www.cloudflare.com/ips-v4 | paste -sd,)

gcloud compute firewall-rules update mosaiq-allow-https \
  --project=iac-dev-01 --source-ranges="$CF4"
gcloud compute firewall-rules delete mosaiq-allow-http --project=iac-dev-01
```

GCP refuses a rule that mixes address families ("Mixture of IPv4 and IPv6 in
the same rule is not allowed"), and the instance is `IPV4_ONLY` — no
`ipv6AccessConfigs`, so a Cloudflare edge can only ever reach the origin over
IPv4. Only the v4 list goes in. If the instance ever gains an IPv6 address, the
v6 ranges need a second rule of their own.

Verified the same day from a host that is not a Cloudflare edge:

| Check | Result |
|---|---|
| `curl --resolve mosaiq.maxthecoder.online:443:34.51.123.31 -ksI https://mosaiq.maxthecoder.online/ --max-time 10` | exit `28`, timed out — it answered `HTTP/2 200` from `nginx/1.26.3` before |
| the same request through the hostname | `HTTP/2 200`, `server: cloudflare` |
| `curl -H 'Host: mosaiq.maxthecoder.online' http://34.51.123.31/` | exit `28`, timed out |
| `http://mosaiq.maxthecoder.online/` | `301` to HTTPS, from the edge |
| `gcloud compute firewall-rules list --filter="name~mosaiq"` | `mosaiq-allow-ssh`, `mosaiq-allow-https`, `mosaiq-deny-other-ingress` — no `mosaiq-allow-http` |

`mosaiq-allow-ssh` is untouched, so OS Login and the F6-06 deploy workflow —
which reaches the instance over `tcp:22`, not through the proxy — are unaffected.
If firewalld on the host still has the `http` service open it is now unreachable
from outside either way; removing it is tidy-up, not a fix:
`sudo firewall-cmd --permanent --remove-service=http && sudo firewall-cmd --reload`.

**When Cloudflare's ranges change, two places need the new list**: this rule and
[`deploy/nginx/cloudflare-real-ip.conf`](../deploy/nginx/cloudflare-real-ip.conf),
the NGINX `real_ip` trust list — the regeneration one-liner is in that file's
header. A stale rule here does not degrade gracefully: an edge in a new range
cannot reach the origin at all.

To restore direct access, recreate the deleted rule as it was — `tcp:80` from
`0.0.0.0/0`, target tag `mosaiq-server`, priority 900, network `default`,
described "F1-02: Allow HTTP access to the MOSAIQ deployment VM" — and set
`mosaiq-allow-https --source-ranges=0.0.0.0/0`.

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

### Access policy and the application role

Stories `F1-04` (#52) and `F1-05` (#53), applied on 2026-09-07. The listener is
unchanged — `127.0.0.1:5432` and `[::1]:5432` only — but it is no longer an
inherited default. The packaged `postgresql.conf` shipped these settings
commented out; they are now stated in a version-controlled drop-in, so reading
the configuration shows a decision and a package upgrade cannot move the
listener without the change appearing in this repository.

| Field | Value |
|---|---|
| Drop-in | `/var/lib/pgsql/18/data/conf.d/mosaiq.conf`, from `deploy/postgresql/mosaiq.conf` (`0600 postgres:postgres`) |
| Settings | `listen_addresses = 'localhost'`, `port = 5432`, `password_encryption = 'scram-sha-256'` |
| Include | one `include_dir = 'conf.d'` line appended to `postgresql.conf`; the original is kept as `postgresql.conf.bak-20260907T002635Z` |
| `pg_hba.conf` | 6 rules — `local … peer`, plus `127.0.0.1/32` and `::1/128` on `scram-sha-256`, for `all` and for `replication`. No parse errors and no non-loopback TCP rule |
| Remote access | SSH port-forwarding to that same loopback listener. No database port is open in GCP or firewalld |

The drop-in states values that were already in effect, so the reload left
nothing pending and no restart was taken. `listen_addresses` and `port` are
postmaster-context settings, so they keep reporting `default` as their source
until the server next restarts; `password_encryption` switched to
`configuration file` immediately.

The credential lives only in `/etc/mosaiq/mosaiq.env` (`0640 root:mosaiq`); it
is not in the repository and not in any shell history.

Runbook: [`deploy/postgresql/README.md`](../deploy/postgresql/README.md).
Acceptance evidence: [`f1-04-f1-05-postgresql-access.md`](evidence/f1-04-f1-05-postgresql-access.md).

## Reverse proxy

NGINX in front of the application — decision [ADR-0009](adr/0009-nginx-as-the-reverse-proxy.md),
story `F6-01` (#77). Config and runbook: [`deploy/`](../deploy/README.md).

| Field | Value |
|---|---|
| Package | `nginx` (CentOS Stream 10 AppStream) |
| Config | `/etc/nginx/conf.d/mosaiq.conf` + `/etc/nginx/conf.d/cloudflare-real-ip.conf`, from `deploy/nginx/` (deploy both — `mosaiq.conf` includes the other) |
| Listener | `:80` redirects to HTTPS; `:443 ssl http2` `default_server`, `server_name mosaiq.maxthecoder.online` |
| Upstream | `127.0.0.1:8000` (gunicorn — `F6-02`, #78) |
| Real client IP | Cloudflare edge ranges trusted (`cloudflare-real-ip.conf`), `real_ip_header CF-Connecting-IP` — the access log and the `X-Forwarded-For` handed to gunicorn carry the visitor, not an edge IP |
| SELinux | `httpd_can_network_connect` set to `1` |
| TLS | `/etc/nginx/tls/mosaiq.{crt,key}` — a **Cloudflare Origin CA** pair on the instance, browsers see Cloudflare's managed edge certificate ([ADR-0012](adr/0012-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md), Path C, `F6-03` #79). Paths A (Let's Encrypt) and B (self-signed) documented as fallbacks. |
| HSTS | `max-age=2592000` (30 days) on HTTPS responses. Raise to `31536000` once Cloudflare's edge certificate has auto-renewed once. No `includeSubDomains`. |

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

### DNS and the published certificate

The instance itself still has no DNS name, but the delivery is published under
one. `mosaiq.maxthecoder.online` is an `A` record for `34.51.123.31` in the
Cloudflare zone `maxthecoder.online` — a domain a team member (Max) owns — with
Cloudflare **proxying enabled**. The decision, alternatives and bus-factor are
in [ADR-0012](adr/0012-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md).

| Field | Value |
|---|---|
| Hostname | `mosaiq.maxthecoder.online` |
| Cloudflare zone | `maxthecoder.online` (Max's account) |
| Record | `A` → `34.51.123.31`, proxied (orange cloud) |
| Edge TLS | Cloudflare Universal SSL (managed, auto-renewing) — what browsers see |
| Cloudflare SSL mode | Full (strict) |
| Origin cert | Cloudflare Origin CA, RSA 2048, 15-year, at `/etc/nginx/tls/mosaiq.{crt,key}` |

`34.51.123.31` is a **reserved static** external address (`mosaiq-ip`, type
`EXTERNAL`, region `northamerica-south1`, `IN_USE`), so a stop/start does not
rotate it.

Applied on `mosaiq-deployment-vm` on 2026-09-07:

- `/etc/nginx/tls/mosaiq.{crt,key}` — the Cloudflare Origin CA pair
  (`CN=CloudFlare Origin Certificate`, valid to 2041-09-03). The prior
  self-signed pair is kept as `*.selfsigned-20260907T064344Z`.
- `/etc/nginx/conf.d/mosaiq.conf` + `cloudflare-real-ip.conf` from the repo
  (`server_name mosaiq.maxthecoder.online`, `real_ip_header CF-Connecting-IP`,
  HSTS `max-age=2592000`). `nginx -t` clean, reloaded.
- App env already set for TLS: `SESSION_COOKIE_SECURE=true`,
  `TRUSTED_PROXY_HOPS=1`.

Cloudflare edge settings: SSL/TLS mode **Full (strict)**, **Always Use HTTPS**
on, Universal SSL active for the hostname.

Verified from outside GCP: `https://mosaiq.maxthecoder.online/` → `HTTP/2 200`
(TLS 1.3) with a valid edge certificate (`CN=maxthecoder.online`, Google Trust
Services, via Cloudflare Universal SSL); `http://` → `301`; the origin presents
`CN=CloudFlare Origin Certificate` past the edge; the NGINX access log shows the
real client IP, not a Cloudflare edge address.

**Optional, deferred:** firewall hardening to Cloudflare ranges —
[#167](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/167).

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

## Continuous deployment

Story `F6-06` (#90), decision [ADR-0011](adr/0011-one-environment-deployed-from-main.md).
The instance is a single production environment: `.github/workflows/deploy.yml`
is the only thing that deploys to it, a push to `main` is the only trigger, and
`develop` never deploys. Runbook: [`deploy/README.md`](../deploy/README.md).

Authentication is Workload Identity Federation, so **no long-lived credential
exists** — GitHub mints an OIDC token per run and GCP exchanges it for
short-lived credentials. Created once in project `iac-dev-01`; these resources
are not reproducible from this repository, which is why they are recorded here:

| Field | Value |
|---|---|
| Service account | `mosaiq-deploy@iac-dev-01.iam.gserviceaccount.com` |
| Workload identity pool | `github-actions` (global) |
| Provider | `github`, issuer `https://token.actions.githubusercontent.com` |
| Attribute mapping | `google.subject=assertion.sub`, `attribute.repository=assertion.repository`, `attribute.ref=assertion.ref` |
| Provider condition | `assertion.repository == 'Equipo-03-Proyecto10/retail-segmentation'` |
| Impersonation | `roles/iam.workloadIdentityUser` for `attribute.ref/refs/heads/main` only |
| Instance-level role | `roles/compute.viewer`, bound on `mosaiq-deployment-vm` rather than the project |
| Project-level roles | `roles/compute.osAdminLogin`, and custom `mosaiqDeployProjectRead` holding one permission (`compute.projects.get`) |
| Service-account role | `roles/iam.serviceAccountUser` on `23051370455-compute@developer.gserviceaccount.com`, the service account the instance itself runs as |
| Repository secrets used | none |

The OS Login role is granted on the project, not the instance, and that is
deliberate rather than sloppy. Bound only on the instance, OS Login created the
service account's login profile but served no keys for it: the POSIX user
resolved through NSS while `google_authorized_keys` returned nothing, which
reaches the runner as an unexplained `Permission denied (publickey)`. The
project holds one VM, so the practical scope is the same.

`gcloud compute ssh` refuses with `PERMISSION_DENIED: User does not have
iam.serviceAccounts.actAs permission on the instance's service account` unless
that last role is granted. Reaching an instance means acting as the identity it
runs as, so the caller needs `actAs` on it — a separate grant from the login
roles, and easy to miss because the error names a permission nobody asked for.
It is bound on that one service account, not on the project.

Two layers restrict the deploy to `main`: the workflow trigger, and the IAM
binding above. A `workflow_dispatch` from another branch fails at the
authentication step by design.

The instance also has OS Login enabled (`enable-oslogin=TRUE`), so SSH keys
placed in metadata are ignored and access is governed by IAM — which is what
makes the service account's instance-scoped `osAdminLogin` sufficient and a
stored private key unnecessary.

### Deployment behaviour

`deploy/deploy.sh` checks out the merged commit SHA (not a branch tip, so a
re-run is deterministic), installs `web/requirements.txt`, restarts `mosaiq`,
and health-checks `https://127.0.0.1/` through NGINX. Any failure restores the
previously serving commit and restarts again. The guarantee is that a broken
deploy is not left serving — not that the switch is seamless; there is one
instance and one gunicorn, so a restart is visible.

A deploy never runs SQL. Schema changes remain a manual, deliberate step.

First deploy: run
[34076420600](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/34076420600)
on 2026-09-07, which moved the instance from `71428a3` — a `develop` commit a
manual run had parked it on — to `e4da24d`, the merge commit on `main`.
Acceptance evidence, including the four failures it took to get the deploy
identity right: [`f6-06-continuous-deployment.md`](evidence/f6-06-continuous-deployment.md).
