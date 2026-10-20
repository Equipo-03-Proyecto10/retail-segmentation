"""Refresh rotation, account re-checks and failure semantics."""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt
import pytest
import redis
from mosaiq_kit import ApiError

from app.store import password_fingerprint, refresh_fingerprint
from tests.conftest import USER_ID, sign_in

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


def _refresh(client: Any, token: str) -> Any:
    return client.post("/v1/sessions/refresh", json={"refresh_token": token})


def _code(response: Any) -> tuple[int, str | None]:
    return response.status_code, (response.get_json(silent=True) or {}).get("code")


def _claims(token: str) -> dict[str, Any]:
    return jwt.decode(token, options={"verify_signature": False})


def test_current_refresh_rechecks_then_rotates_atomically_without_extending_liveness(
    client: Any, session: dict[str, str], redis_admin: redis.Redis
) -> None:
    old_hash = refresh_fingerprint(session["refresh_token"])
    old_claims = _claims(session["access_token"])
    sid = old_claims["sid"]
    ttl_before = redis_admin.ttl("pub:auth:v1:live:" + sid)
    response = _refresh(client, session["refresh_token"])
    assert response.status_code == 200
    result = response.get_json()
    new_claims = _claims(result["access_token"])
    new_hash = refresh_fingerprint(result["refresh_token"])
    assert new_claims["role"] == old_claims["role"] == "ANALYST"
    assert redis_admin.hget("auth:refresh:" + old_hash, "state") == "rotated"
    assert redis_admin.hget("auth:refresh:" + new_hash, "state") == "current"
    assert redis_admin.smembers("auth:refresh_family:" + sid) == {
        old_hash,
        new_hash,
    }
    assert redis_admin.ttl("pub:auth:v1:live:" + sid) <= ttl_before


def test_reusing_a_rotated_refresh_revokes_the_whole_family_without_scan(
    client: Any, session: dict[str, str], redis_admin: redis.Redis
) -> None:
    first = _refresh(client, session["refresh_token"])
    assert first.status_code == 200
    sid = _claims(session["access_token"])["sid"]
    hashes = {
        refresh_fingerprint(session["refresh_token"]),
        refresh_fingerprint(first.get_json()["refresh_token"]),
    }
    reused = _refresh(client, session["refresh_token"])
    assert _code(reused) == (401, "refresh_reused")
    assert not redis_admin.exists("pub:auth:v1:live:" + sid)
    assert not redis_admin.exists("auth:session:" + sid)
    assert not redis_admin.exists("auth:refresh_family:" + sid)
    assert all(not redis_admin.exists("auth:refresh:" + value) for value in hashes)


def test_two_concurrent_refreshes_have_one_success_and_one_reuse(
    app: Any, session: dict[str, str]
) -> None:
    barrier = threading.Barrier(2)
    replies: list[Any] = []

    def run() -> None:
        client = app.test_client()
        barrier.wait()
        replies.append(_refresh(client, session["refresh_token"]))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(_code(reply) for reply in replies) == [
        (200, None),
        (401, "refresh_reused"),
    ]


def test_unknown_refresh_changes_no_keys(client: Any, redis_admin: redis.Redis) -> None:
    before = set(redis_admin.scan_iter(match="auth:*")) | set(
        redis_admin.scan_iter(match="pub:auth:v1:live:*")
    )
    response = _refresh(client, "unknown-refresh-token")
    after = set(redis_admin.scan_iter(match="auth:*")) | set(
        redis_admin.scan_iter(match="pub:auth:v1:live:*")
    )
    assert _code(response) == (401, "invalid_refresh")
    assert after == before


