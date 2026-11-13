"""rfm-engine: stood up empty (F22-04, RN-65).

The service answers its meta routes and refuses every other request until the
stories after it (F22-05 onwards) register the operations of
contracts/rfm-engine/openapi.yaml. It is ready when PostgreSQL answers and its
own schema is there.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask
from mosaiq_kit import Kit, configure_logging


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
    return app
