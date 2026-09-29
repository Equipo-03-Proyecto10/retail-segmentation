"""CSRF defence for every state-changing request (#294, ADR-0025).

Every test here opts out of the suite-wide stub (tests/conftest.py) with
`pytestmark`, so these exercise the real check `web.middleware.csrf.verify_csrf`
performs, not the no-op every other test runs against.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.services import auth

pytestmark = pytest.mark.real_csrf

_TOKEN = "the-session-token"


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


def _with_session_token(client: FlaskClient, token: str = _TOKEN) -> None:
    with client.session_transaction() as flask_session:
        flask_session["csrf_token"] = token


def _allow_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make POST /login succeed against a mocked connection, the same way
    tests/test_event_logging.py does, so the "correct token" cases can prove
    the request actually reached the view rather than just avoiding a 403."""
    user = Mock(user_id="user-1", role_id=2, role_code="ANALYST", name="User")
    user.name = "User"
    monkeypatch.setattr(auth, "get_user_by_email", Mock(return_value=user))
    monkeypatch.setattr(auth, "_hasher", Mock(verify=Mock(return_value=True)))


_CREDENTIALS = {"email": "user@example.com", "password": "correct horse"}


def test_a_post_with_no_token_at_all_is_refused(app: Flask) -> None:
    client = app.test_client()

    response = client.post("/login", data=_CREDENTIALS)

    assert response.status_code == 403


def test_a_nul_does_not_hide_a_missing_csrf_token(app: Flask) -> None:
    response = app.test_client().post(
        "/login", data={"email": "a\x00b@example.com", "password": "password"}
    )

    assert response.status_code == 403


def test_a_nul_with_a_valid_csrf_token_is_then_refused_as_input(app: Flask) -> None:
    client = app.test_client()
    _with_session_token(client)

    response = client.post(
        "/login",
        data={
            "email": "a\x00b@example.com",
            "password": "password",
            "csrf_token": _TOKEN,
        },
    )

    assert response.status_code == 400


def test_a_post_with_a_token_but_no_session_token_is_refused(app: Flask) -> None:
    client = app.test_client()

    response = client.post("/login", data=_CREDENTIALS | {"csrf_token": _TOKEN})

    assert response.status_code == 403


def test_a_post_with_the_wrong_token_is_refused(app: Flask) -> None:
    client = app.test_client()
    _with_session_token(client)

    response = client.post(
        "/login", data=_CREDENTIALS | {"csrf_token": "not-the-right-token"}
    )

    assert response.status_code == 403


def test_a_post_with_the_correct_token_reaches_the_view(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_login(monkeypatch)
    client = app.test_client()
    _with_session_token(client)

    response = client.post("/login", data=_CREDENTIALS | {"csrf_token": _TOKEN})

    assert response.status_code == 302


def test_a_mismatched_origin_is_refused_even_with_the_correct_token(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_login(monkeypatch)
    client = app.test_client()
    _with_session_token(client)

    response = client.post(
        "/login",
        data=_CREDENTIALS | {"csrf_token": _TOKEN},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403


def test_a_matching_origin_is_accepted(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_login(monkeypatch)
    client = app.test_client()
    _with_session_token(client)

    response = client.post(
        "/login",
        data=_CREDENTIALS | {"csrf_token": _TOKEN},
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 302


def test_a_missing_origin_header_does_not_refuse_by_itself(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older browsers send no Origin on a same-site form POST; the token is
    what an unsafe request still cannot skip, not this header."""
    _allow_login(monkeypatch)
    client = app.test_client()
    _with_session_token(client)

    response = client.post("/login", data=_CREDENTIALS | {"csrf_token": _TOKEN})

    assert response.status_code == 302


def test_get_requests_are_never_checked(app: Flask) -> None:
    client = app.test_client()

    assert client.get("/login").status_code == 200


def test_the_login_page_renders_a_token_matching_the_session(app: Flask) -> None:
    client = app.test_client()

    body = client.get("/login").get_data(as_text=True)

    with client.session_transaction() as flask_session:
        token = flask_session["csrf_token"]
    assert f'name="csrf_token" value="{token}"' in body


def test_the_token_is_stable_within_one_session(app: Flask) -> None:
    client = app.test_client()

    client.get("/login")
    with client.session_transaction() as flask_session:
        first = flask_session["csrf_token"]

    client.get("/login")
    with client.session_transaction() as flask_session:
        second = flask_session["csrf_token"]

    assert first == second


_ROOT = Path(__file__).resolve().parents[1]


def test_every_post_form_sends_the_token() -> None:
    """Every other test stubs the check off, so a form added without its token
    would pass the suite and be refused in production."""
    missing = [
        f"{path.relative_to(_ROOT)}: {form[:60]}"
        for path in (_ROOT / "web/templates").rglob("*.html")
        for form in re.findall(r"<form\b.*?</form>", path.read_text(), re.S | re.I)
        if re.search(r"method=[\"\']post", form, re.I)
        and 'name="csrf_token" value="{{ csrf_token() }}"' not in form
    ]

    assert missing == []


def test_a_forwarded_host_with_a_port_matches_an_origin_with_that_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behind the compose overlay (published on 8080) the browser sends
    Origin: http://127.0.0.1:8080; X-Forwarded-Host must carry the port."""
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
            trusted_proxy_hops=1,
        ),
        database_connector=Mock(return_value=MagicMock()),
    )
    _allow_login(monkeypatch)
    client = application.test_client()
    _with_session_token(client)

    response = client.post(
        "/login",
        data={**_CREDENTIALS, "csrf_token": _TOKEN},
        headers={
            "X-Forwarded-For": "203.0.113.9",
            "X-Forwarded-Proto": "http",
            "X-Forwarded-Host": "127.0.0.1:8080",
            "Origin": "http://127.0.0.1:8080",
        },
    )

    assert response.status_code == 302


def test_the_compose_overlay_forwards_the_host_with_its_port() -> None:
    conf = (_ROOT / "deploy/nginx/mosaiq.compose.conf").read_text()

    assert re.search(r"proxy_set_header\s+X-Forwarded-Host\s+\$http_host;", conf)
