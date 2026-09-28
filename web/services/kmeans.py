"""K-means over normalised R/F/M features, written in the application (F9-02).

ADR-0021 has the application implement Lloyd's algorithm itself rather than take a
scientific-computing dependency, so this file imports nothing outside the standard
library. That makes three failure modes this code's own responsibility, and each
one fails by producing plausible output rather than an error, which is the same
failure ADR-0018 exists to prevent one level up. Each therefore has a defined
behaviour, recorded on the run, and a test:

* **An empty cluster.** After an assignment step a cluster may hold no customer.
  It is refilled with the customer *farthest from their own centroid*, chosen
  only among clusters that hold at least two so that no cluster is emptied to
  fill another, and among equally far customers the lowest customer id moves. A run
  never silently ends with fewer than k clusters, and the number of refills is
  recorded (`empty_cluster_events`).
* **Not converging.** The fit stops when the largest centroid movement in an
  iteration is within the tolerance, or after the iteration limit. Which one is
  recorded (`stopped_on`), and a fit that hit the limit is recorded with
  `converged: false`; it is never presented as converged.
* **A customer equidistant from two centroids.** They go to the lower-numbered
  cluster. Cluster numbers are arbitrary, but they are fixed within one fit, and
  the fit is seeded, so repeating it does not move the customer.

The fit is reproducible from what the run records: the seed, k, the iteration limit,
the tolerance and the feature window. Customers are sorted by id before anything is
done, so the order rows arrive in cannot reach the result. Only `Random.random()`
is used, which Python guarantees to produce the same sequence for the same seed
across versions.

Normalisation is ADR-0018's: min-max to [0, 1] across the run, recency reversed so
higher always means better, and a feature that is constant across every customer
maps to 0 rather than dividing by zero.

Raw cluster numbers are diagnostics of one fit. They are here so ADR-0018's mapping
(F9-03) can order the clusters, and they never leave this module's result as
anything a report could compare.
"""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

Point = tuple[float, float, float]

INITIALISATION = "k-means++, seeded"
NORMALISATION = (
    "min-max to [0, 1] across the run; recency reversed so higher is better; "
    "a constant feature is 0"
)
EMPTY_CLUSTER_POLICY = (
    "an emptied cluster takes the customer farthest from their own cluster's "
    "centre, from a cluster holding at least two; lowest customer id on a tie"
)
TIE_BREAK = "a customer equidistant from two clusters goes to the lower-numbered one"

# Mean silhouette compares every customer with every other, which is quadratic in
# pure Python. Past this many customers it is recorded as absent rather than made
# to run for minutes; inertia and the sizes are always recorded.
SILHOUETTE_MAX_CUSTOMERS = 2000


class InvalidParameters(ValueError):
    """Parameters that cannot define a fit."""


class TooFewCustomers(ValueError):
    """Fewer customers than clusters: k clusters cannot all hold someone."""


