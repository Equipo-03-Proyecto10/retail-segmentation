"""The Flask application: register a draft version, read versions and their
publications. Publication, rollback and training are their own stories
(F22-13, F22-12); their operations are marked planned in the contract."""

from __future__ import annotations

from typing import Any

import psycopg
from flask import Flask, request
from mosaiq_kit import ApiError, Kit, Page, Representation, configure_logging

from . import rules, store
from .config import Settings

METHODS = ("RFM_RULES", "KMEANS")
STATES = ("DRAFT", "PUBLISHED", "RETIRED")
NAME_CONSTRAINT = "model_version_name_key"


def _invalid_query(field: str, allowed: tuple[str, ...]) -> ApiError:
    return ApiError(
        422,
        "invalid_query",
        f"{field} must be one of {', '.join(allowed)}.",
        [{"field": field, "code": "invalid_value"}],
    )


def _filter(args: Any, name: str, allowed: tuple[str, ...]) -> str | None:
    value = args.get(name)
    if value is None:
        return None
    if value not in allowed:
        raise _invalid_query(name, allowed)
    return value


def _scoring(stored: dict[str, Any]) -> dict[str, Any]:
    """The frozen scoring in the contract's member order, which the XML
    sequence needs and jsonb does not keep."""
    boundaries = stored["quintile_boundaries"]
    bounds = stored["normalization_bounds"]
    return {
        "quintile_boundaries": (
            None
            if boundaries is None
            else {
                key: boundaries[key]
                for key in (
                    "recency_boundaries",
                    "frequency_boundaries",
                    "monetary_boundaries",
                )
            }
        ),
        "centroids": [
            {key: c[key] for key in ("cluster_index", "label_code", "r", "f", "m")}
            for c in stored["centroids"]
        ],
        "normalization_bounds": (
            None
            if bounds is None
            else {
                key: bounds[key]
                for key in ("r_min", "r_max", "f_min", "f_max", "m_min", "m_max")
            }
        ),
    }


def _reference(stored: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_size": stored["sample_size"],
        "captured_at": stored["captured_at"],
        "features": [
            {key: f[key] for key in ("feature", "bin_edges", "proportions")}
            for f in stored["features"]
        ],
        "labels": [
            {key: label[key] for key in ("label_code", "share")}
            for label in stored["labels"]
        ],
    }


def version_view(row: dict[str, Any]) -> dict[str, Any]:
    """One version as `model-versions` serves it."""
    return {
        "version_id": row["version_id"],
        "name": row["name"],
        "method": row["method"],
        "state": row["state"],
        "source": row["source"],
        "window_days": row["window_days"],
        "seed": row["seed"],
        "parameters": {
            "rule_set_id": row["rule_set_id"],
            "k": row["k"],
            "max_iterations": row["max_iterations"],
            "tolerance": row["tolerance"],
            "features": row["features"],
            "normalization": row["normalization"],
        },
        "frozen_scoring": _scoring(row["frozen_scoring"]),
        "reference_distribution": _reference(row["reference_distribution"]),
        "dataset": {
            "fingerprint": row["fingerprint"],
            "origin": row["origin"],
            "row_count": row["row_count"],
        },
        "metrics": {
            "inertia": row["inertia"],
            "silhouette": row["silhouette"],
            "stability": row["stability"],
        },
        "owner_id": row["owner_id"],
        "source_run_id": row["source_run_id"],
        "training_id": None,
        "created_at": row["created_at"],
        "published_at": row["published_at"],
        "retired_at": row["retired_at"],
    }


def create_app() -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__)
    configure_logging()
    holder: dict[str, Kit] = {}

    def database_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            connection.execute("SELECT version_id FROM models.model_version LIMIT 1")
        return True

    kit = Kit(
        app,
        service="model-registry",
        suffix="models",
        contracts_dir=settings.contracts_dir,
        ready_checks=[database_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit

    @kit.operation("listModelVersions")
    def list_model_versions() -> Page:
        method = _filter(request.args, "method", METHODS)
        state = _filter(request.args, "state", STATES)
        with kit.db.connection() as connection:
            rows, total = store.list_versions(
                connection, method, state, kit.size, (kit.page - 1) * kit.size
            )
        return Page(
            "ModelVersions",
            "ModelVersion",
            "versions",
            [version_view(row) for row in rows],
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("getModelVersion")
    def get_model_version(version_id: int) -> Representation:
        with kit.db.connection() as connection:
            row = store.get_version(connection, version_id)
        if row is None:
            raise _version_not_found()
        return Representation("ModelVersion", version_view(row))

    @kit.operation("createModelVersion")
    def create_model_version() -> Representation:
        body = kit.body
        details = rules.invalid_details(body)
        if details:
            raise ApiError(
                422,
                "invalid_body",
                "The version breaks its rules.",
                details,
            )
        try:
            with kit.db.transaction() as connection:
                if store.name_taken(connection, body["name"]):
                    raise _name_taken(body["name"])
                version_id = store.insert_version(
                    connection, body, str(kit.principal["sub"])
                )
                row = store.get_version(connection, version_id)
        except psycopg.errors.UniqueViolation as error:
            # Another registration took the name between the check and the insert.
            if error.diag.constraint_name == NAME_CONSTRAINT:
                raise _name_taken(body["name"]) from None
            raise
        return Representation(
            "ModelVersion",
            version_view(row),
            status=201,
            headers={"Location": f"/v1/model-versions/{version_id}"},
        )

    @kit.operation("listModelPublications")
    def list_model_publications(version_id: int) -> Page:
        with kit.db.connection() as connection:
            if store.get_version(connection, version_id) is None:
                raise _version_not_found()
            rows, total = store.list_publications(
                connection, version_id, kit.size, (kit.page - 1) * kit.size
            )
        return Page(
            "ModelPublications",
            "ModelPublication",
            "publications",
            rows,
            page=kit.page,
            size=kit.size,
            total=total,
        )

    return app


def _version_not_found() -> ApiError:
    return ApiError(404, "model_version_not_found", "The model version does not exist.")


def _name_taken(name: str) -> ApiError:
    return ApiError(
        409,
        "model_version_name_taken",
        f"A model version named {name} already exists.",
        [{"field": "name", "code": "taken"}],
    )
