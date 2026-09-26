"""The migration-matrix route (F7-05): who may reach it, and that it renders."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config


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


def test_an_analyst_reaches_the_page(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.migration_matrix.list_runs", lambda *a, **k: ([], 0)
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert client.get("/migration-matrix/").status_code == 200


@pytest.mark.parametrize(
    "role_code", ["STORE_MANAGER", "INVENTORY_PLANNER", "CUSTOMER"]
)
def test_a_profile_without_segment_read_is_refused(app: Flask, role_code: str) -> None:
    client = app.test_client()
    _sign_in(client, role_code)

    assert client.get("/migration-matrix/").status_code == 403


def test_signed_out_it_sends_you_to_sign_in(app: Flask) -> None:
    response = app.test_client().get("/migration-matrix/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_selecting_two_runs_renders_the_matrix(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.services.segment_migration import (
        CustomerMigration,
        MigrationCategory,
    )

    monkeypatch.setattr(
        "web.routes.migration_matrix.list_runs", lambda *a, **k: ([], 0)
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs", lambda _c, a, b: (a, b)
    )
    monkeypatch.setattr("web.routes.migration_matrix.get_run", lambda *a, **k: None)
    monkeypatch.setattr(
        "web.routes.migration_matrix.compute_migration",
        lambda *a, **k: [
            CustomerMigration("c1", "CHAMPION", "LOYAL", MigrationCategory.MOVED)
        ],
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.get_label_ordinals",
        lambda *a, **k: {"CHAMPION": 1, "LOYAL": 2},
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/migration-matrix/?run_a=1&run_b=2").get_data(as_text=True)

    assert "CHAMPION" in body
    assert "LOYAL" in body


def test_an_unknown_run_shows_an_error_not_a_500(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.services.segment_migration import UnknownRun

    monkeypatch.setattr(
        "web.routes.migration_matrix.list_runs", lambda *a, **k: ([], 0)
    )

    def _raise(*_a, **_k):
        raise UnknownRun("Run 999 does not exist.")

    monkeypatch.setattr("web.routes.migration_matrix.order_runs", _raise)
    monkeypatch.setattr("web.routes.migration_matrix.get_run", lambda *a, **k: None)
    client = app.test_client()
    _sign_in(client, "ANALYST")

    response = client.get("/migration-matrix/?run_a=1&run_b=999")

    assert response.status_code == 200
    assert "no longer exists" in response.get_data(as_text=True)


def _run(run_id: int, day: int):
    from datetime import datetime

    from web.db.segments import SegmentationRun

    return SegmentationRun(
        run_id, "RFM_RULES", 90, {}, 30, None, None, datetime(2026, 1, day)
    )


def _stub_matrix(monkeypatch: pytest.MonkeyPatch, runs: list) -> None:
    monkeypatch.setattr(
        "web.routes.migration_matrix.list_runs", lambda *a, **k: (runs, len(runs))
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.compute_migration", lambda *a, **k: []
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.get_label_ordinals",
        lambda *a, **k: {"CHAMPION": 1},
    )


def test_runs_picked_in_reverse_are_shown_in_the_order_the_matrix_uses(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows come from the earlier run, so the "Earlier run" picker must name
    it even when the user chose the later run there."""
    _stub_matrix(monkeypatch, [_run(2, 2), _run(1, 1)])
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs",
        lambda _c, a, b: (min(a, b), max(a, b)),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/migration-matrix/?run_a=2&run_b=1").get_data(as_text=True)

    run_a_select = body[body.index('id="run_a"') : body.index('id="run_b"')]
    run_b_select = body[body.index('id="run_b"') :]
    assert '<option value="1" selected>' in run_a_select
    assert '<option value="2" selected>' in run_b_select.split("</select>")[0]


def test_a_run_older_than_the_option_list_stays_selected(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_matrix(monkeypatch, [_run(200, 2)])
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs", lambda _c, a, b: (a, b)
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.get_run", lambda _c, run_id: _run(run_id, 1)
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/migration-matrix/?run_a=5&run_b=200").get_data(as_text=True)

    run_a_select = body[body.index('id="run_a"') : body.index('id="run_b"')]
    assert '<option value="5" selected>' in run_a_select
