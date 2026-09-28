"""The K-means fit (F9-02, ADR-0021).

ADR-0021 has the application write Lloyd's algorithm itself, which makes three
failure modes this code's own responsibility: empty clusters, a fit that reaches
its iteration limit without converging, and ties between equally distant
centroids. Each fails by producing plausible output rather than an error, so each
has a defined behaviour here and a test that pins it. The fit is pure: plain
numbers in, a result out, no database and no clock.
"""

from __future__ import annotations

import itertools
import json
import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from web.services import kmeans
from web.services.kmeans import (
    EMPTY_CLUSTER_POLICY,
    INITIALISATION,
    NORMALISATION,
    TIE_BREAK,
    InvalidParameters,
    KMeansParams,
    RawRfm,
    TooFewCustomers,
    fit,
    fit_customers,
    min_max,
    nearest,
    normalise,
    recorded_parameters,
    refill_empty_clusters,
)

_DAY = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _ids(n: int) -> list[str]:
    return [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, n + 1)]


def _blobs(per_blob: int = 8) -> tuple[list[str], list[tuple[float, float, float]]]:
    """Three tight, well-separated groups of points in the unit cube."""
    rng = random.Random(11)
    centres = [(0.1, 0.1, 0.1), (0.5, 0.9, 0.5), (0.9, 0.2, 0.9)]
    points = [
        tuple(c + rng.uniform(-0.02, 0.02) for c in centre)
        for centre in centres
        for _ in range(per_blob)
    ]
    return _ids(len(points)), points


def _cloud(n: int, seed: int) -> tuple[list[str], list[tuple[float, float, float]]]:
    rng = random.Random(seed)
    return _ids(n), [(rng.random(), rng.random(), rng.random()) for _ in range(n)]


def _groups(result) -> set[frozenset[str]]:
    """The partition as a set of customer groups, with no cluster id in it."""
    return {frozenset(result.members(c)) for c in range(result.params.k)}


# ---------- normalisation ----------


def test_min_max_maps_the_smallest_to_zero_and_the_largest_to_one() -> None:
    assert min_max([10.0, 20.0, 30.0]) == [0.0, 0.5, 1.0]


def test_a_constant_feature_maps_to_zero_rather_than_dividing_by_zero() -> None:
    assert min_max([7.0, 7.0, 7.0]) == [0.0, 0.0, 0.0]


def test_a_constant_feature_also_maps_to_zero_when_reversed() -> None:
    """Reversal must not turn "no information" into the best score."""
    assert min_max([7.0, 7.0], reverse=True) == [0.0, 0.0]


def test_one_customer_is_a_constant_feature_in_every_dimension() -> None:
    row = RawRfm(_ids(1)[0], _DAY, 3, Decimal("50.00"))

    assert normalise([row]) == [(0.0, 0.0, 0.0)]


def test_recency_is_reversed_so_the_most_recent_customer_scores_highest() -> None:
    rows = [
        RawRfm("a", _DAY - timedelta(days=30), 1, Decimal("1")),
        RawRfm("b", _DAY - timedelta(days=10), 1, Decimal("1")),
        RawRfm("c", _DAY, 1, Decimal("1")),
    ]

    r = [point[0] for point in normalise(rows)]

    assert r == [0.0, pytest.approx(2 / 3), 1.0]


def test_higher_frequency_and_spend_score_higher() -> None:
    rows = [
        RawRfm("a", _DAY, 1, Decimal("10.00")),
        RawRfm("b", _DAY, 5, Decimal("50.00")),
        RawRfm("c", _DAY, 9, Decimal("90.00")),
    ]

    points = normalise(rows)

    assert [p[1] for p in points] == [0.0, 0.5, 1.0]
    assert [p[2] for p in points] == [0.0, 0.5, 1.0]


def test_a_feature_that_is_constant_across_customers_is_zero_while_the_rest_scale() -> (
    None
):
    rows = [
        RawRfm("a", _DAY, 4, Decimal("10.00")),
        RawRfm("b", _DAY, 4, Decimal("30.00")),
    ]

    assert normalise(rows) == [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0)]


