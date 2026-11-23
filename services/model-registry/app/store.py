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

_VERSIONS = (
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
    " (SELECT t.training_id FROM models.model_training AS t"
    "   WHERE t.version_id = v.version_id),"
    " v.created_at, v.published_at, v.retired_at"
    " FROM models.model_version AS v"
    " WHERE (%s::bigint IS NULL OR v.version_id = %s)"
    " AND (%s::text IS NULL OR v.method = %s)"
    " AND (%s::text IS NULL OR v.state = %s)"
    " ORDER BY v.version_id DESC LIMIT %s OFFSET %s"
)
_COUNT_VERSION = (
    "SELECT count(*) FROM models.model_version AS v"
    " WHERE (%s::text IS NULL OR v.method = %s)"
    " AND (%s::text IS NULL OR v.state = %s)"
)
VERSION_COLUMNS = (
    "version_id name method state source window_days seed rule_set_id k"
    " max_iterations tolerance normalization features frozen_scoring"
    " reference_distribution fingerprint origin row_count inertia silhouette"
    " stability owner_id source_run_id training_id created_at published_at retired_at"
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
    rows = connection.execute(
        _VERSIONS, (None, None, method, method, state, state, limit, offset)
    ).fetchall()
    (total,) = connection.execute(
        _COUNT_VERSION, (method, method, state, state)
    ).fetchone()
    return [_version(row) for row in rows], total


def get_version(connection: Connection[Any], version_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        _VERSIONS, (version_id, version_id, None, None, None, None, 1, 0)
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
    connection: Connection[Any],
    body: dict[str, Any],
    owner_id: str,
    source: str = "REGISTERED",
) -> int:
    """Register a DRAFT, with its features, in the caller's transaction.

    `source` is `REGISTERED` for a version an administrator posts and `TRAINING` for
    one a
    training registered (F22-12)."""
    parameters = body["parameters"]
    dataset = body["dataset"]
    metrics = body["metrics"]
    (version_id,) = connection.execute(
        "INSERT INTO models.model_version"
        " (name, method, source, window_days, seed, rule_set_id, k, max_iterations,"
        "  tolerance, normalization, frozen_scoring, reference_distribution,"
        "  dataset_fingerprint, dataset_origin, dataset_row_count,"
        "  inertia, silhouette, stability, owner_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        "         %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING version_id",
        (
            body["name"],
            body["method"],
            source,
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


def lock_version(connection: Connection[Any], version_id: int) -> dict[str, Any] | None:
    """Lock one version and return the state needed by a transition."""
    row = connection.execute(
        "SELECT version_id, state FROM models.model_version"
        " WHERE version_id = %s FOR UPDATE",
        (version_id,),
    ).fetchone()
    return None if row is None else {"version_id": row[0], "state": row[1]}


def lock_published(connection: Connection[Any]) -> int | None:
    """Lock the current published row; the partial index guarantees at most one."""
    row = connection.execute(
        "SELECT version_id FROM models.model_version"
        " WHERE state = 'PUBLISHED' FOR UPDATE"
    ).fetchone()
    return None if row is None else row[0]


def transition_publication(
    connection: Connection[Any],
    *,
    version_id: int,
    previous_version_id: int | None,
    action: str,
    reason: str,
    published_by: str,
) -> dict[str, Any]:
    """Change the current projection and append its fact in one transaction."""
    if previous_version_id is not None:
        connection.execute(
            "UPDATE models.model_version"
            " SET state = 'RETIRED', retired_at = now()"
            " WHERE version_id = %s AND state = 'PUBLISHED'",
            (previous_version_id,),
        )
    connection.execute(
        "UPDATE models.model_version"
        " SET state = 'PUBLISHED', published_at = now(), retired_at = NULL"
        " WHERE version_id = %s",
        (version_id,),
    )
    row = connection.execute(
        "INSERT INTO models.model_publication"
        " (version_id, action, previous_version_id, reason, published_by)"
        " VALUES (%s, %s, %s, %s, %s)"
        " RETURNING publication_id, version_id, action, previous_version_id,"
        " reason, published_by::text, published_at",
        (version_id, action, previous_version_id, reason, published_by),
    ).fetchone()
    keys = (
        "publication_id version_id action previous_version_id reason"
        " published_by published_at"
    ).split()
    return dict(zip(keys, row, strict=True))


# ---------- trainings (F22-12) ----------

_TRAININGS = (
    "SELECT t.training_id, t.method, t.state, t.version_name, t.window_days, t.seed,"
    " t.k, t.max_iterations, t.tolerance, t.normalization,"
    " ARRAY(SELECT f.feature::text FROM models.model_training_feature AS f"
    "        WHERE f.training_id = t.training_id"
    "        ORDER BY array_position(ARRAY['R', 'F', 'M'], f.feature::text)),"
    " t.dataset_fingerprint, t.dataset_origin, t.dataset_row_count,"
    " t.inertia, t.silhouette, t.iterations, t.converged, t.version_id,"
    " t.started_at, t.finished_at, t.started_by::text, t.failure_code"
    " FROM models.model_training AS t"
    " WHERE (%s::bigint IS NULL OR t.training_id = %s)"
    " AND (%s::text IS NULL OR t.method = %s)"
    " AND (%s::text IS NULL OR t.state = %s)"
    " ORDER BY t.training_id DESC LIMIT %s OFFSET %s"
)
TRAINING_COLUMNS = (
    "training_id method state version_name window_days seed k max_iterations"
    " tolerance normalization features fingerprint origin row_count inertia"
    " silhouette iterations converged version_id started_at finished_at started_by"
    " failure_code"
).split()


def list_trainings(
    connection: Connection[Any],
    method: str | None,
    state: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """A page of trainings, newest first, and how many match the filters."""
    rows = connection.execute(
        _TRAININGS, (None, None, method, method, state, state, limit, offset)
    ).fetchall()
    (total,) = connection.execute(
        "SELECT count(*) FROM models.model_training AS t"
        " WHERE (%s::text IS NULL OR t.method = %s)"
        " AND (%s::text IS NULL OR t.state = %s)",
        (method, method, state, state),
    ).fetchone()
    return [dict(zip(TRAINING_COLUMNS, row, strict=True)) for row in rows], total


def get_training(
    connection: Connection[Any], training_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        _TRAININGS, (training_id, training_id, None, None, None, None, 1, 0)
    ).fetchone()
    return None if row is None else dict(zip(TRAINING_COLUMNS, row, strict=True))


def fail_stale_trainings(connection: Connection[Any], seconds: int) -> int:
    """Close the trainings that have run past their deadline as FAILED `timed_out`, so a
    process that died mid-fit cannot hold the one running slot for ever."""
    return connection.execute(
        "UPDATE models.model_training SET state = 'FAILED', finished_at = now(),"
        " failure_code = 'timed_out'"
        " WHERE state = 'RUNNING' AND started_at < now() - make_interval(secs => %s)",
        (seconds,),
    ).rowcount


def fail_running_trainings(connection: Connection[Any], code: str) -> int:
    """Close every RUNNING training as FAILED. Only at start: the one process that fits
    trainings has just begun, so none of them is still being fitted."""
    return connection.execute(
        "UPDATE models.model_training SET state = 'FAILED', finished_at = now(),"
        " failure_code = %s WHERE state = 'RUNNING'",
        (code,),
    ).rowcount


def insert_training(
    connection: Connection[Any], body: dict[str, Any], seed: int, started_by: str
) -> int:
    """Insert the RUNNING row and its variables, in the caller's transaction."""
    parameters = body["parameters"]
    (training_id,) = connection.execute(
        "INSERT INTO models.model_training"
        " (method, version_name, window_days, seed, k, max_iterations, tolerance,"
        "  normalization, started_by)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING training_id",
        (
            body["method"],
            body["version_name"],
            body["window_days"],
            seed,
            parameters["k"],
            parameters["max_iterations"],
            parameters["tolerance"],
            parameters["normalization"],
            started_by,
        ),
    ).fetchone()
    for feature in parameters["features"]:
        connection.execute(
            "INSERT INTO models.model_training_feature (training_id, feature)"
            " VALUES (%s, %s)",
            (training_id, feature),
        )
    return training_id


def fail_training(connection: Connection[Any], training_id: int, code: str) -> bool:
    """Close one RUNNING training as FAILED; False when it was no longer RUNNING."""
    return (
        connection.execute(
            "UPDATE models.model_training SET state = 'FAILED', finished_at = now(),"
            " failure_code = %s WHERE training_id = %s AND state = 'RUNNING'",
            (code, training_id),
        ).rowcount
        == 1
    )


def complete_training(
    connection: Connection[Any],
    training_id: int,
    *,
    version_id: int,
    dataset: dict[str, Any],
    metrics: dict[str, Any],
    iterations: int,
    converged: bool,
    stopped_on: str,
    final_shift: float,
    empty_cluster_events: int,
    silhouette_sample_size: int | None,
    cluster_sizes: list[int],
) -> bool:
    """Close one RUNNING training as COMPLETED with the version it registered and the
    size
    of each cluster, best first; False when it was no longer RUNNING."""
    done = (
        connection.execute(
            "UPDATE models.model_training SET state = 'COMPLETED', finished_at = now(),"
            " dataset_fingerprint = %s, dataset_origin = %s, dataset_row_count = %s,"
            " inertia = %s, silhouette = %s, iterations = %s, converged = %s,"
            " stopped_on = %s, final_shift = %s, empty_cluster_events = %s,"
            " silhouette_sample_size = %s, version_id = %s"
            " WHERE training_id = %s AND state = 'RUNNING'",
            (
                dataset["fingerprint"],
                dataset["origin"],
                dataset["row_count"],
                metrics["inertia"],
                metrics["silhouette"],
                iterations,
                converged,
                stopped_on,
                final_shift,
                empty_cluster_events,
                silhouette_sample_size,
                version_id,
                training_id,
            ),
        ).rowcount
        == 1
    )
    if done:
        for index, count in enumerate(cluster_sizes):
            connection.execute(
                "INSERT INTO models.model_training_cluster"
                " (training_id, cluster_index, customer_count) VALUES (%s, %s, %s)",
                (training_id, index, count),
            )
    return done


def label_vocabulary(connection: Connection[Any]) -> list[str]:
    """The label vocabulary, best to worst, from segments' contract view."""
    rows = connection.execute(
        "SELECT label_code FROM api_segments.label_v1 ORDER BY ordinal_position"
    ).fetchall()
    return [row[0] for row in rows]


_NET_LINES = (
    "SELECT transaction_id, customer_id::text, occurred_at, product_id, net_quantity,"
    " net_amount, data_origin FROM api_sales.net_purchase_line_v1"
    " WHERE occurred_at >= now() - make_interval(days => %s) AND occurred_at <= now()"
    " ORDER BY transaction_id, product_id"
)


def stream_net_lines(connection: Connection[Any], window_days: int, size: int = 5000):
    """The net purchase lines of the window, one at a time, in a fixed order.

    A server-side cursor, so the lines never sit in memory together: a training keeps
    one
    small aggregate per customer, however many lines the window holds."""
    with connection.cursor(name="training_net_lines") as cursor:
        cursor.itersize = size
        cursor.execute(_NET_LINES, (window_days,))
        yield from cursor
