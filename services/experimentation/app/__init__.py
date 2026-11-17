"""experimentation: stood up empty (F25-07), then conversions (F25-11), then plans.

The service answers its meta routes, the list of sale attributions, which is
empty until F25-12 (#843) builds attribution, the two operations on
conversions (app/conversions.py), and the plan of an experiment: planning it,
and reading one or many back (app/plan.py, app/experiments.py). Every other operation of
contracts/experimentation/openapi.yaml is marked planned (ADR-0073) until the
stories after it register it and remove the marker. It is ready when PostgreSQL
answers and its own schema is there.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, request
from mosaiq_kit import Kit, Page, Representation, configure_logging

from app import conversions, experiments, plan


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

    @kit.operation("getExperiment")
    def get_experiment(experiment_id: int) -> Representation:
        with kit.db.connection() as connection:
            return Representation(
                "Experiment", experiments.get_experiment(connection, experiment_id)
            )

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
