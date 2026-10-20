"""Credential authentication, token claims and sign-in limits."""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt
import pytest
import redis

from app.passwords import DUMMY_HASH
from app.store import (
    EXPIRED_REFRESH_GRACE_SECONDS,
    password_fingerprint,
    refresh_fingerprint,
)
from tests.conftest import EMAIL, ISSUER, KID, PASSWORD, USER_ID, sign_in

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


def _code(response: Any) -> tuple[int, str | None]:
    return response.status_code, (response.get_json(silent=True) or {}).get("code")


def test_active_seeded_user_creates_exact_rs256_claims_and_redis_keys(
    client: Any, app: Any, redis_admin: redis.Redis
) -> None:
    response = sign_in(client)
    assert response.status_code == 201
    result = response.get_json()
    header = jwt.get_unverified_header(result["access_token"])
    assert header["alg"] == "RS256" and header["kid"] == KID
    signing = app.extensions["auth_service"].signing
    claims = jwt.decode(
        result["access_token"],
        signing.private_key.public_key(),
        algorithms=["RS256"],
        issuer=ISSUER,
        audience="mosaiq-api",
    )
    assert set(claims) == {"iss", "aud", "sub", "sid", "role", "jti", "iat", "exp"}
    assert claims["sub"] == USER_ID and claims["role"] == "ANALYST"
    assert claims["exp"] - claims["iat"] == 600

    sid = claims["sid"]
    stored = redis_admin.hgetall("auth:session:" + sid)
    assert set(stored) == {
        "user_id",
        "role",
        "expires_at",
        "password_fingerprint",
    }
    assert stored["user_id"] == USER_ID and stored["role"] == "ANALYST"
    credential = app.extensions["auth_service"].credential_by_user(USER_ID)
    assert stored["password_fingerprint"] == password_fingerprint(
        credential["password_hash"]
    )
    assert redis_admin.sismember("auth:user_sids:" + USER_ID, sid)
    token_hash = refresh_fingerprint(result["refresh_token"])
    assert redis_admin.hget("auth:refresh:" + token_hash, "state") == "current"
    assert redis_admin.sismember("auth:refresh_family:" + sid, token_hash)
    keys = (
        "auth:session:" + sid,
        "auth:user_sids:" + USER_ID,
        "pub:auth:v1:live:" + sid,
        "auth:session_users",
    )
    assert all(0 < redis_admin.ttl(key) <= 43_200 for key in keys)
    # The refresh entry and its family outlive the session by the grace that
    # lets a late refresh answer session_expired.
    for key in ("auth:refresh:" + token_hash, "auth:refresh_family:" + sid):
        assert 43_200 < redis_admin.ttl(key) <= 43_200 + EXPIRED_REFRESH_GRACE_SECONDS
    remaining = int(stored["expires_at"]) - int(time.time())
    assert abs(redis_admin.ttl("pub:auth:v1:live:" + sid) - remaining) <= 1


