"""The migration-explanation route (F7-06): who may reach it, and its
error handling for missing or unknown parameters."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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


def _run(run_id: int, run_at: datetime) -> SegmentationRun:
    return SegmentationRun(
        run_id=run_id,
        method="RFM_RULES",
        window_days=180,
        parameters={"window_days": 180},
        customer_count=30,
        executed_by=None,
        executed_by_name=None,
        run_at=run_at,
    )


def _assignment(**overrides) -> RunAssignment:
    defaults = dict(
        customer_id="00000000-0000-0000-0000-000000000001",
        customer_name="Ada Lovelace",
        segment_id=1,
        label_code="CHAMPION",
        r_score=5,
        f_score=5,
        m_score=5,
        recency_last_purchase_at=datetime(2026, 1, 1),
        frequency_count=6,
        monetary_total=Decimal("100.00"),
    )
    return RunAssignment(**{**defaults, **overrides})


@pytest.mark.parametrize(
    "role_code", ["STORE_MANAGER", "INVENTORY_PLANNER", "CUSTOMER"]
)
def test_a_profile_without_segment_read_is_refused(app: Flask, role_code: str) -> None:
    client = app.test_client()
    _sign_in(client, role_code)

    assert client.get("/migration-explanation/").status_code == 403


def test_signed_out_it_sends_you_to_sign_in(app: Flask) -> None:
    response = app.test_client().get("/migration-explanation/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_missing_query_parameters_is_a_404(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert client.get("/migration-explanation/").status_code == 404
    assert client.get("/migration-explanation/?run_a=1").status_code == 404


def test_an_unknown_run_is_a_404(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    from web.services.segment_migration import UnknownRun

    def _raise(*_a, **_k):
        raise UnknownRun("Run 999 does not exist.")

    monkeypatch.setattr("web.routes.migration_explanation.order_runs", _raise)
    client = app.test_client()
    _sign_in(client, "ANALYST")

    response = client.get("/migration-explanation/?run_a=1&run_b=999&customer_id=c1")
    assert response.status_code == 404


def test_a_customer_absent_from_both_runs_is_a_404(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.migration_explanation.order_runs", lambda *a, **k: (1, 2)
    )
    monkeypatch.setattr(
        "web.routes.migration_explanation.get_run",
        lambda _c, run_id: _run(run_id, datetime(2026, 1, run_id)),
    )
    monkeypatch.setattr(
        "web.routes.migration_explanation.get_customer_assignment_for_run",
        lambda *a, **k: None,
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    response = client.get("/migration-explanation/?run_a=1&run_b=2&customer_id=nobody")
    assert response.status_code == 404


def test_renders_the_explanation_for_a_moved_customer(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.migration_explanation.order_runs", lambda *a, **k: (1, 2)
    )
    monkeypatch.setattr(
        "web.routes.migration_explanation.get_run",
        lambda _c, run_id: _run(run_id, datetime(2026, 1, run_id)),
    )
    before = _assignment(label_code="CHAMPION", r_score=5)
    after = _assignment(label_code="AT_RISK", r_score=1)
    monkeypatch.setattr(
        "web.routes.migration_explanation.get_customer_assignment_for_run",
        lambda _c, run_id, _cid: before if run_id == 1 else after,
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get(
        "/migration-explanation/?run_a=1&run_b=2&customer_id=c1"
    ).get_data(as_text=True)

    assert "Ada Lovelace" in body
    assert "CHAMPION" in body
    assert "AT_RISK" in body
