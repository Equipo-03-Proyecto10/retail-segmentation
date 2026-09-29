"""The segment history report page (F12-02).

`build_report` is built and tested in web/services/segment_history_report.py.
What is covered here is the page: who may open it, that every applied filter
is reflected in what is asked of the service, that an expanded row shows the
R/F/M explanation, that no filter combination renders an empty table with no
explanation, and that it is refused by the default-deny gate. The database is
a mock and `build_report` is replaced, so each test states exactly the report
the page was given.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.segment_history_report import UNASSIGNED, HistoryEntry
from web.db.segments import RunAssignment, SegmentationRun
from web.services.segment_history_report import InvalidPeriod, ReportPage, ReportRow
from web.services.segment_migration import ComponentDelta, MigrationExplanation

_URL = "/segment-history-report/"
_WHEN = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


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
        database_connector=Mock(return_value=MagicMock()),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _sign_in(client: FlaskClient, role_code: str = "ANALYST") -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


def _run(run_id: int, **overrides) -> SegmentationRun:
    defaults = dict(
        run_id=run_id,
        method="RFM_RULES",
        window_days=180,
        parameters={},
        customer_count=3,
        executed_by=None,
        executed_by_name=None,
        run_at=_WHEN,
    )
    return SegmentationRun(**{**defaults, **overrides})


def _assignment(**overrides) -> RunAssignment:
    defaults = dict(
        customer_id="00000000-0000-0000-0000-000000000001",
        customer_name="Ada Lovelace",
        segment_id=1,
        label_code="LOYAL",
        r_score=4,
        f_score=3,
        m_score=5,
        recency_last_purchase_at=_WHEN,
        frequency_count=6,
        monetary_total=Decimal("400.00"),
    )
    return RunAssignment(**{**defaults, **overrides})


def _component(
    name: str, before: int | None = 2, after: int | None = 4
) -> ComponentDelta:
    return ComponentDelta(name, before, after, before, after)


def _explanation(**overrides) -> MigrationExplanation:
    defaults = dict(
        recency=_component("Recency"),
        frequency=_component("Frequency"),
        monetary=_component("Monetary", before=1, after=5),
        label_before="CHAMPION",
        label_after="LOYAL",
        label_changed=True,
    )
    return MigrationExplanation(**{**defaults, **overrides})


def _row(**overrides) -> ReportRow:
    entry = overrides.pop(
        "entry",
        HistoryEntry(
            history_id=2,
            run_id=31,
            run_at=_WHEN,
            method="RFM_RULES",
            window_days=180,
            valid_from=_WHEN,
            valid_to=None,
            label_name="Loyal",
            assignment=_assignment(),
        ),
    )
    explanation = overrides.pop("explanation", _explanation())
    return ReportRow(entry=entry, explanation=explanation)


def _report(**overrides) -> ReportPage:
    defaults = dict(rows=(_row(),), total=1, page=1, page_count=1)
    return ReportPage(**{**defaults, **overrides})


def _open(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    report: ReportPage | None = None,
    *,
    role: str = "ANALYST",
    url: str = _URL,
    side_effect=None,
):
    fake = Mock(return_value=report if report is not None else _report())
    if side_effect is not None:
        fake = Mock(side_effect=side_effect)
    monkeypatch.setattr("web.routes.segment_history_report.build_report", fake)
    monkeypatch.setattr(
        "web.routes.segment_history_report.list_runs",
        Mock(return_value=([_run(31), _run(30)], 2)),
    )
    monkeypatch.setattr(
        "web.routes.segment_history_report.get_label_ordinals",
        Mock(return_value={"CHAMPION": 1, "LOYAL": 2, "LOST": 3}),
    )
    client = app.test_client()
    _sign_in(client, role)
    return client.get(url), fake


def _body(response) -> str:
    return response.get_data(as_text=True)


# ---------- who may open it ----------


@pytest.mark.parametrize("role_code", ["INVENTORY_PLANNER", "CUSTOMER"])
def test_a_profile_without_segment_read_is_refused(app, monkeypatch, role_code) -> None:
    response, fake = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 403
    fake.assert_not_called()


@pytest.mark.parametrize(
    "role_code", ["ADMIN", "ANALYST", "MARKETING", "AUDITOR", "STORE_MANAGER"]
)
def test_the_roles_that_read_segments_may_open_it(app, monkeypatch, role_code) -> None:
    response, _ = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 200


def test_signed_out_it_sends_you_to_sign_in(app) -> None:
    response = app.test_client().get(_URL)

    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_the_page_only_reads(app, monkeypatch) -> None:
    _open(app, monkeypatch)
    client = app.test_client()
    _sign_in(client)

    assert client.post(_URL).status_code == 405


# ---------- every applied filter reaches the service ----------


def test_a_run_filter_reaches_the_service(app, monkeypatch) -> None:
    _, fake = _open(app, monkeypatch, url=_URL + "?run=31")

    assert fake.call_args.kwargs["run_id"] == 31


def test_a_label_filter_reaches_the_service(app, monkeypatch) -> None:
    _, fake = _open(app, monkeypatch, url=_URL + "?label=LOYAL")

    assert fake.call_args.kwargs["label_code"] == "LOYAL"


def test_the_unassigned_choice_reaches_the_service_as_the_sentinel(
    app, monkeypatch
) -> None:
    _, fake = _open(app, monkeypatch, url=_URL + "?label=unassigned")

    assert fake.call_args.kwargs["label_code"] == UNASSIGNED


def test_a_period_filter_reaches_the_service(app, monkeypatch) -> None:
    _, fake = _open(
        app, monkeypatch, url=_URL + "?period_start=2026-04-01&period_end=2026-09-01"
    )

    kwargs = fake.call_args.kwargs
    assert str(kwargs["period_start"]) == "2026-04-01"
    assert str(kwargs["period_end"]) == "2026-09-01"


def test_all_three_filters_combine(app, monkeypatch) -> None:
    _, fake = _open(
        app,
        monkeypatch,
        url=_URL + "?run=31&label=LOYAL&period_start=2026-04-01&period_end=2026-09-01",
    )

    kwargs = fake.call_args.kwargs
    assert kwargs["run_id"] == 31
    assert kwargs["label_code"] == "LOYAL"
    assert str(kwargs["period_start"]) == "2026-04-01"


def test_the_chosen_filters_are_reflected_back_into_the_form(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch, url=_URL + "?run=31&label=LOYAL")[0])

    assert 'value="31" selected' in body.replace("  ", " ")
    assert 'value="LOYAL" selected' in body.replace("  ", " ")


@pytest.mark.parametrize("raw", ["abc", "1.5", "-1", "2147483648", "99999999999"])
def test_a_run_choice_that_is_not_a_whole_number_is_a_400(
    app, monkeypatch, raw
) -> None:
    response, fake = _open(app, monkeypatch, url=_URL + f"?run={raw}")

    assert response.status_code == 400
    fake.assert_not_called()


def test_an_invalid_date_is_a_400_that_explains_itself(app, monkeypatch) -> None:
    response, fake = _open(app, monkeypatch, url=_URL + "?period_start=not-a-date")

    assert response.status_code == 400
    assert "YYYY-MM-DD" in _body(response)
    fake.assert_not_called()


def test_a_period_the_service_refuses_is_a_400(app, monkeypatch) -> None:
    response, _ = _open(
        app,
        monkeypatch,
        side_effect=InvalidPeriod("The period's start must be on or before its end."),
    )

    assert response.status_code == 400
    assert "start must be on or before" in _body(response)


def test_an_unknown_run_id_is_not_an_error_just_no_rows(app, monkeypatch) -> None:
    """Filtering, unlike selecting a run to view, does not need the run to
    exist: it is a filter no row happens to match."""
    response, fake = _open(
        app, monkeypatch, _report(rows=(), total=0), url=_URL + "?run=999999"
    )

    assert response.status_code == 200
    fake.assert_called_once()
    assert "No rows match" in _body(response)


# ---------- expanding a row shows the R/F/M explanation ----------


def test_an_expandable_row_shows_the_component_deltas(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch)[0])

    assert "<details>" in body and "<summary>Why this label?</summary>" in body
    assert "Recency" in body and "Frequency" in body and "Monetary" in body
    assert "CHAMPION" in body and "LOYAL" in body


def test_an_unchanged_label_says_it_stayed(app, monkeypatch) -> None:
    unchanged = _report(
        rows=(
            _row(
                explanation=_explanation(
                    label_before="LOYAL", label_after="LOYAL", label_changed=False
                )
            ),
        )
    )

    body = _body(_open(app, monkeypatch, unchanged)[0])

    assert "did not change" in body


def test_a_first_ever_row_says_so_instead_of_showing_an_explanation(
    app, monkeypatch
) -> None:
    first = _report(rows=(_row(explanation=None),))

    body = _body(_open(app, monkeypatch, first)[0])

    assert "First assignment on record" in body
    assert "<details>" not in body


def test_no_scores_for_a_run_is_said_plainly(app, monkeypatch) -> None:
    no_scores = _report(
        rows=(
            _row(
                explanation=_explanation(
                    recency=_component("Recency", before=None, after=4),
                    label_before=None,
                )
            ),
        )
    )

    body = _body(_open(app, monkeypatch, no_scores)[0])

    assert "No scores for this run" in body


# ---------- no rows: the report says so ----------


def test_a_filter_combination_with_no_rows_says_so(app, monkeypatch) -> None:
    empty = _report(rows=(), total=0)

    response, _ = _open(app, monkeypatch, empty, url=_URL + "?run=31&label=LOST")
    body = _body(response)

    assert response.status_code == 200
    assert "No rows match those filters" in body
    assert "<table" not in body


# ---------- what the page must never do ----------


def test_the_page_never_names_a_segmentation_method_by_kind_or_a_cluster(
    app, monkeypatch
) -> None:
    body = _body(_open(app, monkeypatch)[0]).lower()

    for word in ("kmeans", "k-means", "cluster"):
        assert word not in body


def test_names_are_escaped(app, monkeypatch) -> None:
    hostile = _report(
        rows=(
            _row(
                entry=HistoryEntry(
                    history_id=2,
                    run_id=31,
                    run_at=_WHEN,
                    method="RFM_RULES",
                    window_days=180,
                    valid_from=_WHEN,
                    valid_to=None,
                    label_name="Loyal",
                    assignment=_assignment(customer_name="<script>alert(1)</script>"),
                )
            ),
        )
    )

    body = _body(_open(app, monkeypatch, hostile)[0])

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