def test_wrong_unknown_and_inactive_accounts_share_invalid_credentials(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = {"X-Real-IP": "192.0.2.20", "X-Correlation-ID": "same-error"}
    wrong = client.post(
        "/v1/sessions",
        json={"email": EMAIL, "password": "wrong", "device_label": "pytest"},
        headers=headers,
    )
    unknown = client.post(
        "/v1/sessions",
        json={
            "email": "missing@example.test",
            "password": "wrong",
            "device_label": "pytest",
        },
        headers=headers | {"X-Real-IP": "192.0.2.21"},
    )
    service = app.extensions["auth_service"]
    original = service.credential_by_email
    active = original(EMAIL)
    monkeypatch.setattr(
        service,
        "credential_by_email",
        lambda _email: active | {"is_active": False},
    )
    inactive = client.post(
        "/v1/sessions",
        json={"email": EMAIL, "password": PASSWORD, "device_label": "pytest"},
        headers=headers | {"X-Real-IP": "192.0.2.22"},
    )
    assert (
        _code(wrong)
        == _code(unknown)
        == _code(inactive)
        == (
            401,
            "invalid_credentials",
        )
    )
    assert wrong.get_json() == unknown.get_json() == inactive.get_json()


def test_unknown_email_costs_exactly_one_dummy_argon2id_verify(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = app.extensions["auth_service"]
    monkeypatch.setattr(service, "credential_by_email", lambda _email: None)
    calls: list[tuple[str, str]] = []

    def verify(password_hash: str, password: str) -> bool:
        calls.append((password_hash, password))
        return False

    monkeypatch.setattr("app.service.verify_password", verify)
    with pytest.raises(Exception) as caught:
        service.sign_in("missing@example.test", "guess")
    assert getattr(caught.value, "code", None) == "invalid_credentials"
    assert calls == [(DUMMY_HASH, "guess")]


def test_credential_read_uses_only_parameterized_published_view(app: Any) -> None:
    service = app.extensions["auth_service"]

    class Connection:
        query = ""
        params: tuple[str, ...] = ()

        def execute(self, query: str, params: tuple[str, ...]) -> Connection:
            self.query, self.params = query, params
            return self

        @staticmethod
        def fetchone() -> None:
            return None

    connection = Connection()

    class Context:
        def __enter__(self) -> Connection:
            return connection

        def __exit__(self, *_args: object) -> None:
            return None

    class Database:
        @staticmethod
        def connection() -> Context:
            return Context()

    service.database = Database()
    assert service.credential_by_email("Mixed@Example.test") is None
    assert "FROM api_users.credential_v1" in connection.query
    assert "%s" in connection.query and connection.params == ("mixed@example.test",)
    assert "app_user" not in connection.query


def test_x_real_ip_alone_selects_the_login_counter(
    client: Any, redis_admin: redis.Redis
) -> None:
    body = {"email": EMAIL, "password": "wrong", "device_label": "pytest"}
    for forwarded in ("198.51.100.1", "203.0.113.9"):
        client.post(
            "/v1/sessions",
            json=body,
            headers={"X-Real-IP": "192.0.2.30", "X-Forwarded-For": forwarded},
        )
    assert redis_admin.get("auth:rl:login:192.0.2.30") == "2"
    assert redis_admin.get("auth:rl:login:198.51.100.1") is None
    assert redis_admin.get("auth:rl:login:203.0.113.9") is None


def test_five_failures_lock_only_the_email_ip_pair(
    client: Any, redis_admin: redis.Redis
) -> None:
    ip = "192.0.2.40"
    body = {"email": EMAIL, "password": "wrong", "device_label": "pytest"}
    for _ in range(5):
        assert _code(
            client.post("/v1/sessions", json=body, headers={"X-Real-IP": ip})
        ) == (
            401,
            "invalid_credentials",
        )
    locked = sign_in(client, ip=ip)
    assert _code(locked) == (429, "rate_limited")
    assert 0 < int(locked.headers["Retry-After"]) <= 900
    assert sign_in(client, ip="192.0.2.41").status_code == 201
    failure_keys = list(redis_admin.scan_iter(match="auth:fail:*:192.0.2.40"))
    assert len(failure_keys) == 1 and redis_admin.get(failure_keys[0]) == "5"
    assert 890 <= redis_admin.ttl(failure_keys[0]) <= 900


def test_at_most_two_argon2id_verifies_run_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import passwords

    active = 0
    maximum = 0
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    class Hasher:
        @staticmethod
        def verify(_password_hash: str, _password: str) -> bool:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.08)
            with lock:
                active -= 1
            return True

    monkeypatch.setattr(passwords, "_hasher", Hasher())

    def run() -> None:
        barrier.wait()
        assert passwords.verify_password(DUMMY_HASH, "password")

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert maximum == 2


def test_sign_in_fails_closed_when_redis_state_is_down(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import create_app

    monkeypatch.setenv("DATABASE_URL", database_url)
    unavailable = redis.Redis(
        host="127.0.0.1",
        port=1,
        socket_connect_timeout=0.01,
        socket_timeout=0.01,
        decode_responses=True,
    )
    application = create_app(start_background=False, state_redis=unavailable)
    try:
        response = sign_in(application.test_client(), ip="192.0.2.50")
        assert _code(response) == (503, "auth_unavailable")
    finally:
        application.extensions["mosaiq_kit"].db.close()


def test_sign_in_maps_postgresql_outage_to_database_unavailable(
    app: Any, client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = app.extensions["auth_service"]

    def unavailable(_email: str) -> None:
        from mosaiq_kit import ApiError

        raise ApiError(
            503, "database_unavailable", "The service database is unavailable."
        )

    monkeypatch.setattr(service, "credential_by_email", unavailable)
    response = sign_in(client, ip="192.0.2.51")
    assert _code(response) == (503, "database_unavailable")
