# Deploying MOSAIQ on the instance

The GCP instance `mosaiq-deployment-vm` (CentOS Stream 10) runs the application
as **gunicorn under systemd behind NGINX** — [ADR-0006](../docs/adr/0006-run-under-both-systemd-and-docker-compose.md),
[ADR-0009](../docs/adr/0009-nginx-as-the-reverse-proxy.md). This directory holds
what is applied there:

PostgreSQL listener, HBA policy, SSH access and application-role verification:
[`postgresql/README.md`](postgresql/README.md) (F1-04 / F1-05).

| File | Goes to |
|---|---|
| `nginx/mosaiq.conf` | `/etc/nginx/conf.d/mosaiq.conf` (`:80` redirect + `:443` proxy) |
| `nginx/cloudflare-real-ip.conf` | `/etc/nginx/conf.d/cloudflare-real-ip.conf` — `mosaiq.conf` includes it; copy both together or `nginx -t` fails |
| `nginx/mosaiq.compose.conf` | not deployed — local verification only, see `../compose.proxy.yaml` |
| `postgresql/mosaiq.conf` | `/var/lib/pgsql/18/data/conf.d/mosaiq.conf` (loopback listener, SCRAM) |
| `systemd/mosaiq.service` | `/etc/systemd/system/mosaiq.service` |
| `systemd/mosaiq-infra.service` | `/etc/systemd/system/mosaiq-infra.service`: the infrastructure project (F18-01, below) |
| `systemd/mosaiq-svc@.service` | `/etc/systemd/system/mosaiq-svc@.service`: one compose project per service (F18-01) |
| `check-services.sh` | not installed: run from the checkout, as root, after any change to the services (F18-01) |
| `deploy.sh` | copied to `/tmp/mosaiq-deploy.sh` by the deploy workflow on each run, not installed |
| the TLS certificate | `/etc/nginx/tls/mosaiq.{crt,key}` — a Cloudflare Origin CA pair on the instance (F6-03 Path C, [ADR-0013](../docs/adr/0013-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)), not in the repo |

### Deployment layout

| Path | What |
|---|---|
| `/opt/mosaiq/current` | checkout of this repository |
| `/opt/mosaiq/venv` | virtualenv, `web/requirements.txt` installed |
| `/etc/mosaiq/mosaiq.env` | the environment file the unit reads (from `.env.example`) |
| user/group `mosaiq` | unprivileged, owns `/opt/mosaiq`, runs gunicorn |
| `/opt/mosaiq/services/<name>/<version>` | one staged release of a service: its `compose.yaml`, `redis/`, `nginx/`, `mongo/` if it has one, and `release.env`; `current` links the one running (F18-01, F18-02) |
| `/etc/mosaiq/svc/<name>.env` | a service's secrets, `0600 root`, read by its compose file's `env_file` |
| `/etc/mosaiq/keys/` | the signing key (`0400`, UID 10001) and `jwks.json` (`0444`), [auth-keys.md](auth-keys.md) |
| `/etc/mosaiq/redis/users.acl`, `/etc/mosaiq/mongodb/root.env` | the assembled aclfile and MongoDB's root account, [infra/README.md](infra/README.md) |

---

## F6-01 — NGINX reverse proxy

Applied by Max + Marcelo in the deployment pairing session (`docs/process.md`
§4), normally together with F6-02. Every step runs on the instance over SSH:

```sh
gcloud compute ssh mosaiq-deployment-vm --zone=northamerica-south1-a
```

### Steps

1. **Install NGINX.**
   ```sh
   sudo dnf install -y nginx
   ```

2. **Remove the stock default server** so ours is authoritative. In
   `/etc/nginx/nginx.conf` delete (or comment out) the whole
   `server { listen 80 default_server; ... }` block in the `http {}` section.
   The `conf.d/*.conf` include stays.

3. **Install the site config** from a checkout of this repo on the instance.
   `mosaiq.conf` includes `cloudflare-real-ip.conf`, so copy both — `nginx -t`
   fails on a missing include:
   ```sh
   sudo cp deploy/nginx/mosaiq.conf            /etc/nginx/conf.d/mosaiq.conf
   sudo cp deploy/nginx/cloudflare-real-ip.conf /etc/nginx/conf.d/cloudflare-real-ip.conf
   ```

4. **Allow NGINX to reach the upstream** (SELinux is enforcing on CentOS; without
   this every proxied request is a 502):
   ```sh
   sudo setsebool -P httpd_can_network_connect 1
   ```

5. **firewalld — nothing to do, and nothing it would do.** `eth0` is in the
   `trusted` zone, whose target is `ACCEPT`: it admits everything on the
   interface whatever its service list says. Adding or removing a service there
   changes only the listing. Ingress is decided by the GCP firewall alone
   (`docs/infra.md`, "firewalld is not a second layer"). Public `:80` was
   dropped in #167, so do **not** re-add the `http` service here.

6. **Test and start.**
   ```sh
   sudo nginx -t
   sudo systemctl enable --now nginx
   ```

7. **Have the app answering on `127.0.0.1:8000`.** For F6-01 alone, run it by
   hand (`/opt/mosaiq/venv/bin/gunicorn --bind 127.0.0.1:8000
   "web.app:create_app()"` with the environment set). F6-02 below makes it
   permanent.

### Acceptance criteria (attach the output to #77)

```sh
# 1. Port 80 forwards to the application.
curl -sI http://localhost/                 # from the instance  -> HTTP/1.1 200
curl -sI http://<external-ip>/              # from your laptop   -> HTTP/1.1 200

# 2. The application port is not reachable directly from outside.
curl -m5 http://<external-ip>:8000/         # from your laptop   -> timeout / refused
```

`<external-ip>` is `gcloud compute instances describe mosaiq-deployment-vm
--zone=northamerica-south1-a --format='value(networkInterfaces[0].accessConfigs[0].natIP)'`.

