"""Configuration, read from the environment.

Nothing here defaults to a secret, and nothing here reads a variable that is not
documented in `.env.example`. `.env` is loaded only as a fallback for local
development: a variable already present in the process environment always wins,
which is what lets the same code run unchanged under systemd on the instance.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_SECRET_KEY = "dev-only-not-a-secret"
DEFAULT_ENVIRONMENT = "development"
DEFAULT_PORT = 5000
DEFAULT_LOG_LEVEL = "INFO"


def load_dotenv_file() -> None:
    """Load `.env` for local development, without overriding real variables."""
    load_dotenv(override=False)


@dataclass(frozen=True)
class Config:
    """Everything the application reads from its environment.

    `DATABASE_URL` is deliberately absent. The connection is F3-02; a field
    added before there is code behind it would leave a reader unsure whether
    the application already talks to PostgreSQL. It does not yet.

    `log_level` names the threshold for the application logger. It is a field
    rather than a hard-coded `INFO` so the instance can raise it to `WARNING`
    without a code change once F6-02 puts the app under systemd.
    """

    secret_key: str
    environment: str
    port: int
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        """Read the configuration.

        `environ` defaults to the real environment; the tests pass their own so
        that a developer's local `.env` cannot change what they assert.
        """
        env = os.environ if environ is None else environ
        return cls(
            secret_key=env.get("FLASK_SECRET_KEY", DEFAULT_SECRET_KEY),
            environment=env.get("FLASK_ENV", DEFAULT_ENVIRONMENT),
            port=int(env.get("PORT", str(DEFAULT_PORT))),
            log_level=env.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
        )
