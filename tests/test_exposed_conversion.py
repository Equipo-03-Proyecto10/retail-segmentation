"""#343: the per-exposure conversion rate beside intent to treat, and an uplift
page that asks for an evaluation instead of answering 409.

The database is mocked. ADR-0019 keeps intent to treat as the primary measure,
so what is checked is that the second rate is defined as the issue defines it,
sits beside the first and never replaces it.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from itertools import chain, repeat
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from tests.test_experiment_assignment import _experiment
from web.app import create_app
from web.config import Config
from web.db import experiment_conversions as conversions_db
from web.db import experiment_report as report_db
from web.db import experiments as experiments_db
from web.db.experiment_conversions import ExposedConversion, GroupConversion
from web.db.experiment_report import ReportGroup
from web.services import experiment_report as report_service
from web.services import experiment_uplift as uplift

USER_ID = "11111111-1111-1111-1111-000000000001"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


# ---------- the definition ----------


def test_the_rate_is_bought_after_exposure_over_exposed() -> None:
    assert ExposedConversion(62, "TREATMENT", 200, 50).rate == 0.25


def test_nobody_exposed_is_no_rate_not_a_division_by_zero() -> None:
    assert ExposedConversion(61, "CONTROL", 0, 0).rate is None


def test_a_report_group_has_both_rates_over_their_own_denominators() -> None:
    group = ReportGroup(31, 62, "TREATMENT", 1000, 400, 150, 20, exposed_converted=100)

    assert group.conversion_rate == 0.15  # 150 of the 1000 assigned
    assert group.exposed_conversion_rate == 0.25  # 100 of the 400 exposed


def test_a_group_nobody_was_assigned_to_has_neither_rate() -> None:
    group = ReportGroup(31, 61, "CONTROL", 0, 0, 0, 0)

    assert group.conversion_rate is None and group.exposed_conversion_rate is None


def test_the_read_counts_from_the_first_exposure_inside_the_window() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (61, "CONTROL", 0, 0),
        (62, "TREATMENT", 200, 50),
    ]

    rows = conversions_db.list_exposed_conversion(connection, 31)

    statement, parameters = _cursor(connection).execute.call_args.args
    assert parameters == (31, 31)
    assert (
        "min(x.exposed_at)" in statement
    ), "one customer is one, however often reached"
    assert "t.occurred_at >= fx.first_at" in statement
    assert "make_interval(days => e.conversion_window_days)" in statement
    assert "experiment_conversion" not in statement, "no evaluation needed"
    assert [r.rate for r in rows] == [None, 0.25]


def test_the_report_shares_the_same_definition_of_the_rate() -> None:
    assert conversions_db.EXPOSED_CONVERTED_SQL in report_db._LIST_GROUPS


# ---------- the measurement ----------


def _wire(monkeypatch, groups=None, exposed=None, experiment=None) -> None:
    monkeypatch.setattr(
        experiments_db,
        "get_experiment",
        Mock(return_value=experiment or _experiment(assignments=300)),
    )
    monkeypatch.setattr(
        "web.routes.experiments.get_experiment",
        Mock(return_value=experiment or _experiment(assignments=300)),
    )
    monkeypatch.setattr(
        conversions_db,
        "list_group_conversion",
        Mock(
            return_value=groups
            or [
                GroupConversion(
                    61,
                    "CONTROL",
                    100,
                    10,
                    5,
                    85,
                    0,
                    "Holdout",
                    "No treatment delivered.",
                ),
                GroupConversion(
                    62,
                    "TREATMENT",
                    100,
                    25,
                    0,
                    75,
                    0,
                    "Free shipping",
                    "Free standard shipping.",
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        conversions_db,
        "list_exposed_conversion",
        Mock(
            return_value=(
                exposed
                if exposed is not None
                else [
                    ExposedConversion(61, "CONTROL", 0, 0),
                    ExposedConversion(62, "TREATMENT", 80, 30),
                ]
            )
        ),
    )


def test_the_measurement_carries_the_exposed_rate_for_treatment_arms_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch)

    result = uplift.measure_uplift(MagicMock(), 31, NOW)

    assert [(e.group_id, e.rate) for e in result.exposed] == [(62, 0.375)]


def test_intent_to_treat_is_unchanged_by_the_second_measure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch)

    arm = uplift.measure_uplift(MagicMock(), 31, NOW).arms[0].comparison

    assert (arm.treatment_n, arm.treatment_converted) == (100, 25)
    assert arm.uplift == pytest.approx(0.15)


def test_unrecorded_sales_are_a_conversion_not_evaluated_and_still_a_refusal() -> None:
    assert issubclass(uplift.ConversionNotEvaluated, uplift.UpliftRefused)


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


def test_the_page_shows_both_rates_and_states_the_definition(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch)

    response = _get(app)

    body = " ".join(response.get_data(as_text=True).split())
    assert response.status_code == 200
    assert "Conversion among exposed customers" in body
    assert "37.50%" in body, "30 of 80 exposed"
    assert "25.00%" in body, "the intent-to-treat rate, still shown"
    assert "bought after their first exposure" in body
    assert "over customers exposed" in body
    assert "+15.00" in body, "uplift is still intent to treat"
    assert "Free shipping" in body and "Free standard shipping." in body


def test_before_conversion_is_evaluated_the_page_is_a_200_that_says_what_to_do(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(
        monkeypatch,
        groups=[
            GroupConversion(61, "CONTROL", 100, 10, 0, 90, 1),
            GroupConversion(62, "TREATMENT", 100, 20, 0, 80, 2),
        ],
    )

    response = _get(app)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Conversion not evaluated yet" in body
    assert "3 assigned customers" in body
    assert "/experiments/31/conversion" in body, "the call to evaluate"


def test_a_reader_who_cannot_record_conversions_is_told_who_can(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(
        monkeypatch,
        groups=[
            GroupConversion(61, "CONTROL", 100, 10, 0, 90, 1),
            GroupConversion(62, "TREATMENT", 100, 20, 0, 80, 0),
        ],
    )

    body = _get(app, "ANALYST").get_data(as_text=True)

    assert "ask a marketing user" in body


def test_every_other_refusal_is_still_a_409(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, experiment=_experiment(control_groups=0, assignments=10))
    monkeypatch.setattr(
        "web.routes.experiments.get_experiment",
        Mock(return_value=_experiment(control_groups=0, assignments=10)),
    )

    response = _get(app)

    assert response.status_code == 409


# ---------- the report and its export ----------


def _report_groups() -> list[ReportGroup]:
    return [
        ReportGroup(
            31,
            61,
            "CONTROL",
            1000,
            0,
            100,
            40,
            name="Holdout",
            treatment_description="No treatment delivered.",
        ),
        ReportGroup(
            31,
            62,
            "TREATMENT",
            1000,
            400,
            150,
            20,
            name="Free shipping",
            treatment_description="Free standard shipping.",
            exposed_converted=100,
        ),
    ]


def _wire_report(monkeypatch) -> None:
    monkeypatch.setattr(
        report_db,
        "list_report_experiments",
        Mock(return_value=([_experiment(assignments=2000)], 1)),
    )
    monkeypatch.setattr(
        report_db, "list_report_groups", Mock(return_value=_report_groups())
    )


def test_the_export_carries_both_rates_and_leaves_the_control_exposure_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_report(monkeypatch)

    text = report_service.export_csv(
        MagicMock(), campaign_id=None, data_origin=None, now=NOW
    )
    control, treatment = list(csv.DictReader(io.StringIO(text)))

    assert (treatment["rate_assigned_percent"], treatment["rate_exposed_percent"]) == (
        "15.00",
        "25.00",
    )
    assert treatment["converted_after_exposure"] == "100"
    assert control["rate_assigned_percent"] == "10.00"
    assert control["rate_exposed_percent"] == "" == control["converted_after_exposure"]
    assert treatment["data_origin"] == "OBSERVED", "provenance is kept"
    assert (treatment["arm_name"], treatment["treatment_description"]) == (
        "Free shipping",
        "Free standard shipping.",
    )


def test_the_report_page_shows_both_rates(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_report(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(user_id=USER_ID, role_code="MARKETING", name="T")

    body = " ".join(client.get("/experiment-report/").get_data(as_text=True).split())

    assert "Rate (assigned)" in body and "Rate (exposed)" in body
    assert "15.00%" in body and "25.00%" in body
    assert "intent-to-treat rate" in body
    assert "Free shipping" in body and "Free standard shipping." in body


def test_the_report_query_keeps_arm_names_beside_the_exposure_rate() -> None:
    assert "g.name, g.treatment_description" in report_db._LIST_GROUPS
    assert (
        "g.name, g.treatment_description"
        in report_db._LIST_GROUPS.split("GROUP BY", 1)[1]
    )
