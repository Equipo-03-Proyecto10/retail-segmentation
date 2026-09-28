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
4. pair that order with the vocabulary in its declared best-to-worst order, whose
   size must equal k.

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
from collections.abc import Sequence

from web.services.kmeans import KMeansFit


class VocabularySizeMismatch(ValueError):
    """A label vocabulary whose size is not the number of clusters."""


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
    """Raise VocabularySizeMismatch unless there is exactly one label per cluster."""
    if len(vocabulary) != k:
        raise VocabularySizeMismatch(
            f"K-means was asked for k={k} clusters but the label vocabulary has "
            f"{len(vocabulary)} labels. They must be equal, so every cluster has "
            "one label and no label is left over."
        )


def label_clusters(result: KMeansFit, vocabulary: Sequence[str]) -> dict[int, str]:
    """The label of every cluster, pairing the best-to-worst order with the
    vocabulary given best to worst.

    Raises VocabularySizeMismatch unless there is exactly one label per cluster.
    """
    check_vocabulary_size(len(result.centroids), vocabulary)
    return {
        cluster: vocabulary[rank] for rank, cluster in enumerate(rank_clusters(result))
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
