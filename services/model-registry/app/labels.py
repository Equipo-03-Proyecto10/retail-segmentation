"""From clusters to stable labels (RN-38, ADR-0030).

A K-means cluster number is a name one fit gave its cluster, so a version that kept it
would
say a customer moved when nobody did. A cluster takes its label from what it contains,
by
one rule, and the version freezes the pairing:

1. order the clusters by the descending sum of their centroid's R, F and M, each
normalized
   so that higher is better;
2. a tie goes to the higher R, then the higher F, then the higher M;
3. a tie between identical centroids goes to the cluster holding the lexicographically
   smallest customer id (ids compare as text, so "10" sorts before "9");
4. pair that order with the label vocabulary, given best to worst, by proportional
rank: of
   `k` clusters and `V` labels the cluster in position `i` takes the label in position
   `i * (V - 1) / (k - 1)`, rounded to the nearest and, on a half, toward the worse
   label.

The sum is taken with `math.fsum`, which does not depend on the order of its terms, so
two
centroids made of the same three numbers tie on the sum and fall through to R, as the
rule
says, instead of being ranked by rounding.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .kmeans import Fit

MIN_CLUSTERS = 2


def rank(fit: Fit) -> list[int]:
    """Raw cluster numbers, best first. The order reads contents, never numbers."""

    def key(cluster: int) -> tuple:
        r, f, m = fit.centroids[cluster]
        return (-math.fsum((r, f, m)), -r, -f, -m, min(fit.members(cluster)))

    return sorted(range(len(fit.centroids)), key=key)


def label_position(position: int, k: int, vocabulary_size: int) -> int:
    """The vocabulary position of the cluster in `position` of `k`, in integers so no
    float
    decides a label. Position 0 is always the first label and `k - 1` the last."""
    span = vocabulary_size - 1
    return (2 * position * span + (k - 1)) // (2 * (k - 1))


def labels_by_position(k: int, vocabulary: Sequence[str]) -> list[str]:
    if k < MIN_CLUSTERS:
        raise ValueError(
            "A single cluster has no best-to-worst order to pair with labels."
        )
    if not vocabulary:
        raise ValueError("The label vocabulary is empty.")
    return [vocabulary[label_position(p, k, len(vocabulary))] for p in range(k)]


def label_clusters(fit: Fit, vocabulary: Sequence[str]) -> dict[int, str]:
    """The label of every raw cluster number."""
    labels = labels_by_position(len(fit.centroids), vocabulary)
    return {cluster: labels[place] for place, cluster in enumerate(rank(fit))}
