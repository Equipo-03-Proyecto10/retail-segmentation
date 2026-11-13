"""rfm-engine: stood up empty (F22-04, RN-65).

The service answers its meta routes and one real operation, the list of rule
sets, which is empty. Every other operation of contracts/rfm-engine/openapi.yaml
is marked planned (ADR-0073) until the stories after it (F22-05 onwards) register
it and remove the marker. It is ready when PostgreSQL answers and its
own schema is there.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask
from mosaiq_kit import Kit, Page, configure_logging


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    configure_logging()
    holder: dict[str, Kit] = {}

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            row = connection.execute(
                "SELECT to_regnamespace(%s) IS NOT NULL", ("rfm",)
            ).fetchone()
        return bool(row and row[0])

    kit = Kit(
        app,
        service="rfm-engine",
        suffix="rfm",
        contracts_dir=Path(__file__).resolve().parent.parent / "contracts",
        ready_checks=[schema_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit

    @kit.operation("listRfmRuleSets")
    def list_rule_sets() -> Page:
        # No rule set exists until F22-09 creates them: the empty page is true.
        return Page(
            "RfmRuleSets",
            "RfmRuleSet",
            "rule_sets",
            [],
            page=kit.page,
            size=kit.size,
            total=0,
            info_key="page_info",
        )

    return app
