"""The experiment dashboard's figures (AN-03, ADR-0019, ADR-0028).

The database is mocked. What is proved is the arithmetic the charts are drawn
from, that it is the arithmetic the uplift page already uses, what is refused
and in whose words, and that an experiment with no control is compared against
a named reference arm and never against "everyone else".
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock

import pytest

from tests.test_experiment_assignment import _experiment
from web.db import experiment_conversions as conversions_db
from web.db import experiment_dashboard as dashboard_db
from web.db import experiments as experiments_db
from web.db.experiment_conversions import ExposedConversion, GroupConversion
from web.db.experiment_dashboard import ConversionDay
from web.services import experiment_dashboard as service
from web.services import experiment_uplift as uplift
from web.services.experiments import ExperimentNotFound

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _group(group_id, kind, assigned, converted, **overrides) -> GroupConversion:
    fields = dict(
        group_id=group_id,
        kind=kind,
        assigned=assigned,
        converted=converted,
        pending=0,
        not_converted=assigned - converted,
        unrecorded=0,
        name=f"Arm {group_id}",
        treatment_description="",
    )
    return GroupConversion(**{**fields, **overrides})


CONTROLLED = [
    _group(61, "CONTROL", 1000, 100, name="Holdout"),
    _group(62, "TREATMENT", 1000, 150, name="Offer"),
]
CONTROLLED_EXPOSED = [
    ExposedConversion(61, "CONTROL", 0, 0),
    ExposedConversion(62, "TREATMENT", 800, 140),
]
NO_CONTROL = [
    _group(72, "TREATMENT", 500, 50, name="Second offer"),
    _group(71, "TREATMENT", 500, 75, name="First offer"),
]
NO_CONTROL_EXPOSED = [
    ExposedConversion(71, "TREATMENT", 400, 70),
    ExposedConversion(72, "TREATMENT", 450, 45),
]


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    experiment,
    groups,
    exposed,
    days=(),
):
    monkeypatch.setattr(experiments_db, "get_experiment", Mock(return_value=experiment))
    monkeypatch.setattr(
        conversions_db, "list_group_conversion", Mock(return_value=list(groups))
    )
    monkeypatch.setattr(
        conversions_db, "list_exposed_conversion", Mock(return_value=list(exposed))
    )
    monkeypatch.setattr(
        dashboard_db, "list_conversion_days", Mock(return_value=list(days))
    )


def _build(monkeypatch, experiment=None, groups=CONTROLLED, exposed=None, days=()):
    _wire(
        monkeypatch,
        experiment or _experiment(assignments=2000),
        groups,
        CONTROLLED_EXPOSED if exposed is None else exposed,
        days,
    )
    return service.build_dashboard(MagicMock(), 31, now=NOW)


# ---------- the rate per arm, twice ----------


def test_each_arm_has_an_intent_to_treat_rate_and_a_rate_among_the_exposed(
    monkeypatch,
) -> None:
    dashboard = _build(monkeypatch)

    control, treatment = dashboard.arms
    assert (control.rate_assigned, treatment.rate_assigned) == (0.1, 0.15)
    # 140 of the 800 exposed bought after exposure: not 150 of 1000.
    assert treatment.rate_exposed == pytest.approx(0.175)
    # The control is never exposed, so it has no exposed rate, not a zero.
    assert control.rate_exposed is None


def test_an_arm_with_nobody_assigned_has_no_rate_not_a_zero_rate(monkeypatch) -> None:
    dashboard = _build(
        monkeypatch,
        groups=[_group(61, "CONTROL", 0, 0), _group(62, "TREATMENT", 10, 1)],
    )

    assert dashboard.arms[0].rate_assigned is None


# ---------- the uplift, with the interval ----------


def test_uplift_against_the_control_is_the_uplift_pages_own_figure(
    monkeypatch,
) -> None:
    dashboard = _build(monkeypatch)

    expected = uplift.compare_proportions(1000, 100, 1000, 150)
    (difference,) = dashboard.differences
    assert difference.comparison == expected
    assert dashboard.baseline == service.Baseline("control", 61, "Holdout")
    assert expected.ci_low < expected.uplift < expected.ci_high
    assert dashboard.refusal is None


def test_each_treatment_arm_is_set_against_the_control(monkeypatch) -> None:
    groups = [*CONTROLLED, _group(63, "TREATMENT", 1000, 120, name="Other offer")]
    exposed = [*CONTROLLED_EXPOSED, ExposedConversion(63, "TREATMENT", 500, 60)]

    dashboard = _build(
        monkeypatch,
        _experiment(assignments=3000, treatment_groups=2),
        groups,
        exposed,
    )

    assert [d.group_id for d in dashboard.differences] == [62, 63]
    assert dashboard.differences[1].comparison.uplift == pytest.approx(0.02)


# ---------- no control: a reference arm, never "everyone else" (ADR-0028) ----------


def test_without_a_control_the_arm_defined_first_is_the_reference(
    monkeypatch,
) -> None:
    dashboard = _build(
        monkeypatch,
        _experiment(assignments=1000, control_groups=0, treatment_groups=2),
        NO_CONTROL,
        NO_CONTROL_EXPOSED,
    )

    # Group 71 was defined before 72, whatever order the rows came back in.
    assert dashboard.baseline == service.Baseline("reference", 71, "First offer")
    (difference,) = dashboard.differences
    assert difference.group_id == 72
    assert difference.comparison == uplift.compare_proportions(500, 75, 500, 50)
    assert difference.comparison.uplift == pytest.approx(-0.05)
    assert dashboard.refusal is None


def test_the_uplift_service_still_refuses_an_experiment_without_a_control() -> None:
    """The dashboard adds a labelled arm-against-arm view; it does not change
    ADR-0028's refusal to call anything an uplift without a control."""
    reason = uplift.refusal_before_counts(31, 0, "CONVERSION", 1000)

    assert reason is not None and "no control group" in reason


