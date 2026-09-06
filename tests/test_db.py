"""PostgreSQL uses DATABASE_URL and follows the Flask context lifecycle."""

from pathlib import Path
from unittest.mock import Mock

import pytest

import web.db as database
from web.app import create_app
from web.config import Config, ConfigurationError, load_dotenv_file


def _config() -> Config:
    return Config(
        secret_key="test",
        environment="testing",
        port=5000,
        log_level="INFO",
        database_url="configured-by-test",
    )


def test_application_start_uses_database_url_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected_url = "configured-by-dotenv"
    connection = Mock()
    connector = Mock(return_value=connection)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(f"DATABASE_URL={expected_url}\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def load_test_environment() -> None:
        load_dotenv_file(dotenv_path)

    monkeypatch.setattr("web.app.load_dotenv_file", load_test_environment)

    create_app(database_connector=connector)

    connector.assert_called_once_with(expected_url)
    connection.close.assert_called_once_with()


def test_application_start_fails_clearly_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = Mock()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("web.app.load_dotenv_file", lambda: None)

    with pytest.raises(ConfigurationError, match=r"DATABASE_URL.*\.env"):
        create_app(database_connector=connector)

    connector.assert_not_called()


def test_connect_delegates_to_psycopg() -> None:
    expected_connection = Mock()
    psycopg_connect = Mock(return_value=expected_connection)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(database.psycopg, "connect", psycopg_connect)
        connection = database.connect("configured-by-test")

    assert connection is expected_connection
    psycopg_connect.assert_called_once_with("configured-by-test")


def test_connection_is_reused_and_closed_with_the_application_context() -> None:
    startup_connection = Mock()
    request_connection = Mock()
    request_connection.closed = False
    connector = Mock(side_effect=[startup_connection, request_connection])
    app = create_app(_config(), database_connector=connector)

    with app.app_context():
        first = database.get_connection()
        second = database.get_connection()
        assert first is second

    startup_connection.close.assert_called_once_with()
    request_connection.close.assert_called_once_with()
    assert connector.call_count == 2


def test_application_python_source_has_no_embedded_connection_string() -> None:
    sources = Path("web").rglob("*.py")

    assert all("postgresql://" not in source.read_text() for source in sources)
