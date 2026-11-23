"""The F23-10 study: planted drift, detection and false alarms (#822)."""

from pathlib import Path

import pytest

from analysis import drift_detection_study as study
from analysis.dataset import generate

ROOT = Path(__file__).resolve().parents[1]
SMALL = generate(customers=600)
WINDOW = generate(study.window_seed(0), 600)
RULES = study.freeze(SMALL)
REFERENCE = study.reference_of(study.score(RULES, SMALL.sales))


def _states(variables):
    return {v.variable: v.state for v in variables}


def test_the_monitor_and_the_scoring_are_the_production_modules():
    assert Path(study.drift.__file__) == (
        ROOT / "services" / "model-registry" / "app" / "drift.py"
    )
    assert Path(study.profile.__file__) == (
        ROOT / "services" / "rfm-engine" / "app" / "profile.py"
    )
    assert Path(study.training.__file__) == (
        ROOT / "services" / "model-registry" / "app" / "training.py"
    )


def test_the_reference_is_in_the_shape_the_monitor_reads():
    assert [f["feature"] for f in REFERENCE["features"]] == ["R", "F", "M"]
    assert sum(item["share"] for item in REFERENCE["labels"]) == pytest.approx(1)
    for feature in REFERENCE["features"]:
        assert len(feature["bin_edges"]) == len(feature["proportions"]) + 1
        assert sum(feature["proportions"]) == pytest.approx(1)


def test_the_reference_population_judged_against_itself_is_normal():
    variables = study.judge(REFERENCE, study.score(RULES, SMALL.sales), {})
    assert [v.variable for v in variables] == ["SEGMENT_SHARES", "R", "F", "M"]
    assert set(_states(variables).values()) == {"NORMAL"}


def test_fewer_customers_than_the_minimum_are_not_compared():
    few = study.score(RULES, SMALL.sales)[: study.drift.MIN_CUSTOMERS - 1]
    assert study.judge(REFERENCE, few, {}) == []


def test_an_alarm_alerts_only_in_the_second_window_as_the_monitor_does():
    profiles = study.score(RULES, study.plant(WINDOW, "FREQUENCY", 0.6, 0))
    first = study.judge(REFERENCE, profiles, {})
    assert "F" in study.alarming(first)
    assert study.alerting(first) == []
    second = study.judge(REFERENCE, profiles, _states(first))
    assert "F" in study.alerting(second)
    assert study._explains("FREQUENCY", second)


def test_spend_multiplies_every_amount_and_moves_nothing_else():
    planted = study.plant(WINDOW, "SPEND", 0.5, 0)
    assert len(planted) == len(WINDOW.sales)
    for before, after in zip(WINDOW.sales, planted, strict=True):
        assert after.amount == (before.amount * study.Decimal("1.5")).quantize(
            study._CENT
        )
        assert (after.seconds_ago, after.category) == (
            before.seconds_ago,
            before.category,
        )
    assert study.planted_effect("SPEND", WINDOW.sales, planted) == pytest.approx(
        0.5, abs=1e-4
    )


def test_frequency_keeps_each_customers_newest_sale_so_recency_holds():
    planted = study.plant(WINDOW, "FREQUENCY", 0.5, 0)
    old = study._measures(WINDOW.sales)
    new = study._measures(planted)
    assert new.keys() <= old.keys()
    for who, measured in new.items():
        assert measured.last_at == old[who].last_at
        assert measured.count <= old[who].count
    assert study.planted_effect("FREQUENCY", WINDOW.sales, planted) < -0.3


@pytest.mark.parametrize("kind", ["CATEGORY", "CHANNEL"])
def test_a_category_or_channel_shift_leaves_what_the_monitor_reads_unchanged(kind):
    planted = study.plant(WINDOW, kind, 0.5, 0)
    assert study.planted_effect(kind, WINDOW.sales, planted) > 0.3
    same = study.judge(REFERENCE, study.score(RULES, WINDOW.sales), {})
    shifted = study.judge(REFERENCE, study.score(RULES, planted), {})
    assert [(v.variable, v.statistics) for v in shifted] == [
        (v.variable, v.statistics) for v in same
    ]


def test_the_planting_is_a_function_of_its_seed():
    assert study.plant(WINDOW, "CHANNEL", 0.25, 3) == study.plant(
        WINDOW, "CHANNEL", 0.25, 3
    )
    assert study.plant(WINDOW, "CHANNEL", 0.25, 3) != study.plant(
        WINDOW, "CHANNEL", 0.25, 4
    )


def test_a_window_is_never_the_reference_draw():
    seeds = {study.window_seed(i) for i in range(study.WINDOWS)}
    assert len(seeds) == study.WINDOWS
    assert study.DEFAULT_SEED not in seeds


def test_the_upper_bound_is_clopper_pearson():
    # No success in n trials: the bound solves (1 - p)^n = 0.05.
    assert study.upper_bound(0, 300) == pytest.approx(1 - 0.05 ** (1 / 300), 1e-9)
    assert study.upper_bound(5, 5) == 1.0
    assert study.upper_bound(3, 100) > 0.03
    assert study.FALSE_ALARM_LIMIT == 0.01


def test_with_a_fixed_seed_the_figures_repeat_and_are_pinned():
    first = study.figures(study.run(windows=4, detection_pairs=1, customers=600))
    second = study.figures(study.run(windows=4, detection_pairs=1, customers=600))
    assert first == second
    assert study.digest(first) == study.digest(second)
    assert study.digest(first) == (
        "12fb6701588c7aeac51377c18d614d0ba50791714c4610870a9d2473c53d9160"
    )


def test_the_report_states_every_criterion():
    text = study.report(study.run(windows=4, detection_pairs=1, customers=600))
    for heading in (
        "## False alarms: windows with no shift",
        "## Detection: windows with a planted shift",
        "## What an alert says",
        "## Repeatability",
    ):
        assert heading in text
    for kind in study.SHIFTS:
        assert f"| {kind} |" in text
    assert "concept drift" not in text.lower()
