"""A K-means training: read the window, fit it, and register the result as a DRAFT
(F22-12).

`POST /v1/model-trainings` stores the request as a RUNNING row and answers at once; this
module is what runs after the answer. It reads the net purchases of the window through
sales' contract view (ADR-0062), fits them with the registry's own K-means (ADR-0021,
RN-58),
names every cluster by what it contains (RN-38, ADR-0030) and registers a DRAFT version
with
the dataset, the parameters, the seed, the metrics, the frozen centroids and the
reference
distribution (ADR-0064). It publishes nothing.

A training always ends: COMPLETED with its version, or FAILED with a code. It never
leaves a
version half registered, because the version, the cluster sizes and the training's own
row
change in one transaction.
"""

from __future__ import annotations

import hashlib
import logging
import math
import secrets
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from . import kmeans, labels, rules, store

LOGGER = logging.getLogger(__name__)
DECIMALS = 6
BINS = 10
NAME_CONSTRAINT = "model_version_name_key"


class Failed(Exception):
    """A training that ends FAILED; `code` is what the training records."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def read_window(
    connection: psycopg.Connection[Any], window_days: int
) -> dict[str, Any]:
    """The window's net purchases as one aggregate per customer, with the dataset's
    fingerprint, origin and size.

    One consistent snapshot, read as a stream. The fingerprint is the lowercase SHA-256
    of the
    lines in `(transaction_id, product_id)` order, each as
    `transaction|customer|instant in
    UTC|product|net quantity|net amount`, so the same sales give the same fingerprint
    on any
    day and any change to a sale, a return included, gives another one.
    """
    digest = hashlib.sha256()
    customers: dict[str, list[Any]] = {}
    lines = 0
    previous_sale = None
    origin = "OBSERVED"
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for (
            sale,
            customer,
            at,
            product,
            quantity,
            amount,
            data_origin,
        ) in store.stream_net_lines(connection, window_days):
            lines += 1
            digest.update(
                f"{sale}|{customer}|{at.astimezone(UTC).isoformat()}|{product}"
                f"|{quantity}|{amount}\n".encode()
            )
            if data_origin == "SEEDED":
                origin = "SEEDED"
            held = customers.get(customer)
            if held is None:
                customers[customer] = [at, 1, amount]
                previous_sale = sale
                continue
            if at > held[0]:
                held[0] = at
            if sale != previous_sale:
                held[1] += 1
            held[2] += amount
            previous_sale = sale
        vocabulary = store.label_vocabulary(connection)
    return {
        "sales": [
            kmeans.Sales(customer, held[0], held[1], held[2])
            for customer, held in customers.items()
        ],
        "dataset": {
            "fingerprint": digest.hexdigest(),
            "origin": origin,
            "row_count": lines,
        },
        "vocabulary": vocabulary,
    }


def _round(value: float) -> float:
    return round(value, DECIMALS)


def _edges(values: list[float]) -> list[float]:
    """Ten bins of equal count where the values allow it: the edges are the values at
    the
    tenth, twentieth, ... percent by nearest rank, kept only where they increase. Every
    value is in the bin whose lower edge it reaches, and the highest is in the last."""
    ordered = sorted(values)
    n = len(ordered)
    edges: list[float] = []
    for step in range(BINS):
        edge = ordered[min(n - 1, step * n // BINS)]
        if not edges or edge > edges[-1]:
            edges.append(edge)
    top = ordered[-1]
    if not edges or top > edges[-1]:
        edges.append(top)
    if len(edges) == 1:
        edges.append(edges[0] + 1)
    return edges


def _histogram(values: list[float]) -> dict[str, list[float]]:
    edges = _edges(values)
    counts = [0] * (len(edges) - 1)
    for value in values:
        bin_index = len(counts) - 1
        for index in range(len(counts)):
            if value < edges[index + 1]:
                bin_index = index
                break
        counts[bin_index] += 1
    n = len(values)
    return {"bin_edges": edges, "proportions": [count / n for count in counts]}


def build_version(
    training: dict[str, Any],
    dataset: kmeans.Dataset,
    fitted: kmeans.Fit,
    vocabulary: list[str],
    data: dict[str, Any],
    captured_at: datetime,
) -> tuple[dict[str, Any], list[int]]:
    """The DRAFT version a fit becomes, in the shape `POST /v1/model-versions` takes,
    and the
    size of each cluster best first.

    `cluster_index` is the cluster's position in best-to-worst order and never the
    number the
    fit gave it, so nothing a client reads depends on one fit's arbitrary names (RN-38).
    """
    order = labels.rank(fitted)
    names = labels.label_clusters(fitted, vocabulary)
    centroids = [
        {
            "cluster_index": position,
            "label_code": names[cluster],
            "r": _round(fitted.centroids[cluster][0]),
            "f": _round(fitted.centroids[cluster][1]),
            "m": _round(fitted.centroids[cluster][2]),
        }
        for position, cluster in enumerate(order)
    ]
    sizes = [fitted.sizes[cluster] for cluster in order]
    n = len(dataset.customer_ids)
    per_label: dict[str, int] = {}
    for item, size in zip(centroids, sizes, strict=True):
        per_label[item["label_code"]] = per_label.get(item["label_code"], 0) + size
    columns = {"R": 0, "F": 1, "M": 2}
    features = training["features"]
    (r_low, r_high), (f_low, f_high), (m_low, m_high) = dataset.bounds
    body = {
        "name": training["version_name"],
        "method": "KMEANS",
        "window_days": training["window_days"],
        "seed": training["seed"],
        "parameters": {
            "rule_set_id": None,
            "k": training["k"],
            "max_iterations": training["max_iterations"],
            "tolerance": training["tolerance"],
            "features": features,
            "normalization": "MIN_MAX",
        },
        "frozen_scoring": {
            "quintile_boundaries": None,
            "centroids": centroids,
            "normalization_bounds": {
                "r_min": _round(r_low),
                "r_max": _round(r_high),
                "f_min": f_low,
                "f_max": f_high,
                "m_min": m_low,
                "m_max": m_high,
            },
        },
        "reference_distribution": {
            "sample_size": n,
            "captured_at": captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "features": [
                {
                    "feature": name,
                    **_histogram([point[columns[name]] for point in dataset.raw]),
                }
                for name in features
            ],
            "labels": [
                {"label_code": label, "share": per_label[label] / n}
                for label in vocabulary
                if label in per_label
            ],
        },
        "dataset": data["dataset"],
        "metrics": {
            "inertia": _round(fitted.inertia),
            "silhouette": (
                None if fitted.silhouette is None else _round(fitted.silhouette)
            ),
            "stability": None,
        },
    }
    return body, sizes


def fit_training(
    training: dict[str, Any], data: dict[str, Any], deadline: float | None = None
) -> tuple[dict[str, Any], kmeans.Fit, list[int]]:
    """Fit one training over the window it read. Pure: it touches no database, so it is
    the same for the same data and configuration (RN-58)."""
    prepared = kmeans.prepare(data["sales"])
    try:
        fitted = kmeans.fit(
            prepared,
            k=training["k"],
            seed=training["seed"],
            max_iterations=training["max_iterations"],
            tolerance=training["tolerance"],
            axes=[kmeans.AXES[name] for name in training["features"]],
            deadline=deadline,
        )
    except kmeans.TooFewCustomers:
        raise Failed("too_few_customers") from None
    except TimeoutError:
        raise Failed("timed_out") from None
    if not data["vocabulary"]:
        raise Failed("label_vocabulary_unavailable")
    body, sizes = build_version(
        training, prepared, fitted, data["vocabulary"], data, datetime.now(UTC)
    )
    if rules.invalid_details(body) or not math.isfinite(fitted.inertia):
        raise Failed("invalid_result")
    return body, fitted, sizes


def run(db: Any, training_id: int, timeout_seconds: int) -> None:
    """Run one training to its end. Never raises: whatever happens, the row ends FAILED
    or
    COMPLETED, or, if even that cannot be written, is closed by the next start or
    request
    after its deadline."""
    started = time.monotonic()
    try:
        with db.connection() as connection:
            training = store.get_training(connection, training_id)
        if training is None or training["state"] != "RUNNING":
            return
        try:
            with db.connection() as connection:
                data = read_window(connection, training["window_days"])
        except psycopg.Error:
            LOGGER.exception("training %s could not read the window", training_id)
            raise Failed("source_unavailable") from None
        body, fitted, sizes = fit_training(training, data, started + timeout_seconds)
        if not fitted.converged:
            LOGGER.warning(
                "kmeans_not_converged training=%s iterations=%s final_shift=%.6f",
                training_id,
                fitted.iterations,
                fitted.final_shift,
            )
        _register(db, training, body, fitted, sizes)
    except Failed as failure:
        _fail(db, training_id, failure.code)
    except Exception:  # noqa: BLE001 - a training must end, whatever went wrong
        LOGGER.exception("training %s failed", training_id)
        _fail(db, training_id, "internal_error")


def _register(
    db: Any,
    training: dict[str, Any],
    body: dict[str, Any],
    fitted: kmeans.Fit,
    sizes: list[int],
) -> None:
    training_id = training["training_id"]
    try:
        with db.transaction() as connection:
            version_id = store.insert_version(
                connection, body, training["started_by"], source="TRAINING"
            )
            done = store.complete_training(
                connection,
                training_id,
                version_id=version_id,
                dataset=body["dataset"],
                metrics=body["metrics"],
                iterations=fitted.iterations,
                converged=fitted.converged,
                stopped_on=fitted.stopped_on,
                final_shift=_round(fitted.final_shift),
                empty_cluster_events=fitted.empty_cluster_events,
                silhouette_sample_size=fitted.silhouette_sample,
                cluster_sizes=sizes,
            )
            if not done:
                # Closed as timed out while it was being fitted: nothing is registered.
                raise _Abandoned
    except _Abandoned:
        LOGGER.warning("training %s was closed before it finished", training_id)
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name != NAME_CONSTRAINT:
            raise
        _fail(db, training_id, "model_version_name_taken")


class _Abandoned(Exception):
    pass


def _fail(db: Any, training_id: int, code: str) -> None:
    try:
        with db.transaction() as connection:
            store.fail_training(connection, training_id, code)
    except Exception:  # noqa: BLE001
        LOGGER.exception("training %s could not be closed as %s", training_id, code)


def start(db: Any, training_id: int, timeout_seconds: int) -> threading.Thread:
    """Run the training after the answer, on a thread of its own."""
    thread = threading.Thread(
        target=run,
        args=(db, training_id, timeout_seconds),
        name=f"training-{training_id}",
        daemon=True,
    )
    thread.start()
    return thread


def choose_seed(requested: Decimal | int | None) -> int:
    """The requested seed, or one the service draws; either way it is recorded."""
    if requested is not None:
        return int(requested)
    return secrets.randbelow(2**31)
