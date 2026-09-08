"""The application starts and serves a page."""

import mimetypes
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


def test_the_landing_page_uses_the_supplied_mosaic_imagery(
    client: FlaskClient,
) -> None:
    body = client.get("/").get_data(as_text=True)

    for image in (
        "mosaic-seasons-panel.png",
        "mosaic-oceanus-head.png",
        "mosaic-helmet.png",
    ):
        assert f"/static/images/mosaics/{image}" in body
    assert "Sign in to MOSAIQ" in body


def test_the_browser_receives_html(client: FlaskClient) -> None:
    """C-1 and C-2: the exchange format is HTML, never JSON or XML."""
    response = client.get("/")

    assert response.mimetype == "text/html"


def test_web_fonts_keep_their_type_on_a_host_that_does_not_know_them(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vendored IBM Plex files go out as fonts, not as a byte stream.

    Flask serves web/static through `mimetypes`, which reads the host's mime
    database and then the standard library's map. The instance is Python 3.12
    on CentOS Stream: no /etc/mime.types, and no `.woff2` in the stdlib map, so
    the fonts were served as `application/octet-stream`. A developer on a newer
    Python cannot see it. Stripping the entry here reproduces the instance.
    """
    stripped = mimetypes.MimeTypes()
    for suffix in (".woff2", ".woff"):
        stripped.types_map[True].pop(suffix, None)
        stripped.types_map[False].pop(suffix, None)
    monkeypatch.setattr(mimetypes, "_db", stripped)
    assert mimetypes.guess_type("a.woff2") == (None, None), "the instance's state"

    application = create_app(
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
    response = application.test_client().get(
        "/static/css/mosaiq/fonts/ibm-plex-sans-variable-latin.woff2"
    )

    assert response.status_code == 200
    assert response.mimetype == "font/woff2"
