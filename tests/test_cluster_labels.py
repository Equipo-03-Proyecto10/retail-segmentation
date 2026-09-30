"""Mapping K-means clusters to the stable label vocabulary (F9-03, ADR-0018).

Cluster numbers are arbitrary per fit: with the same customers and the same
partition, one run may call a cluster 3 and the next may call it 1. A report that
compared them would claim 100% migration when nobody moved, raise no error and
reach a dashboard as false business information. The mapping here is what stops
that, so it is a pure function of the clusters' *contents* (their centroids and
their members) and never of their numbers, and these tests drive it with plain
fits whose contents they control.

The ordering, from the ADR: centroids by descending R + F + M; ties by descending
R, then F, then M; then by the lexicographically smallest customer id in the
cluster, which is deterministic and has no commercial meaning.
"""

from __future__ import annotations

import itertools
import random

import pytest

from web.services.cluster_labels import (
    MAPPING_RULE,
    VocabularySizeMismatch,
    describe_mapping,
    label_clusters,
    label_index,
    label_pairs,
    labels_by_rank,
    rank_clusters,
)
from web.services.kmeans import KMeansFit, KMeansParams, fit

_VOCAB = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "LOST"]


def _make_fit(clusters: dict[int, tuple[tuple[float, float, float], list[str]]]):
    """A finished fit whose clusters' contents are exactly as given:
    {cluster number: (centroid, member customer ids)}."""
    k = len(clusters)
    rows = sorted(
        (customer, number, clusters[number][0])
        for number in clusters
        for customer in clusters[number][1]
    )
    return KMeansFit(
        params=KMeansParams(k=k, seed=0),
        customer_ids=tuple(row[0] for row in rows),
        points=tuple(row[2] for row in rows),
        assignments=tuple(row[1] for row in rows),
        centroids=tuple(clusters[number][0] for number in range(k)),
        iterations=1,
        converged=True,
        stopped_on="tolerance",
        final_shift=0.0,
        empty_cluster_events=0,
        inertia=0.0,
        silhouette=None,
        cluster_sizes=tuple(
            sorted((len(clusters[n][1]) for n in clusters), reverse=True)
        ),
    )


def _permuted(result: KMeansFit, permutation: dict[int, int]) -> KMeansFit:
    """The same partition with every cluster renamed: cluster c becomes
    permutation[c], and the centroids move with their clusters."""
    centroids = [None] * len(result.centroids)
    for old, new in permutation.items():
        centroids[new] = result.centroids[old]
    return KMeansFit(
        **{
            **result.__dict__,
            "assignments": tuple(permutation[a] for a in result.assignments),
            "centroids": tuple(centroids),
        }
    )


def _best_to_worst(result: KMeansFit) -> list[int]:
    return rank_clusters(result)


# ---------- the ordering ----------


def test_centroids_are_ordered_by_descending_r_plus_f_plus_m() -> None:
    result = _make_fit(
        {
            0: ((0.1, 0.1, 0.1), ["c"]),  # 0.3
            1: ((0.9, 0.8, 0.7), ["a"]),  # 2.4
            2: ((0.4, 0.4, 0.4), ["b"]),  # 1.2
        }
    )

    assert _best_to_worst(result) == [1, 2, 0]


def test_a_tie_on_the_sum_is_broken_by_the_higher_r() -> None:
    result = _make_fit(
        {
            0: ((0.25, 0.5, 0.5), ["a"]),  # 1.25
            1: ((0.75, 0.25, 0.25), ["b"]),  # 1.25, but more recent
        }
    )

    assert _best_to_worst(result) == [1, 0]


def test_then_by_the_higher_f() -> None:
    result = _make_fit(
        {
            0: ((0.5, 0.25, 0.75), ["a"]),  # 1.5
            1: ((0.5, 0.75, 0.25), ["b"]),  # 1.5, same R, more frequent
        }
    )

    assert _best_to_worst(result) == [1, 0]


def test_then_by_the_higher_m() -> None:
    """With the sum, R and F equal, M can differ only below the resolution of the
    sum: here by 2**-54, which the exact sum rounds away. The ADR lists M so the
    rule is total, and this is the only place it can decide."""
    result = _make_fit(
        {
            0: ((0.5, 0.25, 0.25), ["a"]),
            1: ((0.5, 0.25, 0.25 + 2**-54), ["b"]),
        }
    )

    assert _best_to_worst(result) == [1, 0]


def test_identical_centroids_are_broken_by_the_smallest_member_id() -> None:
    result = _make_fit(
        {
            0: ((0.5, 0.5, 0.5), ["m", "z"]),
            1: ((0.5, 0.5, 0.5), ["a", "y"]),
        }
    )

    assert _best_to_worst(result) == [1, 0]