def test_every_normalised_value_is_inside_the_unit_interval() -> None:
    rng = random.Random(5)
    rows = [
        RawRfm(
            f"c{i}",
            _DAY - timedelta(days=rng.randint(0, 400)),
            rng.randint(1, 60),
            Decimal(rng.randint(1, 90000)) / 100,
        )
        for i in range(200)
    ]

    for point in normalise(rows):
        assert all(0.0 <= value <= 1.0 for value in point)


def test_normalisation_does_not_depend_on_which_moment_recency_is_measured_from() -> (
    None
):
    """Days since the last purchase, reversed, is the same as the purchase instant
    itself scaled: the reference moment cancels. So a run is reproducible without
    recording a clock."""
    rows = [
        RawRfm(f"c{i}", _DAY - timedelta(days=d), 1, Decimal(1))
        for i, d in enumerate([0, 3, 9, 40])
    ]
    later = [
        RawRfm(r.customer_id, r.last_purchase_at + timedelta(days=500), 1, Decimal(1))
        for r in rows
    ]

    assert normalise(rows) == normalise(later)


# ---------- the fit ----------


def test_well_separated_groups_are_recovered_exactly() -> None:
    ids, points = _blobs()

    result = fit(ids, points, KMeansParams(k=3, seed=1))

    assert _groups(result) == {
        frozenset(ids[0:8]),
        frozenset(ids[8:16]),
        frozenset(ids[16:24]),
    }
    assert result.converged is True


def test_the_same_data_seed_and_parameters_give_the_same_partition() -> None:
    ids, points = _cloud(60, 3)
    params = KMeansParams(k=4, seed=99)

    first = fit(ids, points, params)
    for _ in range(4):
        again = fit(ids, points, params)
        assert again.assignments == first.assignments
        assert again.centroids == first.centroids
        assert again.inertia == first.inertia
        assert again.iterations == first.iterations


def test_stored_assignments_use_the_nearest_final_centroids() -> None:
    points = [
        (0.370594, 0.907625, 0.396027),
        (0.206053, 0.817798, 0.758784),
        (0.698283, 0.021814, 0.407230),
    ]

    result = fit(_ids(3), points, KMeansParams(k=2, seed=7, tolerance=0.5))

    assert result.converged is True
    assert result.assignments == tuple(
        nearest(point, result.centroids) for point in result.points
    )


@pytest.mark.parametrize("max_iterations", [1, 100])
def test_seeded_random_fits_store_the_nearest_final_centroid_partition(
    max_iterations,
) -> None:
    for seed in range(12):
        ids, points = _cloud(30, seed)
        result = fit(
            ids,
            points,
            KMeansParams(
                k=4,
                seed=seed,
                max_iterations=max_iterations,
                tolerance=0.0,
            ),
        )

        assert result.empty_cluster_events == 0
        assert result.assignments == tuple(
            nearest(point, result.centroids) for point in result.points
        )
        if max_iterations == 1:
            assert result.stopped_on == "iteration_limit"


def test_the_partition_does_not_depend_on_the_order_customers_arrive_in() -> None:
    """The database returns rows in whatever order it likes. The fit sorts by
    customer id before it does anything, so that order cannot reach the result."""
    ids, points = _cloud(24, 8)
    pairs = list(zip(ids, points, strict=True))
    params = KMeansParams(k=3, seed=5)
    baseline = fit(ids, points, params)

    rng = random.Random(0)
    for _ in range(6):
        rng.shuffle(pairs)
        shuffled_ids, shuffled_points = zip(*pairs, strict=True)
        result = fit(list(shuffled_ids), list(shuffled_points), params)
        assert _groups(result) == _groups(baseline)
        assert result.centroids == baseline.centroids


def test_a_different_seed_is_a_different_run_and_is_recorded_as_such() -> None:
    ids, points = _cloud(40, 2)

    a = fit(ids, points, KMeansParams(k=4, seed=1))
    b = fit(ids, points, KMeansParams(k=4, seed=2))

    assert a.params.seed == 1 and b.params.seed == 2


