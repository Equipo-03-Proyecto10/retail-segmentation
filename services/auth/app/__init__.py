"""auth-service: RS256 sign-in, rotation, liveness and revocation (F14-02)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import redis
from flask import Flask, Response, request
from mosaiq_kit import ApiError, Kit, NoContent, Representation, configure_logging

from app.background import BackgroundWorkers
from app.keys import load_signing_material
from app.service import AuthService
from app.store import SessionStore, auth_unavailable

FAILURE_LIMIT = 5
FAILURE_WINDOW_SECONDS = 900
LOGIN_LIMIT = 30
LOGIN_WINDOW_SECONDS = 60


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer.") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


def _contracts_dir() -> Path:
    service_contracts = Path(__file__).resolve().parent.parent / "contracts"
    if (service_contracts / "auth/openapi.yaml").is_file():
        return service_contracts
    repository_contracts = Path(__file__).resolve().parents[3] / "contracts"
    if (repository_contracts / "auth/openapi.yaml").is_file():
        return repository_contracts
    raise RuntimeError("auth contracts are not staged in the service build context.")


def _state_redis(url: str, *, socket_timeout: float) -> redis.Redis:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = "[" + hostname + "]"
    netloc = hostname + ((":" + str(parsed.port)) if parsed.port else "")
    clean_url = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))
    return redis.Redis.from_url(
        clean_url,
        username="svc_auth",
        password=os.environ.get("REDIS_AUTH_PASSWORD"),
        socket_connect_timeout=0.2,
        socket_timeout=socket_timeout,
        health_check_interval=0,
        decode_responses=True,
    )


def _rate_limited(ttl: int) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        "Too many sign-in attempts; retry later.",
        headers={"Retry-After": str(max(1, ttl))},
    )


class SignInLimits:
    """The IP request counter and the independent email/IP failure lock."""

    def __init__(self, client: redis.Redis):
        self.redis = client

    @staticmethod
    def failure_key(email: str, ip: str) -> str:
        digest = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()
        return f"auth:fail:{digest}:{ip}"

    def before_attempt(self, email: str, ip: str) -> None:
        login_key = "auth:rl:login:" + ip
        failure_key = self.failure_key(email, ip)
        try:
            pipe = self.redis.pipeline(transaction=False)
            pipe.incr(login_key)
            pipe.expire(login_key, LOGIN_WINDOW_SECONDS, nx=True)
            pipe.get(failure_key)
            count, _expiry, failures = pipe.execute()
            if int(count) > LOGIN_LIMIT:
                ttl = self.redis.ttl(login_key)
                raise _rate_limited(ttl if ttl > 0 else LOGIN_WINDOW_SECONDS)
            if int(failures or 0) >= FAILURE_LIMIT:
                ttl = self.redis.ttl(failure_key)
                raise _rate_limited(ttl if ttl > 0 else FAILURE_WINDOW_SECONDS)
        except ApiError:
            raise
        except redis.RedisError:
            raise auth_unavailable() from None

    def failed(self, email: str, ip: str) -> None:
        key = self.failure_key(email, ip)
        try:
            pipe = self.redis.pipeline(transaction=False)
            pipe.incr(key)
            # The fifth failure starts a full fifteen-minute lock, rather than
            # inheriting the remaining time from the first failed attempt.
            pipe.expire(key, FAILURE_WINDOW_SECONDS)
            pipe.execute()
        except redis.RedisError:
            raise auth_unavailable() from None

    def succeeded(self, email: str, ip: str) -> None:
        try:
            self.redis.delete(self.failure_key(email, ip))
        except redis.RedisError:
            raise auth_unavailable() from None


def create_app(
    *,
    start_background: bool = True,
    state_redis: redis.Redis | None = None,
    stream_redis: redis.Redis | None = None,
) -> Flask:
    app = Flask(__name__, static_folder=None)
    configure_logging()

    issuer = os.environ.get("AUTH_ISSUER", "https://auth.invalid/")
    kid = os.environ.get("AUTH_KID", "")
    private_path = os.environ.get(
        "AUTH_PRIVATE_KEY_PATH", f"/run/mosaiq/auth-{kid}.pem"
    )
    jwks_path = os.environ.get("AUTH_JWKS_PATH", "/run/mosaiq/jwks.json")
    signing = load_signing_material(private_path, jwks_path, kid)
    access_ttl = _positive_int("AUTH_ACCESS_TOKEN_TTL_SECONDS", 600)
    session_ttl = _positive_int("AUTH_SESSION_ABSOLUTE_TTL_SECONDS", 43_200)
    if access_ttl != 600:
        raise RuntimeError("AUTH_ACCESS_TOKEN_TTL_SECONDS must be 600 for auth v1.")

    holder: dict[str, Kit] = {}

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            row = connection.execute(
                "SELECT to_regclass(%s) IS NOT NULL",
                ("api_users.credential_v1",),
            ).fetchone()
        return bool(row and row[0])

    kit = Kit(
        app,
        service="auth",
        suffix="auth",
        contracts_dir=_contracts_dir(),
        ready_checks=[schema_ready],
    )
    # auth has two service-specific limiters. The kit's generic key would not
    # represent either contract and would also count refresh requests.
    kit.rate_limit_enabled = False
    holder["kit"] = kit

    redis_url = os.environ.get("REDIS_STATE_URL", "redis://redis-state:6379/0")
    durable_client = state_redis or _state_redis(redis_url, socket_timeout=2)
    store = SessionStore(durable_client)
    limits = SignInLimits(durable_client)
    service = AuthService(
        database=kit.db,
        store=store,
        signing=signing,
        issuer=issuer,
        access_ttl=access_ttl,
        session_ttl=session_ttl,
    )
    if state_redis is not None:
        kit.auth.redis = state_redis

    app.extensions["mosaiq_kit"] = kit
    app.extensions["auth_service"] = service
    app.extensions["auth_store"] = store

    @kit.operation("createSession")
    def create_session() -> Representation:
        email = kit.body["email"].lower()
        ip = request.headers.get("X-Real-IP") or "unknown"
        limits.before_attempt(email, ip)
        try:
            result = service.sign_in(email, kit.body["password"])
        except ApiError as error:
            if error.code == "invalid_credentials":
                limits.failed(email, ip)
            raise
        limits.succeeded(email, ip)
        return Representation("TokenResult", result, status=201)

    @kit.operation("refreshSession")
    def refresh_session() -> Representation:
        return Representation("TokenResult", service.refresh(kit.body["refresh_token"]))

    @kit.operation("getCurrentSession")
    def get_current_session() -> Representation:
        principal = kit.principal
        assert principal is not None
        return Representation(
            "SessionClaims",
            {
                "sub": principal["sub"],
                "sid": principal["sid"],
                "role": principal["role"],
                "issued_at": datetime.fromtimestamp(int(principal["iat"]), UTC),
                "expires_at": datetime.fromtimestamp(int(principal["exp"]), UTC),
            },
        )

    @kit.operation("deleteCurrentSession")
    def delete_current_session() -> NoContent:
        principal = kit.principal
        assert principal is not None
        store.revoke_session(str(principal["sid"]))
        return NoContent()

    @app.route("/v1/jwks.json", methods=["GET"], provide_automatic_options=False)
    def get_jwks() -> Response:
        accept = request.headers.get("Accept")
        if accept and accept.strip() != "*/*":
            accepted = request.accept_mimetypes.best_match(
                ["application/jwk-set+json", "application/json"]
            )
            if accepted is None or request.accept_mimetypes[accepted] == 0:
                raise ApiError(
                    406,
                    "not_acceptable",
                    "The requested response format is unsupported.",
                )
        return Response(
            json.dumps(signing.public_jwks, separators=(",", ":")),
            content_type="application/jwk-set+json",
        )

    if start_background:
        if os.environ.get("MOSAIQ_SERVICE_NAME") != "auth":
            raise RuntimeError("MOSAIQ_SERVICE_NAME must be auth.")
        consumer_client = stream_redis or _state_redis(redis_url, socket_timeout=6)
        workers = BackgroundWorkers(service, consumer_client)
        workers.start()
        app.extensions["auth_background"] = workers

    return app


__all__ = ["create_app"]