def test_the_customer_id_comparison_is_lexicographic_not_numeric() -> None:
    """Ids are text. "10" sorts before "9", which is why the record calls this
    tie-break deterministic and without commercial meaning."""
    result = _make_fit(
        {
            0: ((0.5, 0.5, 0.5), ["9"]),
            1: ((0.5, 0.5, 0.5), ["10"]),
        }
    )

    assert _best_to_worst(result) == [1, 0]


def test_a_sum_tie_that_floating_point_would_break_by_noise_is_broken_by_r() -> None:
    """0.1 + 0.2 + 0.3 is 0.6000000000000001 and 0.3 + 0.2 + 0.1 is 0.6, so a
    plain sum would rank these by rounding instead of tying them and moving on to
    R. The sum is exact and order-independent, so they tie and R decides."""
    result = _make_fit(
        {
            0: ((0.1, 0.2, 0.3), ["a"]),
            1: ((0.3, 0.2, 0.1), ["b"]),  # the same three numbers, higher R
        }
    )

    assert _best_to_worst(result) == [1, 0]


def test_the_order_does_not_depend_on_the_cluster_numbers() -> None:
    result = _make_fit(
        {
            0: ((0.2, 0.2, 0.2), ["a"]),
            1: ((0.9, 0.9, 0.9), ["b"]),
            2: ((0.5, 0.5, 0.5), ["c"]),
        }
    )
    contents = [result.centroids[c] for c in rank_clusters(result)]

    for permutation in itertools.permutations(range(3)):
        moved = _permuted(result, dict(enumerate(permutation)))
        assert [moved.centroids[c] for c in rank_clusters(moved)] == contents


# ---------- pairing with the vocabulary ----------


def test_the_best_cluster_takes_the_first_label_and_the_worst_the_last() -> None:
    result = _make_fit(
        {
            0: ((0.1, 0.1, 0.1), ["e"]),
            1: ((0.9, 0.9, 0.9), ["a"]),
            2: ((0.5, 0.5, 0.5), ["c"]),
            3: ((0.7, 0.7, 0.7), ["b"]),
            4: ((0.3, 0.3, 0.3), ["d"]),
        }
    )

    assert label_clusters(result, _VOCAB) == {
        1: "CHAMPION",
        3: "LOYAL",
        2: "POTENTIAL",
        4: "AT_RISK",
        0: "LOST",
    }


def test_every_cluster_gets_exactly_one_label_and_no_label_is_used_twice() -> None:
    result = _make_fit({n: ((n / 10, 0.5, 0.5), [f"c{n}"]) for n in range(5)})

    labels = label_clusters(result, _VOCAB)

    assert sorted(labels) == [0, 1, 2, 3, 4]
    assert sorted(labels.values()) == sorted(_VOCAB)


@pytest.mark.parametrize("k", [0, 1])
def test_fewer_than_two_clusters_is_refused(k: int) -> None:
    with pytest.raises(VocabularySizeMismatch, match=f"k={k}.*at least 2"):
        labels_by_rank(k, _VOCAB)


def test_an_empty_vocabulary_is_refused() -> None:
    with pytest.raises(VocabularySizeMismatch, match="empty"):
        labels_by_rank(3, [])


# ---------- any k: pairing by proportional rank (ADR-0030, #336) ----------

_SIX = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"]


@pytest.mark.parametrize(
    ("k", "expected"),
    [
        (2, ["CHAMPION", "LOST"]),
        (4, ["CHAMPION", "POTENTIAL", "AT_RISK", "LOST"]),
        (5, ["CHAMPION", "LOYAL", "AT_RISK", "HIBERNATING", "LOST"]),
        (6, _SIX),
        (
            8,
            [
                "CHAMPION",
                "LOYAL",
                "LOYAL",
                "POTENTIAL",
                "AT_RISK",
                "HIBERNATING",
                "HIBERNATING",
                "LOST",
            ],
        ),
    ],
)
def test_the_pairing_table_for_the_six_label_vocabulary(k, expected) -> None:
    assert labels_by_rank(k, _SIX) == expected


def test_k_equal_to_the_vocabulary_size_is_the_one_to_one_pairing_of_adr_0018() -> None:
    for size in range(1, 12):
        vocabulary = [f"L{n}" for n in range(size)]
        if size >= 2:
            assert labels_by_rank(size, vocabulary) == vocabulary


@pytest.mark.parametrize(("k", "size"), itertools.product(range(2, 15), range(1, 9)))
def test_best_is_always_the_best_label_worst_always_the_worst_and_order_holds(
    k: int, size: int
) -> None:
    indexes = [label_index(rank, k, size) for rank in range(k)]

    assert indexes[0] == 0 and indexes[-1] == size - 1
    assert indexes == sorted(indexes)  # a better cluster never gets a worse label
    assert all(0 <= index < size for index in indexes)