def test_one_cluster_holds_everyone_and_is_their_mean() -> None:
    ids, points = _cloud(10, 4)

    result = fit(ids, points, KMeansParams(k=1, seed=0))

    assert set(result.assignments) == {0}
    mean = tuple(sum(p[d] for p in points) / 10 for d in range(3))
    assert result.centroids[0] == pytest.approx(mean)
    assert result.silhouette is None  # undefined for one cluster


def test_as_many_clusters_as_customers_puts_each_alone() -> None:
    ids, points = _cloud(5, 6)

    result = fit(ids, points, KMeansParams(k=5, seed=0))

    assert sorted(result.cluster_sizes) == [1, 1, 1, 1, 1]
    assert result.inertia == pytest.approx(0.0)


def test_fewer_customers_than_clusters_is_refused_with_a_clear_message() -> None:
    ids, points = _cloud(3, 1)

    with pytest.raises(TooFewCustomers, match="3.*5"):
        fit(ids, points, KMeansParams(k=5, seed=0))


def test_the_same_customer_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        fit(
            ["a", "a", "b"],
            [(0, 0, 0), (1, 1, 1), (2, 2, 2)],
            KMeansParams(k=2, seed=0),
        )


@pytest.mark.parametrize(
    "params",
    [
        dict(k=0, seed=0),
        dict(k=-1, seed=0),
        dict(k=2, seed=0, max_iterations=0),
        dict(k=2, seed=0, tolerance=-0.1),
        dict(k=2, seed=0, tolerance=float("nan")),
        dict(k=2, seed=0, tolerance=float("inf")),
        dict(k=2, seed=1.5),
        dict(k=2.0, seed=0),
        dict(k=True, seed=0),
    ],
)
def test_parameters_that_cannot_define_a_fit_are_refused(params) -> None:
    with pytest.raises(InvalidParameters):
        KMeansParams(**params)


def test_points_and_customers_must_pair_up() -> None:
    with pytest.raises(ValueError, match="pair"):
        fit(["a", "b"], [(0, 0, 0)], KMeansParams(k=1, seed=0))


# ---------- convergence: stopped on the tolerance, or on the limit ----------


def test_a_fit_that_settles_records_that_it_stopped_on_the_tolerance() -> None:
    ids, points = _blobs()

    result = fit(ids, points, KMeansParams(k=3, seed=1, max_iterations=50))

    assert result.converged is True
    assert result.stopped_on == "tolerance"
    assert result.iterations < 50
    assert result.final_shift <= result.params.tolerance


def test_a_fit_that_reaches_the_limit_says_so_and_is_not_presented_as_converged() -> (
    None
):
    ids, points = _cloud(50, 3)

    result = fit(
        ids, points, KMeansParams(k=4, seed=7, max_iterations=1, tolerance=0.0)
    )

    assert result.iterations == 1
    assert result.converged is False
    assert result.stopped_on == "iteration_limit"
    assert result.final_shift > 0.0


def test_a_fit_that_converges_on_its_last_allowed_iteration_counts_as_converged() -> (
    None
):
    ids, points = _cloud(50, 3)
    params = KMeansParams(k=4, seed=7, max_iterations=200, tolerance=1e-9)
    settled = fit(ids, points, params)
    assert settled.converged and settled.iterations > 1

    exactly = fit(ids, points, KMeansParams(4, 7, settled.iterations, 1e-9))
    one_short = fit(ids, points, KMeansParams(4, 7, settled.iterations - 1, 1e-9))

    assert exactly.converged is True and exactly.stopped_on == "tolerance"
    assert one_short.converged is False and one_short.stopped_on == "iteration_limit"


def test_the_partition_a_non_converged_fit_returns_uses_final_centroids() -> None:
    """An iteration-limit stop still finishes with a final assignment step."""
    ids, points = _cloud(40, 9)

    result = fit(
        ids, points, KMeansParams(k=3, seed=2, max_iterations=1, tolerance=0.0)
    )

    assert len(result.assignments) == 40
    assert result.assignments == tuple(
        nearest(point, result.centroids) for point in result.points
    )


