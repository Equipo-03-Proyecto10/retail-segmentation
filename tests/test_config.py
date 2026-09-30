"""Configuration is read from the environment, with documented defaults."""

import pytest

from web.config import (
    DEFAULT_DATA_IS_SYNTHETIC,
    DEFAULT_ENVIRONMENT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGIN_THROTTLE_MAX_ATTEMPTS,
    DEFAULT_LOGIN_THROTTLE_WINDOW_SECONDS,
    DEFAULT_PORT,
    DEFAULT_SECRET_KEY,
    DEFAULT_SESSION_ABSOLUTE_TIMEOUT_SECONDS,
    DEFAULT_SESSION_COOKIE_SECURE,
    DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
    DEFAULT_TRUSTED_PROXY_HOPS,
    Config,
    ConfigurationError,
)


def test_reads_every_value_from_the_environment() -> None:
    config = Config.from_env(
        {
            "FLASK_SECRET_KEY": "set-by-the-environment",
            "FLASK_ENV": "production",
            "PORT": "8080",
            "LOG_LEVEL": "WARNING",
            "SESSION_COOKIE_SECURE": "true",
            "SESSION_IDLE_TIMEOUT_SECONDS": "900",
            "SESSION_ABSOLUTE_TIMEOUT_SECONDS": "14400",
            "TRUSTED_PROXY_HOPS": "2",
            "DATABASE_URL": "configured-by-the-environment",
            "DATA_IS_SYNTHETIC": "false",
            "LOGIN_THROTTLE_MAX_ATTEMPTS": "7",
            "LOGIN_THROTTLE_WINDOW_SECONDS": "120",
        }
    )

    assert config.secret_key == "set-by-the-environment"
    assert config.environment == "production"
    assert config.port == 8080
    assert config.log_level == "WARNING"
    assert config.session_cookie_secure is True
    assert config.session_idle_timeout_seconds == 900
    assert config.session_absolute_timeout_seconds == 14400
    assert config.trusted_proxy_hops == 2
    assert config.database_url == "configured-by-the-environment"
    assert config.data_is_synthetic is False
    assert config.login_throttle_max_attempts == 7
    assert config.login_throttle_window_seconds == 120


def test_falls_back_to_documented_defaults_for_optional_values() -> None:
    config = Config.from_env({"DATABASE_URL": "configured-by-the-environment"})

    assert config.secret_key == DEFAULT_SECRET_KEY
    assert config.environment == DEFAULT_ENVIRONMENT
    assert config.port == DEFAULT_PORT
    assert config.log_level == DEFAULT_LOG_LEVEL
    assert config.session_cookie_secure is DEFAULT_SESSION_COOKIE_SECURE
    assert config.session_idle_timeout_seconds == DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS
    assert (
        config.session_absolute_timeout_seconds
        == DEFAULT_SESSION_ABSOLUTE_TIMEOUT_SECONDS
    )
    assert config.data_is_synthetic is DEFAULT_DATA_IS_SYNTHETIC
    assert config.trusted_proxy_hops == DEFAULT_TRUSTED_PROXY_HOPS
    assert config.login_throttle_max_attempts == DEFAULT_LOGIN_THROTTLE_MAX_ATTEMPTS
    assert config.login_throttle_window_seconds == DEFAULT_LOGIN_THROTTLE_WINDOW_SECONDS


@pytest.mark.parametrize("database_url", [None, "", "   "])
def test_database_url_is_required(database_url: str | None) -> None:
    environment = {} if database_url is None else {"DATABASE_URL": database_url}

    with pytest.raises(ConfigurationError, match=r"DATABASE_URL.*\.env"):
        Config.from_env(environment)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("YES", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("", False),
        ("nonsense", False),
    ],
)
def test_session_cookie_secure_is_read_tolerantly(value: str, expected: bool) -> None:
    config = Config.from_env({"DATABASE_URL": "x", "SESSION_COOKIE_SECURE": value})

    assert config.session_cookie_secure is expected


@pytest.mark.parametrize("value", ["0", "-1", "nonsense", ""])
def test_session_timeouts_use_safe_defaults_when_non_positive(value: str) -> None:
    config = Config.from_env(
        {
            "DATABASE_URL": "x",
            "SESSION_IDLE_TIMEOUT_SECONDS": value,
            "SESSION_ABSOLUTE_TIMEOUT_SECONDS": value,
        }
    )

    assert config.session_idle_timeout_seconds == DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS
    assert (
        config.session_absolute_timeout_seconds
        == DEFAULT_SESSION_ABSOLUTE_TIMEOUT_SECONDS
    )


def test_the_default_secret_is_refused_outside_development() -> None:
    with pytest.raises(ConfigurationError, match=r"FLASK_SECRET_KEY"):
        Config.from_env({"DATABASE_URL": "x", "FLASK_ENV": "production"})


def test_a_real_secret_outside_development_is_accepted() -> None:
    config = Config.from_env(
        {
            "DATABASE_URL": "x",
            "FLASK_ENV": "production",
            "FLASK_SECRET_KEY": "a-real-generated-value",
        }
    )

    assert config.environment == "production"


def test_the_default_secret_is_allowed_in_development() -> None:
    config = Config.from_env({"DATABASE_URL": "x"})

    assert config.secret_key == DEFAULT_SECRET_KEY


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", 0), ("1", 1), ("3", 3), ("", 0), ("-2", 0), ("nonsense", 0)],
)
def test_trusted_proxy_hops_is_non_negative(value: str, expected: int) -> None:
    config = Config.from_env({"DATABASE_URL": "x", "TRUSTED_PROXY_HOPS": value})

    assert config.trusted_proxy_hops == expected


@pytest.mark.parametrize(
    "value", ["0", "-1", "nonsense", ""], ids=["zero", "negative", "text", "empty"]
)
def test_non_positive_login_throttle_values_use_safe_defaults(value: str) -> None:
    config = Config.from_env(
        {
            "DATABASE_URL": "x",
            "LOGIN_THROTTLE_MAX_ATTEMPTS": value,
            "LOGIN_THROTTLE_WINDOW_SECONDS": value,
        }
    )

    assert config.login_throttle_max_attempts == DEFAULT_LOGIN_THROTTLE_MAX_ATTEMPTS
    assert config.login_throttle_window_seconds == DEFAULT_LOGIN_THROTTLE_WINDOW_SECONDS
