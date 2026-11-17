"""The F23-04 study: a registered result reproduced, and its stability (#816)."""

import pytest

from analysis import stability_study as study
from analysis.dataset import generate
from analysis.normalization_study import Features, n_minmax
from web.services.kmeans import KMeansParams, fit

SMALL = generate(customers=300)


def _small_points():
    return study._points(SMALL, 0, 180)


def test_the_registered_result_is_reproduced_and_pinned():
    # The registered configuration of the evidence document: the same seed, k and
    # window always give this partition, these metrics and this digest.
    result = study.reproduce(repeats=2)
    assert result["differences"] == []
    assert result["digest"] == (
        "040e8e1a8171ac792d4e0c6235376b13a45602afa075f67d372a020e677d26e0"
    )
    quality = result["record"]["quality"]
    assert quality["inertia"] == 54.170765
    assert quality["cluster_sizes"] == [1471, 1231, 748, 529]
    assert quality["converged"] is True


def test_a_fit_that_differs_is_reported(monkeypatch):
    ids, points = _small_points()
    first = fit(ids, points, KMeansParams(k=3, seed=2))
    changed = fit(ids, points, KMeansParams(k=3, seed=2))
    # Fake a changed partition: the check must see it, not assume it cannot happen.
    object.__setattr__(
        changed, "assignments", tuple((c + 1) % 3 for c in changed.assignments)
    )
    sequence = iter([first, changed])
    monkeypatch.setattr(study, "_fit_registered", lambda registered: next(sequence))
    result = study.reproduce(repeats=2)
    assert "fit 2: assignments" in result["differences"]


def test_window_zero_is_the_production_normalization():
    f = Features(SMALL)
    ids, points = _small_points()
    assert ids == f.ids
    assert points == n_minmax(f)


def test_the_same_fit_has_not_moved():
    ids, points = _small_points()
    a = fit(ids, points, KMeansParams(k=3, seed=11))
    same = study.compare_fits(a, a)
    assert same["ari"] == 1.0
    assert same["rank_changed"] == 0.0
    assert same["centroid_max"] == 0.0
    assert same["common"] == len(ids)


def test_the_seed_study_reports_a_measure_in_range():
    ids, points = _small_points()
    result = study.seed_study(ids, points, 3)
    assert result["pairs"] == len(study.SEEDS) * (len(study.SEEDS) - 1) // 2
    assert -1.0 <= result["lowest"] <= result["mean"] <= 1.0


def test_the_bootstrap_repeats_exactly_and_covers_every_cluster():
    ids, points = _small_points()
    first = study.bootstrap_study(ids, points, 3, resamples=3)
    again = study.bootstrap_study(ids, points, 3, resamples=3)
    assert first == again
    assert len(first["jaccard"]) == 3
    assert sum(first["reference_sizes"]) == len(ids)
    assert all(0.0 <= j <= 1.0 for j in first["jaccard"])


def test_the_window_study_compares_adjacent_windows_and_the_seed_baseline(monkeypatch):
    monkeypatch.setattr(study, "SHIFTS", (0, 30, 60))
    monkeypatch.setattr(study, "NOISE_SEEDS", (12,))
    result = study.window_study(SMALL, 3)
    assert result["windows"] == 3
    assert len(result["adjacent"]) == 2
    assert 0.0 <= result["noise"]["rank_changed"] <= 1.0
    assert result["adjacent"][0]["common"] <= len(SMALL.customers)


@pytest.mark.parametrize(
    ("seed_mean", "boot_mean", "weakest", "expected"),
    [
        (0.95, 0.90, 0.90, "stable"),
        (0.95, 0.90, 0.70, "moderate"),  # a weak cluster is not stable
        (0.70, 0.90, 0.90, "moderate"),
        (0.55, 0.90, 0.90, "poor"),
        (0.95, 0.55, 0.90, "poor"),
        (0.95, 0.90, 0.05, "poor"),  # a cluster of one is not found again
    ],
)
def test_the_verdict_follows_the_stated_thresholds(
    seed_mean, boot_mean, weakest, expected
):
    seed = {"mean": seed_mean}
    boot = {"mean": boot_mean, "jaccard_lowest": weakest}
    assert study.verdict(seed, boot) == expected