# ---------- empty clusters ----------


def _duplicates() -> tuple[list[str], list[tuple[float, float, float]]]:
    """Two stacks of identical customers. With k=3 there is nowhere for a third
    centroid to attract anyone, which is the way an empty cluster really arises."""
    points = [(0.0, 0.0, 0.0)] * 4 + [(1.0, 1.0, 1.0)] * 4
    return _ids(8), points


def test_an_empty_cluster_is_refilled_and_the_run_still_has_k_clusters() -> None:
    ids, points = _duplicates()

    result = fit(ids, points, KMeansParams(k=3, seed=0))

    assert len(result.cluster_sizes) == 3
    assert all(size >= 1 for size in result.cluster_sizes)
    assert sum(result.cluster_sizes) == 8
    assert result.empty_cluster_events >= 1


def test_a_refilled_cluster_takes_the_customer_farthest_from_their_own_centroid() -> (
    None
):
    points = [(0, 0, 0), (0, 0, 0), (0, 0, 0.3), (1, 1, 1), (1, 1, 1)]
    centroids = [(0, 0, 0.1), (1, 1, 1), (5, 5, 5)]  # the third attracted no one
    assignments = [0, 0, 0, 1, 1]

    events = refill_empty_clusters(points, assignments, centroids)

    # squared distances to their own centroid are 0.01, 0.01, 0.04, 0, 0, so the
    # customer at index 2 is the farthest and is the one who moves
    assert assignments == [0, 0, 2, 1, 1]
    assert events == 1


def test_among_equally_far_customers_the_lowest_customer_id_moves() -> None:
    points = [(0, 0, 0)] * 3 + [(1, 1, 1)] * 2
    centroids = [(0, 0, 0), (1, 1, 1), (5, 5, 5)]
    assignments = [0, 0, 0, 1, 1]

    refill_empty_clusters(points, assignments, centroids)

    assert assignments == [2, 0, 0, 1, 1]


def test_a_cluster_holding_one_customer_never_gives_them_up() -> None:
    """Otherwise refilling one empty cluster would empty another."""
    points = [(0, 0, 0), (1, 1, 1), (1, 1, 1)]
    centroids = [(0, 0, 0), (1, 1, 1), (9, 9, 9)]
    assignments = [0, 1, 1]

    refill_empty_clusters(points, assignments, centroids)

    assert assignments[0] == 0
    assert sorted(set(assignments)) == [0, 1, 2]


def test_two_empty_clusters_are_refilled_each_from_a_cluster_that_can_spare() -> None:
    points = [(0, 0, 0)] * 4
    centroids = [(0, 0, 0), (7, 7, 7), (8, 8, 8)]
    assignments = [0, 0, 0, 0]

    events = refill_empty_clusters(points, assignments, centroids)

    assert events == 2
    assert sorted(assignments) == [0, 0, 1, 2]


def test_nothing_is_refilled_when_no_cluster_is_empty() -> None:
    assignments = [0, 1, 0, 1]

    assert (
        refill_empty_clusters([(0, 0, 0)] * 4, assignments, [(0, 0, 0), (1, 1, 1)]) == 0
    )
    assert assignments == [0, 1, 0, 1]


def test_the_empty_cluster_handling_is_deterministic() -> None:
    ids, points = _duplicates()
    params = KMeansParams(k=3, seed=4)

    runs = [fit(ids, points, params) for _ in range(5)]

    assert len({(r.assignments, r.empty_cluster_events) for r in runs}) == 1


