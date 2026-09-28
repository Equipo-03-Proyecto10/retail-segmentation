"""Pages must not be kept by the browser after sign-out (#291).

An after_request hook sets `Cache-Control: no-store` on everything except
/static. These tests use pages reachable without a database. The hook does not
look at who is signed in, so a signed-in page gets the same headers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from web.app import create_app
from web.config import Config


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


@pytest.mark.parametrize("path", ["/login", "/catalog/", "/no-such-page"])
def test_dynamic_responses_are_never_stored(app: Flask, path: str) -> None:
    response = app.test_client().get(path)

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_static_files_stay_cacheable(app: Flask) -> None:
    response = app.test_client().get("/static/css/app.css")

    assert response.status_code == 200
    assert "no-store" not in response.headers.get("Cache-Control", "")
