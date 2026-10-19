"""RS256 token verification and fail-closed session liveness."""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jwt
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from .types import ApiError


def _invalid_token() -> ApiError:
    return ApiError(401, "invalid_token", "The bearer token is invalid.")


class JwksStore:
    """A reloadable, public-key-only JWKS."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._keys: dict[str, jwt.PyJWK] = {}
        self._lock = threading.Lock()
        self._reload_requested = True
        self._unknown_reload_at: float | None = None
        try:
            signal.signal(signal.SIGHUP, self._handle_sighup)
        except (ValueError, AttributeError):
            pass

    def _handle_sighup(self, _signum: int, _frame: object) -> None:
        self._reload_requested = True

    def request_reload(self) -> None:
        """Mark the key set stale, matching the SIGHUP behavior."""

        self._reload_requested = True

    def _reload(self) -> None:
        document = json.loads(self.path.read_text(encoding="utf-8"))
        keys: dict[str, jwt.PyJWK] = {}
        for item in document.get("keys", []):
            kid = item.get("kid")
            if isinstance(kid, str):
                keys[kid] = jwt.PyJWK.from_dict(item)
        self._keys = keys
        self._reload_requested = False

    def key(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            reloaded = False
            if self._reload_requested:
                self._reload()
                reloaded = True
            key = self._keys.get(kid)
            if key is not None:
                return key
            now = time.monotonic()
            if reloaded:
                self._unknown_reload_at = now
            elif self._unknown_reload_at is None or now - self._unknown_reload_at >= 30:
                self._unknown_reload_at = now
                self._reload()
                key = self._keys.get(kid)
            if key is None:
                raise KeyError(kid)
            return key


def service_redis(url: str, suffix: str) -> redis.Redis:
    """Build the restricted redis-state connection used by a service."""

    password_name = "REDIS_" + suffix.upper() + "_PASSWORD"
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = "[" + hostname + "]"
    netloc = hostname + ((":" + str(parsed.port)) if parsed.port else "")
    credential_free_url = urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )
    return redis.Redis.from_url(
        credential_free_url,
        username="svc_" + suffix,
        password=os.environ.get(password_name),
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        retry=Retry(NoBackoff(), 0),
        retry_on_error=[],
        health_check_interval=0,
        decode_responses=True,
    )


class Authenticator:
    """Verify bearer tokens and require a live Redis session."""

    def __init__(self, suffix: str):
        self.issuer = os.environ.get("AUTH_ISSUER", "https://auth.invalid/")
        self.jwks = JwksStore(os.environ.get("AUTH_JWKS_PATH", "/run/mosaiq/jwks.json"))
        self.redis = service_redis(
            os.environ.get("REDIS_STATE_URL", "redis://redis-state:6379/0"), suffix
        )

    def verify(self, authorization: str | None) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise _invalid_token()
        token = authorization.removeprefix("Bearer ").strip()
        if not token or " " in token:
            raise _invalid_token()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise _invalid_token()
            key = self.jwks.key(header["kid"])
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience="mosaiq-api",
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub", "sid", "role"]},
            )
        except ApiError:
            raise
        except (jwt.PyJWTError, KeyError, OSError, ValueError, json.JSONDecodeError):
            raise _invalid_token() from None
        try:
            is_live = self.redis.exists("pub:auth:v1:live:" + str(claims["sid"]))
        except redis.RedisError:
            raise ApiError(
                503, "auth_unavailable", "Session state cannot be checked."
            ) from None
        if not is_live:
            raise ApiError(
                401, "session_revoked", "The token session is no longer live."
            )
        return claims
