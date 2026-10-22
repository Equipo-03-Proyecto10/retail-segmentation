"""redis/sales.acl: the exact keys and stream sales-service may touch."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import redis

ACL = Path(__file__).resolve().parents[1] / "redis/sales.acl"
EXPECTED = (
    "user svc_sales on {{password}} resetchannels -@all +@connection +@read"
    " +@write -@dangerous -scan -randomkey ~sales:rl:* +waitaof"
    " (%R~pub:auth:v1:live:* +exists)"
    " (~stream:sales.v1.recorded +xadd)"
)
USER = "svc_sales_acltest"


def test_the_file_is_exactly_the_one_sales_user_line() -> None:
    assert ACL.read_text().splitlines() == [EXPECTED]
    assert "idempot" not in EXPECTED.lower()


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
    client.ping()
    line = ACL.read_text().strip().replace("{{password}}", ">pw")
    arguments = re.findall(r"\([^)]*\)|\S+", line)
    assert arguments[:2] == ["user", "svc_sales"]
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
        ("EXISTS", "pub:auth:v1:live:session"),
        ("INCR", "sales:rl:actor:window"),
        ("EXPIRE", "sales:rl:actor:window", "60"),
        ("XADD", "stream:sales.v1.recorded", "*", "event_id", "1"),
        ("WAITAOF", "1", "0", "2000"),
    ],
)
def test_dryrun_allows_only_what_sales_needs(
    redis_admin: Any, command: tuple[str, ...]
) -> None:
    assert _dryrun(redis_admin, *command) == "OK"


@pytest.mark.parametrize(
    "command",
    [
        ("XADD", "stream:stock.v1.changed", "*", "a", "b"),
        ("GET", "auth:session:x"),
        ("GET", "pub:auth:v1:live:session"),
        ("DEL", "pub:auth:v1:live:session"),
        ("SET", "sales:idempotency:key", "1"),
        ("INCR", "products:rl:actor:window"),
        ("SCAN", "0"),
        ("RANDOMKEY",),
        ("FLUSHALL",),
    ],
)
def test_dryrun_denies_other_boundaries_and_dangerous_commands(
    redis_admin: Any, command: tuple[str, ...]
) -> None:
    assert _dryrun(redis_admin, *command) != "OK"
