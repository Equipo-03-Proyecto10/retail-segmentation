# ops-console

A status panel over the services ([ADR-0055](../../docs/adr/0055-an-ops-console-supervises-services-through-their-meta-routes.md)).
It is not a microservice: it implements no business rule and is not in
`contracts/registry.yaml`. It reads, every 10 s:

- each registered service's `/health/live`, `/health/ready` and, every 5 min,
  the `info.version` of its `/openapi.yaml`, over the `mosaiq` network;
- `/run/mosaiq/ops/containers.json`, written by `host/mosaiq-ops-snapshot.sh`
  on the host, so the container never sees the Docker socket;
- `INFO memory` and `XINFO GROUPS` for every `stream:*` on both Redis
  instances, as the read-only user in `redis/ops-console.acl`.

It keeps one hour of readiness samples in memory and serves `/` (HTML,
refreshing itself) and `/status.json` on `127.0.0.1:5090`.

## Develop

```sh
cd ops/console
cp ../../contracts/registry.yaml registry.yaml   # git-ignored; CI does the same
uv sync
uv run black --check . && uv run ruff check . && uv run pytest
```

Against services published on the host's loopback:

```sh
OPS_CONSOLE_REGISTRY=registry.yaml OPS_CONSOLE_HOST_TEMPLATE=127.0.0.1 \
OPS_CONSOLE_SNAPSHOT=/tmp/containers.json \
  uv run flask --app 'app:create_app()' run --port 5090
host/mosaiq-ops-snapshot.sh /tmp/containers.json     # once, or in a loop
```

The registry is baked into the image: adding a service to the registry needs
a `VERSION` bump here to show it.

## Install on the instance (once)

```sh
sudo install -d /opt/mosaiq/ops-console
sudo install -m 0755 host/mosaiq-ops-snapshot.sh /opt/mosaiq/ops-console/
sudo install -m 0644 compose.yaml /opt/mosaiq/ops-console/
echo "IMAGE_TAG=$(cat VERSION)" | sudo tee /opt/mosaiq/ops-console/image.env
sudo install -m 0644 host/mosaiq-ops-snapshot.{service,timer} host/mosaiq-ops-console.service \
  /etc/systemd/system/

# The Redis password, generated once, then the aclfile reassembled with it.
pw=$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
printf 'REDIS_OPS_CONSOLE_PASSWORD=%s\n' "$pw" | sudo tee /etc/mosaiq/svc/ops-console.env >/dev/null
sudo chmod 0600 /etc/mosaiq/svc/ops-console.env
sudo REDIS_OPS_PASSWORD=… deploy/infra/redis/assemble-acl.sh   # from the checkout

docker pull ghcr.io/equipo-03-proyecto10/mosaiq-ops-console:$(cat VERSION)
sudo systemctl daemon-reload
sudo systemctl enable --now mosaiq-ops-snapshot.timer mosaiq-ops-console.service
curl -s localhost:5090/status.json | head -c 300
```

To update, bump `VERSION`, merge, then set `IMAGE_TAG` in
`/opt/mosaiq/ops-console/image.env`, pull and
`sudo systemctl restart mosaiq-ops-console`.

## Open it

```sh
gcloud compute ssh mosaiq-deployment-vm --zone=northamerica-south1-a \
  -- -N -L 5090:127.0.0.1:5090
```

then <http://localhost:5090>.