def test_no_seed_and_no_data_ever_leaves_fewer_than_k_clusters() -> None:
    """The failure this guards against is silent: a run that quietly produces
    fewer clusters than k would later be paired with the wrong labels."""
    for seed in range(60):
        n = 6 + seed % 40
        k = 2 + seed % 5
        # a coarse grid makes duplicates, and duplicates make empty clusters
        rng = random.Random(seed)
        points = [(rng.randint(0, 2) / 2, rng.randint(0, 2) / 2, 0.0) for _ in range(n)]
        result = fit(_ids(n), points, KMeansParams(k=k, seed=seed))

        assert len(result.cluster_sizes) == k, (seed, n, k)
        assert min(result.cluster_sizes) >= 1, (seed, n, k)
        assert sum(result.cluster_sizes) == n, (seed, n, k)


# ---------- ties between equally distant centroids ----------


def test_a_customer_equidistant_from_two_centroids_goes_to_the_lower_index() -> None:
    assert nearest((0.5, 0.0, 0.0), [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]) == 0
    assert nearest((0.5, 0.0, 0.0), [(1.0, 0.0, 0.0), (0.0, 0.0, 0.0)]) == 0


def test_identical_centroids_send_everyone_to_the_lowest_of_them() -> None:
    assert (
        nearest((0.2, 0.2, 0.2), [(0.9, 0.9, 0.9), (0.2, 0.2, 0.2), (0.2, 0.2, 0.2)])
        == 1
    )


def test_a_customer_exactly_between_two_clusters_does_not_move_between_fits() -> None:
    points = [
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.1),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 0.1),
        (0.5, 0.0, 0.05),
    ]
    ids = _ids(5)
    params = KMeansParams(k=2, seed=3)

    first = fit(ids, points, params)
    for _ in range(5):
        assert fit(ids, points, params).assignments == first.assignments


# ---------- quality metrics ----------


def test_inertia_is_the_sum_of_squared_distances_to_each_own_centroid() -> None:
    ids = _ids(4)
    points = [(0.0, 0.0, 0.0), (0.2, 0.0, 0.0), (1.0, 1.0, 1.0), (1.0, 1.0, 0.8)]

    result = fit(ids, points, KMeansParams(k=2, seed=0))

    assert result.inertia == pytest.approx(0.02 + 0.02)


def test_silhouette_is_near_one_for_well_separated_groups() -> None:
    ids, points = _blobs()

    result = fit(ids, points, KMeansParams(k=3, seed=1))

    assert result.silhouette > 0.9


def test_silhouette_is_lower_for_a_cloud_with_no_structure() -> None:
    ids, points = _cloud(60, 12)

    assert fit(ids, points, KMeansParams(k=4, seed=1)).silhouette < 0.6


def test_a_singleton_cluster_scores_zero_rather_than_dividing_by_nothing() -> None:
    ids = _ids(3)
    points = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.1), (1.0, 1.0, 1.0)]

    result = fit(ids, points, KMeansParams(k=2, seed=0))

    assert math.isfinite(result.silhouette)


def test_silhouette_is_skipped_and_says_so_above_its_size_limit(monkeypatch) -> None:
    """It compares every customer with every other, which is quadratic in pure
    Python. Past the limit it is recorded as absent rather than made to run long."""
    monkeypatch.setattr(kmeans, "SILHOUETTE_MAX_CUSTOMERS", 10)
    ids, points = _cloud(11, 1)

    result = fit(ids, points, KMeansParams(k=2, seed=0))

    assert result.silhouette is None
    snapshot = recorded_parameters(result, 180)
    assert snapshot["quality"]["silhouette"] is None
    assert "10" in snapshot["quality"]["silhouette_skipped"]


def test_the_cluster_sizes_are_recorded_largest_first_with_no_cluster_id() -> None:
    ids = _ids(7)
    points = [(0.0, 0.0, 0.0)] * 4 + [(1.0, 1.0, 1.0)] * 2 + [(0.5, 0.5, 0.5)]

    result = fit(ids, points, KMeansParams(k=3, seed=0))

    assert list(result.cluster_sizes) == sorted(result.cluster_sizes, reverse=True)
    assert sum(result.cluster_sizes) == 7


# ---------- what the run records ----------


