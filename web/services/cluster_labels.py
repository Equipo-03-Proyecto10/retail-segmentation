"""From clusters to stable labels (F9-03, ADR-0018).

K-means cluster numbers are arbitrary names one fit gave its clusters. With the
same customers and the same partition, one run may call a cluster 3 and the next
may call it 1, so a report that compared the numbers would claim 100% migration
when no customer moved. It would raise no error and reach a dashboard as false
business information. Business identity therefore comes from the stable label
vocabulary, and this module is the only place a cluster becomes one.

The rule is ADR-0018's, and it is a function of what a cluster *contains* and
never of what it is *called*:

1. order the clusters by the descending sum of their centroid's R, F and M, all
   normalised so that higher is better;
2. break a tie by the higher R, then the higher F, then the higher M;
3. break a tie between identical centroids by the lexicographically smallest
   customer id among the cluster's members;
4. pair that order with the vocabulary in its declared best-to-worst order by
   proportional rank (ADR-0030): the cluster in position `i` of `k` takes the
   label in position `i * (V - 1) / (k - 1)` of the `V` labels, rounded to the
   nearest, and a half rounded towards the worse label. The best cluster is
   always the best label and the worst always the worst. With `k == V` this is
   position `i` exactly, the one-to-one pairing ADR-0018 required; with fewer
   clusters some labels hold none, and with more some are shared.

The customer-id tie-break is deterministic and has no commercial meaning. It exists
so that two clusters that are the same in every measure cannot be ordered by
anything arbitrary. Ids are compared as text, so "10" sorts before "9".

The sum is taken with `math.fsum`, which is exact and does not depend on the order
of its terms. A plain sum does: 0.1 + 0.2 + 0.3 is 0.6000000000000001 and
0.3 + 0.2 + 0.1 is 0.6, so two centroids made of the same three numbers would be
ranked by rounding instead of tying on the sum and moving on to R, as the rule
says. Ties are otherwise on the float values exactly as the fit computed them.
"""

import math
from collections import Counter
from collections.abc import Sequence

from web.services.kmeans import KMeansFit

# ADR-0030: one cluster has no best-to-worst order to pair with anything.
MIN_CLUSTERS = 2
MAPPING_RULE = "rank_proportional_endpoints_anchored"


class VocabularySizeMismatch(ValueError):
    """A number of clusters, or a vocabulary, that cannot be paired by rank."""


def _order_key(result: KMeansFit, cluster: int) -> tuple:
    recency, frequency, monetary = result.centroids[cluster]
    return (
        -math.fsum((recency, frequency, monetary)),
        -recency,
        -frequency,
        -monetary,
        min(result.members(cluster)),
    )


def rank_clusters(result: KMeansFit) -> list[int]:
    """Cluster numbers, best first. The order depends on the clusters' contents
    only, so renaming every cluster leaves the sequence of contents unchanged."""
    return sorted(range(len(result.centroids)), key=lambda c: _order_key(result, c))


def check_vocabulary_size(k: int, vocabulary: Sequence[str]) -> None:
    """Raise VocabularySizeMismatch unless `k` clusters can be paired with the
    vocabulary by rank: at least two clusters, and at least one label."""
    if k < MIN_CLUSTERS:
        raise VocabularySizeMismatch(
            f"K-means was asked for k={k} clusters. It needs at least "
            f"{MIN_CLUSTERS}: a single cluster has no best-to-worst order to "
            "pair with the labels."
        )
    if not vocabulary:
        raise VocabularySizeMismatch("The label vocabulary is empty.")


def label_index(rank: int, k: int, vocabulary_size: int) -> int:
    """The vocabulary position of the cluster in position `rank` of `k`.

    `rank * (V - 1) / (k - 1)`, rounded to the nearest position with a half
    going to the worse (higher) one, computed in integers so no float decides a
    label. Position 0 is always 0 and position `k - 1` always `V - 1`.
    """
    span = vocabulary_size - 1
    return (2 * rank * span + (k - 1)) // (2 * (k - 1))


def labels_by_rank(k: int, vocabulary: Sequence[str]) -> list[str]:
    """The label of each position in the best-to-worst order of `k` clusters."""
    check_vocabulary_size(k, vocabulary)
    return [vocabulary[label_index(rank, k, len(vocabulary))] for rank in range(k)]


def label_clusters(result: KMeansFit, vocabulary: Sequence[str]) -> dict[int, str]:
    """The label of every cluster, pairing the best-to-worst order with the
    vocabulary given best to worst, by proportional rank (ADR-0030).

    Raises VocabularySizeMismatch for fewer than two clusters.
    """
    labels = labels_by_rank(len(result.centroids), vocabulary)
    return {cluster: labels[rank] for rank, cluster in enumerate(rank_clusters(result))}


def describe_mapping(k: int, vocabulary: Sequence[str]) -> dict:
    """What a run records about its pairing (ADR-0030), keyed by label code and
    by position in the best-to-worst order -- never by cluster number, which
    means nothing outside one fit (ADR-0018, RN-38).

    `shared_labels` are the labels more than one cluster took;
    `labels_without_cluster` the ones no cluster took, and so no customer.
    Both are in the vocabulary's order.
    """
    by_rank = labels_by_rank(k, vocabulary)
    counts = Counter(by_rank)
    return {
        "rule": MAPPING_RULE,
        "vocabulary_size": len(vocabulary),
        "labels_by_rank": by_rank,
        "clusters_per_label": {label: counts[label] for label in vocabulary},
        "shared_labels": [label for label in vocabulary if counts[label] > 1],
        "labels_without_cluster": [label for label in vocabulary if not counts[label]],
    }


def label_pairs(result: KMeansFit, vocabulary: Sequence[str]) -> dict[str, str]:
    """Each customer's label, by the label of the cluster they are in."""
    labels = label_clusters(result, vocabulary)
    return {
        customer: labels[cluster]
        for customer, cluster in zip(
            result.customer_ids, result.assignments, strict=True
        )
    }
