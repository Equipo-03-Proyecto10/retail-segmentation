"""Infrastructure topology of the delivered system, with vendor icons.

Renders `infrastructure.svg` through Graphviz. Run it from the repository root
with the environment described in requirements.txt:

    docs/architecture/.venv/bin/python docs/architecture/infrastructure.py

Sources: docs/scope.md section 3, docs/infra.md, deploy/nginx/mosaiq.conf,
deploy/systemd/mosaiq.service, deploy/deploy.sh, ADR-0009, ADR-0013.
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.gcp.compute import ComputeEngine
from diagrams.gcp.network import FirewallRules
from diagrams.generic.storage import Storage
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.client import User
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.logging import Rsyslog
from diagrams.onprem.network import Gunicorn, Nginx
from diagrams.programming.framework import Flask
from diagrams.saas.cdn import Cloudflare

OUT = Path(__file__).with_suffix("")

# MOSAIQ design-system colours, from docs/design-system/tokens/primitives.css.
ORANGE = "#D45400"
GREY = "#8C8C86"
RED = "#C0342B"
GREEN = "#1F7A4C"

GRAPH_ATTR = {
    "fontname": "IBM Plex Sans, sans-serif",
    "fontsize": "16",
    "bgcolor": "white",
    "pad": "0.4",
    "nodesep": "0.5",
    "ranksep": "0.8",
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

with Diagram(
    "MOSAIQ — infrastructure topology (first delivery)",
    filename=str(OUT),
    outformat="svg",
    show=False,
    direction="LR",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    visitor = User("Visitor browser")
    direct = User("Any other source\nreaching the origin address")
    ci = GithubActions("deploy.yml\nafter a merge to main")

    with Cluster("Cloudflare edge — ADR-0013", graph_attr=CLUSTER_ATTR):
        edge_cf = Cloudflare("Managed certificate\nAlways Use HTTPS\nCF-Connecting-IP")

    with Cluster("GCP · iac-dev-01 · northamerica-south1-a", graph_attr=CLUSTER_ATTR):
        with Cluster("VPC ingress — tag mosaiq-server", graph_attr=CLUSTER_ATTR):
            fw_https = FirewallRules(
                "ALLOW :443\nCloudflare ranges only", fontcolor=GREEN
            )
            fw_ssh = FirewallRules("ALLOW :22\nOS Login", fontcolor=GREEN)
            fw_deny = FirewallRules("DENY other ingress\npriority 1000", fontcolor=RED)

        with Cluster(
            "mosaiq-deployment-vm · e2-standard-2 · CentOS 10 Stream",
            graph_attr=CLUSTER_ATTR,
        ):
            vm = ComputeEngine("50 GB pd-balanced")
            nginx = Nginx("TLS termination\nHSTS · CSP · 5 MiB cap")
            gunicorn = Gunicorn("2 workers, systemd\n127.0.0.1:8000")
            flask = Flask("server-rendered HTML")
            postgres = PostgreSQL("retail — 19 tables, 4NF\n127.0.0.1:5432")
            uploads = Storage("web/uploads/")
            journal = Rsyslog("journald")

    visitor >> Edge(label="HTTPS") >> edge_cf
    edge_cf >> Edge(label="edge IPs only") >> fw_https
    ci >> Edge(label="SSH") >> fw_ssh
    (
        direct
        >> Edge(label="no route past this rule", style="dashed", color=RED)
        >> fw_deny
    )

    fw_https >> Edge(color=ORANGE) >> nginx
    fw_ssh >> Edge(label="deploy.sh", style="dashed") >> vm
    nginx >> Edge(label="proxy_pass\nX-Forwarded-For", color=ORANGE) >> gunicorn
    gunicorn >> Edge(color=ORANGE) >> flask
    flask >> Edge(label="psycopg\nparameterized SQL", color=ORANGE) >> postgres
    flask >> Edge(label="uploads") >> uploads
    gunicorn >> Edge(label="stdout / stderr") >> journal
