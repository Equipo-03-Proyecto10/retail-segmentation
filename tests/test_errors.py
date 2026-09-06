"""Failures reach the visitor as a plain page and the log as one line (#73)."""

import logging
import re
from unittest.mock import Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.middleware import public

_REFERENCE = re.compile(r"[0-9a-f]{8}")


def _app() -> Flask:
    app = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            trusted_proxy_hops=0,
            database_url="unused-by-test",
        ),
        database_connector=Mock(return_value=Mock()),
    )
    # Return the 500 response to the client instead of re-raising, which is what
    # a real request against the deployed app would see.
    app.config["PROPAGATE_EXCEPTIONS"] = False

    # The authorization gate refuses anything undeclared (#69), including a
    # route a test adds. This one is about error handling, not access.
    @app.get("/boom")
    @public
    def boom() -> str:
        raise RuntimeError("deliberate failure raised by the test")

    return app


@pytest.fixture
def client() -> FlaskClient:
    return _app().test_client()


def test_unknown_url_returns_the_branded_page_not_the_default(
    client: FlaskClient,
) -> None:
    response = client.get("/nope")
    body = response.get_data(as_text=True)

    assert response.status_code == 404
    assert "MOSAIQ" in body
    assert "Werkzeug" not in body


def test_unhandled_exception_returns_a_controlled_500(client: FlaskClient) -> None:
    response = client.get("/boom")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Traceback" not in body
    assert "RuntimeError" not in body


def test_the_failure_is_logged_with_request_context(
    client: FlaskClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        client.get("/boom")

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "the unhandled exception was not logged at ERROR"
    message = errors[0].getMessage()
    assert "GET" in message
    assert "/boom" in message


def test_the_error_page_shows_a_reference_shared_with_the_log(
    client: FlaskClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        body = client.get("/boom").get_data(as_text=True)

    on_page = _REFERENCE.search(body)
    assert on_page, "the error page carries no reference id"
    assert on_page.group(0) in caplog.text
