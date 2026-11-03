"""The F23-02 study's helpers and a small end-to-end run (#814)."""

import math

import pytest

from analysis import normalization_study as study
from analysis.dataset import generate


def test_quantile_interpolates():
    assert study.quantile([1, 2, 3, 4], 0.5) == 2.5
    assert study.quantile([5], 0.99) == 5


def test_adjusted_rand_ignores_label_names_and_penalises_disagreement():
    assert study.adjusted_rand([0, 0, 1, 1], ["a", "a", "b", "b"]) == 1.0
    assert study.adjusted_rand([0, 0, 1, 1], [0, 1, 0, 1]) < 0.1


def test_spearman_is_one_for_a_monotone_relation():
    values = [1.0, 2.0, 3.0, 10.0]
    assert math.isclose(study.spearman(values, [v**3 for v in values]), 1.0)


def test_eta_squared_is_one_when_groups_explain_everything():
    assert study.eta_squared([1, 1, 5, 5], ["a", "a", "b", "b"]) == 1.0


def test_decision_rule():
    assert study.decide(True, 0.0, [0, 0, 0]).startswith("kept")
    assert "redundant" in study.decide(False, 0.9, [0.1, 0.85, 0.1])
    assert "does not separate" in study.decide(False, 0.05, [0.1, 0.1, 0.1])
    assert study.decide(False, 0.4, [0.1, 0.5, 0.1]).startswith("candidate")


@pytest.fixture(scope="module")
def small():
    return study.Features(generate(customers=600))


def test_every_treatment_builds_one_point_per_customer(small):
    for build in {**study.NORMALIZATIONS, **study.EXTREME_TREATMENTS}.values():
        assert len(build(small)) == len(small.ids)


def test_winsorizing_caps_the_extreme_spender(small):
    plain = study.n_minmax(small)
    capped = study.winsorized(0.99)(small)
    assert max(p[2] for p in plain) == 1.0
    assert statistics_median([p[2] for p in capped]) > statistics_median(
        [p[2] for p in plain]
    )


def statistics_median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def test_describe_covers_every_customer(small):
    result = study.describe(small, study.n_log)
    assert sum(result["sizes"]) == len(small.ids)
    assert result["fits"] == len(study.STABILITY_SEEDS) + 1
