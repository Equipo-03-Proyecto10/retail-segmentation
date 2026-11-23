"""The F23-10 study: planted drift, detection and false alarms (#822)."""

import os
from pathlib import Path

import pytest

from analysis import drift_detection_study as study
from analysis.dataset import generate

ROOT = Path(__file__).resolve().parents[1]
SMALL = generate(customers=600)
WINDOW = generate(study.window_seed(0), 600)
RULES = study.freeze(SMALL)
REFERENCE = study.reference_of(study.score(RULES, SMALL.sales))


def test_the_monitor_and_the_scoring_are_the_production_modules():
    assert Path(study.monitor.__file__) == (
        ROOT / "services" / "model-registry" / "app" / "monitor.py"
    )
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


def test_the_study_calls_run_once_and_reads_the_persisted_monitor_tables():
    window_source = Path(study.__file__).read_text()
    assert "monitor.run_once(connection, end)" in window_source
    for table in (
        "models.drift_report",
        "models.drift_variable",
        "models.drift_statistic",
        "models.drift_bin",
        "models.drift_alert",
    ):
        assert table in window_source
    # The earlier defect was a local `judge()` that copied monitor.run_once's
    # orchestration. The study may contain scoring helpers, but not that copy.
    assert "def judge(" not in window_source


def test_the_database_harness_refuses_a_nonlocal_database():
    with pytest.raises(ValueError, match="only against a local database"):
        study.MonitorStudy("postgresql://example.com/retail", REFERENCE)


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
    same = study.score(RULES, WINDOW.sales)
    shifted = study.score(RULES, planted)
    assert [profile.values() for profile in shifted] == [
        profile.values() for profile in same
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
    # The study writes immutable drift reports and audit events, so it runs only on
    # a database named for it, never on the TEST_DATABASE_URL the other suites share:
    # 32 reports left there fail model-registry's own monitor tests afterwards.
    database_url = os.environ.get("DRIFT_STUDY_DATABASE_URL")
    if database_url is None:
        # Without one, determinism of all random inputs is still checked; the
        # database-backed branch below runs where a throwaway database is given.
        first = study._experiment_start(REFERENCE, 4, 1, 600)
        second = study._experiment_start(REFERENCE, 4, 1, 600)
        assert first == second
        return
    first = study.figures(
        study.run(
            windows=4,
            detection_pairs=1,
            customers=600,
            database_url=database_url,
        )
    )
    second = study.figures(
        study.run(
            windows=4,
            detection_pairs=1,
            customers=600,
            database_url=database_url,
        )
    )
    assert first == second
    assert study.digest(first) == study.digest(second)
    assert study.digest(first) == (
        "12fb6701588c7aeac51377c18d614d0ba50791714c4610870a9d2473c53d9160"
    )


def test_the_report_states_every_criterion():
    class EmptyMonitor:
        def __init__(self, _database_url, _reference):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_error):
            pass

        def window(self, profiles, _end):
            assert len(profiles) >= study.drift.MIN_CUSTOMERS
            variables = tuple(
                study.drift.judge(
                    name,
                    (
                        study.drift.segment_bins(
                            REFERENCE["labels"],
                            [profile.label_code for profile in profiles],
                        )
                        if name == study.drift.SEGMENT_SHARES
                        else study.drift.feature_bins(
                            next(
                                feature
                                for feature in REFERENCE["features"]
                                if feature["feature"] == name
                            ),
                            [
                                getattr(
                                    profile,
                                    {"R": "r_days", "F": "f_count", "M": "m_total"}[
                                        name
                                    ],
                                )
                                for profile in profiles
                            ],
                        )
                    ),
                    len(profiles),
                    None,
                )
                for name in (study.drift.SEGMENT_SHARES, "R", "F", "M")
            )
            return study.Observation(variables, frozenset())

    result = study.run(
        windows=2,
        detection_pairs=1,
        customers=600,
        database_url="postgresql://unused/unused",
        monitor_type=EmptyMonitor,
    )
    text = study.report(result)
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
