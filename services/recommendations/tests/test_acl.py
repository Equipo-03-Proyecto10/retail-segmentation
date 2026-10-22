"""redis/recommendations.acl: its two prefixes, the liveness check, and a
consumer group on each of six streams, and nothing else."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import redis

ACL = Path(__file__).resolve().parents[1] / "redis/recommendations.acl"
STREAMS = (
    "stream:products.v1.changed",
    "stream:categories.v1.changed",
    "stream:stock.v1.changed",
    "stream:preferences.v1.changed",
    "stream:sales.v1.recorded",
    "stream:segments.v1.run_recorded",
)
EXPECTED = (
    "user svc_recommendations on {{password}} resetchannels -@all +@connection"
    " +@read +@write -@dangerous -scan -randomkey"
    " ~recommendations:rl:* ~recommendations:cache:* +waitaof +multi +exec +discard"
    " (%R~pub:auth:v1:live:* +exists)"
    " (" + " ".join("~" + stream for stream in STREAMS) + " +xreadgroup +xack"
    " +xgroup|create +xpending +xclaim)"
)
USER = "svc_recommendations_acltest"


def test_the_file_is_exactly_the_one_recommendations_user_line() -> None:
    assert ACL.read_text().splitlines() == [EXPECTED]
    assert "+xadd" not in EXPECTED


@pytest.fixture
def redis_admin() -> Iterator[Any]:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        if os.environ.get("MOSAIQ_REQUIRE_REDIS") == "1":
            pytest.fail(
                "TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping"
            )
        pytest.skip("TEST_REDIS_URL is not set")
    client = redis.Redis.from_url(url, decode_responses=True)
    line = ACL.read_text().strip().replace("{{password}}", ">pw")
    arguments = re.findall(r"\([^)]*\)|\S+", line)
    assert arguments[:2] == ["user", "svc_recommendations"]
    client.execute_command("ACL", "DELUSER", USER)
    client.execute_command("ACL", "SETUSER", USER, *arguments[2:])
    try:
        yield client
    finally:
        client.execute_command("ACL", "DELUSER", USER)
        client.close()


def _dryrun(client: Any, *command: str) -> str:
    return str(client.execute_command("ACL", "DRYRUN", USER, *command))


@pytest.mark.parametrize(
    "command",
    [
        ("PING",),
        ("MULTI",),
        ("EXEC",),
        ("DISCARD",),
        ("EXISTS", "pub:auth:v1:live:session"),
        ("INCR", "recommendations:rl:actor:window"),
        ("SET", "recommendations:cache:c:1:10", "{}", "EX", "120"),
        ("SADD", "recommendations:cache:index", "recommendations:cache:c:1:10"),
        ("RENAME", "recommendations:cache:index", "recommendations:cache:index-old:x"),
        ("DEL", "recommendations:cache:c:1:10"),
    ]
    + [
        ("XREADGROUP", "GROUP", "recommendations", "c", "STREAMS", s, ">")
        for s in STREAMS
    ]
    + [("XGROUP", "CREATE", s, "recommendations", "$", "MKSTREAM") for s in STREAMS],
)
def test_dryrun_allows_what_recommendations_needs(
    redis_admin: Any, command: tuple[str, ...]
) -> None:
    assert _dryrun(redis_admin, *command) == "OK"


@pytest.mark.parametrize(
    "command",
    [
        ("XADD", "stream:sales.v1.recorded", "*", "a", "b"),
        ("GET", "products:cache:x"),
        ("GET", "auth:session:x"),
        ("GET", "pub:auth:v1:live:session"),
        (
            "XREADGROUP",
            "GROUP",
            "recommendations",
            "c",
            "STREAMS",
            "stream:users.v1.changed",
            ">",
        ),
        ("SCAN", "0"),
        ("KEYS", "*"),
        ("RANDOMKEY",),
        ("FLUSHALL",),
    ],
)
def test_dryrun_denies_other_boundaries_and_dangerous_commands(
    redis_admin: Any, command: tuple[str, ...]
) -> None:
    assert _dryrun(redis_admin, *command) != "OK"
