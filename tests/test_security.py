"""Session cookies carry the RNF-05 attributes (#72, F4-04)."""

from unittest.mock import Mock

import pytest
from flask import Flask, session
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.middleware import public


def _client(*, session_cookie_secure: bool) -> FlaskClient:
    app = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=session_cookie_secure,
            database_url="unused-by-test",
        ),
        database_connector=Mock(return_value=Mock()),
    )

    # Declared public because the authorization gate refuses anything
    # undeclared (#69); this test is about the cookie, not about access.
    @app.get("/opens-a-session")
    @public
    def opens_a_session() -> str:
        session["seen"] = True
        return "ok"

    return app.test_client()


def _session_cookie(response: object) -> str:
    for header in response.headers.getlist("Set-Cookie"):  # type: ignore[attr-defined]
        if header.startswith("session="):
            return header
    raise AssertionError("no session cookie was set")


def test_the_session_cookie_is_httponly_and_samesite_lax() -> None:
    cookie = _session_cookie(
        _client(session_cookie_secure=False).get("/opens-a-session")
    )

    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_secure_is_set_only_when_configured() -> None:
    without = _session_cookie(
        _client(session_cookie_secure=False).get("/opens-a-session")
    )
    with_tls = _session_cookie(
        _client(session_cookie_secure=True).get("/opens-a-session")
    )

    assert "Secure" not in without
    assert "Secure" in with_tls


@pytest.mark.parametrize("secure", [True, False])
def test_the_factory_applies_the_attributes_to_app_config(secure: bool) -> None:
    app: Flask = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=secure,
            database_url="unused-by-test",
        ),
        database_connector=Mock(return_value=Mock()),
    )

    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_SECURE"] is secure
