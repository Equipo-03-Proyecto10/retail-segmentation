"""Durable access-change consumption and the 60-second reconciliation path."""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
import redis
from mosaiq_kit import ApiError

from app.background import ACCESS_CHANGED_STREAM, BackgroundWorkers, Reconciler
from app.store import password_fingerprint
from tests.conftest import USER_ID

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


def _sid(session: dict[str, str]) -> str:
    return jwt.decode(session["access_token"], options={"verify_signature": False})[
        "sid"
    ]


def _event(user_id: str, event_id: str | None = None) -> dict[str, str]:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "v": "1",
        "event_type": "UPDATE",
        "entity_id": user_id,
        "occurred_at": "2026-10-20T00:00:00Z",
    }


def test_users_service_or_web_deactivation_event_revokes_before_xack(
    app: Any,
    session: dict[str, str],
    redis_admin: redis.Redis,
) -> None:
    service = app.extensions["auth_service"]
    second = service.sign_in("user2@mosaiq-demo.com", "Password123!")
    sids = {_sid(session), _sid(second)}
    workers = BackgroundWorkers(service, redis_admin, interval=60)
    # Establish the group at '$', then append the web/users relay equivalent.
    workers.consumer.run_once(block_ms=1)
    redis_admin.xadd(ACCESS_CHANGED_STREAM, _event(USER_ID))
    assert workers.consumer.run_once(block_ms=10) == 1
    assert all(not redis_admin.exists("pub:auth:v1:live:" + sid) for sid in sids)
    assert redis_admin.xpending(ACCESS_CHANGED_STREAM, "auth")["pending"] == 0


