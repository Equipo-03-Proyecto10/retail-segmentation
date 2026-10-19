"""An experiment's end is announced by the next signed-in GET (AN-04, #419;
ADR-0047, RN-64).

Nothing runs between requests, so the announcement rides on one: a hook of the
notifications blueprint runs the announcer before a signed-in `GET`, and never
before a `HEAD`, a `POST` or a request that was refused. The database is a mock;
the statement itself runs against PostgreSQL in
tests/test_notification_announcer_db.py.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, Mock

import psycopg
import pytest
from flask import Flask
from flask.testing import FlaskClient

from tests.test_notification_service import USER_ID
from web.app import create_app
from web.config import Config
from web.db import notifications as notifications_db
from web.services import notifications
from web.services.notifications import (
    EXPERIMENT_END_LOOKBACK_DAYS,
    announce_experiment_ends,
)

pytestmark = pytest.mark.real_experiment_announcer


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
def announce(monkeypatch: pytest.MonkeyPatch) -> Mock:
    fake = Mock(return_value=0)
    monkeypatch.setattr("web.routes.notifications.announce_experiment_ends", fake)
    return fake


def _sign_in(app: Flask, role: str = "ANALYST") -> FlaskClient:
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["role_code"] = role
        flask_session["name"] = "Test User"
    return client


# ---------- when it runs ----------


def test_a_signed_in_get_announces_once_on_the_requests_connection(
    app: Flask, announce: Mock
) -> None:
    response = _sign_in(app).get("/notifications/")

    assert response.status_code == 200
    announce.assert_called_once()
    assert announce.call_args.args[0] is not None


@pytest.mark.parametrize(
    "path", ["/", "/notifications/", "/catalog/", "/campaigns/", "/reports/"]
)
def test_any_signed_in_page_a_role_may_read_announces(
    app: Flask, announce: Mock, path: str
) -> None:
    _sign_in(app, "ADMIN").get(path)

    announce.assert_called_once()


def test_a_head_request_never_announces(app: Flask, announce: Mock) -> None:
    client = _sign_in(app)

    assert client.head("/notifications/").status_code == 200
    assert client.head("/").status_code in (200, 302)

    announce.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/notifications/read-all", "/notifications/9/read", "/logout"]
)
def test_a_post_never_announces(app: Flask, announce: Mock, path: str) -> None:
    _sign_in(app).post(path)

    announce.assert_not_called()


def test_other_methods_never_announce(app: Flask, announce: Mock) -> None:
    client = _sign_in(app)

    client.options("/notifications/")
    client.put("/notifications/")
    client.delete("/notifications/")

    announce.assert_not_called()


def test_an_anonymous_visitor_never_announces(app: Flask, announce: Mock) -> None:
    client = app.test_client()

    client.get("/login")  # public
    client.get("/")  # public
    client.get("/notifications/")  # redirected to sign in

    announce.assert_not_called()


def test_a_request_the_gate_refused_never_announces(app: Flask, announce: Mock) -> None:
    response = _sign_in(app, "CUSTOMER").get("/audit/")

    assert response.status_code == 403
    announce.assert_not_called()


def test_a_route_that_does_not_exist_never_announces(
    app: Flask, announce: Mock
) -> None:
    assert _sign_in(app).get("/no-such-page").status_code == 404

    announce.assert_not_called()


def test_a_static_file_never_announces(app: Flask, announce: Mock) -> None:
    assert _sign_in(app).get("/static/css/app.css").status_code == 200

    announce.assert_not_called()


def test_it_runs_before_the_page_is_built_so_the_page_already_shows_the_news(
    app: Flask, announce: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    announce.side_effect = lambda connection: order.append("announce") or 1
    reads = Mock(side_effect=lambda *a, **k: order.append("read") or 0)
    monkeypatch.setattr(notifications, "count_notifications", reads)
    monkeypatch.setattr(notifications, "count_unread", Mock(return_value=0))
    monkeypatch.setattr(notifications, "list_notifications", Mock(return_value=[]))

    _sign_in(app).get("/notifications/")

    assert order == ["announce", "read"]


# ---------- when it fails ----------


def test_a_failed_announcement_is_logged_and_the_page_is_still_served(
    app: Flask, announce: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    announce.side_effect = psycopg.OperationalError("connection lost")

    with caplog.at_level(logging.WARNING):
        page = _sign_in(app).get("/notifications/")

    assert page.status_code == 200
    assert "experiment_end_announcement_failed reason=OperationalError" in caplog.text
    assert "connection lost" not in caplog.text


def test_a_defect_that_is_not_a_database_error_is_not_swallowed(
    app: Flask, announce: Mock
) -> None:
    announce.side_effect = RuntimeError("a bug")

    assert _sign_in(app).get("/notifications/").status_code == 500


# ---------- the service ----------


@pytest.fixture
def statement(monkeypatch: pytest.MonkeyPatch) -> Mock:
    fake = Mock(return_value=(2, 8))
    monkeypatch.setattr(notifications, "announce_ended_experiments", fake)
    return fake


def test_the_announcer_addresses_the_holders_of_campaign_read_over_fourteen_days(
    statement: Mock,
) -> None:
    connection = MagicMock()

    announced = announce_experiment_ends(connection)

    assert announced == 2
    statement.assert_called_once_with(
        connection,
        role_codes=["ADMIN", "ANALYST", "AUDITOR", "MARKETING"],
        lookback_days=14,
    )
    assert EXPERIMENT_END_LOOKBACK_DAYS == 14
    connection.commit.assert_called_once_with()


def test_a_quiet_read_commits_and_logs_nothing(
    statement: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    statement.return_value = (0, 0)

    with caplog.at_level(logging.INFO):
        announced = announce_experiment_ends(MagicMock())

    assert announced == 0
    assert "notification_written" not in caplog.text


def test_an_announcement_is_logged_with_counts_and_no_names(
    statement: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        announce_experiment_ends(MagicMock())

    assert (
        "notification_written kind=EXPERIMENT_ENDED experiments=2 recipients=8"
        in caplog.text
    )


def test_a_failed_announcement_rolls_back_so_the_next_read_tries_again(
    statement: Mock,
) -> None:
    statement.side_effect = psycopg.OperationalError("down")
    connection = MagicMock()

    with pytest.raises(psycopg.OperationalError):
        announce_experiment_ends(connection)

    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


# ---------- the statement ----------


def test_the_statement_states_the_rules_of_rn_64() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (1, 4)

    result = notifications_db.announce_ended_experiments(
        connection, role_codes=["ADMIN"], lookback_days=14
    )

    sql, parameters = cursor.execute.call_args.args
    assert result == (1, 4)
    assert parameters == (14, ["ADMIN"])
    assert "e.ends_on < CURRENT_DATE" in sql  # ended means before today
    assert "e.ends_on >= CURRENT_DATE - %s::int" in sql  # the lookback
    assert "ORDER BY ends_on, experiment_id" in sql  # oldest end gets the lowest id
    assert "NOT EXISTS" in sql  # idempotent
    assert "ON CONFLICT (experiment_id) DO NOTHING" in sql  # conflict-safe
    assert "FROM announced AS a" in sql  # recipients only of what was inserted
    assert "u.is_active" in sql
    connection.commit.assert_not_called()
