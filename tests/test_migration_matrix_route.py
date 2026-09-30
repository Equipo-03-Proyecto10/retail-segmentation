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


@pytest.mark.parametrize("role_code", ["INVENTORY_PLANNER", "CUSTOMER"])
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


def _stub_selected_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep selected ids in the option-list phase out of real SQL."""
    monkeypatch.setattr(
        "web.routes.migration_matrix.get_run",
        lambda _connection, run_id: _run(run_id, run_id),
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


def test_a_cell_filter_lists_only_that_cells_customers_with_explanations(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.services.segment_migration import (
        CustomerMigration,
        Direction,
        MigrationCategory,
    )

    customer_one = "11111111-1111-1111-1111-111111111111"
    customer_two = "22222222-2222-2222-2222-222222222222"
    customer_three = "33333333-3333-3333-3333-333333333333"
    migrations = [
        CustomerMigration(
            customer_one,
            "CHAMPION",
            "LOYAL",
            MigrationCategory.MOVED,
            Direction.DECLINED,
        ),
        CustomerMigration(
            customer_two,
            "CHAMPION",
            "LOYAL",
            MigrationCategory.MOVED,
            Direction.DECLINED,
        ),
        CustomerMigration(
            customer_three,
            "CHAMPION",
            "AT_RISK",
            MigrationCategory.MOVED,
            Direction.DECLINED,
        ),
    ]
    _stub_matrix(monkeypatch, [])
    _stub_selected_runs(monkeypatch)
    monkeypatch.setattr(
        "web.routes.migration_matrix.compute_migration",
        lambda *a, **k: migrations,
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.get_label_ordinals",
        lambda *a, **k: {"CHAMPION": 1, "LOYAL": 2, "AT_RISK": 3},
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs", lambda _c, a, b: (a, b)
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.list_customer_names",
        lambda _c, ids: {
            customer_one: "Ada One",
            customer_two: "Bea Two",
            customer_three: "Cy Three",
        },
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    response = client.get(
        "/migration-matrix/?run_a=1&run_b=2&from_state=CHAMPION&to_state=LOYAL"
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "Ada One" in body and "Bea Two" in body
    assert "Cy Three" not in body
    assert "Before" in body and "After" in body and "declined" in body
    assert "before=CHAMPION&amp;after=LOYAL" in body
    assert (
        f"/migration-explanation/?run_a=1&amp;run_b=2&amp;customer_id={customer_one}"
        in body
    )


def test_a_cell_filter_shows_improved_direction(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.services.segment_migration import (
        CustomerMigration,
        Direction,
        MigrationCategory,
    )

    customer_id = "11111111-1111-1111-1111-111111111111"
    _stub_matrix(monkeypatch, [])
    _stub_selected_runs(monkeypatch)
    monkeypatch.setattr(
        "web.routes.migration_matrix.compute_migration",
        lambda *a, **k: [
            CustomerMigration(
                customer_id,
                "LOST",
                "CHAMPION",
                MigrationCategory.MOVED,
                Direction.IMPROVED,
            )
        ],
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.get_label_ordinals",
        lambda *a, **k: {"CHAMPION": 1, "LOST": 6},
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs", lambda _c, a, b: (a, b)
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.list_customer_names",
        lambda _c, ids: {customer_id: "Improving customer"},
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    response = client.get(
        "/migration-matrix/?run_a=1&run_b=2&before=LOST&after=CHAMPION"
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Improving customer" in body and "improved" in body


def test_new_customer_filter_uses_an_unambiguous_new_before_state(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.services.segment_migration import CustomerMigration, MigrationCategory

    customer_id = "11111111-1111-1111-1111-111111111111"
    _stub_matrix(monkeypatch, [])
    _stub_selected_runs(monkeypatch)
    monkeypatch.setattr(
        "web.routes.migration_matrix.compute_migration",
        lambda *a, **k: [
            CustomerMigration(
                customer_id,
                None,
                "CHAMPION",
                MigrationCategory.ABSENT_FROM_EARLIER,
            )
        ],
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs", lambda _c, a, b: (a, b)
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.list_customer_names",
        lambda _c, ids: {customer_id: "New customer"},
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get(
        "/migration-matrix/?run_a=1&run_b=2&from_state=NEW&to_state=CHAMPION"
    ).get_data(as_text=True)

    assert "New customer" in body
    assert "NEW" in body
    assert "<td>NEW</td>" in body


@pytest.mark.parametrize(
    "query, message",
    [
        ("from_state=not-a-state", "not in this matrix"),
        ("to_state=not-a-state", "not in this matrix"),
        ("from_state=CHAMPION&from_state=LOYAL", "only once"),
    ],
)
def test_an_invalid_cell_filter_is_reported_without_recomputing_details(
    app: Flask, monkeypatch: pytest.MonkeyPatch, query: str, message: str
) -> None:
    _stub_matrix(monkeypatch, [])
    _stub_selected_runs(monkeypatch)
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs", lambda _c, a, b: (a, b)
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.compute_migration", lambda *a, **k: []
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.get_label_ordinals",
        lambda *a, **k: {"CHAMPION": 1},
    )
    monkeypatch.setattr(
        "web.routes.migration_matrix.list_customer_names",
        lambda *_a, **_k: pytest.fail("invalid filters must not load customers"),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    response = client.get(f"/migration-matrix/?run_a=1&run_b=2&{query}")

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)


def test_a_cell_filter_without_two_runs_is_refused(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_matrix(monkeypatch, [])
    client = app.test_client()
    _sign_in(client, "ANALYST")

    response = client.get("/migration-matrix/?from_state=CHAMPION")

    assert response.status_code == 400
    assert "Choose two runs" in response.get_data(as_text=True)


def test_the_matrix_has_a_scrollable_table_for_narrow_screens(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_matrix(monkeypatch, [])
    _stub_selected_runs(monkeypatch)
    monkeypatch.setattr(
        "web.routes.migration_matrix.order_runs", lambda _c, a, b: (a, b)
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/migration-matrix/?run_a=1&run_b=2").get_data(as_text=True)

    assert 'class="mq-table-wrap"' in body
    assert 'class="mq-table__link"' in body
