"""Infrastructure topology of the delivered system, with vendor icons.

Renders `infrastructure.svg` through Graphviz. Run it from the repository root
with the environment described in requirements.txt:

    docs/architecture/.venv/bin/python docs/architecture/infrastructure.py

Sources: deploy/README.md, deploy/nginx/mosaiq.conf, deploy/systemd/*.service,
deploy/infra/compose.yaml, docs/infra.md, bench/README.md, ADR-0013, ADR-0034,
ADR-0037, ADR-0040, ADR-0041, ADR-0042, ADR-0044.

No edge joins two services. They meet only through PostgreSQL (the producer's
api_* objects) and Redis Streams (ADR-0044), so every edge from the services
cluster ends at a datastore or comes from NGINX.
"""

import base64
import re
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.gcp.compute import ComputeEngine
from diagrams.gcp.network import FirewallRules
from diagrams.onprem.client import Client, User
from diagrams.onprem.container import Docker
from diagrams.onprem.database import Mongodb, PostgreSQL
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.network import Gunicorn, Nginx
from diagrams.programming.framework import Flask
from diagrams.saas.cdn import Cloudflare

OUT = Path(__file__).with_suffix("")

# MOSAIQ design-system colours, from docs/design-system/tokens/primitives.css.
ORANGE = "#D45400"
GREY = "#8C8C86"
RED = "#C0342B"
GREEN = "#1F7A4C"
BLUE = "#1F5FA8"

# (name, port, second wave). The port is the loopback port NGINX proxies to.
SERVICES = [
    ("auth", 5001, False),
    ("users", 5002, False),
    ("products", 5003, False),
    ("categories", 5004, False),
    ("stores", 5005, False),
    ("stock", 5006, False),
    ("customers", 5007, False),
    ("customer-preferences", 5008, False),
    ("sales", 5009, False),
    ("segments", 5010, False),
    ("recommendations", 5011, False),
    ("engagement", 5012, False),
    ("check-ins", 5013, False),
    ("campaigns", 5014, True),
    ("audit-trail", 5015, True),
]

GRAPH_ATTR = {
    "fontname": "IBM Plex Sans, sans-serif",
    "fontsize": "16",
    "bgcolor": "white",
    "pad": "0.4",
    "nodesep": "0.35",
    "ranksep": "0.9",
    "compound": "true",
}
NODE_ATTR = {"fontname": "IBM Plex Sans, sans-serif", "fontsize": "12"}
EDGE_ATTR = {"fontname": "IBM Plex Sans, sans-serif", "fontsize": "11", "color": GREY}
CLUSTER_ATTR = {
    "fontname": "IBM Plex Sans, sans-serif",
    "fontsize": "14",
    "bgcolor": "#FAFAF9",
    "pencolor": "#D5D5D1",
    "margin": "18",
}

SERVICES_LABEL = "Fifteen services, each its own compose project mosaiq-<name>"
PG_LABEL = "PostgreSQL 18 — database retail"
INFRA_LABEL = "mosaiq-infra — compose project"

