"""K-means over normalized R, F and M, written for the registry (F22-12, ADR-0021).

The web fits its clusters with its own Lloyd's algorithm (`web/services/kmeans.py`) and
takes
no scientific-computing dependency. This module is the registry's port of that rule,
and it is
held to the web's results by golden fixtures (`tests/golden/`, ADR-0045): it imports
nothing
from the web and shares no code with it, so a drift in either one shows up as a failed
comparison instead of being hidden by a shared file. The rule is RN-58 and what it
fixes is
what this module reproduces:

* The fit is a function of the customers, `k`, the seed, the iteration limit and the
  tolerance. Customers are sorted by id before anything is done, so the order rows
  arrive in
  cannot reach the result. Only `random.Random.random()` is drawn from the seed, whose
  sequence Python guarantees to be the same for the same seed.
* Initial centres are k-means++: the first uniformly, each next with probability
proportional
  to its squared distance from the nearest centre already chosen. When every customer
  left
  coincides with a centre the weights are all zero and the first unused customer in id
  order
  is taken, which is what makes identical customers, and so empty clusters, reachable.
* A customer equidistant from two centroids goes to the lower-numbered one.
* A cluster left empty by an assignment step takes the customer farthest from the
centroid of
  their own cluster, drawn only from clusters that hold at least two, the lowest
  customer id
  on a tie; a fit never ends with fewer than `k` clusters and counts every refill.
* The fit stops when the largest centroid shift is within the tolerance or at the
iteration
  limit, and which one is recorded. A fit that reached the limit is `converged = False`.
* One final assignment after the last update makes the partition and its measures
belong to
  the final centroids.
* The mean silhouette compares every customer with every other up to
  `SILHOUETTE_MAX_CUSTOMERS`; above that it is the mean over a seeded sample, each
  sampled
  customer still compared with every customer, and the fit says it is a sample.

Normalization is ADR-0018's: min-max to [0, 1] across the dataset, recency reversed so
that
higher always means better, and a feature that is the same for every customer is 0, in
both
directions, rather than a division by zero.

A variable set is a choice of axes. The fit runs over the chosen ones, and every
cluster is
still described by all three normalized coordinates (the mean of its customers), so a
version
always freezes an R, an F and an M per centroid and the ranking of RN-38 reads the same
three
numbers whatever was fitted.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

Point = tuple[float, ...]

AXES = {"R": 0, "F": 1, "M": 2}
SILHOUETTE_MAX_CUSTOMERS = 2000
SILHOUETTE_SAMPLE_SIZE = 1000


class TooFewCustomers(ValueError):
    """Fewer customers than clusters: `k` clusters cannot all hold someone."""


@dataclass(frozen=True)
class Sales:
    """One customer's net purchases over the window, as the fit reads them."""

    customer_id: str
    last_purchase_at: datetime
    frequency: int
    monetary: Decimal | float | int


@dataclass(frozen=True)
class Dataset:
    """The customers of a fit, sorted by id, with the raw measures and their scaling.

    `raw` holds recency in days before the most recent purchase of the dataset, the
    frequency and the monetary total. `bounds` are the minimum and maximum of each,
    which
    are what the min-max scaling used.
    """

    customer_ids: tuple[str, ...]
    raw: tuple[Point, ...]
    points: tuple[Point, ...]
    bounds: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Fit:
    """A finished fit. `assignments[i]` is the cluster of `customer_ids[i]`."""

    customer_ids: tuple[str, ...]
    assignments: tuple[int, ...]
    centroids: tuple[Point, ...]
    iterations: int
    converged: bool
    stopped_on: str
    final_shift: float
    empty_cluster_events: int
    inertia: float
    silhouette: float | None
    silhouette_sample: int | None
    sizes: tuple[int, ...]

    def members(self, cluster: int) -> tuple[str, ...]:
        return tuple(
            customer
            for customer, assigned in zip(
                self.customer_ids, self.assignments, strict=True
            )
            if assigned == cluster
        )


