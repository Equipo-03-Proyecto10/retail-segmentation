# Deploying MOSAIQ on the instance

The GCP instance `mosaiq-deployment-vm` (CentOS Stream 10) runs the application
as **gunicorn under systemd behind NGINX** — [ADR-0006](../docs/adr/0006-run-under-both-systemd-and-docker-compose.md),
[ADR-0009](../docs/adr/0009-nginx-as-the-reverse-proxy.md). This directory holds
what is applied there:

| File | Goes to |
|---|---|
| `nginx/mosaiq.conf` | `/etc/nginx/conf.d/mosaiq.conf` |
| `nginx/mosaiq.compose.conf` | not deployed — local verification only, see `../compose.proxy.yaml` |

The systemd unit is F6-02 (#78) and is not here yet.

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

7. **Have the app answering on `127.0.0.1:8000`.** For F6-01 on its own, run it
   by hand:
   ```sh
   cd /path/to/checkout
   gunicorn --bind 127.0.0.1:8000 "web.app:create_app()"
   ```
   F6-02 replaces this with a systemd unit that starts on boot. Set
   `TRUSTED_PROXY_HOPS=1` and a real `FLASK_SECRET_KEY` in the app's
   environment.

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

## Verifying the proxy locally (no instance)

```sh
docker compose -f compose.yaml -f compose.proxy.yaml up --build
curl -sI http://localhost:8080/            # -> HTTP/1.1 200, through NGINX
docker compose -f compose.yaml -f compose.proxy.yaml down
```

If `8080` is taken, set `MOSAIQ_PROXY_PORT` (e.g. `MOSAIQ_PROXY_PORT=8088
docker compose -f compose.yaml -f compose.proxy.yaml up`).

Plain `docker compose up` is unchanged — the app stays on `http://localhost:8000`.
