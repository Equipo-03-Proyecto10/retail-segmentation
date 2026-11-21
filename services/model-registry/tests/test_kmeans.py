"""The registry's own K-means and labelling: the hazards RN-58 names, label pairing
(RN-38)
and the shape of what a fit becomes. Parity with the web is `test_golden.py`'s job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app import kmeans, labels, rules, training

VOCABULARY = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"]
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def customers(n: int) -> list[kmeans.Sales]:
    return [
        kmeans.Sales(
            f"c{index:04d}",
            NOW - timedelta(days=(index * 7) % 170),
            1 + index % 9,
            Decimal(10 + (index * 37) % 900),
        )
        for index in range(n)
    ]


def run(rows: list[kmeans.Sales], **changes: object) -> kmeans.Fit:
    options = {"k": 3, "seed": 5, "max_iterations": 100, "tolerance": 0.0001} | changes
    return kmeans.fit(kmeans.prepare(rows), **options)  # type: ignore[arg-type]


def config(**changes: object) -> dict[str, object]:
    return {
        "version_name": "x",
        "window_days": 180,
        "seed": 3,
        "k": 5,
        "max_iterations": 100,
        "tolerance": 0.0001,
        "features": ["R", "F", "M"],
        "started_by": "u",
    } | changes


def test_the_order_customers_arrive_in_does_not_change_the_fit() -> None:
    rows = customers(60)
    a, b = run(rows), run(list(reversed(rows)))
    assert a.customer_ids == b.customer_ids and a.assignments == b.assignments
    assert a.centroids == b.centroids and a.inertia == b.inertia


def test_a_constant_feature_scales_to_zero_and_nothing_divides_by_zero() -> None:
    rows = [
        kmeans.Sales(f"c{i}", NOW - timedelta(days=i), 2, Decimal(50 + i))
        for i in range(12)
    ]
    prepared = kmeans.prepare(rows)
    assert {point[1] for point in prepared.points} == {0.0}
    assert run(rows).inertia >= 0


def test_fewer_customers_than_clusters_is_refused() -> None:
    with pytest.raises(kmeans.TooFewCustomers):
        run(customers(2), k=3)


def test_identical_customers_leave_no_cluster_empty() -> None:
    rows = [kmeans.Sales(f"c{i}", NOW, 3, Decimal(100)) for i in range(9)]
    fitted = run(rows, k=3)
    assert all(size >= 1 for size in fitted.sizes) and sum(fitted.sizes) == 9


def test_the_same_seed_repeats() -> None:
    rows = customers(80)
    assert run(rows, seed=1).assignments == run(rows, seed=1).assignments


def test_the_iteration_limit_stops_and_says_so() -> None:
    fitted = run(customers(200), k=6, max_iterations=1, tolerance=1e-12)
    assert fitted.iterations == 1 and not fitted.converged


def test_a_deadline_in_the_past_stops_the_fit() -> None:
    with pytest.raises(TimeoutError):
        run(customers(50), deadline=0.0)


def test_a_subset_of_variables_fits_only_those() -> None:
    fitted = run(customers(70), axes=(0, 1))
    assert all(len(centroid) == 3 for centroid in fitted.centroids)
    assert sum(fitted.sizes) == 70


@pytest.mark.parametrize("k", range(2, 9))
def test_every_cluster_count_gets_labels_from_best_to_worst(k: int) -> None:
    names = labels.labels_by_position(k, VOCABULARY)
    assert len(names) == k and names[0] == "CHAMPION" and names[-1] == "LOST"
    positions = [VOCABULARY.index(name) for name in names]
    assert positions == sorted(positions)


def test_labelling_needs_a_vocabulary_and_two_clusters() -> None:
    with pytest.raises(ValueError):
        labels.labels_by_position(1, VOCABULARY)
    with pytest.raises(ValueError):
        labels.labels_by_position(3, [])


def test_what_a_fit_becomes_validates_under_the_registrys_own_rules() -> None:
    data = {
        "sales": customers(120),
        "dataset": {"fingerprint": "a" * 64, "origin": "OBSERVED", "row_count": 500},
        "vocabulary": VOCABULARY,
    }
    body, fitted, sizes = training.fit_training(config(), data)
    assert rules.invalid_details(body) == []
    assert sum(sizes) == 120 and len(body["frozen_scoring"]["centroids"]) == 5
    for feature in body["reference_distribution"]["features"]:
        assert feature["bin_edges"] == sorted(set(feature["bin_edges"]))
        assert sum(feature["proportions"]) == pytest.approx(1.0)
    assert fitted.iterations >= 1


def test_a_run_with_no_labels_available_fails_with_its_code() -> None:
    data = {"sales": customers(30), "dataset": {}, "vocabulary": []}
    with pytest.raises(training.Failed) as error:
        training.fit_training(config(k=3), data)
    assert error.value.code == "label_vocabulary_unavailable"
