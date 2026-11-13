"""experimentation: stood up empty (F25-07, RN-23, RN-24, RN-42, RN-48, RN-49).

The service answers its meta routes and one real operation, the list of sale
attributions, which is empty: no sale is credited to a campaign until F25-03
(#843) builds attribution. Every other operation of
contracts/experimentation/openapi.yaml is marked planned (ADR-0073) until the
stories after it register it and remove the marker. It is ready when PostgreSQL
answers and its own schema is there.
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
                "SELECT to_regnamespace(%s) IS NOT NULL", ("experiments",)
            ).fetchone()
        return bool(row and row[0])

    kit = Kit(
        app,
        service="experimentation",
        suffix="experiments",
        contracts_dir=Path(__file__).resolve().parent.parent / "contracts",
        ready_checks=[schema_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit

    @kit.operation("listSaleAttributions")
    def list_sale_attributions() -> Page:
        # No attribution exists until #843 builds it: the empty page is true.
        return Page(
            "SaleAttributions",
            "SaleAttribution",
            "attributions",
            [],
            page=kit.page,
            size=kit.size,
            total=0,
            info_key="page_info",
        )

    return app
