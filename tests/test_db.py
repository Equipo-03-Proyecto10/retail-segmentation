"""PostgreSQL uses DATABASE_URL and follows the Flask context lifecycle."""

from pathlib import Path
from unittest.mock import MagicMock, Mock

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
        session_cookie_secure=False,
        trusted_proxy_hops=0,
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
    # MagicMock, not Mock: opening a connection now runs the registered
    # initializer, which uses `connection.cursor()` as a context manager.
    request_connection = MagicMock()
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


def test_a_new_connection_is_told_who_is_acting() -> None:
    """The audit triggers read `mosaiq.user_id` off the connection (#69)."""
    connection = MagicMock()
    connection.closed = False
    app = create_app(
        _config(),
        database_connector=Mock(side_effect=[Mock(), connection]),
    )

    with app.app_context():
        database.get_connection()

    cursor = connection.cursor.return_value.__enter__.return_value
    statement, parameters = cursor.execute.call_args.args

    assert "set_config" in statement
    # Outside a request there is no session, so the actor is genuinely unknown
    # and the trigger's NULLIF turns the empty string into a NULL actor.
    assert parameters == ("mosaiq.user_id", "")


def test_the_initializer_runs_once_per_connection_rather_than_per_call() -> None:
    connection = MagicMock()
    connection.closed = False
    app = create_app(
        _config(),
        database_connector=Mock(side_effect=[Mock(), connection]),
    )

    with app.app_context():
        database.get_connection()
        database.get_connection()

    assert connection.cursor.call_count == 1


def test_application_python_source_has_no_embedded_connection_string() -> None:
    sources = Path("web").rglob("*.py")

    assert all("postgresql://" not in source.read_text() for source in sources)
