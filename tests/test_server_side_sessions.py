"""Server-side sessions (ADR-0022, #251): RF-02 and RF-09.

The cookie is only a pointer to an `app_session` row. These tests run the real
resolution path (`real_sessions`), with the database answer stubbed at
`web.services.auth.load_principal`, and check what the gate does with it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock
from uuid import UUID

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.sessions import SessionPrincipal
from web.services import auth

pytestmark = pytest.mark.real_sessions

_SID = "5e551011-0000-4000-8000-000000000001"
_USER = UUID("11111111-1111-1111-1111-000000000002")


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


def _cookie_says(client: FlaskClient, role_code: str, sid: str | None = _SID) -> None:
    with client.session_transaction() as flask_session:
        if sid is not None:
            flask_session["sid"] = sid
        flask_session["user_id"] = str(_USER)
        flask_session["role_code"] = role_code
        flask_session["name"] = "Demo User 2"


def _database_says(monkeypatch, principal: SessionPrincipal | None) -> Mock:
    lookup = Mock(return_value=principal)
    monkeypatch.setattr(auth, "load_principal", lookup)
    return lookup


def test_a_revoked_or_deactivated_session_is_signed_out(app, monkeypatch) -> None:
    """RF-02 and RF-09: the cookie still says ADMIN, the database says the
    session is gone (revoked, or its user deactivated)."""
    _database_says(monkeypatch, None)
    client = app.test_client()
    _cookie_says(client, "ADMIN")

    response = client.get("/admin/users")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    with client.session_transaction() as flask_session:
        assert "user_id" not in flask_session


def test_a_cookie_from_before_server_side_sessions_is_signed_out(
    app, monkeypatch
) -> None:
    """No sid: the cookie was issued before ADR-0022. Nothing to look up."""
    lookup = _database_says(monkeypatch, None)
    client = app.test_client()
    _cookie_says(client, "ADMIN", sid=None)

    assert client.get("/admin/users").status_code == 302
    lookup.assert_not_called()


def test_the_database_role_wins_over_the_cookie(app, monkeypatch) -> None:
    """A role change applies on the next request, not the next sign-in."""
    _database_says(monkeypatch, SessionPrincipal(_USER, 2, "ANALYST", "Demo User 2"))
    client = app.test_client()
    _cookie_says(client, "ADMIN")

    assert client.get("/admin/users/new").status_code == 403
    with client.session_transaction() as flask_session:
        assert flask_session["role_code"] == "ANALYST"


def test_a_live_session_reaches_what_its_role_allows(app, monkeypatch) -> None:
    lookup = _database_says(
        monkeypatch, SessionPrincipal(_USER, 2, "ANALYST", "Demo User 2")
    )
    monkeypatch.setattr("web.routes.run_history.list_runs", lambda *a, **k: ([], 0))
    client = app.test_client()
    _cookie_says(client, "ANALYST")

    assert client.get("/run-history/").status_code == 200
    assert lookup.call_args.args[1] == UUID(_SID)


def test_signing_out_revokes_the_session_server_side(app, monkeypatch) -> None:
    _database_says(monkeypatch, SessionPrincipal(_USER, 2, "ANALYST", "Demo User 2"))
    revoke = Mock()
    monkeypatch.setattr(auth, "revoke_session", revoke)
    client = app.test_client()
    _cookie_says(client, "ANALYST")

    response = client.post("/logout")

    assert response.status_code == 302
    revoke.assert_called_once()
    assert revoke.call_args.args[1] == UUID(_SID)
    with client.session_transaction() as flask_session:
        assert "sid" not in flask_session and "user_id" not in flask_session


def test_signing_in_opens_a_session_and_keeps_only_its_id(app, monkeypatch) -> None:
    user = Mock(user_id=_USER, role_id=2, role_code="ANALYST")
    user.name = "Demo User 2"
    monkeypatch.setattr(
        "web.routes.auth.authenticate", lambda *a: Mock(success=True, user=user)
    )
    monkeypatch.setattr(auth, "open_session", Mock(return_value=UUID(_SID)))
    client = app.test_client()

    response = client.post(
        "/login", data={"email": "user2@mosaiq-demo.com", "password": "x"}
    )

    assert response.status_code == 302
    with client.session_transaction() as flask_session:
        assert flask_session["sid"] == _SID


@pytest.mark.parametrize("sid", [None, "", "not-a-uuid"])
def test_an_unusable_session_id_resolves_to_nobody(sid) -> None:
    connection = MagicMock()

    assert auth.current_principal(connection, sid) is None
    connection.cursor.assert_not_called()


def test_the_lookup_requires_an_open_session_and_an_active_user() -> None:
    from web.db.sessions import load_principal

    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None

    assert load_principal(connection, UUID(_SID)) is None
    sql = cursor.execute.call_args.args[0]
    assert "s.revoked_at IS NULL" in sql and "u.is_active" in sql
