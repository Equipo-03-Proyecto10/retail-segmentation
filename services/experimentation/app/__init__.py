"""experimentation: stood up empty (F25-07), then conversions, plans and exposures.

The service answers its meta routes, the list of sale attributions, which is
empty until F25-12 (#843) builds attribution, the two operations on
conversions (app/conversions.py), and the plan of an experiment: planning it,
and reading one or many back (app/plan.py, app/experiments.py). Real floor
exposures arrive only through the engagement stream and are projected by
app/exposures.py (F25-10). Every other operation of
contracts/experimentation/openapi.yaml is marked planned (ADR-0073) until the
stories after it register it and remove the marker. It is ready when PostgreSQL
answers and its own schema is there.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, request
from mosaiq_kit import Kit, Page, Representation, configure_logging
from mosaiq_kit.auth import service_redis
from mosaiq_kit.streams import StreamConsumer

from app import (
    assignment,
    attribution,
    conversions,
    experiments,
    exposures,
    plan,
    results,
)
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
        filters = attribution.parse_filters(request.args)
        with kit.db.connection() as connection:
            rows, total = attribution.list_attributions(
                connection, filters, page=kit.page, size=kit.size
            )
        return Page(
            "SaleAttributions",
            "SaleAttribution",
            "attributions",
            rows,
            page=kit.page,
            size=kit.size,
            total=total,
            info_key="page_info",
        )

    @kit.operation("getSaleAttribution")
    def get_sale_attribution(attribution_id: int) -> Representation:
        with kit.db.connection() as connection:
            return Representation(
                "SaleAttribution",
                attribution.get_attribution(connection, attribution_id),
            )

    @kit.operation("evaluateAttributions")
    def evaluate_attributions(experiment_id: int) -> Representation:
        with kit.db.transaction() as connection:
            evaluated = attribution.evaluate(connection, experiment_id)
        return Representation("AttributionEvaluation", evaluated)

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

    @kit.operation("planExperiment")
    def plan_experiment() -> Representation:
        body = kit.body
        actor = str(kit.principal["sub"])
        with kit.db.transaction() as connection:
            experiment_id = plan.plan(connection, body, actor=actor)
            created = experiments.get_experiment(connection, experiment_id)
        response = Representation("Experiment", created, status=201)
        response.headers = {"Location": f"/v1/experiments/{experiment_id}"}
        return response

    @kit.operation("assignExperiment")
    def assign_experiment() -> Representation:
        body = kit.body
        actor = str(kit.principal["sub"])
        with kit.db.transaction() as connection:
            summary = assignment.assign(
                connection,
                body["experiment_id"],
                actor=actor,
                acknowledge_underpowered=bool(body.get("acknowledge_underpowered")),
            )
        return Representation("AssignmentSummary", summary, status=201)

    @kit.operation("listExperimentAssignments")
    def list_experiment_assignments() -> Page:
        filters = assignment.parse_filters(request.args)
        with kit.db.connection() as connection:
            rows, total = assignment.list_assignments(
                connection, filters, page=kit.page, size=kit.size
            )
        return Page(
            "ExperimentAssignments",
            "ExperimentAssignment",
            "assignments",
            rows,
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("getExperiment")
    def get_experiment(experiment_id: int) -> Representation:
        with kit.db.connection() as connection:
            return Representation(
                "Experiment", experiments.get_experiment(connection, experiment_id)
            )

    @kit.operation("getExperimentResult")
    def get_experiment_result(experiment_id: int) -> Representation:
        basis = results.parse_basis(request.args)
        # Reading may record the sales not recorded yet and, for the first primary
        # read at or after the analysis instant, store the result (app/results.py).
        with kit.db.transaction() as connection:
            result = results.get_result(connection, experiment_id, basis)
        return Representation("ExperimentResult", result)

    @kit.operation("listExperiments")
    def list_experiments() -> Page:
        filters = experiments.parse_filters(request.args)
        with kit.db.connection() as connection:
            rows, total = experiments.list_experiments(
                connection, filters, page=kit.page, size=kit.size
            )
        return Page(
            "Experiments",
            "Experiment",
            "experiments",
            rows,
            page=kit.page,
            size=kit.size,
            total=total,
            info_key="page_info",
        )

    return app
