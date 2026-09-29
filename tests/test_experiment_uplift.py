"""Intent-to-treat uplift and its two validations (F11-07, ADR-0019).

The database is mocked. The validations run on the record's own inputs; the A/A
check over the real `transaction` rows needs PostgreSQL and was not run here.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from itertools import chain, repeat
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from tests.test_experiment_assignment import _experiment
from web.app import create_app
from web.config import Config
from web.db import experiment_conversions as db
from web.db import experiments
from web.db.experiment_conversions import GroupConversion
from web.services import experiment_uplift as uplift
from web.services.experiments import ExperimentNotFound

USER_ID = "11111111-1111-1111-1111-000000000001"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


# ---------- the arithmetic ----------


def test_the_difference_and_interval_are_the_textbook_ones() -> None:
    c = uplift.compare_proportions(1000, 100, 1000, 150)

    assert c.uplift == pytest.approx(0.05)
    assert c.control_rate == 0.1 and c.treatment_rate == 0.15
    assert c.ci_low == pytest.approx(0.05 - 1.96 * 0.0147479, abs=1e-5)
    assert c.p_value == pytest.approx(0.00072, abs=1e-5)
    assert c.significant and c.excludes_zero


def test_identical_arms_are_not_significant() -> None:
    c = uplift.compare_proportions(500, 50, 500, 50)

    assert c.uplift == 0 and c.z == 0 and c.p_value == pytest.approx(1)
    assert not c.significant and not c.excludes_zero


def test_nobody_converting_in_either_arm_is_no_difference_not_a_crash() -> None:
    c = uplift.compare_proportions(10, 0, 10, 0)

    assert c.p_value == 1 and (c.ci_low, c.ci_high) == (0, 0)


@pytest.mark.parametrize("args", [(0, 0, 5, 1), (5, 6, 5, 1), (5, 1, 5, -1)])
def test_impossible_counts_are_rejected(args: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError):
        uplift.compare_proportions(*args)


# ---------- validation: injected uplift ----------


def test_the_injected_fixture_recovers_five_points_within_a_tenth() -> None:
    c = uplift.injected_uplift_fixture()

    assert (c.control_n, c.treatment_n) == (10_000, 10_000)
    assert c.control_rate == pytest.approx(0.10)
    assert c.treatment_rate == pytest.approx(0.15)
    assert abs(c.uplift - 0.05) <= 0.001
    assert c.ci_low > 0  # the 95% interval excludes zero


def test_the_fixture_is_reproducible_from_its_seed() -> None:
    assert uplift.injected_uplift_fixture(7) == uplift.injected_uplift_fixture(7)


def test_the_measurement_finds_no_uplift_when_none_was_injected() -> None:
    c = uplift.injected_uplift_fixture(treatment_rate=0.10)

    assert c.uplift == 0 and not c.significant


# ---------- validation: A/A ----------


def _population(n: int, rate: float, seed: int) -> list[tuple[str, bool]]:
    rng = random.Random(seed)
    return [(f"c{index:06d}", rng.random() < rate) for index in range(n)]


def test_the_split_is_deterministic_whatever_order_customers_arrive_in() -> None:
    ids = [f"c{n}" for n in range(50)]

    assert uplift.split_in_two(ids, "s") == uplift.split_in_two(ids[::-1], "s")


def test_the_two_arms_partition_the_population_and_differ_by_at_most_one() -> None:
    ids = [f"c{n}" for n in range(51)]

    first, second = uplift.split_in_two(ids, "s")

    assert sorted(first + second) == sorted(ids)
    assert abs(len(first) - len(second)) <= 1


def test_an_a_a_split_of_one_population_shows_no_significant_difference() -> None:
    population = _population(4000, 0.12, seed=3)

    comparison = uplift.aa_validation(population)

    assert not comparison.significant
    assert comparison.control_n + comparison.treatment_n == 4000


def test_the_a_a_split_uses_only_the_customer_ids() -> None:
    """Changing who converted must not change who lands in which arm."""
    a = _population(200, 0.1, seed=1)
    b = [(customer, not converted) for customer, converted in a]

    assert uplift.split_in_two([c for c, _ in a], "x") == uplift.split_in_two(
        [c for c, _ in b], "x"
    )


def test_a_measurement_that_finds_a_difference_in_a_a_is_detectable() -> None:
    """The validation can fail: hand it a split that is not A/A."""
    c = uplift.compare_proportions(2000, 200, 2000, 300)

    assert c.significant


def test_the_a_a_population_read_is_bounded_by_the_cutoff_and_parameterized() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [("id-1", True), ("id-2", False)]

    rows = db.read_aa_population(connection, NOW, 30)

    sql = " ".join(cursor.execute.call_args.args[0].split())
    assert "WHERE occurred_at < %s) AS known" in sql
    assert "later.occurred_at >= %s" in sql
    assert cursor.execute.call_args.args[1] == (NOW, NOW, 30, NOW)
    assert rows == [("id-1", True), ("id-2", False)]


def test_a_a_over_the_database_needs_two_customers() -> None:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        ("only", True)
    ]

    with pytest.raises(uplift.UpliftRefused):
        uplift.run_aa_validation(connection, NOW, 30)


# ---------- measuring an experiment ----------


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    experiment=None,
    groups: list[GroupConversion] | None = None,
) -> None:
    monkeypatch.setattr(
        experiments,
        "get_experiment",
        Mock(return_value=experiment or _experiment(assignments=300)),
    )
    monkeypatch.setattr(
        db,
        "list_group_conversion",
        Mock(
            return_value=groups
            or [
                GroupConversion(61, "CONTROL", 100, 10, 5, 85, 0),
                GroupConversion(62, "TREATMENT", 100, 25, 0, 75, 0),
                GroupConversion(63, "TREATMENT", 100, 12, 3, 85, 0),
            ]
        ),
    )


def test_every_assigned_customer_is_compared_not_only_the_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch)

    result = uplift.measure_uplift(MagicMock(), 31, NOW)

    first = result.arms[0].comparison
    assert (first.control_n, first.treatment_n) == (100, 100)
    assert first.uplift == pytest.approx(0.15)
    assert [arm.group_id for arm in result.arms] == [62, 63]
    assert result.pending == 8  # open windows are reported, not hidden


def test_exposure_plays_no_part_in_the_measurement() -> None:
    import inspect

    assert "exposure" not in inspect.getsource(uplift.measure_uplift).lower()


@pytest.mark.parametrize(
    "experiment,message",
    [
        (_experiment(control_groups=0, assignments=10), "no control group"),
        (_experiment(assignments=0), "no assignments"),
        (_experiment(target_metric="AVERAGE_TICKET", assignments=5), "AVERAGE_TICKET"),
    ],
)
def test_uplift_is_refused_rather_than_computed_against_everyone_else(
    monkeypatch: pytest.MonkeyPatch, experiment, message: str
) -> None:
    _wire(monkeypatch, experiment=experiment)

    with pytest.raises(uplift.UpliftRefused, match=message):
        uplift.measure_uplift(MagicMock(), 31, NOW)
    db.list_group_conversion.assert_not_called()


def test_an_unknown_experiment_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(experiments, "get_experiment", Mock(return_value=None))

    with pytest.raises(ExperimentNotFound):
        uplift.measure_uplift(MagicMock(), 99, NOW)


def test_an_empty_control_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(
        monkeypatch,
        groups=[
            GroupConversion(61, "CONTROL", 0, 0, 0, 0, 0),
            GroupConversion(62, "TREATMENT", 10, 1, 0, 9, 0),
        ],
    )

    with pytest.raises(uplift.UpliftRefused, match="control group has no assigned"):
        uplift.measure_uplift(MagicMock(), 31, NOW)


def test_an_empty_treatment_arm_is_refused_instead_of_silently_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        experiment=_experiment(assignments=110),
        groups=[
            GroupConversion(61, "CONTROL", 100, 10, 0, 90, 0),
            GroupConversion(62, "TREATMENT", 10, 1, 0, 9, 0),
            GroupConversion(63, "TREATMENT", 0, 0, 0, 0, 0),
        ],
    )

    with pytest.raises(
        uplift.UpliftRefused, match="treatment group 63 has no assigned customers"
    ):
        uplift.measure_uplift(MagicMock(), 31, NOW)


def test_unrecorded_qualifying_sales_are_refused_before_uplift_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        groups=[
            GroupConversion(61, "CONTROL", 100, 10, 0, 90, 1),
            GroupConversion(62, "TREATMENT", 100, 20, 0, 80, 2),
        ],
    )

    with pytest.raises(
        uplift.UpliftRefused, match="3 assigned customers.*Evaluate conversion"
    ):
        uplift.measure_uplift(MagicMock(), 31, NOW)


@pytest.mark.parametrize(
    "origin,label",
    [("OBSERVED", None), ("SEEDED", "Synthetic"), ("INJECTED", "Synthetic")],
)
def test_seeded_and_injected_results_carry_the_synthetic_label(
    monkeypatch: pytest.MonkeyPatch, origin: str, label: str | None
) -> None:
    _wire(monkeypatch, experiment=_experiment(assignments=300, data_origin=origin))

    assert uplift.measure_uplift(MagicMock(), 31, NOW).label == label


# ---------- the page ----------


@pytest.fixture
def app() -> Flask:
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(side_effect=chain([Mock()], repeat(MagicMock()))),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _get(app: Flask, role: str = "MARKETING"):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(user_id=USER_ID, role_code=role, name="Test User")
    return client.get("/experiments/31/uplift")


def test_the_page_shows_the_uplift_interval_and_preliminary_note(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch)

    response = _get(app)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "+15.00" in body and "95% interval" in body
    assert "Preliminary" in body
    assert "Synthetic" not in body


def test_a_synthetic_experiment_renders_the_literal_label(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, experiment=_experiment(assignments=300, data_origin="INJECTED"))

    assert "Synthetic" in _get(app).get_data(as_text=True)


def test_no_control_is_a_409_with_the_reason(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, experiment=_experiment(control_groups=0, assignments=10))
    monkeypatch.setattr(
        "web.routes.experiments.get_experiment",
        Mock(return_value=_experiment(control_groups=0, assignments=10)),
    )

    response = _get(app)

    assert response.status_code == 409
    assert "never against everyone else" in response.get_data(as_text=True)
