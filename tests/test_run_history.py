"""The run-history view (F7-03).

Read-only, gated on segment.read: a run's list and detail are the same
substrate the rest of the segmentation surface already reads, just traced
back to the run rather than read as "current" (ADR-0010's reasoning for the
consultation module applies here too).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.segments import RunAssignment, SegmentationRun


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


def _run(**overrides) -> SegmentationRun:
    defaults = dict(
        run_id=1,
        method="RFM_RULES",
        window_days=180,
        parameters={"window_days": 180},
        customer_count=30,
        executed_by="11111111-1111-1111-1111-000000000001",
        executed_by_name="MOSAIQ Administrator",
        run_at=datetime(2026, 9, 24, 0, 0, 0),
    )
    return SegmentationRun(**{**defaults, **overrides})


def _assignment(**overrides) -> RunAssignment:
    defaults = dict(
        customer_id="00000000-0000-0000-0000-000000000001",
        customer_name="Ada Lovelace",
        segment_id=4,
        label_code="CHAMPION",
        r_score=5,
        f_score=5,
        m_score=5,
        recency_last_purchase_at=datetime(2026, 9, 20, 0, 0, 0),
        frequency_count=6,
        monetary_total="137.50",
    )
    return RunAssignment(**{**defaults, **overrides})


# ---------- who may reach each surface ----------


def test_an_analyst_reaches_both_pages(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.run_history.list_runs", lambda *a, **k: ([_run()], 1)
    )
    monkeypatch.setattr(
        "web.routes.run_history.get_run", lambda *a, **k: _run(run_id=1)
    )
    monkeypatch.setattr(
        "web.routes.run_history.list_run_assignments",
        lambda *a, **k: ([_assignment()], 1),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert client.get("/run-history/").status_code == 200
    assert client.get("/run-history/1").status_code == 200


@pytest.mark.parametrize(
    "role_code", ["STORE_MANAGER", "INVENTORY_PLANNER", "CUSTOMER"]
)
def test_a_profile_without_segment_read_is_refused(app: Flask, role_code: str) -> None:
    client = app.test_client()
    _sign_in(client, role_code)

    assert client.get("/run-history/").status_code == 403
    assert client.get("/run-history/1").status_code == 403


def test_signed_out_it_sends_you_to_sign_in(app: Flask) -> None:
    response = app.test_client().get("/run-history/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# ---------- the list ----------


def test_the_list_shows_method_window_customers_and_executor(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.run_history.list_runs",
        lambda *a, **k: (
            [_run(run_id=7, customer_count=30, executed_by_name="Grace Hopper")],
            1,
        ),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/run-history/").get_data(as_text=True)

    assert "#7" in body
    assert "RFM_RULES" in body
    assert "180 days" in body
    assert "30" in body
    assert "Grace Hopper" in body


def test_the_list_reports_no_runs_yet(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.run_history.list_runs", lambda *a, **k: ([], 0))
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/run-history/").get_data(as_text=True)

    assert "No runs have completed yet" in body


# ---------- the detail ----------


def test_the_detail_shows_the_run_and_its_assignments(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.run_history.get_run", lambda _c, _id: _run(run_id=3)
    )
    monkeypatch.setattr(
        "web.routes.run_history.list_run_assignments",
        lambda *a, **k: (
            [_assignment(customer_name="Ada Lovelace", label_code="CHAMPION")],
            1,
        ),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/run-history/3").get_data(as_text=True)

    assert "Run #3" in body
    assert "Ada Lovelace" in body
    assert "CHAMPION" in body


def test_unassigned_customers_are_shown_not_omitted(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RN-21: a customer the run scored but could not label still appears."""
    monkeypatch.setattr(
        "web.routes.run_history.get_run", lambda _c, _id: _run(run_id=3)
    )
    monkeypatch.setattr(
        "web.routes.run_history.list_run_assignments",
        lambda *a, **k: (
            [
                _assignment(
                    customer_name="Unscored Customer",
                    segment_id=None,
                    label_code=None,
                    r_score=None,
                    f_score=None,
                    m_score=None,
                    recency_last_purchase_at=None,
                    frequency_count=None,
                    monetary_total=None,
                )
            ],
            1,
        ),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/run-history/3").get_data(as_text=True)

    assert "Unscored Customer" in body
    assert "Unassigned" in body


def test_the_detail_is_a_404_when_the_run_is_unknown(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.run_history.get_run", lambda _c, _id: None)
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert client.get("/run-history/999999").status_code == 404
