"""The segment recalculation (#102, F3-10).

The scoring and matching are one SQL statement, and what it actually does over
real sales is verified against PostgreSQL in
docs/evidence/f3-10-segment-run.md — including the criterion no unit test can
reach, that a second run over the same sales writes nothing at all.

What these tests cover is the application around it: who may run it, what a
window is allowed to be, and that the page reports what the run did.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.segments import RecalculationCounts, recalculate_segments
from web.services.segmentation import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    InvalidWindow,
    RunResult,
    parse_window,
    run,
)


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


def _sign_in(client: FlaskClient, role_code: str) -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


def _flattened(html: str) -> str:
    """Collapse the template's wrapping so an assertion can quote a sentence."""
    return " ".join(html.split())


def _result(**overrides) -> RunResult:
    defaults = dict(
        window_days=180,
        processed=30,
        assigned=18,
        unmatched=12,
        reassigned=30,
        cleared=0,
        seconds=0.0421,
    )
    return RunResult(**{**defaults, **overrides})


# ---------- only the administrator (RN-05) ----------


def test_the_administrator_may_run_it(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.segment_run.run", lambda _c, _w: _result())
    client = app.test_client()
    _sign_in(client, "ADMIN")

    assert client.get("/segment-run/").status_code == 200
    assert (
        client.post(
            "/segment-run/", data={"window": "180", "confirm": "yes"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "role_code",
    [
        "ANALYST",
        "MARKETING",
        "STORE_MANAGER",
        "INVENTORY_PLANNER",
        "AUDITOR",
        "CUSTOMER",
    ],
)
def test_nobody_else_may_run_it(app: Flask, role_code: str) -> None:
    """It rewrites a column on every customer; it is not an analyst's button."""
    client = app.test_client()
    _sign_in(client, role_code)

    assert client.get("/segment-run/").status_code == 403
    assert (
        client.post(
            "/segment-run/", data={"window": "180", "confirm": "yes"}
        ).status_code
        == 403
    )


def test_an_auditor_may_read_the_result_but_not_cause_one(app: Flask) -> None:
    """The auditor reads the audit trail the run leaves, not the trigger."""
    client = app.test_client()
    _sign_in(client, "AUDITOR")

    assert (
        client.post(
            "/segment-run/", data={"window": "180", "confirm": "yes"}
        ).status_code
        == 403
    )
    assert client.get("/audit/?entity=customer").status_code == 200


def test_signed_out_it_sends_you_to_sign_in(app: Flask) -> None:
    response = app.test_client().get("/segment-run/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_an_anonymous_post_is_refused_rather_than_redirected(app: Flask) -> None:
    assert app.test_client().post("/segment-run/").status_code == 403


# ---------- the window ----------


def test_an_omitted_service_window_is_the_default() -> None:
    assert parse_window(None) == DEFAULT_WINDOW_DAYS


def test_a_window_is_read_as_a_number_of_days() -> None:
    assert parse_window("90") == 90
    assert parse_window(" 7 ") == 7


@pytest.mark.parametrize(
    "raw", ["", "   ", "0", "-30", "not-a-number", "180.5", str(MAX_WINDOW_DAYS + 1)]
)
def test_a_window_that_is_not_usable_is_refused(raw: str) -> None:
    with pytest.raises(InvalidWindow):
        parse_window(raw)


def test_a_refused_window_answers_400_and_explains_itself(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client, "ADMIN")

    response = client.post("/segment-run/", data={"window": "0"})

    assert response.status_code == 400
    assert "between 1 and 3650 days" in response.get_data(as_text=True)


def test_the_window_reaches_the_service(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def capture(_connection, window_days):
        seen["window"] = window_days
        return _result(window_days=window_days)

    monkeypatch.setattr("web.routes.segment_run.run", capture)
    client = app.test_client()
    _sign_in(client, "ADMIN")

    client.post("/segment-run/", data={"window": "45", "confirm": "yes"})

    assert seen["window"] == 45


# ---------- what the run reports ----------


def test_the_result_page_reports_what_the_run_did(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.segment_run.run",
        lambda _c, _w: _result(processed=30, assigned=18, unmatched=12, seconds=0.0421),
    )
    client = app.test_client()
    _sign_in(client, "ADMIN")

    body = client.post(
        "/segment-run/", data={"window": "180", "confirm": "yes"}
    ).get_data(as_text=True)

    assert "Customers processed" in body and "30" in body
    assert "Segments assigned" in body and "18" in body
    assert "Matched no rule" in body and "12" in body
    assert "0.04s" in body, "how long it took"


def test_a_run_that_changed_nothing_says_so(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second run over the same sales, which must also audit nothing."""
    monkeypatch.setattr(
        "web.routes.segment_run.run",
        lambda _c, _w: _result(reassigned=0, cleared=0),
    )
    client = app.test_client()
    _sign_in(client, "ADMIN")

    body = _flattened(
        client.post("/segment-run/", data={"window": "180", "confirm": "yes"}).get_data(
            as_text=True
        )
    )

    assert "Nothing changed" in body
    assert "audit log has no new entries" in body


def test_changed_nothing_is_about_writes_not_about_customers() -> None:
    """A run can process every customer and still write nothing."""
    assert _result(processed=30, assigned=18, reassigned=0, cleared=0).changed_nothing
    assert not _result(reassigned=1, cleared=0).changed_nothing
    assert not _result(reassigned=0, cleared=1).changed_nothing


def test_the_run_commits_so_the_audit_entries_survive() -> None:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        30,
        18,
        12,
        30,
        0,
    )

    result = run(connection, 180)

    connection.commit.assert_called_once_with()
    assert (result.processed, result.assigned, result.unmatched) == (30, 18, 12)
    assert result.seconds >= 0


# ---------- the statement itself ----------


def test_the_window_is_a_parameter_and_never_interpolated() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (0, 0, 0, 0, 0)

    recalculate_segments(connection, 90)

    statement, parameters = cursor.execute.call_args.args
    assert parameters == (90,)
    assert "90" not in statement


def test_the_statement_writes_only_where_the_segment_actually_changes() -> None:
    """What keeps the second run silent, and the audit log honest."""
    from web.db.segments import _RECALCULATE

    assert "IS DISTINCT FROM" in _RECALCULATE


def test_every_quintile_is_ordered_deterministically() -> None:
    """Ties broken by customer_id, or two runs could disagree and both be right."""
    from web.db.segments import _RECALCULATE

    windows = [line for line in _RECALCULATE.splitlines() if "OVER (ORDER BY" in line]

    assert len(windows) == 3
    assert all(", customer_id)" in line for line in windows)


def test_the_counts_come_back_in_the_order_the_statement_selects_them() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (30, 18, 12, 7, 3)

    counts = recalculate_segments(connection, 180)

    assert counts == RecalculationCounts(
        processed=30, assigned=18, unmatched=12, reassigned=7, cleared=3
    )


@pytest.mark.parametrize("data", [{}, {"window": ""}, {"window": "   "}])
def test_blank_submissions_never_recalculate(app, monkeypatch, data):
    recalculate = Mock()
    monkeypatch.setattr("web.routes.segment_run.run", recalculate)
    client = app.test_client()
    _sign_in(client, "ADMIN")
    assert client.post("/segment-run/", data=data).status_code == 400
    recalculate.assert_not_called()


def test_segment_run_requires_a_second_post(app, monkeypatch):
    recalculate = Mock(return_value=_result(window_days=45))
    monkeypatch.setattr("web.routes.segment_run.run", recalculate)
    client = app.test_client()
    _sign_in(client, "ADMIN")
    response = client.post("/segment-run/", data={"window": "45"})
    assert response.status_code == 200
    assert b"Confirm recalculation" in response.data
    recalculate.assert_not_called()
    response = client.post("/segment-run/", data={"window": "45", "confirm": "yes"})
    assert response.status_code == 200
    assert recalculate.call_args.args[1] == 45
