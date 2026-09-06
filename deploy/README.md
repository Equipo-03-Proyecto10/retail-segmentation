# Deploying MOSAIQ on the instance

The GCP instance `mosaiq-deployment-vm` (CentOS Stream 10) runs the application
as **gunicorn under systemd behind NGINX** — [ADR-0006](../docs/adr/0006-run-under-both-systemd-and-docker-compose.md),
[ADR-0009](../docs/adr/0009-nginx-as-the-reverse-proxy.md). This directory holds
what is applied there:

| File | Goes to |
|---|---|
| `nginx/mosaiq.conf` | `/etc/nginx/conf.d/mosaiq.conf` |
| `nginx/mosaiq.compose.conf` | not deployed — local verification only, see `../compose.proxy.yaml` |
| `systemd/mosaiq.service` | `/etc/systemd/system/mosaiq.service` |

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

## Verifying the proxy locally (no instance)

```sh
docker compose -f compose.yaml -f compose.proxy.yaml up --build
curl -sI http://localhost:8080/            # -> HTTP/1.1 200, through NGINX
docker compose -f compose.yaml -f compose.proxy.yaml down
```

If `8080` is taken, set `MOSAIQ_PROXY_PORT` (e.g. `MOSAIQ_PROXY_PORT=8088
docker compose -f compose.yaml -f compose.proxy.yaml up`).

Plain `docker compose up` is unchanged — the app stays on `http://localhost:8000`.