def test_consumer_is_idempotent_by_event_id(
    app: Any, redis_admin: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = app.extensions["auth_service"]
    calls: list[str] = []
    monkeypatch.setattr(service.store, "revoke_user", calls.append)
    workers = BackgroundWorkers(service, redis_admin, interval=60)
    workers.consumer.run_once(block_ms=1)
    event_id = str(uuid.uuid4())
    redis_admin.xadd(ACCESS_CHANGED_STREAM, _event(USER_ID, event_id))
    redis_admin.xadd(ACCESS_CHANGED_STREAM, _event(USER_ID, event_id))
    assert workers.consumer.run_once(block_ms=10) == 2
    assert calls == [USER_ID]


def test_consumer_recovers_own_pending_and_claims_entries_idle_over_sixty_seconds(
    app: Any, redis_admin: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = app.extensions["auth_service"]
    calls: list[str] = []
    monkeypatch.setattr(service.store, "revoke_user", calls.append)
    workers = BackgroundWorkers(service, redis_admin, interval=60)
    workers.consumer._ensure_group()
    own_id = redis_admin.xadd(ACCESS_CHANGED_STREAM, _event("own-pending"))
    redis_admin.xreadgroup(
        "auth", workers.consumer.consumer, {ACCESS_CHANGED_STREAM: ">"}, count=1
    )
    claimed_id = redis_admin.xadd(ACCESS_CHANGED_STREAM, _event("stale-pending"))
    redis_admin.xreadgroup(
        "auth", "dead-consumer", {ACCESS_CHANGED_STREAM: ">"}, count=1
    )
    redis_admin.xclaim(
        ACCESS_CHANGED_STREAM,
        "auth",
        "dead-consumer",
        min_idle_time=0,
        message_ids=[claimed_id],
        idle=60_001,
    )
    assert workers.consumer.run_once(block_ms=1) == 2
    assert set(calls) == {"own-pending", "stale-pending"}
    assert redis_admin.xpending(ACCESS_CHANGED_STREAM, "auth")["pending"] == 0
    assert own_id and claimed_id


def test_fifth_failed_stream_delivery_is_acknowledged(
    app: Any, redis_admin: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = app.extensions["auth_service"]

    def fail(_user_id: str) -> None:
        raise RuntimeError("expected handler failure")

    monkeypatch.setattr(service.store, "revoke_user", fail)
    workers = BackgroundWorkers(service, redis_admin, interval=60)
    workers.consumer._ensure_group()
    message_id = redis_admin.xadd(ACCESS_CHANGED_STREAM, _event(USER_ID))
    redis_admin.xreadgroup(
        "auth", workers.consumer.consumer, {ACCESS_CHANGED_STREAM: ">"}, count=1
    )
    for delivery in range(2, 6):
        redis_admin.xclaim(
            ACCESS_CHANGED_STREAM,
            "auth",
            workers.consumer.consumer,
            min_idle_time=0,
            message_ids=[message_id],
            idle=60_001,
            retrycount=delivery,
        )
        workers.consumer.run_once(block_ms=1)
    assert redis_admin.xpending(ACCESS_CHANGED_STREAM, "auth")["pending"] == 0


def test_reconcile_revokes_missing_inactive_role_and_password_changes(
    app: Any,
    redis_admin: redis.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = app.extensions["auth_service"]
    results = [
        service.sign_in("user2@mosaiq-demo.com", "Password123!") for _ in range(4)
    ]
    sids = [_sid(result) for result in results]
    sessions = [redis_admin.hgetall("auth:session:" + sid) for sid in sids]
    users = [f"11111111-1111-1111-1111-{index:012d}" for index in range(101, 105)]
    for sid, stored, user_id in zip(sids, sessions, users, strict=True):
        redis_admin.hset("auth:session:" + sid, "user_id", user_id)
        redis_admin.srem("auth:user_sids:" + USER_ID, sid)
        redis_admin.sadd("auth:user_sids:" + user_id, sid)
        redis_admin.expire("auth:user_sids:" + user_id, 43_200)
        redis_admin.sadd("auth:session_users", user_id)
        stored["user_id"] = user_id
    credentials: dict[str, dict[str, Any]] = {
        users[1]: {
            "user_id": users[1],
            "is_active": False,
            "role_code": sessions[1]["role"],
            "password_hash": "hash-1",
        },
        users[2]: {
            "user_id": users[2],
            "is_active": True,
            "role_code": "ADMIN",
            "password_hash": "hash-2",
        },
        users[3]: {
            "user_id": users[3],
            "is_active": True,
            "role_code": sessions[3]["role"],
            "password_hash": "changed",
        },
    }
    # User 0 is absent; align fingerprints for the role/inactive cases so the
    # asserted reason is isolated. User 3 intentionally differs.
    for index in (1, 2):
        redis_admin.hset(
            "auth:session:" + sids[index],
            "password_fingerprint",
            password_fingerprint(credentials[users[index]]["password_hash"]),
        )
    monkeypatch.setattr(service, "credentials_by_user", lambda _users: credentials)
    assert Reconciler(service).run_once() == 4
    assert all(not redis_admin.exists("pub:auth:v1:live:" + sid) for sid in sids)


def test_reconcile_database_outage_deletes_nothing_and_next_tick_retries(
    app: Any,
    session: dict[str, str],
    redis_admin: redis.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = app.extensions["auth_service"]
    sid = _sid(session)

    def unavailable(_users: list[str]) -> dict[str, Any]:
        raise ApiError(
            503, "database_unavailable", "The service database is unavailable."
        )

    monkeypatch.setattr(service, "credentials_by_user", unavailable)
    reconcile = Reconciler(service, interval_seconds=60)
    assert reconcile.run_once() == 0
    assert redis_admin.exists("pub:auth:v1:live:" + sid)
    stored = redis_admin.hgetall("auth:session:" + sid)
    valid = {
        USER_ID: {
            "user_id": USER_ID,
            "password_hash": "restored hash",
            "is_active": True,
            "role_code": stored["role"],
        }
    }
    redis_admin.hset(
        "auth:session:" + sid,
        "password_fingerprint",
        password_fingerprint("restored hash"),
    )
    monkeypatch.setattr(service, "credentials_by_user", lambda _users: valid)
    assert reconcile.run_once() == 0
    assert redis_admin.exists("pub:auth:v1:live:" + sid)
    monkeypatch.setattr(service, "credential_by_user", lambda _user: valid[USER_ID])
    assert service.refresh(session["refresh_token"])["refresh_token"]


def test_factory_background_workers_are_started_and_stoppable(
    database_url: str,
    redis_admin: redis.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import create_app

    monkeypatch.setenv("DATABASE_URL", database_url)
    application = create_app(
        start_background=True,
        state_redis=redis_admin,
        stream_redis=redis_admin,
    )
    workers = application.extensions["auth_background"]
    try:
        assert workers.consumer._thread.is_alive()
        assert workers.reconciler._thread.is_alive()
    finally:
        workers.stop()
        application.extensions["mosaiq_kit"].db.close()
