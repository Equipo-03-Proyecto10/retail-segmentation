"""redis/auth.acl, against a disposable Redis 8 (ADR-0037).

The line is checked as text always, and loaded into a Redis for ACL DRYRUN when
TEST_REDIS_URL (or AUTH_TEST_REDIS_URL) names an administrator connection to a
throwaway server. MOSAIQ_REQUIRE_REDIS=1 makes a missing one a failure.
"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ACL = (Path(__file__).resolve().parents[1] / "redis/auth.acl").read_text()
STREAM = "stream:users.v1.access_changed"
LINE = (
    "user svc_auth on {{password}} resetchannels -@all +@connection +@read +@write"
    " -@dangerous -scan -randomkey ~auth:* ~pub:auth:v1:live:* +waitaof +multi"
    " +exec +discard +watch +unwatch (~stream:users.v1.access_changed"
    " +xreadgroup +xack +xgroup|create +xpending +xclaim)"
)


def test_the_acl_is_exactly_the_one_svc_auth_line() -> None:
    assert ACL.strip() == LINE
    assert len(ACL.strip().splitlines()) == 1


@pytest.fixture
def redis_admin() -> Iterator[Any]:
    url = os.environ.get("TEST_REDIS_URL") or os.environ.get("AUTH_TEST_REDIS_URL")
    if not url:
        if os.environ.get("MOSAIQ_REQUIRE_REDIS") == "1":
            pytest.fail(
                "TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping"
            )
        pytest.skip("TEST_REDIS_URL is not set")
    import redis

    client = redis.Redis.from_url(url, decode_responses=True)
    client.ping()
    client.execute_command("ACL", "DELUSER", "svc_auth")
    line = ACL.strip().replace("{{password}}", ">" + secrets.token_urlsafe(24))
    arguments = re.findall(r"\([^)]*\)|\S+", line)
    assert arguments[:2] == ["user", "svc_auth"]
    client.execute_command("ACL", "SETUSER", "svc_auth", *arguments[2:])
    yield client
    client.execute_command("ACL", "DELUSER", "svc_auth")
    client.close()


def dryrun(client: Any, *command: str) -> str:
    return str(client.execute_command("ACL", "DRYRUN", "svc_auth", *command))


@pytest.mark.parametrize(
    "command",
    [
        ("MULTI",),
        ("EXEC",),
        ("WATCH", "auth:refresh:x"),
        ("HSET", "auth:session:x", "role", "ADMIN"),
        ("SET", "pub:auth:v1:live:x", "1", "EX", "60"),
        ("EXPIRE", "pub:auth:v1:live:x", "60"),
        ("SADD", "auth:refresh_family:x", "h"),
        ("SMEMBERS", "auth:refresh_family:x"),
        ("DEL", "auth:session:x"),
        ("INCR", "auth:rl:login:1.2.3.4"),
        ("WAITAOF", "1", "0", "1000"),
        ("XGROUP", "CREATE", STREAM, "auth", "$", "MKSTREAM"),
        ("XREADGROUP", "GROUP", "auth", "c1", "COUNT", "10", "STREAMS", STREAM, ">"),
        ("XACK", STREAM, "auth", "1-1"),
        ("XPENDING", STREAM, "auth"),
        ("XCLAIM", STREAM, "auth", "c1", "60000", "1-1"),
    ],
)
def test_acl_dryrun_allows_what_the_service_does(
    redis_admin: Any, command: tuple[str, ...]
) -> None:
    assert dryrun(redis_admin, *command) == "OK"


@pytest.mark.parametrize(
    "command",
    [
        ("XADD", STREAM, "*", "a", "b"),
        ("GET", "products:cache:x"),
        ("SET", "users:rl:x", "1"),
        ("EVAL", "return 1", "0"),
        ("SCAN", "0"),
        ("RANDOMKEY",),
        ("KEYS", "*"),
        ("FLUSHALL",),
        ("XGROUP", "DESTROY", STREAM, "auth"),
        ("XREAD", "STREAMS", STREAM, "0"),
        ("XREADGROUP", "GROUP", "g", "c", "STREAMS", "stream:products.v1.changed", ">"),
        ("PUBLISH", "pub:auth:v1:live:x", "1"),
    ],
)
def test_acl_dryrun_denies_everything_else(
    redis_admin: Any, command: tuple[str, ...]
) -> None:
    assert dryrun(redis_admin, *command) != "OK"