with Diagram(
    "MOSAIQ — infrastructure topology (fifteen services)",
    filename=str(OUT),
    outformat="svg",
    show=False,
    direction="TB",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    android = Client("Android client\nKotlin · JSON")
    electron = Client("Electron client\nTypeScript · XML + XSD")
    visitor = User("Browser\nserver-rendered HTML")
    direct = User("Any other source\nreaching the origin address")

    with Cluster("Cloudflare edge — ADR-0013", graph_attr=CLUSTER_ATTR):
        edge_cf = Cloudflare("Managed certificate\nAlways Use HTTPS")

    with Cluster("GCP · iac-dev-01 · northamerica-south1-a", graph_attr=CLUSTER_ATTR):
        fw_https = FirewallRules("ALLOW :443\nCloudflare ranges only", fontcolor=GREEN)
        fw_deny = FirewallRules("DENY other ingress\npriority 1000", fontcolor=RED)

        with Cluster(
            "mosaiq-deployment-vm · CentOS 10 Stream", graph_attr=CLUSTER_ATTR
        ):
            vm = ComputeEngine("one instance")
            nginx = Nginx("NGINX :443\n/ → web system\n/api/<name>/ → one port each")

            with Cluster("mosaiq.service", graph_attr=CLUSTER_ATTR):
                gunicorn = Gunicorn("Gunicorn 127.0.0.1:8000")
                flask = Flask("web system\nno Redis · no service calls")

            with Cluster(SERVICES_LABEL, graph_attr=CLUSTER_ATTR):
                svc_nodes = []
                for name, port, second in SERVICES:
                    mark = "\nSECOND WAVE" if second else ""
                    svc_nodes.append(
                        Docker(
                            f"mosaiq-{name}\n/api/{name}/ · :{port}{mark}",
                            fontcolor=ORANGE if second else "black",
                        )
                    )

            bench = Docker(
                "BENCH ONLY — Rust twin\nproducts reads · :5103\nnot routed by NGINX"
            )

            with Cluster(PG_LABEL, graph_attr=CLUSTER_ATTR):
                postgres = PostgreSQL(
                    "public (29 tables, 4NF)\nservice schemas\n"
                    "api_* schemas\neight outboxes"
                )

            with Cluster(INFRA_LABEL, graph_attr=CLUSTER_ATTR):
                redis_state = Redis("redis-state\nsessions · Streams · limits")
                redis_cache = Redis("redis-cache\ncaches · counters")
                mongo = Mongodb("MongoDB\nengagement")

    android >> Edge(label="HTTPS · JSON", color=BLUE) >> edge_cf
    electron >> Edge(label="HTTPS · XML", color=BLUE) >> edge_cf
    visitor >> Edge(label="HTTPS", color=BLUE) >> edge_cf
    edge_cf >> Edge(label="edge IPs only") >> fw_https
    direct >> Edge(label="no route", style="dashed", color=RED) >> fw_deny
    fw_https >> Edge(color=ORANGE) >> nginx

    nginx >> Edge(label="/", color=ORANGE) >> gunicorn
    gunicorn >> flask
    flask >> Edge(label="SQL as retail_app", color=ORANGE) >> postgres

    # NGINX to the services: one compound edge for the fifteen routes. Services
    # to the datastores: one compound edge from the cluster, not fifteen.
    (
        nginx
        >> Edge(
            label="/api/<name>/ routes",
            color=ORANGE,
            lhead="cluster_" + SERVICES_LABEL,
        )
        >> svc_nodes[0]
    )
    (
        svc_nodes[-1]
        >> Edge(
            label="own tables · api_* reads\nsvc_<name> roles",
            ltail="cluster_" + SERVICES_LABEL,
            lhead="cluster_" + PG_LABEL,
        )
        >> postgres
    )
    (
        postgres
        >> Edge(
            label="the owner relays its outbox · XADD",
            lhead="cluster_" + INFRA_LABEL,
        )
        >> redis_state
    )
    (
        redis_state
        >> Edge(
            label="consumer groups · XREADGROUP",
            style="dashed",
            ltail="cluster_" + INFRA_LABEL,
            lhead="cluster_" + SERVICES_LABEL,
        )
        >> svc_nodes[0]
    )
    (
        svc_nodes[-1]
        >> Edge(label="caches", ltail="cluster_" + SERVICES_LABEL)
        >> redis_cache
    )
    svc_nodes[11] >> Edge(label="engagement only") >> mongo
    bench >> Edge(label="svc_products_bench", style="dashed") >> postgres


def embed_icons(svg: Path) -> None:
    """Inline every icon as a data URI so the committed SVG holds no local path."""

    def inline(match: re.Match) -> str:
        data = Path(match.group(1)).read_bytes()
        return (
            'xlink:href="data:image/png;base64,' + base64.b64encode(data).decode() + '"'
        )

    text = svg.read_text()
    svg.write_text(re.sub(r'xlink:href="(/[^"]+\.png)"', inline, text))


embed_icons(OUT.with_suffix(".svg"))