def test_refresh_after_short_absolute_end_is_session_expired(
    app: Any,
    client: Any,
    session: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = app.extensions["auth_service"]
    service.session_ttl = 1
    # The token was created by the fixture before shortening; make this exact
    # session's stored absolute end one second away, then advance auth's clock
    # without waiting for Redis to physically expire the indexed evidence.
    sid = _claims(session["access_token"])["sid"]
    now = int(time.time())
    service.store.redis.hset("auth:session:" + sid, "expires_at", now + 1)
    monkeypatch.setattr("app.service.time.time", lambda: now + 2)
    response = _refresh(client, session["refresh_token"])
    assert _code(response) == (401, "session_expired")


def test_refresh_after_the_session_keys_really_expire_is_session_expired(
    app: Any, client: Any, redis_admin: redis.Redis
) -> None:
    # No clock is faked: the session hash and the liveness key really expire,
    # and the refresh entry, kept past the end, still names the session.
    app.extensions["auth_service"].session_ttl = 2
    response = sign_in(client)
    assert response.status_code == 201
    signed_in = response.get_json()
    sid = _claims(signed_in["access_token"])["sid"]
    time.sleep(3)
    assert redis_admin.exists("pub:auth:v1:live:" + sid) == 0
    assert redis_admin.exists("auth:session:" + sid) == 0
    response = _refresh(client, signed_in["refresh_token"])
    assert _code(response) == (401, "session_expired")
    assert redis_admin.exists("auth:refresh_family:" + sid) == 0


@pytest.mark.parametrize("change", ["missing", "inactive", "role", "password"])
def test_account_change_revokes_the_session(
    change: str,
    app: Any,
    client: Any,
    session: dict[str, str],
    redis_admin: redis.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = app.extensions["auth_service"]
    sid = _claims(session["access_token"])["sid"]
    stored = redis_admin.hgetall("auth:session:" + sid)
    credential: dict[str, Any] | None = {
        "user_id": USER_ID,
        "password_hash": "same hash",
        "is_active": True,
        "role_code": stored["role"],
    }
    redis_admin.hset(
        "auth:session:" + sid,
        "password_fingerprint",
        password_fingerprint("same hash"),
    )
    if change == "missing":
        credential = None
    elif change == "inactive":
        credential["is_active"] = False
    elif change == "role":
        credential["role_code"] = "ADMIN"
    else:
        credential["password_hash"] = "new hash"
    monkeypatch.setattr(service, "credential_by_user", lambda _user_id: credential)
    response = _refresh(client, session["refresh_token"])
    assert _code(response) == (401, "session_revoked")
    assert not redis_admin.exists("pub:auth:v1:live:" + sid)
    assert not redis_admin.exists("auth:session:" + sid)


def test_database_outage_leaves_refresh_current_and_retry_succeeds(
    app: Any,
    client: Any,
    session: dict[str, str],
    redis_admin: redis.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = app.extensions["auth_service"]
    original = service.credential_by_user
    token_hash = refresh_fingerprint(session["refresh_token"])

    def unavailable(_user_id: str) -> None:
        raise ApiError(
            503, "database_unavailable", "The service database is unavailable."
        )

    monkeypatch.setattr(service, "credential_by_user", unavailable)
    failed = _refresh(client, session["refresh_token"])
    assert _code(failed) == (503, "database_unavailable")
    assert redis_admin.hget("auth:refresh:" + token_hash, "state") == "current"
    monkeypatch.setattr(service, "credential_by_user", original)
    assert _refresh(client, session["refresh_token"]).status_code == 200


def test_former_administrator_refresh_is_revoked_after_transfer(
    app: Any, client: Any, redis_admin: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.post(
        "/v1/sessions",
        json={
            "email": "admin@mosaiq-demo.com",
            "password": "Password123!",
            "device_label": "administrator before transfer",
        },
        headers={"X-Real-IP": "192.0.2.70"},
    )
    assert response.status_code == 201
    result = response.get_json()
    sid = _claims(result["access_token"])["sid"]
    service = app.extensions["auth_service"]
    original = service.credential_by_user
    before = original(_claims(result["access_token"])["sub"])
    monkeypatch.setattr(
        service,
        "credential_by_user",
        lambda _user: before | {"role_code": "ANALYST"},
    )
    refused = _refresh(client, result["refresh_token"])
    assert _code(refused) == (401, "session_revoked")
    assert not redis_admin.exists("pub:auth:v1:live:" + sid)


def test_access_exp_never_passes_the_absolute_session_end(
    app: Any, client: Any, session: dict[str, str], redis_admin: redis.Redis
) -> None:
    sid = _claims(session["access_token"])["sid"]
    absolute_end = int(time.time()) + 20
    redis_admin.hset("auth:session:" + sid, "expires_at", absolute_end)
    result = _refresh(client, session["refresh_token"]).get_json()
    assert _claims(result["access_token"])["exp"] <= absolute_end
