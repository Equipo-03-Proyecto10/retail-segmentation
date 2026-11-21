"""RN-58 and RN-38 through the web's K-means and its cluster-to-label pairing (F22-12).

model-registry fits K-means itself, with its own code, and is held to what the web's
`fit_customers` and `label_clusters` return on these inputs. The cases are pure: they
read
no table, so a case's meaning cannot move with the seed. Each records its customers
(or, for
the one set too large to write out, the recipe that draws them), its parameters and the
fit's outcome: the assignments, the centroids, the measures and the label of every
cluster.

Customers are written as hours before a fixed reference instant. Recency is measured
from
the most recent purchase of the set, so the reference never reaches the result.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from tests.golden.cases import Case, Context, accepted
from web.services.cluster_labels import (
    label_clusters,
    labels_by_rank,
    rank_clusters,
)
from web.services.kmeans import (
    KMeansFit,
    KMeansParams,
    RawRfm,
    TooFewCustomers,
    fit_customers,
    normalise,
)

REFERENCE = datetime(2026, 10, 1, tzinfo=UTC)
# The seeded vocabulary, best to worst (sql/02_seed_30_per_table.sql).
VOCABULARY = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"]
TOLERANCE = 1e-4

Customers = list[tuple[str, int, int, str]]


def synthetic(n: int, seed: int) -> Customers:
    """The recipe of the large set. The service's test draws the same customers from the
    same words: `Random(seed)`, and for each customer in turn `randrange(0, 4320)`
    hours,
    `choice` of the frequency pool, and `randrange(500, 900000)` cents."""
    rng = random.Random(seed)
    pool = [1, 1, 1, 2, 3, 5, 8, 13, 30]
    out: Customers = []
    for index in range(n):
        hours = rng.randrange(0, 24 * 180)
        frequency = rng.choice(pool)
        cents = rng.randrange(500, 900000)
        out.append(
            (f"c{index:05d}", hours, frequency, f"{cents // 100}.{cents % 100:02d}")
        )
    return out


def _raw(customers: Customers) -> list[RawRfm]:
    return [
        RawRfm(cid, REFERENCE - timedelta(hours=hours), frequency, Decimal(money))
        for cid, hours, frequency, money in customers
    ]


def digest_of(customer_ids: list[str], assignments: list[int]) -> str:
    """SHA-256 of `id:cluster` lines, so a large set is held to every assignment
    without a
    line of the fixture for each customer."""
    text = "".join(
        f"{cid}:{cluster}\n"
        for cid, cluster in zip(customer_ids, assignments, strict=False)
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _fit(
    customers: Customers,
    k: int,
    seed: int,
    max_iterations: int = 100,
    points: bool = False,
    digest: bool = False,
    tolerance: float = TOLERANCE,
) -> dict[str, Any]:
    try:
        fitted = fit_customers(
            _raw(customers),
            KMeansParams(
                k=k, seed=seed, max_iterations=max_iterations, tolerance=tolerance
            ),
        )
    except TooFewCustomers as error:
        # Its wording is the web's; what the port is held to is that it refuses.
        return {"outcome": "refused", "error": type(error).__name__}
    labels = label_clusters(fitted, VOCABULARY)
    observed: dict[str, Any] = {
        "customer_ids": list(fitted.customer_ids),
        "assignments": list(fitted.assignments),
        "centroids": [list(c) for c in fitted.centroids],
        "iterations": fitted.iterations,
        "converged": fitted.converged,
        "stopped_on": fitted.stopped_on,
        "final_shift": fitted.final_shift,
        "empty_cluster_events": fitted.empty_cluster_events,
        "inertia": fitted.inertia,
        "silhouette": fitted.silhouette,
        "silhouette_sample": fitted.silhouette_sample,
        "cluster_sizes": list(fitted.cluster_sizes),
        "ranked_clusters": rank_clusters(fitted),
        "label_by_cluster": {str(c): labels[c] for c in sorted(labels)},
    }
    if points:
        observed["points"] = [list(p) for p in fitted.points]
    if digest:
        observed["assignments_sha256"] = digest_of(
            list(fitted.customer_ids), list(fitted.assignments)
        )
        del observed["customer_ids"], observed["assignments"]
    return accepted(**observed)


def _normalised(customers: Customers) -> dict[str, Any]:
    return accepted(points=[list(p) for p in normalise(_raw(customers))])


def _pairing(connection: psycopg.Connection) -> dict[str, Any]:
    del connection
    return accepted(
        by_position={str(k): labels_by_rank(k, VOCABULARY) for k in range(2, 9)}
    )


# Centroids whose coordinates are the same three numbers in another order sum to the
# same
# value only if the sum is exact (0.1 + 0.2 + 0.3 is not 0.3 + 0.2 + 0.1 in floats), so
# the
# order falls through to recency. The last two are identical: the smallest customer id
# wins.
TIED_CENTROIDS = [
    [0.1, 0.2, 0.3],
    [0.3, 0.2, 0.1],
    [0.5, 0.5, 0.5],
    [0.5, 0.5, 0.5],
    [0.2, 0.1, 0.3],
]
# The identical pair is told apart by their smallest member, not their largest: a1 < b1
# but
# z9 > b2.
TIED_MEMBERS = [["m1"], ["m2"], ["b1", "b2"], ["a1", "z9"], ["m4"]]


def _ranking(connection: psycopg.Connection) -> dict[str, Any]:
    del connection
    k = len(TIED_CENTROIDS)
    owner = {m: cluster for cluster, group in enumerate(TIED_MEMBERS) for m in group}
    ids = tuple(sorted(owner))
    fitted = KMeansFit(
        params=KMeansParams(k=k, seed=1),
        customer_ids=ids,
        points=tuple((0.0, 0.0, 0.0) for _ in ids),
        assignments=tuple(owner[m] for m in ids),
        centroids=tuple(tuple(c) for c in TIED_CENTROIDS),
        iterations=1,
        converged=True,
        stopped_on="tolerance",
        final_shift=0.0,
        empty_cluster_events=0,
        inertia=0.0,
        silhouette=None,
        cluster_sizes=(1,) * k,
    )
    labels = label_clusters(fitted, VOCABULARY)
    return accepted(
        ranked_clusters=rank_clusters(fitted),
        label_by_cluster={str(c): labels[c] for c in sorted(labels)},
    )


def _first_run_shift(customers: Customers, k: int, seed: int) -> float:
    """The last shift of a fit, which a second fit then takes as its tolerance: it has
    to
    stop on the iteration whose shift equals the tolerance, not run on."""
    fitted = fit_customers(
        _raw(customers), KMeansParams(k=k, seed=seed, tolerance=TOLERANCE)
    )
    return fitted.final_shift


def _blobs() -> Customers:
    near = [(f"a{j}", 72, 2, "10.00") for j in range(6)]
    far = [(f"b{j}", 2160, 9, "900.00") for j in range(6)]
    return near + far


def _scatter(n: int, seed: int) -> Customers:
    return synthetic(n, seed)


def build(connection: psycopg.Connection, context: Context) -> list[Case]:
    del connection, context
    frequencies = [(f"t{i}", 120, f, "10.00") for i, f in enumerate([1, 2, 3])]
    same_recency = [(f"r{i}", 240, 1 + i, f"{10 + 5 * i}.00") for i in range(8)]
    same_frequency = [(f"f{i}", 24 * (i + 1), 4, f"{10 + 5 * i}.00") for i in range(8)]
    identical = [(f"i{j}", 72, 2, "10.00") for j in range(10)]
    medium = _scatter(120, 11)
    small = _scatter(40, 42)

    def fit(
        name: str, customers: Customers, k: int, seed: int, limit: int = 100, **more
    ):
        recorded = {
            "call": "fit_customers",
            "customers": [list(c) for c in customers],
            "k": k,
            "seed": seed,
            "max_iterations": limit,
            "tolerance": TOLERANCE,
        }
        return Case(
            name,
            recorded,
            lambda c: _fit(customers, k, seed, limit, **more),
        )

    big = synthetic(2100, 13)
    edge = _first_run_shift(small, 3, 43)
    return [
        Case(
            "normalise-recency-is-constant",
            {"call": "normalise", "customers": [list(c) for c in same_recency]},
            lambda c: _normalised(same_recency),
        ),
        Case(
            "normalise-frequency-is-constant",
            {"call": "normalise", "customers": [list(c) for c in same_frequency]},
            lambda c: _normalised(same_frequency),
        ),
        fit("fit-forty-customers-k3", small, 3, 42, points=True),
        fit("fit-forty-customers-k3-other-seed", small, 3, 43),
        fit("fit-hundred-and-twenty-customers-k5", medium, 5, 7),
        fit("fit-stops-at-the-iteration-limit", medium, 6, 3, 1),
        Case(
            "fit-stops-when-the-shift-equals-the-tolerance",
            {
                "call": "fit_customers",
                "customers": [list(c) for c in small],
                "k": 3,
                "seed": 43,
                "max_iterations": 100,
                "tolerance": edge,
            },
            lambda c: _fit(small, 3, 43, tolerance=edge),
        ),
        fit("fit-identical-customers-refill-empty-clusters", identical, 3, 4),
        fit("fit-two-blobs-with-more-clusters-than-blobs", _blobs(), 4, 4),
        fit("fit-equidistant-customer-goes-to-the-lower-cluster", frequencies, 2, 1),
        fit("fit-equidistant-customer-with-the-centres-swapped", frequencies, 2, 0),
        fit("fit-fewer-customers-than-clusters", frequencies, 5, 9),
        Case(
            "fit-sampled-silhouette-above-two-thousand-customers",
            {
                "call": "fit_customers",
                "synthetic": {
                    "customers": 2100,
                    "seed": 13,
                    "recipe": synthetic.__doc__,
                },
                "k": 6,
                "seed": 13,
                "max_iterations": 100,
                "tolerance": TOLERANCE,
            },
            lambda c: _fit(big, 6, 13, digest=True),
        ),
        Case(
            "rank-ties-on-the-sum-fall-through-to-recency-then-customer-id",
            {
                "call": "rank_clusters",
                "centroids": TIED_CENTROIDS,
                "members": TIED_MEMBERS,
            },
            _ranking,
        ),
        Case(
            "label-pairing-by-proportional-rank",
            {
                "call": "labels_by_rank",
                "vocabulary": VOCABULARY,
                "k": list(range(2, 9)),
            },
            _pairing,
        ),
    ]


def as_of(context: Context) -> dict[str, Any]:
    del context
    return {"reference": REFERENCE}
