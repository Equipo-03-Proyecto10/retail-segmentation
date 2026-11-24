"""Render an isolated local stack for the Locust run (F29-15, #1191).

    python3 bench/locust/stack.py render --project p1191 --tag p831 \
        > <scratch dir>/compose.json

It starts from `docker compose config` of compose.distributed.yaml, keeps the
services the four profiles reach (and the infrastructure they need), and adds
the web behind the local NGINX of compose.proxy.yaml and the ops console. So
that it can run beside any other stack on the same machine, it:

- renames the external network `mosaiq` and the two fixed-name volumes
  (`mosaiq-pgsocket`, `mosaiq-redis-acl`) after the project;
- publishes only three loopback ports: the gateway, the web's NGINX and the
  ops console;
- drops every `build:`, so nothing is built: the images must already exist
  under `--tag`, or the tag `--image <service>=<tag>` gives that service
  (built from the commit under test, or pulled from GHCR).

Every service keeps the memory and CPU limits of its own compose file, which
are the instance's. Nothing here is used on the instance.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The services the four user classes reach, plus the one each of them needs to
# be complete: sales' import worker shares sales' image and project.
SERVICES = (
    "auth",
    "users",
    "products",
    "stock",
    "sales",
    "sales-import-worker",
    "analytics-kpis",
    "audit-trail",
)
# Every container whose image is a service's; all the others (PostgreSQL, both
# Redis instances, the document store, their one-shot steps and the gateway)
# are the infrastructure, and all of it is kept.
SERVICE_IMAGE = "ghcr.io/equipo-03-proyecto10/mosaiq-"
# The ops console's Redis password is never written by this script, nor into
# the rendered file: compose reads it from the caller's environment when the
# stack comes up, and the redis-acl step writes the env file assemble-acl.sh
# needs into a tmpfs of its own container.
OPS_CONSOLE_SECRET = "${MOSAIQ_LOCUST_OPS_CONSOLE_PASSWORD:-}"
ACL_STEP = (
    'test -n "$$REDIS_OPS_CONSOLE_PASSWORD" '
    '|| { echo "MOSAIQ_LOCUST_OPS_CONSOLE_PASSWORD is not set" >&2; exit 1; }; '
    "cp /repo/docker/distributed/svc/*.env /svc/ && "
    'printf "REDIS_OPS_CONSOLE_PASSWORD=%s\\n" "$$REDIS_OPS_CONSOLE_PASSWORD" '
    ">/svc/ops-console.env && "
    "exec bash /repo/deploy/infra/redis/assemble-acl.sh --write-only"
)


def base_config(project: str, tag: str) -> dict:
    env = os.environ | {"IMAGE_TAG": tag}
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(ROOT / "compose.distributed.yaml"),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def render(args: argparse.Namespace) -> dict:
    project, tag = args.project, args.tag
    config = base_config(project, tag)
    overrides = dict(item.split("=", 1) for item in args.image)
    keep = {
        name
        for name, service in config["services"].items()
        if name in SERVICES or not service.get("image", "").startswith(SERVICE_IMAGE)
    }
    services = {k: v for k, v in config["services"].items() if k in keep}
    missing = set(SERVICES) - set(services)
    if missing:
        raise SystemExit(f"compose.distributed.yaml has no {sorted(missing)}")

    for name, service in services.items():
        service.pop("build", None)
        if name in overrides and "image" in service:
            service["image"] = (
                service["image"].rsplit(":", 1)[0] + ":" + overrides[name]
            )
        if name != "gateway":
            service.pop("ports", None)
        for dependency in list(service.get("depends_on", {})):
            if dependency not in keep:
                del service["depends_on"][dependency]

    services["gateway"]["ports"] = [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 8080,
            "published": str(args.gateway_port),
            "protocol": "tcp",
        }
    ]

    # The ops console's ACL line joins the services' in the aclfile, as
    # assemble-acl.sh does on the instance (its default ACL_SOURCES).
    acl = services["redis-acl"]
    acl["environment"][
        "ACL_SOURCES"
    ] = "/repo/services/*/redis/*.acl /repo/ops/*/redis/*.acl"
    acl["environment"]["SVC_ENV_DIR"] = "/svc"
    acl["environment"]["REDIS_OPS_CONSOLE_PASSWORD"] = OPS_CONSOLE_SECRET
    acl["entrypoint"] = ["bash", "-c", ACL_STEP]
    acl["tmpfs"] = [*acl.get("tmpfs", []), "/svc:mode=0700"]

    web_env = {
        "DATABASE_URL": "postgresql://retail_app:retail_app@db:5432/retail",
        "FLASK_SECRET_KEY": "locust-local-only",
        "FLASK_ENV": "development",
        "SESSION_COOKIE_SECURE": "false",
        "TRUSTED_PROXY_HOPS": "1",
        "UPLOAD_DIR": "web/uploads",
    }
    services["web"] = {
        "image": f"mosaiq-web:{project}",
        "environment": web_env,
        "depends_on": {"db": {"condition": "service_healthy", "required": True}},
        "networks": {"default": None},
        "restart": "unless-stopped",
    }
    services["proxy"] = {
        "image": "nginx:1.27-alpine",
        "depends_on": {"web": {"condition": "service_started", "required": True}},
        "volumes": [
            {
                "type": "bind",
                "source": str(ROOT / "deploy/nginx/mosaiq.compose.conf"),
                "target": "/etc/nginx/conf.d/default.conf",
                "read_only": True,
            }
        ],
        "ports": [
            {
                "mode": "ingress",
                "host_ip": "127.0.0.1",
                "target": 80,
                "published": str(args.web_port),
                "protocol": "tcp",
            }
        ],
        "networks": {"default": None},
        "restart": "unless-stopped",
    }
    services["ops-console"] = {
        "image": f"mosaiq-ops-console:{project}",
        "user": "10001:10001",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": ["/tmp:uid=10001,gid=10001,mode=0700"],
        "mem_limit": "96m",
        "cpus": 0.25,
        "environment": {"REDIS_OPS_CONSOLE_PASSWORD": OPS_CONSOLE_SECRET},
        "depends_on": {
            "redis-state": {"condition": "service_started", "required": True}
        },
        "ports": [
            {
                "mode": "ingress",
                "host_ip": "127.0.0.1",
                "target": 5090,
                "published": str(args.console_port),
                "protocol": "tcp",
            }
        ],
        "networks": {"mosaiq": None},
        "restart": "unless-stopped",
    }

    config["services"] = services
    config["networks"]["mosaiq"] = {"name": f"{project}-mosaiq", "external": True}
    volumes = config["volumes"]
    volumes["mosaiq-pgsocket"] = {"name": f"{project}-pgsocket"}
    volumes["mosaiq-redis-acl"] = {"name": f"{project}-redis-acl"}
    return config


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render", help="print the isolated stack's compose JSON")
    r.add_argument("--project", default="p1191")
    r.add_argument("--tag", required=True, help="the service images' tag")
    r.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="SERVICE=TAG",
        help="another tag for one service's image (repeatable)",
    )
    r.add_argument("--gateway-port", type=int, default=18191)
    r.add_argument("--web-port", type=int, default=18192)
    r.add_argument("--console-port", type=int, default=18193)
    args = parser.parse_args(argv)
    json.dump(render(args), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