def _scale(values: Sequence[float], *, reverse: bool = False) -> list[float]:
    low, high = min(values), max(values)
    if high == low:
        return [0.0] * len(values)
    span = high - low
    if reverse:
        return [(high - value) / span for value in values]
    return [(value - low) / span for value in values]


def prepare(rows: Sequence[Sales]) -> Dataset:
    """Sort the customers by id and normalize their measures (ADR-0018).

    Recency is the days between a customer's last purchase and the latest purchase of
    the
    dataset. Measured from there, the instant the training ran at cancels out of the
    scaled
    value, so the same sales give the same points on any day.
    """
    if len({row.customer_id for row in rows}) != len(rows):
        raise ValueError("A customer appears twice in the dataset.")
    ordered = sorted(rows, key=lambda row: row.customer_id)
    if not ordered:
        return Dataset((), (), (), ((0.0, 0.0),) * 3)
    latest = max(row.last_purchase_at for row in ordered)
    days = [(latest - row.last_purchase_at).total_seconds() / 86400 for row in ordered]
    frequency = [float(row.frequency) for row in ordered]
    monetary = [float(row.monetary) for row in ordered]
    recency_scaled = _scale(days, reverse=True)
    frequency_scaled = _scale(frequency)
    monetary_scaled = _scale(monetary)
    return Dataset(
        customer_ids=tuple(row.customer_id for row in ordered),
        raw=tuple(zip(days, frequency, monetary, strict=True)),
        points=tuple(
            zip(recency_scaled, frequency_scaled, monetary_scaled, strict=True)
        ),
        bounds=(
            (min(days), max(days)),
            (min(frequency), max(frequency)),
            (min(monetary), max(monetary)),
        ),
    )


def _squared(a: Point, b: Point) -> float:
    total = 0.0
    for x, y in zip(a, b, strict=True):
        total += (x - y) ** 2
    return total


def _nearest(point: Point, centroids: Sequence[Point]) -> int:
    """The nearest centroid; the strict comparison keeps the lowest index on a tie."""
    best, best_distance = 0, _squared(point, centroids[0])
    for index in range(1, len(centroids)):
        distance = _squared(point, centroids[index])
        if distance < best_distance:
            best, best_distance = index, distance
    return best


def _seed_centroids(points: Sequence[Point], k: int, seed: int) -> list[Point]:
    rng = random.Random(seed)
    n = len(points)
    first = min(int(rng.random() * n), n - 1)
    taken = {first}
    centres = [points[first]]
    weights = [_squared(point, points[first]) for point in points]
    while len(centres) < k:
        total = sum(weights)
        if total > 0:
            target = rng.random() * total
            running = 0.0
            pick = -1
            for index, weight in enumerate(weights):
                if weight <= 0:
                    continue
                pick = index
                running += weight
                if running >= target:
                    break
        else:
            pick = next(index for index in range(n) if index not in taken)
        taken.add(pick)
        centres.append(points[pick])
        weights = [
            min(weight, _squared(point, points[pick]))
            for weight, point in zip(weights, points, strict=True)
        ]
    return centres


def _refill(
    points: Sequence[Point], assignments: list[int], centroids: Sequence[Point]
) -> int:
    """Give each empty cluster the farthest customer of a cluster that can spare one."""
    sizes = [0] * len(centroids)
    for cluster in assignments:
        sizes[cluster] += 1
    moved = 0
    for empty in range(len(centroids)):
        if sizes[empty]:
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
    width = len(points[0])
    sums = [[0.0] * width for _ in range(k)]
    counts = [0] * k
    for point, cluster in zip(points, assignments, strict=True):
        counts[cluster] += 1
        for axis in range(width):
            sums[cluster][axis] += point[axis]
    return [
        tuple(value / count for value in total)
        for total, count in zip(sums, counts, strict=True)
    ]


