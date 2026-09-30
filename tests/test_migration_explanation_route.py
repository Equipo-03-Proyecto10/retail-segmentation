"""The migration-explanation route (F7-06): who may reach it, and its
error handling for missing or unknown parameters."""

from __future__ import annotations

from datetime import datetime, timedelta
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


@pytest.mark.parametrize("role_code", ["INVENTORY_PLANNER", "CUSTOMER"])
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

    response = client.get(
        "/migration-explanation/"
        "?run_a=1&run_b=999&customer_id=00000000-0000-0000-0000-000000000001"
    )
    assert response.status_code == 404


def test_a_malformed_customer_id_is_a_404(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client, "ANALYST")

    response = client.get("/migration-explanation/?run_a=1&run_b=2&customer_id=bad")
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

    response = client.get(
        "/migration-explanation/"
        "?run_a=1&run_b=2&customer_id=00000000-0000-0000-0000-000000000099"
    )
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
        "/migration-explanation/"
        "?run_a=1&run_b=2&customer_id=00000000-0000-0000-0000-000000000001"
    ).get_data(as_text=True)

    assert "Ada Lovelace" in body
    assert "CHAMPION" in body
    assert "AT_RISK" in body


# ---------- #338: the page in plain language ----------

_EARLIER_AT = datetime(2026, 8, 1, 3, 0, 0, 123456)
_LATER_AT = datetime(2026, 9, 1, 3, 0, 0, 654321)
_CUSTOMER_URL = (
    "/migration-explanation/"
    "?run_a=1&run_b=2&customer_id=00000000-0000-0000-0000-000000000001"
)


def _explained(
    monkeypatch: pytest.MonkeyPatch, app: Flask, before, after
) -> tuple[int, str]:
    monkeypatch.setattr(
        "web.routes.migration_explanation.order_runs", lambda *a, **k: (1, 2)
    )
    monkeypatch.setattr(
        "web.routes.migration_explanation.get_run",
        lambda _c, run_id: _run(run_id, _EARLIER_AT if run_id == 1 else _LATER_AT),
    )
    monkeypatch.setattr(
        "web.routes.migration_explanation.get_customer_assignment_for_run",
        lambda _c, run_id, _cid: before if run_id == 1 else after,
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")
    response = client.get(_CUSTOMER_URL)
    return response.status_code, " ".join(response.get_data(as_text=True).split())


def test_each_measure_reads_as_a_sentence_with_days_and_a_judgement(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _assignment(
        label_code="LOYAL",
        recency_last_purchase_at=_EARLIER_AT - timedelta(days=18),
        frequency_count=7,
        monetary_total=Decimal("1000.00"),
    )
    after = _assignment(
        label_code="AT_RISK",
        r_score=1,
        f_score=2,
        recency_last_purchase_at=_LATER_AT - timedelta(days=72, hours=1),
        frequency_count=3,
        monetary_total=Decimal("1050.00"),
    )
    status, body = _explained(monkeypatch, app, before, after)

    assert status == 200
    assert (
        "Recency went from 18 to 72 days, frequency dropped from 7 to 3, "
        "monetary stable." in body
    )
    assert "Recency went from 18 to 72 days — changed" in body
    assert "Frequency dropped from 7 to 3 purchases — changed." in body
    assert "Monetary went from 1,000.00 to 1,050.00 MXN — stable (within 10%)." in body
    assert "recency within 7 days, the same number of purchases" in body
    # The table: recency in days, never a raw timestamp with microseconds.
    assert "18 days" in body and "72 days" in body
    assert "123456" not in body and "654321" not in body
    assert "00:00" not in body


def test_an_unchanged_score_reads_stable_not_plus_zero(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _assignment(recency_last_purchase_at=_EARLIER_AT - timedelta(days=5))
    after = _assignment(recency_last_purchase_at=_LATER_AT - timedelta(days=5))
    _, body = _explained(monkeypatch, app, before, after)

    assert "+0" not in body
    assert '<td class="mq-table__cell--num">stable</td>' in body


def test_a_score_moved_only_by_the_cut_points_says_the_rank_changed(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _assignment(
        f_score=4, recency_last_purchase_at=_EARLIER_AT - timedelta(days=5)
    )
    after = _assignment(
        f_score=3, recency_last_purchase_at=_LATER_AT - timedelta(days=5)
    )
    _, body = _explained(monkeypatch, app, before, after)

    assert (
        "Frequency stayed at 6 purchases — stable. Its score still went from 4 to 3: "
        "other customers moved the quintile cut points, not this customer's "
        "behaviour." in body.replace("&#39;", "'")
    )


def test_a_customer_absent_from_the_earlier_run_reads_as_new(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    after = _assignment(
        label_code="POTENTIAL",
        recency_last_purchase_at=_LATER_AT - timedelta(days=5),
    )
    status, body = _explained(monkeypatch, app, None, after)

    assert status == 200
    assert "New customer" in body
    assert (
        "Not part of run #1; first scored in run #2 as <strong>POTENTIAL</strong>"
        in (body)
    )
    assert "Unassigned" not in body
    assert "Not in this run" in body


def test_a_customer_scored_but_unassigned_earlier_still_reads_unassigned(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _assignment(
        label_code=None,
        segment_id=None,
        r_score=None,
        f_score=None,
        m_score=None,
        recency_last_purchase_at=None,
        frequency_count=None,
        monetary_total=None,
    )
    after = _assignment(recency_last_purchase_at=_LATER_AT - timedelta(days=5))
    _, body = _explained(monkeypatch, app, before, after)

    assert "<strong>Unassigned</strong> → <strong>CHAMPION</strong>" in body
    assert "New customer" not in body
    assert "the earlier run found no purchase in its window" in body


def test_the_explanation_links_back_to_the_customer_and_the_matrix(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _assignment(recency_last_purchase_at=_EARLIER_AT - timedelta(days=5))
    after = _assignment(recency_last_purchase_at=_LATER_AT - timedelta(days=5))
    _, body = _explained(monkeypatch, app, before, after)

    assert 'href="/catalog/customers/00000000-0000-0000-0000-000000000001"' in body
    assert 'href="/migration-matrix/?run_a=1&amp;run_b=2"' in body


def test_a_move_caused_only_by_the_rank_is_said_once_at_the_top(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _assignment(
        label_code="HIBERNATING",
        f_score=2,
        recency_last_purchase_at=_EARLIER_AT - timedelta(days=5),
    )
    after = _assignment(
        label_code="AT_RISK",
        f_score=3,
        recency_last_purchase_at=_LATER_AT - timedelta(days=5),
    )
    _, body = _explained(monkeypatch, app, before, after)

    assert (
        "The label changed although this customer's own values did not: other "
        "customers moved the quintile cut points" in body
    )


def test_a_new_customer_has_no_summary_of_measures_it_cannot_compare(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    after = _assignment(recency_last_purchase_at=_LATER_AT - timedelta(days=5))
    _, body = _explained(monkeypatch, app, None, after)

    assert "Recency not compared, frequency not compared" not in body
