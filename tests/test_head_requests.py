"""A HEAD request takes the read path of a view, never its write path.

Flask answers HEAD on every GET route, and `request.method` is then "HEAD". A
view that tested only for "GET" sent HEAD down its POST branch: `HEAD /login`
was a failed sign-in, logged as one and answered with 401.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from web.app import create_app
from web.config import Config

WEB = Path(__file__).resolve().parents[1] / "web"


@pytest.fixture
def app() -> Flask:
    return create_app(
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


def test_head_on_the_sign_in_page_does_not_attempt_a_sign_in(
    app: Flask, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("HEAD attempted a sign-in")

    monkeypatch.setattr("web.routes.auth.authenticate", refuse)

    with caplog.at_level(logging.INFO):
        response = app.test_client().head("/login")

    assert response.status_code == 200
    assert "login_refused" not in caplog.text


def test_no_view_treats_only_get_as_a_read() -> None:
    offenders = [
        str(path.relative_to(WEB))
        for path in WEB.rglob("*.py")
        if 'request.method == "GET"' in path.read_text()
    ]

    assert offenders == []
