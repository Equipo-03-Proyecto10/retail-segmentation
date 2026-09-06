"""Configuration is read from the environment, with documented defaults."""

from web.config import (
    DEFAULT_ENVIRONMENT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_PORT,
    DEFAULT_SECRET_KEY,
    Config,
)


def test_reads_every_value_from_the_environment() -> None:
    config = Config.from_env(
        {
            "FLASK_SECRET_KEY": "set-by-the-environment",
            "FLASK_ENV": "production",
            "PORT": "8080",
            "LOG_LEVEL": "WARNING",
        }
    )

    assert config.secret_key == "set-by-the-environment"
    assert config.environment == "production"
    assert config.port == 8080
    assert config.log_level == "WARNING"


def test_falls_back_to_the_defaults_documented_in_env_example() -> None:
    config = Config.from_env({})

    assert config.secret_key == DEFAULT_SECRET_KEY
    assert config.environment == DEFAULT_ENVIRONMENT
    assert config.port == DEFAULT_PORT
    assert config.log_level == DEFAULT_LOG_LEVEL