The second check passes because the GCP firewall denies everything except
`tcp:22` and `tcp:443` from a Cloudflare edge (`docs/infra.md`, #167) and
gunicorn binds loopback only — NGINX is the only thing that can reach `:8000`.

### After it is applied

Record the result in `docs/infra.md` under "Reverse proxy": NGINX version,
`systemctl is-enabled nginx`, and the two `curl` results.

---

## F6-02 — gunicorn under systemd

Runs in the same pairing session, right after F6-01.

### Steps

1. **Create the layout and the service user.**
   ```sh
   sudo useradd --system --home-dir /opt/mosaiq --shell /sbin/nologin mosaiq
   sudo mkdir -p /opt/mosaiq/current /etc/mosaiq
   sudo git clone https://github.com/Equipo-03-Proyecto10/retail-segmentation.git /opt/mosaiq/current
   sudo python3.12 -m venv /opt/mosaiq/venv
   sudo /opt/mosaiq/venv/bin/pip install -r /opt/mosaiq/current/web/requirements.txt
   sudo chown -R mosaiq:mosaiq /opt/mosaiq
   ```

2. **Write the environment file** — copy `.env.example` and change the values
   that differ on the instance:
   ```sh
   sudo cp /opt/mosaiq/current/.env.example /etc/mosaiq/mosaiq.env
   sudo chmod 640 /etc/mosaiq/mosaiq.env && sudo chown root:mosaiq /etc/mosaiq/mosaiq.env
   ```
   Then edit `/etc/mosaiq/mosaiq.env`:
   - `FLASK_ENV=production`
   - `FLASK_SECRET_KEY=` a real value (`python -c "import secrets; print(secrets.token_hex(32))"`)
   - `SESSION_COOKIE_SECURE=true` once F6-03 lands, `false` until then
   - `TRUSTED_PROXY_HOPS=1`
   - `DATABASE_URL=` the instance PostgreSQL and the least-privilege role (F1-05)

3. **Install and start the unit.**
   ```sh
   sudo cp /opt/mosaiq/current/deploy/systemd/mosaiq.service /etc/systemd/system/mosaiq.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now mosaiq
   systemctl status mosaiq        # active (running)
   ```

### Acceptance criteria (attach the output to #78)

```sh
# 1. A killed process restarts on its own.
systemctl show -p MainPID --value mosaiq          # note the PID
sudo kill -9 "$(systemctl show -p MainPID --value mosaiq)"
sleep 5 && systemctl is-active mosaiq             # -> active
curl -sI http://localhost/                        # -> HTTP/1.1 200 (through NGINX)

# 2. It comes back after a reboot, with nobody logged in.
sudo reboot
# reconnect after ~30s:
systemctl is-active mosaiq                         # -> active
curl -sI http://<external-ip>/                     # -> HTTP/1.1 200
```

### After it is applied

Record in `docs/infra.md` under "Application service": `systemctl is-enabled
mosaiq`, the unit path, and the two checks above.

---

## F6-03 — SSL certificate with forced HTTPS

Extends `deploy/nginx/mosaiq.conf` (already in the repo): the `:80` server only
redirects, and a `:443` server terminates TLS. **`nginx -t` fails until a
certificate exists at `/etc/nginx/tls/mosaiq.{crt,key}`** — put one there before
reloading.

**The instance itself still has no DNS name**, but the delivery is published
under one: `mosaiq.maxthecoder.online`, a subdomain of a domain a team member
owns, on Cloudflare. That resolves the Q-2 consequence in `docs/scope.md` §8 —
the AC "certificate valid for the published host" is now genuinely met.

Three certificate paths, in order of preference:

- **Path C — Cloudflare proxied + Origin Certificate** (in use, [ADR-0013](../docs/adr/0013-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)).
  Browsers see Cloudflare's managed, auto-renewing edge certificate; the origin
  carries a 15-year Cloudflare Origin CA pair. Hides the origin IP, adds CDN/DDoS.
- **Path A — Let's Encrypt** on the origin (DNS-only). A publicly trusted cert on
  the box itself; renewal and the origin IP are yours to carry.
- **Path B — self-signed.** Demo stopgap only; the browser warns.

### 0. Firewall

`tcp:443` is open on the GCP firewall (`mosaiq-allow-https`) and in firewalld —
nothing to add for Path A/B/C to work. **For Path C it is pinned to Cloudflare's
ranges and public `:80` is gone** (#167, applied 2026-09-07), so the origin is
reachable only through the edge. Redoing it, or repointing it at a new range
list, needs `roles/compute.securityAdmin`:

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

### 1c. Path C — Cloudflare proxied with an Origin Certificate

**In the Cloudflare dashboard** (zone `maxthecoder.online`):

1. **DNS → Records**: `A` record, name `mosaiq`, value `34.51.123.31` (the
   reserved static address `mosaiq-ip`), **Proxied** (orange cloud).
2. **SSL/TLS → Origin Server → Create Certificate**: let Cloudflare generate the
   key, hostname `mosaiq.maxthecoder.online`, RSA 2048, 15 years. Keep both PEM
   blocks — the private key is shown once.
3. **SSL/TLS → Overview → Full (strict)**. If other origins in the zone lack a
   valid cert, scope it instead with a Configuration Rule on
   `Hostname eq mosaiq.maxthecoder.online`.
4. **SSL/TLS → Edge Certificates**: *Always Use HTTPS* on, *Minimum TLS* 1.2,
   confirm *Universal SSL* is Active for the hostname (up to ~15 min).
5. **Caching → Configuration → Browser Cache TTL**: set it to
   **Respect Existing Headers**. It defaults to *4 hours*, and that value does
   not add caching on top of ours — it **replaces** the `Cache-Control` the
   origin sends. Flask already answers `no-cache` with a strong `ETag`, so
   browsers revalidate and get a cheap `304`; leaving the default gives every
   returning visitor up to four hours of stale CSS and JavaScript after each
   deploy, with no way for the application to break them out of it. The reason
   and the measurement are in [`docs/infra.md`](../docs/infra.md).

**On the instance:**

```sh
sudo install -d -m 750 /etc/nginx/tls
sudo cp /etc/nginx/tls/mosaiq.crt /etc/nginx/tls/mosaiq.crt.bak 2>/dev/null || true
sudo cp /etc/nginx/tls/mosaiq.key /etc/nginx/tls/mosaiq.key.bak 2>/dev/null || true
sudo tee /etc/nginx/tls/mosaiq.crt >/dev/null   # paste the Origin Certificate, Ctrl-D
sudo tee /etc/nginx/tls/mosaiq.key >/dev/null   # paste the Private Key, Ctrl-D
sudo chmod 600 /etc/nginx/tls/mosaiq.key && sudo chmod 644 /etc/nginx/tls/mosaiq.crt
```

certbot is **not** installed or needed on this path.

### 1a. Path A — Let's Encrypt (DNS-only hostname)

```sh
sudo dnf install -y certbot
sudo mkdir -p /var/lib/nginx/acme /etc/nginx/tls
sudo certbot certonly --webroot -w /var/lib/nginx/acme -d mosaiq.maxthecoder.online
sudo ln -sf /etc/letsencrypt/live/mosaiq.maxthecoder.online/fullchain.pem /etc/nginx/tls/mosaiq.crt
sudo ln -sf /etc/letsencrypt/live/mosaiq.maxthecoder.online/privkey.pem   /etc/nginx/tls/mosaiq.key
```

certbot installs a renewal timer — check `systemctl list-timers | grep certbot`.
The Cloudflare record must be **DNS-only** for the HTTP-01 challenge to reach the
origin.

**Path A no longer works as written.** #167 deleted `mosaiq-allow-http` and
pinned `:443` to Cloudflare's ranges, so the HTTP-01 challenge cannot reach
`:80` and Let's Encrypt cannot reach the origin at all. Falling back to Path A
means first recreating the `:80` rule (its exact spec is in `docs/infra.md`) and
widening or removing the `:443` pin — and renewal keeps needing them, every 60
days, not just the first issuance. Path C needs neither.

### 1b. Path B — self-signed (demo fallback)

```sh
sudo mkdir -p /etc/nginx/tls
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout /etc/nginx/tls/mosaiq.key -out /etc/nginx/tls/mosaiq.crt \
  -subj "/CN=mosaiq.maxthecoder.online"
```

### 2. Install the config and reload

`deploy/nginx/mosaiq.conf` already carries `server_name mosaiq.maxthecoder.online`,
the Cloudflare `real_ip` include and the one-year HSTS policy.

```sh
sudo cp /opt/mosaiq/current/deploy/nginx/mosaiq.conf            /etc/nginx/conf.d/mosaiq.conf
sudo cp /opt/mosaiq/current/deploy/nginx/cloudflare-real-ip.conf /etc/nginx/conf.d/cloudflare-real-ip.conf
sudo restorecon -Rv /etc/nginx/tls
sudo nginx -t && sudo systemctl reload nginx
```

`SESSION_COOKIE_SECURE=true` is already set in `/etc/mosaiq/mosaiq.env`; if a
fresh instance shows it `false`, flip it and `sudo systemctl restart mosaiq`.

### Acceptance criteria (attach the output to #79)

```sh
curl -sI  http://mosaiq.maxthecoder.online/     # -> 301 -> https  (Cloudflare edge on Path C)
curl -sI  https://mosaiq.maxthecoder.online/    # -> HTTP/2 200
curl -svo /dev/null https://mosaiq.maxthecoder.online/ 2>&1 | grep -E 'subject:|issuer:|expire'
# Path C, with the firewall hardened: a direct hit on the origin IP times out
curl --resolve mosaiq.maxthecoder.online:443:34.51.123.31 \
     -sI https://mosaiq.maxthecoder.online/ --max-time 6
```

### After it is applied

Update the `TLS` and `HSTS` rows and the `DNS` subsection in `docs/infra.md`
"Reverse proxy": certificate path, `server_name`, the edge/mode on Path C, and
the redirect check.

---

## F6-06 — continuous deployment on merge to `main`

Decision: [ADR-0011](../docs/adr/0011-one-environment-deployed-from-main.md).
The instance is one production environment, `main` is the only branch that
deploys to it, and `develop` never does.

`.github/workflows/deploy.yml` runs on every push to `main`. It authenticates
to GCP with Workload Identity Federation — GitHub mints a short-lived OIDC
token per run and GCP exchanges it for temporary credentials — then copies
`deploy/deploy.sh` to the instance and runs it against the merged commit SHA.
**No key or secret is stored in this repository or its settings.**

### What the deploy does

1. `git fetch`, and refuse a commit that is not in the repository.
2. Record the commit currently serving.
3. Check out the target commit, install `web/requirements.txt` into the venv,
   restart `mosaiq`.
4. Health-check `https://127.0.0.1/` through NGINX, up to ten times.
5. **On any failure in 3–4**, check the previous commit back out, reinstall its
   requirements, restart, and health-check again.
6. Compare the installed `/etc/nginx/conf.d/{mosaiq,cloudflare-real-ip}.conf`
   against the commit now serving and print any difference. It reports; it
   never copies. Same on `--dry-run`, and when the target is already serving.

It never runs SQL. The three scripts in `sql/` build a database from empty and
are not migrations, so a release needing a schema change needs a person —
ADR-0011 records this as a deliberate gap.

### Getting the instance ready to demonstrate (#360)

The instance has to show what the delivery claims. In this order, by whoever
holds the instance (none of it can be done from the repository):

1. **Back up, then re-provision the database** from the three scripts at the
   deployed commit. The final schema has 29 tables and also depends on
   `btree_gist` from `00_create_database.sql`: it includes the complete exposure
   and conversion event chain, named treatment arms with immutable definitions,
   non-overlapping close-only segment history, insert-only segmentation runs,
   protected experiments, the in-app notification tables, the final
   grants/revokes, and seed bands that reach every label (#345, #346, #350,
   #357, #419). Do not patch the old database by hand.
2. **Run `sql/verify_integrity.sql`, then `check-schema-drift.sh`** against a clean
   build of the same commit. Every probe must pass and the schema, privileges and
   role memberships must show no drift.
3. **Install the final NGINX files and validate them**: copy both
   `deploy/nginx/mosaiq.conf` and `cloudflare-real-ip.conf`, run `sudo nginx -t`,
   then reload NGINX. This applies the POST-only sign-in limiter from #348/#378;
   the deployment workflow reports proxy drift but deliberately does not copy it.
4. **Run one `RFM_RULES` and one `KMEANS` segmentation** as the administrator, so
   history derives from the transactions and `executed_by` is set; all thirty
   seeded runs have it `NULL`.
5. **Evaluate conversion** on each seeded experiment, so `/experiments/<id>/uplift`
   has recorded conversions to compare, and verify the dashboard names each
   final arm and excludes experiments attached to finished or cancelled campaigns.
6. **Remove what does not belong**: the September 6 copy of repository files
   (including its `.git/`), the `.env` and the unused `.venv/` at the
   `/opt/mosaiq/` root, and `mosaiq.conf.bak-*` in `/etc/nginx/conf.d/`. Keep
   `current/`, `docs/`, `.cache/` (pip's cache — `/opt/mosaiq` is the `mosaiq`
   user's home) and `venv/`: `venv/` is not a leftover, `mosaiq.service` and
   `deploy.sh` run from it.
7. **Re-diff** the schema and grants against a clean build of `main` once more.

The administrator's address is no longer published in `docs/evidence/` (#360).

### Correcting the seeded experiments' origin (#427)

Seeds before #427 marked experiments 1, 4, 7, … 28 `OBSERVED`, so they showed
as real results with no Synthetic label. A database rebuilt from the scripts
after #427 needs nothing. A database loaded before it is corrected with one
statement, a data fix rather than DDL, to the value the seed now gives those
rows:

```sh
sudo -u postgres psql -v ON_ERROR_STOP=1 -d retail -c \
  "UPDATE experiment SET data_origin = 'SEEDED'
    WHERE experiment_id BETWEEN 1 AND 30 AND data_origin = 'OBSERVED';"
```

It reports `UPDATE 10` the first time and `UPDATE 0` after. Only the thirty
seeded ids are touched, so an experiment created through the application keeps
the origin it was given.

### Correcting the seeded experiments' target metric (#457)

Seeds before #457 gave the even-numbered experiments `AVERAGE_TICKET`, a metric
that is no longer offered or measured (ADR-0053). Such an experiment opens with a
refusal where its uplift should be, and cannot be renamed, because the edit form
refuses the stored metric. A database rebuilt from the scripts after #457 needs
nothing. A database loaded before it is corrected with one data fix:

```sh
sudo -u postgres psql -v ON_ERROR_STOP=1 -d retail -c \
  "UPDATE experiment SET target_metric = 'CONVERSION'
    WHERE experiment_id BETWEEN 1 AND 30 AND target_metric = 'AVERAGE_TICKET';"
```

It reports `UPDATE 15` the first time and `UPDATE 0` after. As above, only the
thirty seeded ids are touched.

### Recording data origin on an existing database (#559)

#559 gives `transaction` and `segmentation_run` a `data_origin` column,
`OBSERVED` or `SEEDED`, `NOT NULL` with no default (ADR-0052). A database
rebuilt from the scripts after #559 needs nothing. A database loaded before it
needs the column, and a value for every existing row. Which rows came from the
seed and which from CSV loads is decided against the database's own rows, so
step (a) comes first and changes nothing.

**(a) Check the rule against the rows (read-only).** The seed numbers its 300
sales `TXN-00000001` to `TXN-00000300`, in `transaction_id` order on an empty
database, and gives each exactly two lines: for `d` in 0 and 1,
`product_id = 1 + ((transaction_id + d) % 40)` and
`quantity = 1 + ((transaction_id + d) % 3)` (`sql/02_seed_30_per_table.sql`).
A CSV file could use the same `TXN-` shape, so the lines corroborate the id:

```sh
sudo -u postgres psql -X -d retail <<'SQL'
-- Sales: seed-shaped id, TXN- prefix, and whether the lines are the seed's.
WITH line AS (
    SELECT l.transaction_id,
           count(*) AS lines,
           count(*) FILTER (WHERE EXISTS (
               SELECT FROM generate_series(0, 1) AS d
                WHERE l.product_id = 1 + ((l.transaction_id + d) % 40)
                  AND l.quantity = 1 + ((l.transaction_id + d) % 3)
           )) AS seed_lines
      FROM transaction_line AS l
     GROUP BY l.transaction_id
)
SELECT t.transaction_id BETWEEN 1 AND 300
       AND t.source_transaction_id = 'TXN-' || lpad(t.transaction_id::text, 8, '0')
           AS seed_shaped,
       t.source_transaction_id LIKE 'TXN-%' AS txn_prefix,
       count(*) AS sales,
       count(*) FILTER (WHERE l.lines = 2 AND l.seed_lines = 2) AS lines_as_seeded
  FROM transaction AS t
  LEFT JOIN line AS l ON l.transaction_id = t.transaction_id
 GROUP BY 1, 2
 ORDER BY 1, 2;

-- Runs: seeded (no actor, among the seed's ids), and whether the window held
-- a seed-shaped sale when the run executed.
SELECT r.executed_by IS NULL AND r.run_id <= 31 AS seed_run,
       EXISTS (
           SELECT FROM transaction AS t
            WHERE t.transaction_id BETWEEN 1 AND 300
              AND t.source_transaction_id = 'TXN-' || lpad(t.transaction_id::text, 8, '0')
              AND t.occurred_at >= r.run_at - make_interval(days => r.window_days)
              AND t.occurred_at <= r.run_at
       ) AS read_seeded_sale,
       count(*) AS runs
  FROM segmentation_run AS r
 GROUP BY 1, 2
 ORDER BY 1, 2;
SQL
```

What the rule needs to hold:

- one sales row `t | t | 300 | 300`: all 300 seed-shaped sales, each with
  exactly its two seeded lines;
- any CSV-loaded sales as `f | f | <n> | …`;
- **no** `f | t` row. A `TXN-` id outside the seed's shape is a CSV sale the
  rule cannot tell from the seed by its id; stop and inspect it. So is a
  seed-shaped row whose `lines_as_seeded` is below `sales`: a CSV file appended
  a line to a seeded sale, which #559 now refuses;
- runs: `t | t` for the seed's runs (30 on a database loaded at `f4ae690`, 31
  from the current seed, which adds a K-means run), and every run executed
  since in `f | t` or `f | f`.

**(b) If every sale is the seed's** (only the `t | t | 300 | 300` sales row),
the database holds demonstration data only: back it up and rebuild it from the
three scripts at the deployed commit, as #346 did. Nothing below is needed.

**(c) Otherwise, backfill in place, once.** Rebuilding would drop real CSV
sales, so the columns are added to the live database instead, by the committed
blocks of `sql/01_schema.sql` and never by DDL typed on the server (AGENTS.md,
ADR-0046). It runs as one transaction with `ON_ERROR_STOP`, so either all of it
applies or none does:

1. the `DATA ORIGIN COLUMNS` block adds both columns, nullable;
2. two `UPDATE`s, the only statements typed here, fill them with the rule from
   (a), as bound parameters (psql's `\bind`). A run is `SEEDED` if it is one of
   the seed's, or if a `SEEDED` sale fell in its window when it ran;
3. the `DATA ORIGIN` block sets `NOT NULL`, which fails if any row was left
   unfilled, adds each `CHECK` under the name a clean build gives it, and
   creates the run-origin function, guard and index;
4. the `BOUNDARY sales` section re-creates the guard that requires `OBSERVED`
   from `svc_sales`.

```sh
cd /opt/mosaiq/current
{
  sed -n '/^-- BEGIN DATA ORIGIN COLUMNS/,/^-- END DATA ORIGIN COLUMNS$/p' sql/01_schema.sql
  cat <<'SQL'
UPDATE transaction
   SET data_origin = CASE
       WHEN transaction_id BETWEEN $1 AND $2
        AND source_transaction_id = $3 || lpad(transaction_id::text, 8, '0')
       THEN $4 ELSE $5 END
 WHERE data_origin IS NULL
\bind 1 300 'TXN-' 'SEEDED' 'OBSERVED' \g

UPDATE segmentation_run AS r
   SET data_origin = CASE
       WHEN (r.executed_by IS NULL AND r.run_id <= $1)
         OR EXISTS (
            SELECT FROM transaction AS t
             WHERE t.data_origin = $2
               AND t.occurred_at >= r.run_at - make_interval(days => r.window_days)
               AND t.occurred_at <= r.run_at)
       THEN $2 ELSE $3 END
 WHERE r.data_origin IS NULL
\bind 31 'SEEDED' 'OBSERVED' \g
SQL
  sed -n '/^-- BEGIN DATA ORIGIN (/,/^-- END DATA ORIGIN$/p' sql/01_schema.sql
  sed -n '/^-- BEGIN BOUNDARY sales$/,/^-- END BOUNDARY sales$/p' sql/01_schema.sql
} | sudo -u postgres psql -X -v ON_ERROR_STOP=1 --single-transaction -d retail -f -
```

It reports `UPDATE <every sale>` and `UPDATE <every run>`. Then compare against
a clean build of the same commit, as in the next section:
`check-schema-drift.sh compare` must report no drift. The columns are last in
both tables in `sql/01_schema.sql` so that a database that gained them by
`ADD COLUMN` dumps identically.

**(d) Order.** The new code names `data_origin` in every sale and run it
writes, and the old code names it in none, so neither can run against the
other's schema. Apply (b) or (c) right after the deploy that ships #559, and
run no CSV load and no segmentation in between.

### Retiring `DATA_IS_SYNTHETIC` (#560)

#560 marks every analytics page Synthetic from the `data_origin` of the rows it
reads (ADR-0052), and the instance flag is gone from the code. Two things
follow on the instance:

1. **#559's step above must be done first.** The pages read `data_origin` on
   sales and runs; against a database without those columns they fail. Deploy
   #560 only after (b) or (c) of the previous section has been applied and
   verified.
2. **Delete the stale line from the environment file.** The application no
   longer reads it, so it is harmless, but an operator reading the file would
   believe it still governs the marker:

   ```bash
   sudo sed -i '/^DATA_IS_SYNTHETIC=/d' /etc/mosaiq/mosaiq.env
   sudo grep -c '^DATA_IS_SYNTHETIC=' /etc/mosaiq/mosaiq.env   # prints 0
   sudo systemctl restart mosaiq
   ```

### Checking the database has kept up with the code

Because the deploy never runs SQL, a database can fall behind the commit it
serves without anything failing. #346 was that: the instance database predated
the exposure trigger (ADR-0027) and the append-only revokes (ADR-0026, ADR-0027),
and `retail_app` could still update and delete assignments and exposures.
[`check-schema-drift.sh`](check-schema-drift.sh) makes the gap visible. It is
read-only on both sides and compares the schema and what `retail_app` may do to
each table (including privileges inherited through `PUBLIC` or role
membership), against a clean build of the three scripts at the deployed commit:

```sh
# 1. Anywhere with a scratch PostgreSQL, at the commit the instance serves:
git checkout <deployed-sha>
psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
deploy/check-schema-drift.sh dump /tmp/reference

# 2. On the instance:
sudo -u postgres PGDATABASE=retail deploy/check-schema-drift.sh dump /tmp/live

# 3. Bring both directories to one place and compare (exit 1 means drift):
deploy/check-schema-drift.sh compare /tmp/reference /tmp/live
```

Run it after any release that changes `sql/01_schema.sql`, and before a demo.

**When it reports drift**, the fix is to rebuild the instance database from the
three scripts, not to patch it: AGENTS.md keeps DDL in `sql/01_schema.sql` and
forbids altering a table by hand on the server. That drops the database, so it
is a decision for whoever owns the instance (the instance holds demonstration
data only), and it should be taken together with the demonstration reset in #360.

### The NGINX config is applied by hand, on purpose

The deploy does not install `deploy/nginx/*.conf`. It runs under `sudo` and
could, but two things argue against it:

- The health check cannot tell a good proxy config from a bad one. It asks
  `https://127.0.0.1/` with `-k`, and our `:443` block is `default_server`, so a
  wrong `server_name`, a missing `real_ip` include or a downgraded HSTS all
  still answer `200`. An automatic copy would hand the rollback trap a failure
  mode it is blind to, and would reconfigure TLS and edge trust as a side effect
  of shipping application code.
- Config is sometimes applied to the instance *ahead* of the repository, which
  is how F6-03 Path C was brought up (#168). A copy on every deploy would
  silently revert that.

So the copy stays a deliberate step, and the deploy's job is to say when it is
overdue — the gap #169 reported. **After merging anything that touches
`deploy/nginx/*.conf`**, on the instance:

```sh
sudo cp /opt/mosaiq/current/deploy/nginx/mosaiq.conf             /etc/nginx/conf.d/mosaiq.conf
sudo cp /opt/mosaiq/current/deploy/nginx/cloudflare-real-ip.conf /etc/nginx/conf.d/cloudflare-real-ip.conf
sudo nginx -t && sudo systemctl reload nginx
```

A run that finds them in step prints `matches the deployed commit`. One that
does not prints a `!!!` banner with the diff and the exact `cp` commands, and
still exits `0` — the application deploy is sound either way. To see the state
without deploying anything, run the script with `--dry-run` as below.

### Running it by hand

The script is not installed on the instance; the workflow copies it per run.
Run it from outside the checkout, because it rewrites that checkout and bash
reads a script incrementally:

```sh
gcloud compute scp deploy/deploy.sh mosaiq-deployment-vm:/tmp/mosaiq-deploy.sh \
  --project=iac-dev-01 --zone=northamerica-south1-a
gcloud compute ssh mosaiq-deployment-vm --project=iac-dev-01 \
  --zone=northamerica-south1-a \
  --command="sudo bash /tmp/mosaiq-deploy.sh <commit-sha> --dry-run"
```

Drop `--dry-run` to deploy. To roll back deliberately, pass the SHA you want
back; the script treats it like any other target.

### The GCP identity this needs

Created once, in project `iac-dev-01`, and recorded in
[`docs/infra.md`](../docs/infra.md) because it cannot be reproduced from this
repository:

| Resource | Value |
|---|---|
| Service account | `mosaiq-deploy@iac-dev-01.iam.gserviceaccount.com` |
| Pool / provider | `github-actions` / `github`, issuer `https://token.actions.githubusercontent.com` |
| Provider condition | `assertion.repository == 'Equipo-03-Proyecto10/retail-segmentation'` |
| May impersonate | only `attribute.ref/refs/heads/main` |
| Instance role | `roles/compute.viewer` — on the one instance, not the project |
| Project roles | `roles/compute.osAdminLogin` (instance-scoped binding creates the login profile but serves no keys), and custom `mosaiqDeployProjectRead` holding only `compute.projects.get` |
| Service-account role | `roles/iam.serviceAccountUser` on the instance's own service account (`23051370455-compute@developer.gserviceaccount.com`) — SSH means acting as it, and gcloud refuses without this |

A `workflow_dispatch` from any branch other than `main` fails at the
authentication step. That is the IAM restriction doing its job, not a bug.

### Verifying the pipeline

F6-06's acceptance was captured in
[the evidence](../docs/evidence/f6-06-continuous-deployment.md); run
[34076420600](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/34076420600)
is the deploy that closed it. What follows is how to re-verify it — after a
change to `deploy.sh`, or to the identity in GCP.

**What is serving right now:**

```sh
gcloud compute ssh mosaiq-deployment-vm --project=iac-dev-01 \
  --zone=northamerica-south1-a --command="\
    sudo -u mosaiq git -C /opt/mosaiq/current rev-parse HEAD; \
    systemctl is-active mosaiq; \
    curl -sk -o /dev/null -w 'https %{http_code}\n' https://127.0.0.1/"
```

That commit must equal the tip of `main`. If it does not, either a deploy failed
or somebody moved the instance by hand — both worth knowing about.

**Check any SHA before you use it.** A terminal that does not handle bracketed
paste inserts its markers literally, and a stray `~` on the end of a SHA is
valid git syntax for *the parent of* that commit. The deploy then quietly
targets a different commit and reports success — this happened, and cost a
rollback test that proved nothing. Resolve first and compare:

```sh
gcloud compute ssh mosaiq-deployment-vm --project=iac-dev-01 --zone=northamerica-south1-a --command='sudo -u mosaiq git -C /opt/mosaiq/current rev-parse <sha>'
```

It must print back what you gave it. The script also prints `deploying:` with
the SHA it resolved — read that line before trusting a result.

**Re-testing the rollback.** It needs a genuinely broken release, not a disabled
health check: the rollback runs the same check, so forcing a failure would make
the recovery look like it failed too. Build a probe on a throwaway branch —
raise in the landing view, so gunicorn starts cleanly and the site then answers
500, the failure `systemctl is-active` cannot see:

```sh
git checkout -b test/deploy-rollback-probe origin/develop
# in web/routes/home.py, first line of index():
#     raise RuntimeError("deliberate failure: deploy rollback probe")
git commit -am "test: deliberately broken landing page, rollback probe" && git push -u origin HEAD
```

Then deploy that SHA, expecting it to be refused and reverted:

```sh
gcloud compute scp deploy/deploy.sh mosaiq-deployment-vm:/tmp/probe-deploy.sh --project=iac-dev-01 --zone=northamerica-south1-a
gcloud compute ssh mosaiq-deployment-vm --project=iac-dev-01 --zone=northamerica-south1-a --command="sudo HEALTH_ATTEMPTS=4 bash /tmp/probe-deploy.sh <probe-sha>"
```

Expect `unhealthy after 4 attempts: HTTP 500`, then `FAILED — rolling back`,
then `rolled back; the previous version is serving again`, exit 70. Delete the
branch and `/tmp/probe-deploy.sh` afterwards, and use a filename nobody else
owns — `/tmp` is sticky, and a leftover file owned by another user blocks the
next upload with a bare "Permission denied".

If it instead prints `ROLLBACK ALSO FAILED`, the instance needs a person: deploy
the last good SHA by hand and raise it, because that is a defect in the pipeline
rather than something to improvise around.

### After it is applied

Record in `docs/infra.md` under "Continuous deployment": the workflow run that
deployed, the commit now serving, and the rollback evidence attached to #90.

---

## Verifying the proxy locally (no instance)

```sh
docker compose -f compose.yaml -f compose.proxy.yaml up --build
curl -sI http://localhost:8080/            # -> HTTP/1.1 200, through NGINX
docker compose -f compose.yaml -f compose.proxy.yaml down
```

If `8080` is taken, set `MOSAIQ_PROXY_PORT` (e.g. `MOSAIQ_PROXY_PORT=8088
docker compose -f compose.yaml -f compose.proxy.yaml up`).

Plain `docker compose up` is unchanged — the app stays on `http://localhost:8000`.

---

## F6-04 — publish documentation and evidence (#80)

The `location /docs/` block in `deploy/nginx/mosaiq.conf` serves a copy of
this repository's `docs/` folder — including every screenshot and evidence
file under `docs/evidence/` — as static files on the same host, so the
delivery can be evaluated from one place.

Markdown is served as `text/plain; charset=utf-8` so a documentation link opens
in the browser. NGINX's `mime.types` has no `.md` entry, so without this every
one of them fell to `application/octet-stream` and downloaded a file the reader
already had. It is `default_type` rather than a `types { … }` block on purpose:
`types` inside a `location` **replaces** the inherited map, which would turn the
stylesheets and the fifty screenshots under the same path into downloads.

### Publishing is part of the deploy

`deploy.sh` publishes `docs/` from the commit it deploys, on every run. Nothing
has to be remembered, and the published copy cannot drift from the deployed
commit. The step reports the file count it wrote:

```
=== Documentation
published 118 files to /opt/mosaiq/docs
```

It never rolls back. A documentation copy is not a reason to take a healthy
application off the instance, so a failure is printed loudly and the deploy
still succeeds — read the step's output rather than only the exit status.

**This used to be a manual `scp` that nobody ran.** The published copy sat on an
ADR-0006-era snapshot for weeks: 8 of 16 ADRs, 1 of 17 evidence documents and
none of the 50 screenshots, while every merge to `main` went green. Deliverables
10, 13 and 14 all live in that tree.
[`docs/evidence/f6-05-final-verification.md`](../docs/evidence/f6-05-final-verification.md)
records how it was found and what it hid.

### Publishing it by hand

Only needed out of band — to serve a tree that is not the deployed commit, or
when the instance is being recovered:

```sh
gcloud compute scp --recurse docs mosaiq-deployment-vm:/tmp/mosaiq-docs \
  --project=iac-dev-01 --zone=northamerica-south1-a
gcloud compute ssh mosaiq-deployment-vm --project=iac-dev-01 \
  --zone=northamerica-south1-a --command="\
    sudo rm -rf /opt/mosaiq/docs && \
    sudo mv /tmp/mosaiq-docs /opt/mosaiq/docs && \
    sudo chown -R mosaiq:mosaiq /opt/mosaiq/docs && \
    sudo restorecon -Rv /opt/mosaiq/docs"
```

**The `restorecon` is not optional.** SELinux is `Enforcing`, `mv` preserves the
context a file had in `/tmp`, and `httpd_t` cannot read `user_tmp_t`. Without it
every file is present and answers `403`. The `semanage fcontext` rule for
`/opt/mosaiq/docs(/.*)?` is already in policy; `restorecon` is what applies it.

Verify against the origin rather than the edge — Cloudflare will serve a cached
copy of the previous tree and make a spot check look like success:

```sh
curl -sS -o /dev/null -D - "https://mosaiq.maxthecoder.online/docs/README.md?cb=$RANDOM" \
  | grep -iE '^HTTP|cf-cache-status'
```

The NGINX config does not change when the copy is refreshed. It only needs
installing the first time (F6-01 step 3, then
`sudo nginx -t && sudo systemctl reload nginx`).

### Rendering

Not done, and worth a decision. Served as `text/plain`, a document is readable
but inert: the cross-references that make `docs/README.md` and
`docs/evidence/README.md` useful — the phase grouping, the deliverable each
evidence file satisfies — are text rather than links, and a reader navigates by
editing the URL or by `autoindex`.

Rendering to HTML at publish time would fix that, and would cost a Markdown
processor on the instance or in the deploy, plus a decision about styling. The
design system is already committed under `docs/design-system/` and is the
obvious thing to render into.

Nobody has asked for it and it changes the published artifact, so it is recorded
here rather than done.

### Acceptance criteria (attach the output to #80)

```sh
curl -skI https://<host>/docs/                       # -> HTTP/2 200
curl -skI https://<host>/docs/evidence/               # -> HTTP/2 200
curl -sk https://<host>/docs/requirements.md | head -3 # readable text, not 404
```

Every link on the generated index page should resolve — `autoindex on`
lists the folder itself, so this is really testing that the copy is
complete and the permissions above were applied.

### After it is applied

Record in `docs/infra.md` under a new "Documentation" row: the URL,
the date published, and the three checks above.

### Seed outboxes and the one-time #597 cleanup

[ADR-0054](../docs/adr/0054-seed-written-outbox-rows-before-the-relays-first-start.md)
requires an empty-database bootstrap to leave all eight outboxes empty. The
seed removes its captured events before `COMMIT`, with capture and integrity
triggers enabled. Never run the seed or its unconditional `DELETE` block on
an in-use database.

The explicitly authorized correction of the 2026-10-20 instance is recorded in
[#597's evidence](../docs/evidence/597-seed-outbox-cleanup.md). Its
procedure is preserved in `deploy/cleanup-seed-outboxes-597.py`; it is specific
to that seed, not a general purge tool. It verifies the exact timestamp,
audited seed identities, expected event identities/counts and unrelayed state.
It archives rows with mode `0600`, deletes by bound event IDs and timestamp in
one transaction, and verifies that every retained event is unchanged. A failed
check rolls back. It requires psycopg from the web's existing environment.

Before the one-time cleanup, its read-only preview was:

```sh
sudo -u postgres /opt/mosaiq/venv/bin/python deploy/cleanup-seed-outboxes-597.py \
  --dsn 'dbname=retail user=postgres' \
  --seed-at '2026-10-20T07:48:39.423052+00:00' \
  --backup /var/tmp/mosaiq-597-outboxes-before.json
```

Adding `--apply` performed the authorized correction. It has already run;
re-running it refuses the missing candidates and does not overwrite the
archive. Preserve that archive on the instance; never commit database exports.

## F18-01 — Docker, the infrastructure project and one unit per service

[ADR-0040](../docs/adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)
(issue #448). Next to the unchanged web, the instance runs the infrastructure
project `mosaiq-infra` (Redis and MongoDB) and each service as its own compose
project, `mosaiq-<name>`, under `mosaiq-svc@<name>.service`.

The instance never builds an image. A service's image exists once
`.github/workflows/services.yml` has built, tested and pushed it to GHCR on a
merge to `main` that bumped its `VERSION`. Every step below is idempotent and
runs as root. A secret is never typed on a command line: `sudo` records its
argv in the journal. Secrets are read from files, or from a pipe or a prompt.

### 1. Swap

With no swap (`swapon --show` prints nothing), a 2 GB swapfile is the
cushion of ADR-0040's memory budget. `docs/infra.md` records its size.

```sh
sudo fallocate -l 2G /swapfile && sudo chmod 0600 /swapfile && sudo restorecon /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap defaults 0 0' | sudo tee -a /etc/fstab
```

### 2. Docker Engine and the Compose plugin

From Docker's CentOS repository, not Podman's emulation. `podman-docker` must
be absent, and SELinux stays enforcing. `docker-ce` leaves `selinux-enabled`
off by default, which runs every container unlabelled. `daemon.json` turns it
on, so each container runs as `container_t` with its own MCS categories.

```sh
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
rpm -q podman-docker && sudo dnf -y remove podman-docker
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo install -m 0644 /dev/stdin /etc/docker/daemon.json <<'JSON'
{ "selinux-enabled": true, "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
sudo systemctl enable --now docker
getenforce                                   # Enforcing
sudo docker info --format '{{json .SecurityOptions}}'   # lists name=selinux
docker --version && docker compose version   # recorded in docs/infra.md
```

The log caps hold each container's logs to 30 MB, so the disk cannot fill with
them.

### 3. The GHCR credential

The service packages are private. Root pulls them with a **classic personal
access token that holds `read:packages` only**:

- **Ownership:** Marcelo Villanueva (`marcelovillanuevam-code`) owns it and
  rotates it.
- **Expiry:** 90 days.
- **Rotation:** before it expires, make the next token, run the login again,
  check that a pull works, then revoke the old one.

The token never goes into the repository, `.env.example`, a unit or an
argument.

```sh
sudo docker login ghcr.io -u marcelovillanuevam-code   # paste the token at the prompt
```

Docker keeps it in `/root/.docker/config.json` (`0600`).

### 4. Secrets and keys

- `/etc/mosaiq/svc/` is `0700 root`, and each `<name>.env` in it is `0600 root`. It holds:
  - `DATABASE_URL=postgresql://svc_<suffix>:<password>@/retail?host=/run/postgresql`,
    with the password from `/root/mosaiq-svc-db.env`, URL-encoded;
  - `REDIS_STATE_URL=redis://redis-state:6379/0` and
    `REDIS_CACHE_URL=redis://redis-cache:6379/0`;
  - `REDIS_<SUFFIX>_PASSWORD`, generated once on the instance;
  - `AUTH_JWKS_PATH=/run/mosaiq/jwks.json` and `MOSAIQ_SERVICE_NAME=<name>`;
  - every other variable that service's section of `.env.example` lists, such as
    the signer's key variables or a MongoDB URI.

  `<suffix>` is the service's role suffix, which is not always its name.
  `docker/distributed/svc/<name>.env` shows each service's full set with local
  values.
- **Redis `ops`:** its password lives in `/root/mosaiq-redis-ops.env` (`0600`).
- **MongoDB root:** `/etc/mosaiq/mongodb/root.env`, per
  [infra/README.md](infra/README.md), "First start", step 2.
- **The signing key and the JWK Set:** [auth-keys.md](auth-keys.md), "First key".

### 5. PostgreSQL

Before a service starts, its role (`sql/00_create_database.sql`) and its
boundary section of `sql/01_schema.sql` must have been applied as `postgres`
(expand before deploy). A release that rebuilds the database does both. Its
`pg_hba.conf` line is described in
[postgresql/README.md](postgresql/README.md), under "The service roles".

The customers section from customers 0.1.6 on (#594) replaces
`customer_email_key` with `ux_customer_email_lower`, a unique index on
`lower(email)`. If the database already holds two customers whose emails
differ only in letter case, creating the index fails and, with
`--single-transaction`, nothing changes. Find them first, and correct one of
each pair through the service's `PATCH` before applying:

```sh
sudo -u postgres psql -X -d retail -c "SELECT lower(email), count(*)
  FROM customer WHERE email IS NOT NULL GROUP BY 1 HAVING count(*) > 1"
```

A labelled container (`container_t`) may not enter the socket's directory
(`postgresql_var_run_t`) or connect to the server under the targeted policy.
So every service fails with `EACCES` on `/run/postgresql/.s.PGSQL.5432` and
answers 503 `schema_not_ready`.

The server must run as `postgresql_t` first, as described in
[postgresql/README.md](postgresql/README.md), "The server runs confined".
Without that rule, the PGDG server runs as `unconfined_service_t`, which the
module grants nothing. Then load the local module that grants that one path,
once:

```sh
cd "$(mktemp -d)" && cp /opt/mosaiq/current/deploy/selinux/mosaiq_svc_pgsocket.te .
sudo checkmodule -M -m -o mosaiq_svc_pgsocket.mod mosaiq_svc_pgsocket.te
sudo semodule_package -o mosaiq_svc_pgsocket.pp -m mosaiq_svc_pgsocket.mod
sudo semodule -i mosaiq_svc_pgsocket.pp && sudo semodule -l | grep mosaiq_svc_pgsocket
```

A running service reconnects on its own once the module is loaded.

### 6. The infrastructure project

The aclfile comes first, because both Redis instances load it at boot. It is
built from the lines of the services staged in step 8.

```sh
sudo install -m 0644 /opt/mosaiq/current/deploy/systemd/mosaiq-infra.service /etc/systemd/system/
sudo bash -c 'set -a; . /root/mosaiq-redis-ops.env; set +a
  ACL_SOURCES="/opt/mosaiq/services/*/current/redis/*.acl" \
  exec /opt/mosaiq/current/deploy/infra/redis/assemble-acl.sh --write-only'
sudo systemctl daemon-reload && sudo systemctl enable --now mosaiq-infra
sudo bash -c 'set -a; . /root/mosaiq-redis-ops.env; set +a
  cd /opt/mosaiq/current && exec deploy/infra/check-redis.sh'
```

The unit creates the external network `mosaiq` when it is missing. Restarting
it recreates only the infrastructure containers: no service container's
`StartedAt` changes.

### 7. A service's MongoDB schema

Any service that ships `mongo/init.js` has that file applied to the live
MongoDB, as root, before the service's first start
([ADR-0038](../docs/adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md): expand
before deploy). Use the command in [infra/README.md](infra/README.md),
"MongoDB", with the password the file reads taken from the service's
`/etc/mosaiq/svc/<name>.env`. A second run changes nothing.

### 8. Staging a service by hand

These are the steps F18-03 (#456) automates on each release.

```sh
name=<name>; version=$(cat /opt/mosaiq/current/services/$name/VERSION)
dir=/opt/mosaiq/services/$name/$version
sudo install -d -m 0755 "$dir"
sudo cp /opt/mosaiq/current/services/$name/compose.yaml "$dir/"
sudo cp -r /opt/mosaiq/current/services/$name/redis /opt/mosaiq/current/services/$name/nginx "$dir/"
[ -d /opt/mosaiq/current/services/$name/mongo ] && sudo cp -r /opt/mosaiq/current/services/$name/mongo "$dir/"
printf 'IMAGE_TAG=%s\n' "$version" | sudo tee "$dir/release.env" >/dev/null
sudo ln -sfn "$version" /opt/mosaiq/services/$name/current
sudo docker compose -p mosaiq-$name --env-file "$dir/release.env" -f "$dir/compose.yaml" pull
```

`release.env` also carries every variable the compose file interpolates
without a default (`grep -o '\${[A-Z_]*}' compose.yaml`), such as the signer's
key id.

Then load its ACL line with the same `assemble-acl.sh` command as step 6,
without `--write-only`. Install the unit once and start the service:

```sh
sudo install -m 0644 /opt/mosaiq/current/deploy/systemd/mosaiq-svc@.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mosaiq-svc@$name
port=$(awk -v service="$name" '
  $1 == "-" && $2 == "name:" { selected = ($3 == service) }
  selected && $1 == "port:" { print $2; exit }
' /opt/mosaiq/current/contracts/registry.yaml)
test -n "$port"
curl -fsS -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$port/health/ready"
```

`/health/ready` answers 503 `schema_not_ready` until step 5 has been done for
that service.

### 9. Checks and evidence

- `sudo bash /opt/mosaiq/current/deploy/check-services.sh` prints nothing.
- `systemctl is-active mosaiq` stays active, and
  `curl -sS -o /dev/null -w '%{http_code}' https://mosaiq.maxthecoder.online/` answers 200, throughout.
- `free -m` and `docker stats --no-stream`, with every service running, go to
  [docs/evidence/f18-01-memory.md](../docs/evidence/f18-01-memory.md) and
  `docs/infra.md`. They are compared with ADR-0040's estimate. If memory is
  short, the record's cut order applies.
- **SELinux:** a container running as UID 10001 must connect through the mounted
  `/run/postgresql`. A new denial goes in a local policy module under
  `deploy/selinux/`, loaded with `semodule -i`, never a `:z` relabel. The
  targeted policy hides some denials, so `semodule -DB` shows them while you
  reproduce, and `semodule -B` restores the policy. Read `/var/log/audit/audit.log`
  with `sudo` throughout. Services retrying a refused connection rotate the
  5 × 8 MB log within minutes, so read it right after reproducing. The outcome is recorded in
  [docs/evidence/f18-01-runtime.md](../docs/evidence/f18-01-runtime.md).

### Rollback

```sh
for d in /opt/mosaiq/services/*/; do sudo systemctl disable --now "mosaiq-svc@$(basename "$d")"; done
sudo systemctl disable --now mosaiq-infra
sudo semodule -r mosaiq_svc_pgsocket
```

Then remove the `svc_*` lines from `pg_hba.conf` and reload PostgreSQL. The web
depends on none of this.

## F18-02 — the services under `/api/<name>/` on 443

[ADR-0040](../docs/adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)
(issue #452). `mosaiq.conf`'s 443 server includes `/etc/nginx/mosaiq-api.d/*.conf`.
Each file there contributes one service's `location /api/<name>/` and its named
413/429 locations. F18-03 installs or replaces these files on each deploy; they
are never edited by hand on the instance.

`mosaiq.conf` is still installed by hand, as F6-06 left it: install the updated
file first, as [The NGINX config is applied by hand](#the-nginx-config-is-applied-by-hand-on-purpose)
describes. An include glob that matches no file is not an error, so the 443
server keeps working while the directory is still empty.

Then, for each staged service directory (step 8 of F18-01 stages `nginx/`),
install its `nginx/<name>.conf` under the matching name:

```sh
sudo install -d -m 0755 /etc/nginx/mosaiq-api.d
for staged in /opt/mosaiq/services/*/current; do
  [ -d "$staged/nginx" ] || continue
  name=$(basename "$(dirname "$staged")")
  sudo install -m 0644 "$staged/nginx/$name.conf" \
    "/etc/nginx/mosaiq-api.d/$name.conf"
done
sudo restorecon -Rv /etc/nginx/mosaiq-api.d
sudo nginx -t && sudo systemctl reload nginx
```

From outside the instance, run `bash deploy/check-api-edge.sh`. It prints
nothing when every criterion of F18-02 holds.

### Rollback

Empty the include directory, validate the remaining NGINX configuration, and
reload:

```sh
sudo find /etc/nginx/mosaiq-api.d -mindepth 1 -maxdepth 1 -type f -delete
sudo nginx -t && sudo systemctl reload nginx
```