def _sample(n: int, seed: int) -> list[int]:
    """Indexes for the silhouette sample: a seeded partial shuffle, in ascending order
    so
    the sum is taken in one fixed order."""
    rng = random.Random(f"silhouette:{seed}")
    pool = list(range(n))
    size = min(SILHOUETTE_SAMPLE_SIZE, n)
    for i in range(size):
        j = i + int(rng.random() * (n - i))
        pool[i], pool[j] = pool[j], pool[i]
    return sorted(pool[:size])


def _silhouette(
    points: Sequence[Point], assignments: Sequence[int], k: int, seed: int
) -> tuple[float | None, int | None]:
    if k < 2:
        return None, None
    n = len(points)
    sampled = n > SILHOUETTE_MAX_CUSTOMERS
    chosen = _sample(n, seed) if sampled else range(n)
    members: list[list[int]] = [[] for _ in range(k)]
    for index, cluster in enumerate(assignments):
        members[cluster].append(index)
    total = 0.0
    for index in chosen:
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
            if c != own and members[c]
        )
        scale = max(inside, outside)
        total += 0.0 if scale == 0 else (outside - inside) / scale
    return total / len(chosen), (len(chosen) if sampled else None)


def fit(
    dataset: Dataset,
    *,
    k: int,
    seed: int,
    max_iterations: int,
    tolerance: float,
    axes: Sequence[int] = (0, 1, 2),
    deadline: float | None = None,
) -> Fit:
    """Cluster the dataset with Lloyd's algorithm over the chosen axes.

    Raises TooFewCustomers when there are fewer customers than `k`. `deadline` is a
    reading
    of `time.monotonic()` after which the fit stops with TimeoutError; it is checked
    once per
    iteration, so a long fit is bounded without changing what a finished one returns.
    """
    n = len(dataset.customer_ids)
    if n < k:
        raise TooFewCustomers(
            f"{n} customers have sales in the window; k={k} needs {k}."
        )
    chosen = tuple(axes)
    points = [tuple(point[axis] for axis in chosen) for point in dataset.points]
    centroids = _seed_centroids(points, k, seed)
    assignments: list[int] = []
    events = 0
    shift = math.inf
    iterations = 0
    for iteration in range(1, max_iterations + 1):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("the fit ran past its deadline")
        iterations = iteration
        assignments = [_nearest(point, centroids) for point in points]
        events += _refill(points, assignments, centroids)
        updated = _means(points, assignments, k)
        shift = max(
            math.dist(old, new) for old, new in zip(centroids, updated, strict=True)
        )
        centroids = updated
        if shift <= tolerance:
            break
    converged = shift <= tolerance
    assignments = [_nearest(point, centroids) for point in points]
    events += _refill(points, assignments, centroids)
    sizes = [0] * k
    for cluster in assignments:
        sizes[cluster] += 1
    inertia = math.fsum(
        _squared(point, centroids[cluster])
        for point, cluster in zip(points, assignments, strict=True)
    )
    silhouette, sample = _silhouette(points, assignments, k, seed)
    # Every cluster is described by all three coordinates. The fitted ones are the
    # centroid itself; an axis that was not fitted is the mean of the cluster's
    # customers.
    described = _means(list(dataset.points), assignments, k)
    full = tuple(
        tuple(
            centroids[cluster][chosen.index(axis)] if axis in chosen else mean[axis]
            for axis in range(3)
        )
        for cluster, mean in enumerate(described)
    )
    return Fit(
        customer_ids=dataset.customer_ids,
        assignments=tuple(assignments),
        centroids=full,
        iterations=iterations,
        converged=converged,
        stopped_on="tolerance" if converged else "iteration_limit",
        final_shift=shift,
        empty_cluster_events=events,
        inertia=inertia,
        silhouette=silhouette,
        silhouette_sample=sample,
        sizes=tuple(sizes),
    )
