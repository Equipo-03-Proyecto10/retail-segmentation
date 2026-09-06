"""Configuration is read from the environment, with documented defaults."""

import pytest

from web.config import (
    DEFAULT_ENVIRONMENT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_PORT,
    DEFAULT_SECRET_KEY,
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
            "DATABASE_URL": "configured-by-the-environment",
        }
    )

    assert config.secret_key == "set-by-the-environment"
    assert config.environment == "production"
    assert config.port == 8080
    assert config.log_level == "WARNING"
    assert config.database_url == "configured-by-the-environment"


def test_falls_back_to_documented_defaults_for_optional_values() -> None:
    config = Config.from_env({"DATABASE_URL": "configured-by-the-environment"})

    assert config.secret_key == DEFAULT_SECRET_KEY
    assert config.environment == DEFAULT_ENVIRONMENT
    assert config.port == DEFAULT_PORT
    assert config.log_level == DEFAULT_LOG_LEVEL


@pytest.mark.parametrize("database_url", [None, "", "   "])
def test_database_url_is_required(database_url: str | None) -> None:
    environment = {} if database_url is None else {"DATABASE_URL": database_url}

    with pytest.raises(ConfigurationError, match=r"DATABASE_URL.*\.env"):
        Config.from_env(environment)
