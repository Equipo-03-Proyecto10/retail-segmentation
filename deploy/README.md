# Deploying MOSAIQ on the instance

The GCP instance `mosaiq-deployment-vm` (CentOS Stream 10) runs the application
as **gunicorn under systemd behind NGINX** — [ADR-0006](../docs/adr/0006-run-under-both-systemd-and-docker-compose.md),
[ADR-0009](../docs/adr/0009-nginx-as-the-reverse-proxy.md). This directory holds
what is applied there:

| File | Goes to |
|---|---|
| `nginx/mosaiq.conf` | `/etc/nginx/conf.d/mosaiq.conf` (`:80` redirect + `:443` proxy) |
| `nginx/mosaiq.compose.conf` | not deployed — local verification only, see `../compose.proxy.yaml` |
| `systemd/mosaiq.service` | `/etc/systemd/system/mosaiq.service` |
| the TLS certificate | `/etc/nginx/tls/mosaiq.{crt,key}` — issued on the instance (F6-03), not in the repo |

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

3. **Install the site config** from a checkout of this repo on the instance:
   ```sh
   sudo cp deploy/nginx/mosaiq.conf /etc/nginx/conf.d/mosaiq.conf
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

Extends `deploy/nginx/mosaiq.conf` (already in the repo): the `:80` server now
only redirects, and a `:443` server terminates TLS. **`nginx -t` fails until a
certificate exists** — issue it before reloading.

**Open question Q-2 (`docs/scope.md` §8): which host is published?** The
certificate step depends on the answer:

- **the instance has a DNS name pointing at it** → Path A (Let's Encrypt), and
  the AC "certificate valid for the published host" is genuinely met;
- **only an IP / a `~user` path on a host the team does not control** → Path B
  (self-signed) is a demo stopgap; the browser warns and the AC is not fully
  met until Q-2 is resolved.

### 0. GCP firewall — allow HTTPS

The instance firewall currently permits only `tcp:22` and `tcp:80`
(`docs/infra.md`). Add `443` (run from a workstation with `gcloud`):

```sh
gcloud compute firewall-rules create mosaiq-allow-https \
  --project=iac-dev-01 --network=default --direction=INGRESS --action=ALLOW \
  --rules=tcp:443 --source-ranges=0.0.0.0/0 --target-tags=mosaiq-server \
  --priority=900
```

Also open it in firewalld if active:
`sudo firewall-cmd --permanent --add-service=https && sudo firewall-cmd --reload`.

### 1a. Path A — Let's Encrypt (real hostname)

```sh
sudo dnf install -y certbot
sudo mkdir -p /var/lib/nginx/acme /etc/nginx/tls
sudo certbot certonly --webroot -w /var/lib/nginx/acme -d <hostname>
sudo ln -sf /etc/letsencrypt/live/<hostname>/fullchain.pem /etc/nginx/tls/mosaiq.crt
sudo ln -sf /etc/letsencrypt/live/<hostname>/privkey.pem   /etc/nginx/tls/mosaiq.key
```

Set `server_name <hostname>;` in both server blocks of `mosaiq.conf` instead of
`_`. certbot installs a renewal timer — check `systemctl list-timers | grep certbot`.

### 1b. Path B — self-signed (demo fallback)

```sh
sudo mkdir -p /etc/nginx/tls
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout /etc/nginx/tls/mosaiq.key -out /etc/nginx/tls/mosaiq.crt \
  -subj "/CN=<instance hostname or IP>"
```

### 2. Reload and flip the cookie

```sh
sudo restorecon -Rv /etc/nginx/tls        # SELinux labels on the new files
sudo nginx -t && sudo systemctl reload nginx
sudo sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=true/' /etc/mosaiq/mosaiq.env
sudo systemctl restart mosaiq
```

### Acceptance criteria (attach the output to #79)

```sh
curl -sI  http://<host>/       # -> 301, Location: https://<host>/
curl -sI  https://<host>/      # -> HTTP/2 200
curl -svo /dev/null https://<host>/ 2>&1 | grep -E 'subject:|issuer:|expire'
```

### After it is applied

Update the `TLS` row in `docs/infra.md` "Reverse proxy": certificate source
(Let's Encrypt / self-signed), `server_name`, and the redirect check.

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
