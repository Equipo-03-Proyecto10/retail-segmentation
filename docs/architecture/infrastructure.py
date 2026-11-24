"""Infrastructure topology of the delivered system, with vendor icons.

Renders `infrastructure.svg` through Graphviz. Run it from the repository root
with the environment described in requirements.txt:

    docs/architecture/.venv/bin/python docs/architecture/infrastructure.py

Sources: deploy/README.md, deploy/nginx/mosaiq.conf, deploy/systemd/*.service,
deploy/infra/compose.yaml, docs/infra.md, bench/README.md, ops/console/README.md,
ADR-0013, ADR-0034, ADR-0037, ADR-0040, ADR-0041, ADR-0042, ADR-0044, ADR-0055,
ADR-0059.

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

# (name, port, wave). The port is the loopback port NGINX proxies to. Wave 1 is
# the core, wave 2 the second wave of ADR-0034, wave 3 the four of ADR-0059.
SERVICES = [
    ("auth", 5001, 1),
    ("users", 5002, 1),
    ("products", 5003, 1),
    ("categories", 5004, 1),
    ("stores", 5005, 1),
    ("stock", 5006, 1),
    ("customers", 5007, 1),
    ("customer-preferences", 5008, 1),
    ("sales", 5009, 1),
    ("segments", 5010, 1),
    ("recommendations", 5011, 1),
    ("engagement", 5012, 1),
    ("check-ins", 5013, 1),
    ("campaigns", 5014, 2),
    ("audit-trail", 5015, 2),
    ("rfm-engine", 5016, 3),
    ("model-registry", 5019, 3),
    ("experimentation", 5020, 3),
    ("analytics-kpis", 5022, 3),
]
WAVE_MARK = {1: "", 2: "\nSECOND WAVE", 3: "\nADR-0059"}
ROW = 4  # services per row in the services cluster

GRAPH_ATTR = {
    "fontname": "IBM Plex Sans, sans-serif",
    "fontsize": "16",
    "bgcolor": "white",
    "pad": "0.4",
    "nodesep": "0.8",
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

SERVICES_LABEL = "Nineteen services, each its own compose project mosaiq-<name>"
PG_LABEL = "PostgreSQL 18 — database retail"
INFRA_LABEL = "mosaiq-infra — compose project"

with Diagram(
    "MOSAIQ — infrastructure topology (nineteen services)",
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
    operator = User("Team operator\ngcloud compute ssh")

    with Cluster("Cloudflare edge — ADR-0013", graph_attr=CLUSTER_ATTR):
        edge_cf = Cloudflare("Managed certificate\nAlways Use HTTPS")

    with Cluster("GCP · iac-dev-01 · northamerica-south1-a", graph_attr=CLUSTER_ATTR):
        fw_https = FirewallRules("ALLOW :443\nCloudflare ranges only", fontcolor=GREEN)
        fw_ssh = FirewallRules("ALLOW :22\nOS Login", fontcolor=GREEN)
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
                for name, port, wave in SERVICES:
                    svc_nodes.append(
                        Docker(
                            f"mosaiq-{name}\n/api/{name}/ · :{port}{WAVE_MARK[wave]}",
                            fontcolor=ORANGE if wave > 1 else "black",
                        )
                    )
                # Four to a row, in registry order, so nineteen labels do not
                # overlap on a single rank. An invisible edge stacks each node
                # above the one four places later; a flat invisible edge between
                # row neighbours keeps them left to right. Four, not five, puts
                # engagement in the right-hand column, nearest MongoDB.
                for upper, lower in zip(svc_nodes, svc_nodes[ROW:], strict=False):
                    upper >> Edge(style="invis") >> lower
                for i, (left, right) in enumerate(
                    zip(svc_nodes, svc_nodes[1:], strict=False)
                ):
                    if (i + 1) % ROW:
                        left >> Edge(style="invis", minlen="0") >> right

            bench = Docker(
                "BENCH ONLY — Rust twin\nproducts reads · :5103\nnot routed by NGINX"
            )
            ops = Docker(
                "OPS ONLY — ops console\n127.0.0.1:5090 · SSH tunnel only\n"
                "not routed by NGINX (ADR-0055)"
            )

            with Cluster(PG_LABEL, graph_attr=CLUSTER_ATTR):
                postgres = PostgreSQL(
                    "public (29 tables, 4NF)\nservice schemas\n"
                    "17 api_* schemas\neleven outboxes"
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
    operator >> Edge(label="SSH :22", color=BLUE) >> fw_ssh
    fw_ssh >> Edge(label="tunnel -L 5090", style="dashed") >> ops

    nginx >> Edge(label="/", color=ORANGE) >> gunicorn
    gunicorn >> flask
    flask >> Edge(label="SQL as retail_app", color=ORANGE) >> postgres

    # NGINX to the services: one compound edge for the nineteen routes. Services
    # to the datastores: one compound edge from the cluster, not nineteen. Each
    # compound edge is anchored on the node nearest the side it leaves or enters
    # by, or Graphviz reorders the columns to shorten it.
    (
        nginx
        >> Edge(
            label="/api/<name>/ routes",
            color=ORANGE,
            lhead="cluster_" + SERVICES_LABEL,
        )
        >> svc_nodes[1]
    )
    (
        svc_nodes[16]
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
        >> svc_nodes[ROW - 1]
    )
    (
        svc_nodes[17]
        >> Edge(label="caches", ltail="cluster_" + SERVICES_LABEL)
        >> redis_cache
    )
    svc_nodes[11] >> Edge(label="engagement only") >> mongo
    bench >> Edge(label="svc_products_bench", style="dashed") >> postgres
    (
        ops
        >> Edge(
            label="health · versions",
            style="dashed",
            lhead="cluster_" + SERVICES_LABEL,
        )
        >> svc_nodes[ROW - 1]
    )
    ops >> Edge(label="XINFO GROUPS", style="dashed") >> redis_state


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
