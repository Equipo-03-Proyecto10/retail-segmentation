"""redis/users.acl and the outbox relay, against a disposable Redis 8."""

from __future__ import annotations

import os
import re
import secrets
import time
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import redis

from tests.conftest import ADMIN, ANALYST, SERVICE

pytestmark = pytest.mark.redis
ACL = (SERVICE / "redis/users.acl").read_text()
STREAM = "stream:users.v1.access_changed"


@pytest.fixture
def redis_admin() -> Iterator[redis.Redis]:
    url = os.environ.get("USERS_TEST_REDIS_URL")
    if not url:
        pytest.skip("USERS_TEST_REDIS_URL is not set")
    client = redis.Redis.from_url(url, decode_responses=True)
    client.ping()
    yield client
    client.delete(STREAM)
    client.execute_command("ACL", "DELUSER", "svc_users")
    client.close()


@pytest.fixture
def svc_users(
    redis_admin: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """Load the committed line as `svc_users`, with a password made here."""
    password = secrets.token_urlsafe(24)
    line = ACL.strip().replace("{{password}}", ">" + password)
    arguments = re.findall(r"\([^)]*\)|\S+", line)
    assert arguments[:2] == ["user", "svc_users"]
    redis_admin.execute_command("ACL", "DELUSER", "svc_users")
    redis_admin.execute_command("ACL", "SETUSER", "svc_users", *arguments[2:])
    monkeypatch.setenv("REDIS_USERS_PASSWORD", password)
    monkeypatch.setenv("REDIS_STATE_URL", os.environ["USERS_TEST_REDIS_URL"])
    return "svc_users", password


def test_the_acl_is_one_svc_users_line() -> None:
    assert len(ACL.strip().splitlines()) == 1
    assert ACL.startswith("user svc_users on {{password}} resetchannels ")
    for rule in (
        "-@all +@connection +@read +@write -@dangerous -scan -randomkey"
        " ~users:rl:* ~users:cache:*",
        "(%R~pub:auth:v1:live:* +exists)",
        "(~stream:users.v1.access_changed +xadd)",
        "+waitaof",
    ):
        assert rule in ACL


@pytest.mark.parametrize(
    "command",
    [
        ("DEL", "pub:auth:v1:live:x"),
        ("HGET", "auth:session:x", "role"),
        ("XADD", "stream:products.v1.changed", "*", "a", "b"),
    ],
)
def test_acl_dryrun_denies_what_is_not_users(
    redis_admin: redis.Redis, svc_users: tuple[str, str], command: tuple[str, ...]
) -> None:
    verdict = redis_admin.execute_command("ACL", "DRYRUN", "svc_users", *command)
    assert verdict != "OK"


@pytest.mark.parametrize(
    "command",
    [
        ("EXISTS", "pub:auth:v1:live:x"),
        ("INCR", "users:rl:pwd:x"),
        ("XADD", STREAM, "*", "a", "b"),
        ("WAITAOF", "1", "0", "2000"),
    ],
)
def test_acl_dryrun_allows_what_the_service_does(
    redis_admin: redis.Redis, svc_users: tuple[str, str], command: tuple[str, ...]
) -> None:
    assert redis_admin.execute_command("ACL", "DRYRUN", "svc_users", *command) == "OK"


@pytest.mark.postgres
def test_a_committed_write_reaches_the_stream_within_five_seconds(
    redis_admin: redis.Redis,
    svc_users: tuple[str, str],
    database: dict[str, str],
    sessions: Any,
    token: Any,
    owner: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database["service"])
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app import create_app

    redis_admin.delete(STREAM)
    app = create_app()
    kit = app.extensions["mosaiq_kit"]
    kit.auth.redis = sessions
    relay = app.extensions["users_relay"]
    try:
        reply = app.test_client().post(
            f"/v1/users/{ANALYST}/deactivation",
            headers={"Authorization": "Bearer " + token(ADMIN, "ADMIN")},
        )
        assert reply.status_code == 200
        entries = redis_admin.xread({STREAM: "0"}, count=10, block=5000)
        assert entries, "nothing reached the stream within 5 s"
        [(_stream, messages)] = entries
        assert [fields["entity_id"] for _id, fields in messages] == [ANALYST]
        assert messages[0][1]["event_type"] == "UPDATE"
        # relayed_at is set once WAITAOF confirms the entry is on disk.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pending(owner):
            time.sleep(0.1)
        assert _pending(owner) == 0
    finally:
        relay.stop()
        kit.db.close()


def _pending(owner: psycopg.Connection) -> int:
    return owner.execute(
        "SELECT count(*) FROM users_events.outbox WHERE relayed_at IS NULL"
    ).fetchone()[0]


def test_the_relay_client_waits_longer_than_waitaof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import relay_redis

    monkeypatch.setenv("REDIS_USERS_PASSWORD", "not-a-real-password")
    client = relay_redis("redis://user:secret@redis-state:6379/0")
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["username"] == "svc_users"
    assert kwargs["socket_timeout"] > 2
    # The URL's credentials are dropped; the password comes from the environment.
    assert kwargs["password"] == "not-a-real-password"
