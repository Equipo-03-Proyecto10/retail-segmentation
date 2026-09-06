"""The application starts and serves a page."""

from unittest.mock import Mock

import pytest
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config


@pytest.fixture
def client() -> FlaskClient:
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
    return app.test_client()


def test_the_landing_page_reaches_every_layer(client: FlaskClient) -> None:
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "MOSAIQ" in body
    assert "testing" in body


def test_the_browser_receives_html(client: FlaskClient) -> None:
    """C-1 and C-2: the exchange format is HTML, never JSON or XML."""
    response = client.get("/")

    assert response.mimetype == "text/html"
