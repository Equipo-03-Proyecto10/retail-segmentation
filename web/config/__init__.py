"""Configuration, read from the environment.

Nothing here defaults to a secret, and nothing here reads a variable that is not
documented in `.env.example`. `.env` is loaded only as a fallback for local
development: a variable already present in the process environment always wins,
which is what lets the same code run unchanged under systemd on the instance.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import load_dotenv

DEFAULT_SECRET_KEY = "dev-only-not-a-secret"
DEFAULT_ENVIRONMENT = "development"
DEFAULT_PORT = 5000
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_SESSION_COOKIE_SECURE = False
DEFAULT_TRUSTED_PROXY_HOPS = 0

# Uploaded images (F3-07, #67). Matches the defaults documented in
# .env.example: 5 MB, and only the three types a browser and Pillow both
# agree are safe to call an image.
DEFAULT_UPLOAD_DIR = "web/uploads"
DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# Multipart headers and the remaining catalog fields, in addition to one image.
FORM_OVERHEAD_BYTES = 64 * 1024
DEFAULT_ALLOWED_IMAGE_TYPES = "image/jpeg,image/png,image/webp"

_TRUE_VALUES = {"1", "true", "yes", "on"}


class ConfigurationError(RuntimeError):
    """The process environment cannot produce a valid application config."""


def _bool_env(value: str | None, *, default: bool = False) -> bool:
    """Read a boolean from an environment string, tolerantly."""
    if value is None or not value.strip():
        return default
    return value.strip().lower() in _TRUE_VALUES


def _proxy_hops_env(value: str | None) -> int:
    """Read a non-negative proxy-hop count; anything unparseable means zero."""
    if value is None or not value.strip():
        return DEFAULT_TRUSTED_PROXY_HOPS
    try:
        return max(0, int(value.strip()))
    except ValueError:
        return DEFAULT_TRUSTED_PROXY_HOPS


def _int_env(value: str | None, *, default: int) -> int:
    """Read a positive integer from an environment string; fall back on
    anything unparseable or non-positive."""
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _set_env(value: str | None, *, default: str) -> frozenset[str]:
    """Read a comma-separated list from an environment string into a set."""
    raw = value if value and value.strip() else default
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _port_env(value: str | None) -> int:
    port = _int_env(value, default=DEFAULT_PORT)
    return port if port <= 65535 else DEFAULT_PORT


def load_dotenv_file(
    dotenv_path: str | os.PathLike[str] | None = None,
) -> None:
    """Load `.env` for local development, without overriding real variables."""
    load_dotenv(dotenv_path=dotenv_path, override=False)


@dataclass(frozen=True)
class Config:
    """Everything the application reads from its environment.

    `log_level` names the threshold for the application logger. It is a field
    rather than a hard-coded `INFO` so the instance can raise it to `WARNING`
    without a code change once F6-02 puts the app under systemd.

    `session_cookie_secure` is likewise a field: TLS is terminated by NGINX in
    front of the app (F6-01/F6-03), so the process itself sees plain HTTP and
    cannot infer whether the `Secure` flag should be set — the deployment says.

    `trusted_proxy_hops` is the number of reverse proxies in front of the app
    whose `X-Forwarded-*` headers may be believed. `0` when the app is reached
    directly; `1` behind the single NGINX (F6-01). Trusting those headers with
    nothing in front lets a client spoof its own address, so the default is `0`.

    `upload_dir`, `max_upload_bytes` and `allowed_image_types` govern F3-07:
    where an uploaded product image is written, the largest file accepted, and
    the MIME types treated as images at all.
    """

    secret_key: str = field(repr=False)
    environment: str
    port: int
    log_level: str
    session_cookie_secure: bool
    database_url: str = field(repr=False)
    # Last, and defaulted: `0` is the safe value the docstring above describes,
    # and it keeps every existing construction site valid. A caller that does
    # not know about reverse proxies gets the un-proxied behaviour rather than
    # a TypeError.
    trusted_proxy_hops: int = DEFAULT_TRUSTED_PROXY_HOPS
    upload_dir: str = DEFAULT_UPLOAD_DIR
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    allowed_image_types: frozenset[str] = field(
        default_factory=lambda: _set_env(None, default=DEFAULT_ALLOWED_IMAGE_TYPES)
    )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        """Read the configuration.

        `environ` defaults to the real environment; the tests pass their own so
        that a developer's local `.env` cannot change what they assert.
        """
        env = os.environ if environ is None else environ
        database_url = env.get("DATABASE_URL")
        if database_url is None or not database_url.strip():
            raise ConfigurationError(
                "Missing required environment variable DATABASE_URL. "
                "Set it in the process environment or a local .env file; "
                "see .env.example."
            )

        secret_key = env.get("FLASK_SECRET_KEY", DEFAULT_SECRET_KEY)
        environment = env.get("FLASK_ENV", DEFAULT_ENVIRONMENT)
        if environment != DEFAULT_ENVIRONMENT and secret_key == DEFAULT_SECRET_KEY:
            raise ConfigurationError(
                "FLASK_SECRET_KEY is still the development default while "
                f"FLASK_ENV is {environment!r}. Generate one with "
                'python -c "import secrets; print(secrets.token_hex(32))" '
                "and set it in the environment; see .env.example."
            )

        return cls(
            secret_key=secret_key,
            environment=environment,
            port=_port_env(env.get("PORT")),
            log_level=env.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
            session_cookie_secure=_bool_env(
                env.get("SESSION_COOKIE_SECURE"),
                default=DEFAULT_SESSION_COOKIE_SECURE,
            ),
            trusted_proxy_hops=_proxy_hops_env(env.get("TRUSTED_PROXY_HOPS")),
            database_url=database_url,
            upload_dir=env.get("UPLOAD_DIR", DEFAULT_UPLOAD_DIR),
            max_upload_bytes=_int_env(
                env.get("MAX_UPLOAD_BYTES"), default=DEFAULT_MAX_UPLOAD_BYTES
            ),
            allowed_image_types=_set_env(
                env.get("ALLOWED_IMAGE_TYPES"), default=DEFAULT_ALLOWED_IMAGE_TYPES
            ),
        )