def test_the_recorded_parameters_state_everything_that_shaped_the_fit() -> None:
    ids, points = _cloud(30, 5)
    result = fit(
        ids, points, KMeansParams(k=4, seed=17, max_iterations=80, tolerance=0.001)
    )

    recorded = recorded_parameters(result, 120)

    assert recorded["k"] == 4
    assert recorded["seed"] == 17
    assert recorded["max_iterations"] == 80
    assert recorded["tolerance"] == 0.001
    assert recorded["window_days"] == 120  # the feature window
    assert recorded["initialisation"] == INITIALISATION
    assert recorded["normalisation"] == NORMALISATION
    assert recorded["empty_cluster_policy"] == EMPTY_CLUSTER_POLICY
    assert recorded["tie_break"] == TIE_BREAK


def test_the_quality_metrics_are_recorded_alongside_the_parameters() -> None:
    ids, points = _blobs()
    result = fit(ids, points, KMeansParams(k=3, seed=1))

    quality = recorded_parameters(result, 180)["quality"]

    assert set(quality) >= {
        "inertia",
        "silhouette",
        "cluster_sizes",
        "iterations",
        "converged",
        "stopped_on",
        "final_shift",
        "empty_cluster_events",
        "customers_clustered",
    }
    assert quality["customers_clustered"] == 24
    assert quality["converged"] is True and quality["stopped_on"] == "tolerance"


def test_the_empty_cluster_events_are_recorded_on_the_run() -> None:
    ids, points = _duplicates()
    result = fit(ids, points, KMeansParams(k=3, seed=0))

    assert recorded_parameters(result, 180)["quality"]["empty_cluster_events"] >= 1


def test_a_non_converged_run_records_that_it_did_not_converge() -> None:
    ids, points = _cloud(50, 3)
    result = fit(
        ids, points, KMeansParams(k=4, seed=7, max_iterations=1, tolerance=0.0)
    )

    quality = recorded_parameters(result, 180)["quality"]

    assert quality["converged"] is False
    assert quality["stopped_on"] == "iteration_limit"


def test_the_recorded_parameters_are_json_and_hold_no_nan() -> None:
    ids, points = _cloud(30, 5)

    recorded = recorded_parameters(fit(ids, points, KMeansParams(k=3, seed=1)), 90)

    assert json.loads(json.dumps(recorded, allow_nan=False)) == recorded


def test_the_recorded_parameters_never_name_a_cluster() -> None:
    """ADR-0018: a raw cluster id is a diagnostic of one fit and cannot enter a
    reporting contract. Sizes are recorded as a list, not as a mapping by id."""
    ids, points = _cloud(30, 5)

    text = json.dumps(
        recorded_parameters(fit(ids, points, KMeansParams(k=3, seed=1)), 90)
    )

    assert "cluster_id" not in text and "centroid" not in text


def test_a_fit_over_the_raw_rows_is_the_normalisation_then_the_fit() -> None:
    rng = random.Random(21)
    rows = [
        RawRfm(
            f"c{i:03d}",
            _DAY - timedelta(days=rng.randint(0, 90)),
            rng.randint(1, 30),
            Decimal(rng.randint(1, 5000)),
        )
        for i in range(30)
    ]
    params = KMeansParams(k=3, seed=8)

    direct = fit_customers(rows, params)
    by_hand = fit([r.customer_id for r in rows], normalise(rows), params)

    assert direct.assignments == by_hand.assignments
    assert direct.centroids == by_hand.centroids


def test_every_order_of_the_same_raw_rows_gives_the_same_groups() -> None:
    rows = [
        RawRfm("a", _DAY - timedelta(days=1), 9, Decimal("900")),
        RawRfm("b", _DAY - timedelta(days=2), 8, Decimal("800")),
        RawRfm("c", _DAY - timedelta(days=60), 1, Decimal("10")),
        RawRfm("d", _DAY - timedelta(days=70), 2, Decimal("20")),
    ]
    params = KMeansParams(k=2, seed=1)

    groups = {
        frozenset(_groups(fit_customers(list(order), params)))
        for order in itertools.permutations(rows)
    }

    assert len(groups) == 1
