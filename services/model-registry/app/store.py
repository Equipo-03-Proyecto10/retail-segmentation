"""Every statement model-registry runs, parameterized, against `models.*`.

The registry owns these tables (ADR-0046) and no other service reads them:
others read the versions through `api_models` views. Nothing here decides
anything: what a valid version is lives in rules.py, and the tables refuse the
rest (immutability, one PUBLISHED row) with triggers and indexes.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

_SELECT_VERSION = (
    "SELECT v.version_id, v.name, v.method, v.state, v.source, v.window_days,"
    " v.seed, v.rule_set_id, v.k, v.max_iterations, v.tolerance, v.normalization,"
    " ARRAY(SELECT f.feature::text FROM models.model_version_feature AS f"
    "        WHERE f.version_id = v.version_id"
    "        ORDER BY array_position(ARRAY['R', 'F', 'M'], f.feature::text)),"
    " v.frozen_scoring, v.reference_distribution,"
    " v.dataset_fingerprint, v.dataset_origin, v.dataset_row_count,"
    " v.inertia, v.silhouette, v.stability, v.owner_id::text,"
    " (SELECT min(r.run_id) FROM models.run_version AS r"
    "   WHERE r.version_id = v.version_id AND v.source = 'WEB_RUN'),"
    " v.created_at, v.published_at, v.retired_at"
    " FROM models.model_version AS v"
)
_COUNT_VERSION = "SELECT count(*) FROM models.model_version AS v"
_FILTER = (
    " WHERE (%s::text IS NULL OR v.method = %s)"
    " AND (%s::text IS NULL OR v.state = %s)"
)
_PAGE = " ORDER BY v.version_id DESC LIMIT %s OFFSET %s"
VERSION_COLUMNS = (
    "version_id name method state source window_days seed rule_set_id k"
    " max_iterations tolerance normalization features frozen_scoring"
    " reference_distribution fingerprint origin row_count inertia silhouette"
    " stability owner_id source_run_id created_at published_at retired_at"
).split()


def _json(value: Any) -> Jsonb:
    """The kit reads numbers in a body as Decimal; jsonb stores them as numbers."""

    def encode(item: Any) -> float:
        if isinstance(item, Decimal):
            return float(item)
        raise TypeError(f"{type(item).__name__} is not JSON serializable")

    return Jsonb(value, dumps=lambda obj: json.dumps(obj, default=encode))


def _version(row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(VERSION_COLUMNS, row, strict=True))


def list_versions(
    connection: Connection[Any],
    method: str | None,
    state: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """A page of versions, newest first, and how many match the filters."""
    # Every part of the statements is a constant; only the %s values vary.
    filters = (method, method, state, state)
    rows = connection.execute(
        _SELECT_VERSION + _FILTER + _PAGE,  # noqa: S608
        (*filters, limit, offset),
    ).fetchall()
    (total,) = connection.execute(
        _COUNT_VERSION + _FILTER,  # noqa: S608
        filters,
    ).fetchone()
    return [_version(row) for row in rows], total


def get_version(connection: Connection[Any], version_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        _SELECT_VERSION + " WHERE v.version_id = %s", (version_id,)
    ).fetchone()
    return None if row is None else _version(row)


def name_taken(connection: Connection[Any], name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM models.model_version WHERE name = %s", (name,)
        ).fetchone()
        is not None
    )


def insert_version(
    connection: Connection[Any], body: dict[str, Any], owner_id: str
) -> int:
    """Register a DRAFT, with its features, in the caller's transaction."""
    parameters = body["parameters"]
    dataset = body["dataset"]
    metrics = body["metrics"]
    (version_id,) = connection.execute(
        "INSERT INTO models.model_version"
        " (name, method, source, window_days, seed, rule_set_id, k, max_iterations,"
        "  tolerance, normalization, frozen_scoring, reference_distribution,"
        "  dataset_fingerprint, dataset_origin, dataset_row_count,"
        "  inertia, silhouette, stability, owner_id)"
        " VALUES (%s, %s, 'REGISTERED', %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        "         %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING version_id",
        (
            body["name"],
            body["method"],
            body["window_days"],
            body.get("seed"),
            parameters["rule_set_id"],
            parameters["k"],
            parameters["max_iterations"],
            parameters["tolerance"],
            parameters["normalization"],
            _json(body["frozen_scoring"]),
            _json(body["reference_distribution"]),
            dataset["fingerprint"],
            dataset["origin"],
            dataset["row_count"],
            metrics["inertia"],
            metrics["silhouette"],
            metrics["stability"],
            owner_id,
        ),
    ).fetchone()
    for feature in parameters["features"]:
        connection.execute(
            "INSERT INTO models.model_version_feature (version_id, feature)"
            " VALUES (%s, %s)",
            (version_id, feature),
        )
    return version_id


def list_publications(
    connection: Connection[Any], version_id: int, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """The publications that made this version current, newest first."""
    rows = connection.execute(
        "SELECT publication_id, version_id, action, previous_version_id, reason,"
        " published_by::text, published_at"
        " FROM models.model_publication WHERE version_id = %s"
        " ORDER BY publication_id DESC LIMIT %s OFFSET %s",
        (version_id, limit, offset),
    ).fetchall()
    (total,) = connection.execute(
        "SELECT count(*) FROM models.model_publication WHERE version_id = %s",
        (version_id,),
    ).fetchone()
    keys = (
        "publication_id version_id action previous_version_id reason"
        " published_by published_at"
    ).split()
    return [dict(zip(keys, row, strict=True)) for row in rows], total
