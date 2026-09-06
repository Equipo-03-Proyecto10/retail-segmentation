"""Configuration is read from the environment, with documented defaults."""

import pytest

from web.config import (
    DEFAULT_ENVIRONMENT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_PORT,
    DEFAULT_SECRET_KEY,
    DEFAULT_SESSION_COOKIE_SECURE,
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
            "TRUSTED_PROXY_HOPS": "2",
            "DATABASE_URL": "configured-by-the-environment",
        }
    )

    assert config.secret_key == "set-by-the-environment"
    assert config.environment == "production"
    assert config.port == 8080
    assert config.log_level == "WARNING"
    assert config.session_cookie_secure is True
    assert config.trusted_proxy_hops == 2
    assert config.database_url == "configured-by-the-environment"


def test_falls_back_to_documented_defaults_for_optional_values() -> None:
    config = Config.from_env({"DATABASE_URL": "configured-by-the-environment"})

    assert config.secret_key == DEFAULT_SECRET_KEY
    assert config.environment == DEFAULT_ENVIRONMENT
    assert config.port == DEFAULT_PORT
    assert config.log_level == DEFAULT_LOG_LEVEL
    assert config.session_cookie_secure is DEFAULT_SESSION_COOKIE_SECURE
    assert config.trusted_proxy_hops == DEFAULT_TRUSTED_PROXY_HOPS


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
