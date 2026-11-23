"""What each segment page says beside incremental changes (F22-17, #813).

ADR-0063: an `INCREMENTAL` run records the customers an event or the clock moved,
so it is not a snapshot of the population. Each page that means a snapshot reads
`FULL` runs, and each that shows runs says what it shows. The database half is in
`test_incremental_reports_postgres.py`; this is the half a person reads.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.segments import Segment, SegmentationRun
from web.services.segment_migration import NotASnapshot


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


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    test_client = app.test_client()
    with test_client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = "ANALYST"
        flask_session["name"] = "Analyst user"
    return test_client


def _run(run_id: int = 1, scope: str = "FULL", method: str = "RFM_RULES", **kw):
    defaults = dict(
        run_id=run_id,
        method=method,
        window_days=180,
        parameters={"window_days": 180},
        customer_count=30 if scope == "FULL" else 1,
        executed_by=None,
        executed_by_name=None,
        run_at=datetime(2026, 9, 24, 0, 0, 0),
        data_origin="SEEDED",
        scope=scope,
    )
    return SegmentationRun(**{**defaults, **kw})


def _text(response) -> str:
    return response.get_data(as_text=True)


# ---------- run history ----------


def _list(monkeypatch, runs) -> Mock:
    fake = Mock(return_value=(runs, len(runs)))
    monkeypatch.setattr("web.routes.run_history.list_runs", fake)
    return fake


def test_the_history_lists_full_runs_and_says_so(client, monkeypatch) -> None:
    fake = _list(monkeypatch, [_run(7)])

    body = _text(client.get("/run-history/"))

    assert fake.call_args.kwargs["scope"] == "FULL"
    assert "Showing full runs" in body
    assert "<td>Full</td>" in body


def test_the_history_lists_incremental_changes_when_asked(client, monkeypatch) -> None:
    fake = _list(monkeypatch, [_run(8, "INCREMENTAL")])

    body = _text(client.get("/run-history/?scope=INCREMENTAL"))

    assert fake.call_args.kwargs["scope"] == "INCREMENTAL"
    assert "not a snapshot of the population" in body
    assert "<td>Incremental</td>" in body


def test_the_history_can_list_every_run(client, monkeypatch) -> None:
    fake = _list(monkeypatch, [_run(8, "INCREMENTAL"), _run(7)])

    body = _text(client.get("/run-history/?scope=ALL"))

    assert fake.call_args.kwargs["scope"] is None
    assert "Showing every run" in body


def test_a_scope_the_form_does_not_offer_is_a_400(client, monkeypatch) -> None:
    _list(monkeypatch, [])

    assert client.get("/run-history/?scope=PARTIAL").status_code == 400


def test_a_change_says_it_is_not_the_whole_population(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "web.routes.run_history.get_run", lambda _c, _id: _run(9, "INCREMENTAL")
    )
    monkeypatch.setattr(
        "web.routes.run_history.list_run_assignments", lambda *a, **k: ([], 0)
    )

    body = _text(client.get("/run-history/9"))

    assert "An incremental change, not a snapshot" in body
    assert "Customers changed" in body


def test_a_full_run_carries_no_such_note(client, monkeypatch) -> None:
    monkeypatch.setattr("web.routes.run_history.get_run", lambda _c, _id: _run(3))
    monkeypatch.setattr(
        "web.routes.run_history.list_run_assignments", lambda *a, **k: ([], 0)
    )

    body = _text(client.get("/run-history/3"))

    assert "incremental-run-note" not in body
    assert "Customers scored" in body


# ---------- the pages that read snapshots ----------


def test_the_dashboard_refuses_a_change_and_says_why(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.get_run",
        lambda _c, run_id: _run(run_id, "INCREMENTAL"),
    )
    monkeypatch.setattr(
        "web.routes.segmentation_dashboard.build_dashboard",
        Mock(
            side_effect=NotASnapshot("Run 9 is an incremental change, not a snapshot")
        ),
    )

    response = client.get("/segmentation-dashboard/?run=9")

    assert response.status_code == 400
    assert "not a snapshot" in _text(response)


def test_the_matrix_refuses_a_change_and_says_why(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "web.routes.migration_matrix.list_runs", lambda *a, **k: ([_run(1)], 1)
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs",
        Mock(side_effect=NotASnapshot("Run 9 is an incremental change")),
    )
    monkeypatch.setattr("web.routes.migration_matrix.get_run", lambda _c, _id: None)

    body = _text(client.get("/migration-matrix/?run_a=1&run_b=9"))

    assert "Run 9 is an incremental change" in body


def test_the_explanation_refuses_a_change(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "web.routes.migration_explanation.order_runs",
        Mock(side_effect=NotASnapshot("Run 9 is an incremental change")),
    )

    response = client.get(
        "/migration-explanation/?run_a=1&run_b=9"
        "&customer_id=00000000-0000-0000-0000-000000000001"
    )

    assert response.status_code == 400


def test_the_comparison_refuses_a_change_chosen_by_id(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "web.routes.model_comparison.list_runs_of_method",
        lambda _c, kind, limit: [_run(1, method=kind)],
    )
    monkeypatch.setattr(
        "web.routes.model_comparison.get_run",
        lambda _c, run_id: _run(run_id, "INCREMENTAL"),
    )

    response = client.get("/model-comparison/?rules_run=9")

    assert response.status_code == 400
    assert "incremental change, not a snapshot" in _text(response)


# ---------- a segment's own page ----------


@pytest.mark.parametrize(("held", "shown"), [(0, False), (2, True)])
def test_a_segment_says_who_holds_its_label_without_being_listed(
    client, monkeypatch, held: int, shown: bool
) -> None:
    monkeypatch.setattr(
        "web.routes.catalog.get_segment",
        lambda _c, _id: Segment(4, "Segment 4", None, 4, datetime(2026, 1, 1), None),
    )
    monkeypatch.setattr("web.routes.catalog.get_segment_rule", lambda _c, _id: None)
    monkeypatch.setattr(
        "web.routes.catalog.list_customers_in_segment", lambda *a, **k: ([], 0)
    )
    monkeypatch.setattr(
        "web.routes.catalog.count_label_holders_outside_segment",
        lambda _c, _id: held,
    )

    body = _text(client.get("/catalog/segments/4"))

    assert ("unplaced-note" in body) is shown
    if shown:
        assert "2 customers currently hold" in body
