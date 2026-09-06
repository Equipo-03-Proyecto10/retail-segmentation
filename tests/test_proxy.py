"""The app believes X-Forwarded-* only when a proxy is declared (#77, F6-01)."""

from unittest.mock import Mock

import pytest
from flask import Flask, request
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config

_FORWARDED = {"X-Forwarded-For": "9.9.9.9", "X-Forwarded-Proto": "https"}


def _client(*, trusted_proxy_hops: int) -> FlaskClient:
    app: Flask = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            trusted_proxy_hops=trusted_proxy_hops,
            database_url="unused-by-test",
        ),
        database_connector=Mock(return_value=Mock()),
    )

    @app.get("/whereami")
    def whereami() -> str:
        return f"{request.remote_addr} {request.scheme}"

    return app.test_client()


def test_forwarded_headers_are_ignored_with_no_proxy() -> None:
    body = _client(trusted_proxy_hops=0).get("/whereami", headers=_FORWARDED).text

    assert "9.9.9.9" not in body
    assert body.endswith("http")


def test_forwarded_headers_are_trusted_behind_one_proxy() -> None:
    body = _client(trusted_proxy_hops=1).get("/whereami", headers=_FORWARDED).text

    assert body == "9.9.9.9 https"


@pytest.mark.parametrize("hops", [0, 1])
def test_a_request_without_forwarded_headers_is_unaffected(hops: int) -> None:
    body = _client(trusted_proxy_hops=hops).get("/whereami").text

    assert body.endswith("http")
    assert "9.9.9.9" not in body