@dataclass(frozen=True)
class KMeansParams:
    """Everything that shapes a fit, and so everything a run must record.

    `k` and `seed` have no default on purpose: a run that quietly chose either
    would be one nobody could reproduce from what it recorded.
    """

    k: int
    seed: int
    max_iterations: int = 100
    tolerance: float = 1e-4

    def __post_init__(self) -> None:
        for name in ("k", "seed", "max_iterations"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidParameters(f"{name} must be a whole number.")
        if self.k < 1:
            raise InvalidParameters("k must be at least 1.")
        if self.max_iterations < 1:
            raise InvalidParameters("max_iterations must be at least 1.")
        if not math.isfinite(self.tolerance) or self.tolerance < 0:
            raise InvalidParameters("tolerance must be a finite number, zero or more.")


@dataclass(frozen=True)
class RawRfm:
    """One customer's raw values over the feature window."""

    customer_id: str
    last_purchase_at: datetime
    frequency: int
    monetary: Decimal | float | int


@dataclass(frozen=True)
class KMeansFit:
    """A finished fit.

    `customer_ids`, `points` and `assignments` are aligned and ordered by customer
    id. `assignments[i]` is the cluster number of customer `customer_ids[i]`, and
    each centroid is the mean of exactly the customers assigned to it.
    """

    params: KMeansParams
    customer_ids: tuple[str, ...]
    points: tuple[Point, ...]
    assignments: tuple[int, ...]
    centroids: tuple[Point, ...]
    iterations: int
    converged: bool
    stopped_on: str
    final_shift: float
    empty_cluster_events: int
    inertia: float
    silhouette: float | None
    cluster_sizes: tuple[int, ...]

    def members(self, cluster: int) -> tuple[str, ...]:
        """The customers in one cluster, ordered by customer id."""
        return tuple(
            customer
            for customer, assigned in zip(
                self.customer_ids, self.assignments, strict=True
            )
            if assigned == cluster
        )


# ---------- normalisation ----------


def min_max(values: Sequence[float], *, reverse: bool = False) -> list[float]:
    """Scale values to [0, 1] across the run; a constant feature is 0.

    A constant feature carries no information, so it maps to 0 in both directions:
    reversing it must not turn "no information" into the best score.
    """
    low, high = min(values), max(values)
    if high == low:
        return [0.0] * len(values)
    span = high - low
    if reverse:
        return [(high - value) / span for value in values]
    return [(value - low) / span for value in values]


def normalise(rows: Sequence[RawRfm]) -> list[Point]:
    """ADR-0018's features, one point per row in the order given.

    Recency is measured in days since the last purchase, from the most recent
    purchase in the run, and reversed: the moment it is measured from cancels out,
    so a run is reproducible without recording a clock.
    """
    if not rows:
        return []
    latest = max(row.last_purchase_at for row in rows)
    days = [(latest - row.last_purchase_at).total_seconds() / 86400 for row in rows]
    recency = min_max(days, reverse=True)
    frequency = min_max([float(row.frequency) for row in rows])
    monetary = min_max([float(row.monetary) for row in rows])
    return list(zip(recency, frequency, monetary, strict=True))


# ---------- the pieces of Lloyd's algorithm ----------


def _squared(a: Point, b: Point) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def nearest(point: Point, centroids: Sequence[Point]) -> int:
    """The index of the nearest centroid. On a tie, the lowest index: the strict
    comparison never replaces the first of two equally near centroids."""
    best, best_distance = 0, _squared(point, centroids[0])
    for index in range(1, len(centroids)):
        distance = _squared(point, centroids[index])
        if distance < best_distance:
            best, best_distance = index, distance
    return best


def _initial_centroids(points: Sequence[Point], k: int, seed: int) -> list[Point]:
    """k-means++: the first centre uniformly, each next with probability
    proportional to its squared distance from the nearest centre chosen so far.

    When every remaining customer already coincides with a centre the weights are
    all zero, and the first unused customer in id order is taken. That is what makes
    identical customers, and so empty clusters, reachable and testable.
    """
    rng = random.Random(seed)
    n = len(points)
    first = min(int(rng.random() * n), n - 1)
    chosen = {first}
    centres = [points[first]]
    nearest_squared = [_squared(point, points[first]) for point in points]

    while len(centres) < k:
        total = sum(nearest_squared)
        if total > 0:
            target = rng.random() * total
            running = 0.0
            pick = -1
            for index, weight in enumerate(nearest_squared):
                if weight <= 0:
                    continue
                pick = index
                running += weight
                if running >= target:
                    break
        else:
            pick = next(index for index in range(n) if index not in chosen)
        chosen.add(pick)
        centres.append(points[pick])
        nearest_squared = [
            min(current, _squared(point, points[pick]))
            for current, point in zip(nearest_squared, points, strict=True)
        ]
    return centres


def refill_empty_clusters(
    points: Sequence[Point], assignments: list[int], centroids: Sequence[Point]
) -> int:
    """Give every empty cluster a customer, in place. Returns how many were moved.

    Each empty cluster, lowest number first, takes the customer farthest from the
    centroid of the cluster they are in, drawn only from clusters holding at least
    two. Among equally far customers the lowest index wins, and customers are
    ordered by id, so it is the lowest customer id.
    """
    k = len(centroids)
    sizes = [0] * k
    for cluster in assignments:
        sizes[cluster] += 1

    moved = 0
    for empty in range(k):
        if sizes[empty] != 0:
            continue
        donor, farthest = -1, -1.0
        for index, cluster in enumerate(assignments):
            if sizes[cluster] < 2:
                continue
            distance = _squared(points[index], centroids[cluster])
            if distance > farthest:
                donor, farthest = index, distance
        sizes[assignments[donor]] -= 1
        assignments[donor] = empty
        sizes[empty] = 1
        moved += 1
    return moved


def _means(points: Sequence[Point], assignments: Sequence[int], k: int) -> list[Point]:
    sums = [[0.0, 0.0, 0.0] for _ in range(k)]
    counts = [0] * k
    for point, cluster in zip(points, assignments, strict=True):
        counts[cluster] += 1
        for axis in range(3):
            sums[cluster][axis] += point[axis]
    return [
        (total[0] / count, total[1] / count, total[2] / count)
        for total, count in zip(sums, counts, strict=True)
    ]


def _silhouette(
    points: Sequence[Point], assignments: Sequence[int], k: int
) -> float | None:
    """Mean silhouette. None for one cluster, where it is undefined, or past
    SILHOUETTE_MAX_CUSTOMERS. A customer alone in their cluster scores 0."""
    n = len(points)
    if k < 2 or n > SILHOUETTE_MAX_CUSTOMERS:
        return None
    members: list[list[int]] = [[] for _ in range(k)]
    for index, cluster in enumerate(assignments):
        members[cluster].append(index)

    total = 0.0
    for index in range(n):
        own = assignments[index]
        if len(members[own]) == 1:
            continue
        inside = math.fsum(
            math.dist(points[index], points[other])
            for other in members[own]
            if other != index
        ) / (len(members[own]) - 1)
        outside = min(
            math.fsum(math.dist(points[index], points[other]) for other in members[c])
            / len(members[c])
            for c in range(k)
            if c != own
        )
        scale = max(inside, outside)
        total += 0.0 if scale == 0 else (outside - inside) / scale
    return total / n


# ---------- the fit ----------


def fit(
    customer_ids: Sequence[str], points: Sequence[Point], params: KMeansParams
) -> KMeansFit:
    """Cluster customers with Lloyd's algorithm.

    Customers are sorted by id first, so the result depends on the data and the
    parameters and on nothing else. Raises TooFewCustomers when there are fewer
    customers than k.
    """
    if len(customer_ids) != len(points):
        raise ValueError("Customers and points must pair up one to one.")
    if len(set(customer_ids)) != len(customer_ids):
        raise ValueError("A customer appears twice in the fit.")
    n = len(customer_ids)
    if n < params.k:
        raise TooFewCustomers(
            f"Only {n} {'customer has' if n == 1 else 'customers have'} sales in "
            f"the window; k={params.k} needs at least {params.k}."
        )

    order = sorted(range(n), key=lambda index: customer_ids[index])
    ids = tuple(customer_ids[index] for index in order)
    data = tuple(tuple(float(value) for value in points[index]) for index in order)

    k = params.k
    centroids = _initial_centroids(data, k, params.seed)
    assignments: list[int] = []
    events = 0
    shift = math.inf
    iterations = 0

    for iteration in range(1, params.max_iterations + 1):
        iterations = iteration
        assignments = [nearest(point, centroids) for point in data]
        events += refill_empty_clusters(data, assignments, centroids)
        updated = _means(data, assignments, k)
        shift = max(
            math.dist(old, new) for old, new in zip(centroids, updated, strict=True)
        )
        centroids = updated
        if shift <= params.tolerance:
            break

    converged = shift <= params.tolerance
    sizes = [0] * k
    for cluster in assignments:
        sizes[cluster] += 1
    inertia = math.fsum(
        _squared(point, centroids[cluster])
        for point, cluster in zip(data, assignments, strict=True)
    )
    return KMeansFit(
        params=params,
        customer_ids=ids,
        points=data,
        assignments=tuple(assignments),
        centroids=tuple(centroids),
        iterations=iterations,
        converged=converged,
        stopped_on="tolerance" if converged else "iteration_limit",
        final_shift=shift,
        empty_cluster_events=events,
        inertia=inertia,
        silhouette=_silhouette(data, assignments, k),
        cluster_sizes=tuple(sorted(sizes, reverse=True)),
    )


def fit_customers(rows: Sequence[RawRfm], params: KMeansParams) -> KMeansFit:
    """Normalise raw R/F/M rows and fit them."""
    return fit([row.customer_id for row in rows], normalise(rows), params)


# ---------- what a run records ----------


def recorded_parameters(result: KMeansFit, window_days: int) -> dict:
    """The parameters and quality measures stored on the run (ADR-0017).

    Cluster sizes are a list, largest first, and not a mapping by cluster number:
    a raw cluster number means something only inside one fit and must not be
    something a report can key on (ADR-0018). Floats are rounded to six places so
    the stored text is stable and readable, and all are finite: jsonb has no NaN.
    """
    quality: dict = {
        "inertia": round(result.inertia, 6),
        "silhouette": (
            None if result.silhouette is None else round(result.silhouette, 6)
        ),
        "cluster_sizes": list(result.cluster_sizes),
        "iterations": result.iterations,
        "converged": result.converged,
        "stopped_on": result.stopped_on,
        "final_shift": round(result.final_shift, 6),
        "empty_cluster_events": result.empty_cluster_events,
        "customers_clustered": len(result.customer_ids),
    }
    if result.silhouette is None:
        quality["silhouette_skipped"] = (
            "a single cluster has no silhouette"
            if result.params.k < 2
            else f"more than {SILHOUETTE_MAX_CUSTOMERS} customers"
        )
    return {
        "k": result.params.k,
        "seed": result.params.seed,
        "max_iterations": result.params.max_iterations,
        "tolerance": result.params.tolerance,
        "window_days": window_days,
        "initialisation": INITIALISATION,
        "normalisation": NORMALISATION,
        "empty_cluster_policy": EMPTY_CLUSTER_POLICY,
        "tie_break": TIE_BREAK,
        "quality": quality,
    }
