"""The campaign and experiment report and its export (F12-04, ADR-0019).

The database is mocked; what is proved is the report's rules and the provenance
that survives into the page and the file.
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
from web.db import experiment_report as db
from web.db.experiment_report import ReportGroup
from web.services import experiment_report as service

USER_ID = "11111111-1111-1111-1111-000000000001"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _groups(experiment_id: int = 31) -> list[ReportGroup]:
    return [
        ReportGroup(experiment_id, 61, "CONTROL", 1000, 0, 100, 40),
        ReportGroup(experiment_id, 62, "TREATMENT", 1000, 700, 150, 20),
    ]


def _wire(
    monkeypatch: pytest.MonkeyPatch, experiments: list, groups: list | None = None
) -> dict[str, Mock]:
    mocks = {
        "list_report_experiments": Mock(return_value=(experiments, len(experiments))),
        "list_report_groups": Mock(
            return_value=_groups() if groups is None else groups
        ),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(db, name, mock)
    return mocks


def _report(monkeypatch, experiment=None, groups=None):
    _wire(monkeypatch, [experiment or _experiment(assignments=2000)], groups)
    return service.build_report(
        MagicMock(), campaign_id=None, data_origin=None, page=1, now=NOW
    )


# ---------- three counts, kept apart ----------


def test_assignment_exposure_and_conversion_are_three_separate_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (row,) = _report(monkeypatch).rows

    treatment = next(g for g in row.groups if g.kind == "TREATMENT")
    assert (treatment.assigned, treatment.exposed, treatment.converted) == (
        1000,
        700,
        150,
    )
    assert treatment.not_converted == 830  # never derived from exposure


def test_uplift_is_intent_to_treat_over_all_assigned_not_the_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (row,) = _report(monkeypatch).rows

    comparison = row.uplift_of(62)
    assert (comparison.control_n, comparison.treatment_n) == (1000, 1000)  # not 700
    assert comparison.uplift == pytest.approx(0.05)
    assert comparison.ci_low < comparison.uplift < comparison.ci_high
    assert row.pending == 60


def test_an_experiment_without_a_control_reports_the_refusal_not_a_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (row,) = _report(
        monkeypatch, _experiment(control_groups=0, assignments=5), _groups()
    ).rows

    assert row.arms == () and "no control group" in row.refusal


def test_unrecorded_qualifying_sales_refuse_uplift_in_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = [
        ReportGroup(31, 61, "CONTROL", 1000, 0, 100, 40, 1),
        ReportGroup(31, 62, "TREATMENT", 1000, 700, 150, 20, 0),
    ]

    (row,) = _report(monkeypatch, groups=groups).rows

    assert row.arms == ()
    assert "qualifying sale that has not been recorded yet" in row.refusal


# ---------- provenance ----------


@pytest.mark.parametrize(
    "origin,label",
    [("OBSERVED", None), ("SEEDED", "Synthetic"), ("INJECTED", "Synthetic")],
)
def test_the_label_follows_the_data_origin(
    monkeypatch: pytest.MonkeyPatch, origin: str, label: str | None
) -> None:
    (row,) = _report(
        monkeypatch, _experiment(assignments=2000, data_origin=origin)
    ).rows

    assert row.label == label


def _exported(monkeypatch, **experiment) -> list[dict[str, str]]:
    _wire(monkeypatch, [_experiment(assignments=2000, **experiment)])
    text = service.export_csv(MagicMock(), campaign_id=None, data_origin=None, now=NOW)
    return list(csv.DictReader(io.StringIO(text)))


def test_the_export_carries_origin_and_synthetic_label_on_every_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _exported(monkeypatch, data_origin="INJECTED")

    assert len(rows) == 2
    assert {r["data_origin"] for r in rows} == {"INJECTED"}
    assert {r["label"] for r in rows} == {"Synthetic"}


def test_an_observed_export_carries_the_origin_and_no_synthetic_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _exported(monkeypatch, data_origin="OBSERVED")

    assert {r["data_origin"] for r in rows} == {"OBSERVED"}
    assert {r["label"] for r in rows} == {""}


def test_the_export_repeats_the_three_counts_and_the_basis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control, treatment = _exported(monkeypatch)

    assert (control["assigned"], control["exposed"], control["converted"]) == (
        "1000",
        "0",
        "100",
    )
    assert treatment["exposed"] == "700"
    assert treatment["uplift_points"] == "5.00" and control["uplift_points"] == ""
    assert "Intent to treat over all assigned customers" in treatment["measurement"]
    assert "preliminary" in treatment["measurement"]


def test_a_cell_that_starts_like_a_formula_is_defused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _exported(monkeypatch, name='=HYPERLINK("http://x")')

    assert rows[0]["experiment"].startswith("'=")


# ---------- filters ----------


def test_both_filters_reach_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _wire(monkeypatch, [])

    service.build_report(
        MagicMock(), campaign_id=7, data_origin="SEEDED", page=3, now=NOW
    )

    kwargs = mocks["list_report_experiments"].call_args.kwargs
    assert kwargs["campaign_id"] == 7 and kwargs["data_origin"] == "SEEDED"
    assert kwargs["offset"] == 2 * service.PAGE_SIZE


def test_the_filter_sql_is_parameterized_and_the_count_shares_it() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)

    db.list_report_experiments(
        connection, campaign_id=7, data_origin="SEEDED", limit=10, offset=0
    )

    page_call, count_call = cursor.execute.call_args_list
    for call in (page_call, count_call):
        assert "e.campaign_id = %(campaign)s::int" in call.args[0]
        assert "e.data_origin = %(origin)s::text" in call.args[0]
        assert call.args[1]["campaign"] == 7 and call.args[1]["origin"] == "SEEDED"


@pytest.mark.parametrize(
    "campaign,origin", [("abc", ""), ("-1", ""), ("99999999999", ""), ("", "REAL")]
)
def test_a_filter_the_report_does_not_offer_is_refused(
    campaign: str, origin: str
) -> None:
    with pytest.raises(service.InvalidFilter):
        service.parse_filters(campaign, origin, {7})


def test_a_numeric_campaign_the_report_does_not_offer_is_refused() -> None:
    with pytest.raises(service.InvalidFilter, match="Choose a campaign"):
        service.parse_filters("999", "", {7, 8})


def test_an_offered_campaign_is_accepted() -> None:
    assert service.parse_filters("7", "OBSERVED", {7, 8}) == (7, "OBSERVED")


def test_no_experiment_ids_means_no_group_query() -> None:
    connection = MagicMock()

    assert db.list_report_groups(connection, [], NOW) == []
    connection.cursor.assert_not_called()


# ---------- the pages ----------


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


def _client(app: Flask, role: str = "MARKETING"):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(user_id=USER_ID, role_code=role, name="Test User")
    return client


def test_the_page_labels_each_count_and_the_basis_of_the_uplift(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, [_experiment(assignments=2000)])
    monkeypatch.setattr(
        "web.routes.experiment_report.list_campaign_choices", Mock(return_value=[])
    )

    response = _client(app).get("/experiment-report/")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Assigned</strong> counts customers placed in the arm" in body
    assert "Exposed</strong> counts assigned customers reached" in body
    assert "Converted</strong> counts assigned customers with a qualifying sale" in body
    assert "intent to treat over all assigned customers" in body.lower()
    assert "95% interval" in body and "+5.00" in body
    assert "Synthetic" not in body


def test_a_synthetic_experiment_shows_the_label_on_the_page(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, [_experiment(assignments=2000, data_origin="SEEDED")])
    monkeypatch.setattr(
        "web.routes.experiment_report.list_campaign_choices", Mock(return_value=[])
    )

    assert "Synthetic" in _client(app).get("/experiment-report/").get_data(as_text=True)


def test_a_bad_filter_is_a_400(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _wire(monkeypatch, [])
    monkeypatch.setattr(
        "web.routes.experiment_report.list_campaign_choices", Mock(return_value=[])
    )

    response = _client(app).get("/experiment-report/?origin=REAL")

    assert response.status_code == 400
    mocks["list_report_experiments"].assert_not_called()


def test_a_numeric_campaign_not_offered_by_the_report_is_a_400(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks = _wire(monkeypatch, [])
    monkeypatch.setattr(
        "web.routes.experiment_report.list_campaign_choices", Mock(return_value=[])
    )

    response = _client(app).get("/experiment-report/?campaign=999")

    assert response.status_code == 400
    mocks["list_report_experiments"].assert_not_called()


def test_the_export_is_a_csv_download_carrying_the_label(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, [_experiment(assignments=2000, data_origin="INJECTED")])

    response = _client(app).get("/experiment-report/export.csv")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "INJECTED" in response.get_data(as_text=True)
    assert "Synthetic" in response.get_data(as_text=True)


def test_the_export_refuses_a_bad_filter(app: Flask) -> None:
    assert (
        _client(app).get("/experiment-report/export.csv?campaign=x").status_code == 400
    )


def test_a_profile_without_campaign_read_is_refused(app: Flask) -> None:
    for path in ("/experiment-report/", "/experiment-report/export.csv"):
        assert _client(app, "STORE_MANAGER").get(path).status_code == 403


# ---------- #358: numbers stay numbers in the export ----------


def test_a_negative_number_is_not_quoted_as_text() -> None:
    assert service._safe("-50.00") == "-50.00"
    assert service._safe("-3") == "-3"
    assert service._safe("0.05") == "0.05"


@pytest.mark.parametrize(
    "text",
    [
        "=1+1",
        "+1",
        "@SUM(A1)",
        "-1+2",
        "-cmd|' /C calc'!A0",
        "\tx",
        "-",
        "--5",
        "-5.",
        "-.5",
    ],
)
def test_text_that_could_be_a_formula_is_still_defused(text: str) -> None:
    assert service._safe(text) == "'" + text


def test_a_negative_uplift_exports_as_a_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        [_experiment(assignments=2000)],
        [
            ReportGroup(31, 61, "CONTROL", 1000, 0, 200, 0),
            ReportGroup(31, 62, "TREATMENT", 1000, 700, 100, 0),
        ],
    )
    text = service.export_csv(MagicMock(), campaign_id=None, data_origin=None, now=NOW)
    _, treatment = list(csv.DictReader(io.StringIO(text)))

    assert treatment["uplift_points"] == "-10.00"
    assert treatment["ci_low_points"].startswith("-")
    assert "'" not in treatment["uplift_points"] + treatment["ci_low_points"]
