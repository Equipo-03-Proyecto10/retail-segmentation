"""experimentation: experiment facts and engagement exposure projection.

The service answers its meta routes, the list of sale attributions, which is
empty until F25-12 (#843) builds attribution, and the two operations on
conversions (app/conversions.py). Real floor exposures arrive only through the
engagement stream and are projected by app/exposures.py (F25-10). Every other
operation of contracts/experimentation/openapi.yaml is marked planned
(ADR-0073) until the stories after it register it and remove the marker. It is
ready when PostgreSQL answers and its own schema is there.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, request
from mosaiq_kit import Kit, Page, Representation, configure_logging
from mosaiq_kit.auth import service_redis
from mosaiq_kit.streams import StreamConsumer

from app import conversions, exposures
from app.config import ENGAGEMENT_STREAM, Settings


def _consumer_client(url: str):
    """A blocking stream read needs longer than the request client's 200 ms."""
    client = service_redis(url, "experiments")
    client.connection_pool.connection_kwargs["socket_timeout"] = 10
    return client


def create_app() -> Flask:
    settings = Settings.from_env()
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

    def handle_exposure(fields: dict[str, str]) -> None:
        with kit.db.transaction() as connection:
            exposures.apply(connection, fields)

    app.extensions["experimentation_handle_exposure"] = handle_exposure
    if settings.stream_consumer_enabled:
        consumer = StreamConsumer(
            _consumer_client(settings.redis_state_url),
            ENGAGEMENT_STREAM,
            handle_exposure,
        )
        consumer.start()
        app.extensions["experimentation_consumer"] = consumer

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

    @kit.operation("listConversions")
    def list_conversions(experiment_id: int) -> Page:
        filters = conversions.parse_filters(request.args)
        with kit.db.connection() as connection:
            rows, total = conversions.list_conversions(
                connection, experiment_id, filters, page=kit.page, size=kit.size
            )
        return Page(
            "Conversions",
            "Conversion",
            "conversions",
            rows,
            page=kit.page,
            size=kit.size,
            total=total,
            info_key="page_info",
        )

    @kit.operation("evaluateConversions")
    def evaluate_conversions(experiment_id: int) -> Representation:
        with kit.db.transaction() as connection:
            evaluated = conversions.evaluate(connection, experiment_id)
        return Representation("ConversionEvaluation", evaluated)

    return app
