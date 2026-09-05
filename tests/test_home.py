"""The application starts and serves a page."""

import pytest
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config


@pytest.fixture
def client() -> FlaskClient:
    app = create_app(Config(secret_key="test", environment="testing", port=5000))
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