def test_a_half_rounds_towards_the_worse_label() -> None:
    # k = 5 over six labels: position 2 falls at 2.5, between POTENTIAL and AT_RISK
    assert 2 * (6 - 1) / (5 - 1) == 2.5
    assert labels_by_rank(5, _SIX)[2] == "AT_RISK"
    # k = 3 over six labels: position 1 falls at 2.5 as well
    assert labels_by_rank(3, _SIX) == ["CHAMPION", "AT_RISK", "LOST"]


def test_five_clusters_over_six_labels_pair_by_contents_leaving_a_label_unused() -> (
    None
):
    result = _make_fit(
        {n: ((1 - n / 10, 1 - n / 10, 1 - n / 10), [f"c{n}"]) for n in range(5)}
    )

    labels = label_clusters(result, _SIX)

    assert [labels[c] for c in _best_to_worst(result)] == labels_by_rank(5, _SIX)
    assert "POTENTIAL" not in labels.values()


def test_eight_clusters_over_six_labels_share_labels_and_ignore_cluster_numbers() -> (
    None
):
    result = _make_fit({n: ((n / 10, n / 10, n / 10), [f"c{n}"]) for n in range(8)})
    renamed = _permuted(result, {n: (n * 3) % 8 for n in range(8)})

    by_customer = label_pairs(result, _SIX)

    assert label_pairs(renamed, _SIX) == by_customer
    assert sorted(by_customer.values()).count("LOYAL") == 2
    assert sorted(by_customer.values()).count("HIBERNATING") == 2
    assert set(by_customer.values()) <= set(_SIX)


def test_the_recorded_mapping_is_keyed_by_label_and_rank_never_by_cluster() -> None:
    assert describe_mapping(8, _SIX) == {
        "rule": MAPPING_RULE,
        "vocabulary_size": 6,
        "labels_by_rank": labels_by_rank(8, _SIX),
        "clusters_per_label": {
            "CHAMPION": 1,
            "LOYAL": 2,
            "POTENTIAL": 1,
            "AT_RISK": 1,
            "HIBERNATING": 2,
            "LOST": 1,
        },
        "shared_labels": ["LOYAL", "HIBERNATING"],
        "labels_without_cluster": [],
    }
    five = describe_mapping(5, _SIX)
    assert five["shared_labels"] == []
    assert five["labels_without_cluster"] == ["POTENTIAL"]
    assert describe_mapping(6, _SIX)["clusters_per_label"] == dict.fromkeys(_SIX, 1)


# ---------- the customer-level result ----------


def test_label_pairs_names_each_customer_by_their_clusters_label() -> None:
    result = _make_fit(
        {
            0: ((0.1, 0.1, 0.1), ["b", "c"]),
            1: ((0.9, 0.9, 0.9), ["a"]),
        }
    )

    assert label_pairs(result, ["TOP", "BOTTOM"]) == {
        "a": "TOP",
        "b": "BOTTOM",
        "c": "BOTTOM",
    }


# ---------- cluster ids are permuted and nothing changes ----------


def test_renaming_every_cluster_of_a_fixed_partition_changes_no_customers_label() -> (
    None
):
    rng = random.Random(4)
    ids = [f"{i:04d}" for i in range(40)]
    points = [(rng.random(), rng.random(), rng.random()) for _ in ids]
    result = fit(ids, points, KMeansParams(k=5, seed=3))
    baseline = label_pairs(result, _VOCAB)

    for permutation in itertools.permutations(range(5)):
        moved = _permuted(result, dict(enumerate(permutation)))
        assert label_pairs(moved, _VOCAB) == baseline


def test_one_partition_under_different_numbers_is_labelled_alike() -> None:
    """The ADR's failure case, end to end: the algorithm named the clusters
    differently, nobody moved, and the labels must say so."""
    rng = random.Random(9)
    centres = [
        (0.1, 0.1, 0.1),
        (0.5, 0.9, 0.5),
        (0.9, 0.2, 0.9),
        (0.9, 0.9, 0.1),
        (0.2, 0.7, 0.8),
    ]
    ids, points = [], []
    for c, centre in enumerate(centres):
        for i in range(6):
            ids.append(f"{c}{i}")
            points.append(tuple(v + rng.uniform(-0.01, 0.01) for v in centre))

    numberings = {}
    for seed in range(40):
        result = fit(ids, points, KMeansParams(k=5, seed=seed))
        partition = frozenset(frozenset(result.members(c)) for c in range(5))
        numberings.setdefault(partition, []).append(
            (seed, label_pairs(result, _VOCAB), result.assignments)
        )
    same_partition = max(numberings.values(), key=len)
    assert len(same_partition) > 1
    assert (
        len({assignments for _, _, assignments in same_partition}) > 1
    ), "the seeds must number the same partition differently for this to prove anything"
    assert len({tuple(sorted(pairs.items())) for _, pairs, _ in same_partition}) == 1
