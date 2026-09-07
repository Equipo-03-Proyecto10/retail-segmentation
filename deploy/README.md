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
| `deploy.sh` | copied to `/tmp/mosaiq-deploy.sh` by the deploy workflow on each run, not installed |
| the TLS certificate | `/etc/nginx/tls/mosaiq.{crt,key}` — a Cloudflare Origin CA pair on the instance (F6-03 Path C, [ADR-0012](../docs/adr/0012-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)), not in the repo |

### Deployment layout

| Path | What |
|---|---|
| `/opt/mosaiq/current` | checkout of this repository |
| `/opt/mosaiq/venv` | virtualenv, `web/requirements.txt` installed |
| `/etc/mosaiq/mosaiq.env` | the environment file the unit reads (from `.env.example`) |
| user/group `mosaiq` | unprivileged, owns `/opt/mosaiq`, runs gunicorn |

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

5. **Open HTTP in firewalld** if it is running (the GCP firewall already allows
   `:80`, this is the host firewall):
   ```sh
   sudo firewall-cmd --state >/dev/null 2>&1 \
     && sudo firewall-cmd --permanent --add-service=http \
     && sudo firewall-cmd --reload
   ```

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
`tcp:22` and `tcp:80` (`docs/infra.md`) and gunicorn binds loopback only —
NGINX is the only thing that can reach `:8000`.

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

- **Path C — Cloudflare proxied + Origin Certificate** (in use, [ADR-0012](../docs/adr/0012-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)).
  Browsers see Cloudflare's managed, auto-renewing edge certificate; the origin
  carries a 15-year Cloudflare Origin CA pair. Hides the origin IP, adds CDN/DDoS.
- **Path A — Let's Encrypt** on the origin (DNS-only). A publicly trusted cert on
  the box itself; renewal and the origin IP are yours to carry.
- **Path B — self-signed.** Demo stopgap only; the browser warns.

### 0. Firewall

`tcp:443` is already open on the GCP firewall (`mosaiq-allow-https`) and in
firewalld — nothing to add for Path A/B/C to work. **Optional hardening for
Path C** (needs `roles/compute.securityAdmin`): pin `:443` to Cloudflare's
ranges and drop public `:80`, so the origin is reachable only through the edge.

```sh
CF4=$(curl -s https://www.cloudflare.com/ips-v4 | paste -sd,)
CF6=$(curl -s https://www.cloudflare.com/ips-v6 | paste -sd,)
gcloud compute firewall-rules update mosaiq-allow-https \
  --project=iac-dev-01 --source-ranges="$CF4,$CF6"
gcloud compute firewall-rules delete mosaiq-allow-http --project=iac-dev-01
```

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

### 1b. Path B — self-signed (demo fallback)

```sh
sudo mkdir -p /etc/nginx/tls
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout /etc/nginx/tls/mosaiq.key -out /etc/nginx/tls/mosaiq.crt \
  -subj "/CN=mosaiq.maxthecoder.online"
```

### 2. Install the config and reload

`deploy/nginx/mosaiq.conf` already carries `server_name mosaiq.maxthecoder.online`,
the Cloudflare `real_ip` include and the staged HSTS.

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

It never runs SQL. The three scripts in `sql/` build a database from empty and
are not migrations, so a release needing a schema change needs a person —
ADR-0011 records this as a deliberate gap.

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

### Publish or refresh the copy

Run after any change to `docs/`, from a checkout of this repo:

```sh
gcloud compute scp --recurse docs mosaiq-deployment-vm:/tmp/mosaiq-docs \
  --project=iac-dev-01 --zone=northamerica-south1-a
gcloud compute ssh mosaiq-deployment-vm --project=iac-dev-01 \
  --zone=northamerica-south1-a --command="\
    sudo rm -rf /opt/mosaiq/docs && \
    sudo mv /tmp/mosaiq-docs /opt/mosaiq/docs && \
    sudo chown -R mosaiq:mosaiq /opt/mosaiq/docs"
```

Then reload the already-updated NGINX config (see F6-01 step 3, then
`sudo nginx -t && sudo systemctl reload nginx`).

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