def test_without_a_control_an_empty_arm_is_refused(monkeypatch) -> None:
    dashboard = _build(
        monkeypatch,
        _experiment(assignments=500, control_groups=0, treatment_groups=2),
        [_group(71, "TREATMENT", 500, 75), _group(72, "TREATMENT", 0, 0)],
        NO_CONTROL_EXPOSED,
    )

    assert dashboard.baseline is None and dashboard.differences == ()
    assert "two treatment arms" in dashboard.refusal


# ---------- refusals ----------


def test_a_qualifying_sale_not_yet_recorded_withholds_the_difference_not_the_rates(
    monkeypatch,
) -> None:
    groups = [CONTROLLED[0], _group(62, "TREATMENT", 1000, 150, unrecorded=3)]

    dashboard = _build(monkeypatch, groups=groups)

    assert dashboard.baseline is None
    assert "not been recorded" in dashboard.refusal
    assert dashboard.unrecorded == 3
    assert [a.rate_assigned for a in dashboard.arms] == [0.1, 0.15]


def test_without_a_control_an_unrecorded_sale_is_refused_in_the_same_way(
    monkeypatch,
) -> None:
    groups = [NO_CONTROL[1], _group(72, "TREATMENT", 500, 50, unrecorded=1)]

    dashboard = _build(
        monkeypatch,
        _experiment(assignments=1000, control_groups=0, treatment_groups=2),
        groups,
        NO_CONTROL_EXPOSED,
    )

    assert "not been recorded" in dashboard.refusal


def test_a_metric_that_is_not_conversion_is_refused_with_the_uplift_pages_words(
    monkeypatch,
) -> None:
    dashboard = _build(
        monkeypatch, _experiment(assignments=2000, target_metric="AVERAGE_TICKET")
    )

    assert "AVERAGE_TICKET" in dashboard.refusal
    assert dashboard.baseline is None


def test_an_experiment_with_no_assignments_charts_nothing(monkeypatch) -> None:
    dashboard = _build(
        monkeypatch,
        _experiment(assignments=0),
        [_group(61, "CONTROL", 0, 0), _group(62, "TREATMENT", 0, 0)],
    )

    assert dashboard.over_time is None
    assert dashboard.baseline is None
    assert "no assignments" in dashboard.refusal


def test_an_unknown_experiment_is_not_found(monkeypatch) -> None:
    monkeypatch.setattr(experiments_db, "get_experiment", Mock(return_value=None))

    with pytest.raises(ExperimentNotFound):
        service.build_dashboard(MagicMock(), 999, now=NOW)


# ---------- provenance ----------


@pytest.mark.parametrize(
    "origin,label",
    [("OBSERVED", None), ("SEEDED", "Synthetic"), ("INJECTED", "Synthetic")],
)
def test_the_label_follows_the_experiments_data_origin(
    monkeypatch, origin: str, label: str | None
) -> None:
    dashboard = _build(monkeypatch, _experiment(assignments=2000, data_origin=origin))

    assert dashboard.label == label


# ---------- conversions over time ----------


def test_the_share_converted_only_rises_and_ends_at_the_intent_to_treat_rate() -> None:
    days = [
        ConversionDay(62, 0, 20),
        ConversionDay(62, 3, 50),
        ConversionDay(62, 13, 80),
    ]

    shares = service.cumulative_share(days, 62, 1000, 14)

    assert len(shares) == 14
    assert shares[0] == 0.02 and shares[2] == 0.02 and shares[3] == 0.07
    assert list(shares) == sorted(shares)
    assert shares[-1] == 0.15


def test_another_arms_days_are_not_counted() -> None:
    days = [ConversionDay(61, 0, 99), ConversionDay(62, 0, 1)]

    assert service.cumulative_share(days, 62, 10, 3) == (0.1, 0.1, 0.1)


def test_an_empty_arm_stays_at_zero_for_every_day() -> None:
    assert service.cumulative_share([], 62, 0, 4) == (0.0, 0.0, 0.0, 0.0)


def test_the_over_time_series_has_one_line_per_arm_over_the_whole_window(
    monkeypatch,
) -> None:
    days = [ConversionDay(61, 2, 100), ConversionDay(62, 5, 150)]

    dashboard = _build(monkeypatch, days=days)

    assert dashboard.over_time.window_days == 14
    assert [arm.group_id for arm, _ in dashboard.over_time.series] == [61, 62]
    finals = [shares[-1] for _, shares in dashboard.over_time.series]
    assert finals == [0.1, 0.15]
    assert not dashboard.over_time.truncated


def test_a_very_long_window_is_drawn_up_to_a_bound_and_says_so(monkeypatch) -> None:
    dashboard = _build(
        monkeypatch, _experiment(assignments=2000, conversion_window_days=30_000)
    )

    assert dashboard.over_time.drawn_days == service.MAX_CHART_DAYS
    assert dashboard.over_time.truncated


# ---------- the SQL ----------


def test_the_conversion_days_statement_is_parameterized_and_stays_in_the_window() -> (
    None
):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [(62, 3, 5)]

    result = dashboard_db.list_conversion_days(connection, 31)

    assert result == [ConversionDay(62, 3, 5)]
    statement, params = cursor.execute.call_args.args
    assert params["experiment"] == 31
    assert "31" not in statement
    assert "make_interval(days => e.conversion_window_days)" in statement
    assert "min(t.occurred_at)" in statement
