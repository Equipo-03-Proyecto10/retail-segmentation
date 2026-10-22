"""redis/campaigns.acl: the exact keys campaigns-service may touch."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import redis

ACL = Path(__file__).resolve().parents[1] / "redis/campaigns.acl"
EXPECTED = (
    "user svc_campaigns on {{password}} resetchannels -@all +@connection +@read"
    " +@write -@dangerous -scan -randomkey ~campaigns:rl:*"
    " (%R~pub:auth:v1:live:* +exists)"
)
USER = "svc_campaigns_acltest"


def test_the_file_is_exactly_the_one_campaigns_user_line() -> None:
    assert ACL.read_text().splitlines() == [EXPECTED]


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
    assert arguments[:2] == ["user", "svc_campaigns"]
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
        ("INCR", "campaigns:rl:actor:window"),
        ("EXPIRE", "campaigns:rl:actor:window", "60"),
    ],
)
def test_dryrun_allows_only_what_campaigns_needs(
    redis_admin: Any, command: tuple[str, ...]
) -> None:
    assert _dryrun(redis_admin, *command) == "OK"


@pytest.mark.parametrize(
    "command",
    [
        ("XADD", "stream:segments.v1.run_recorded", "*", "a", "b"),
        ("GET", "auth:session:x"),
        ("GET", "pub:auth:v1:live:session"),
        ("DEL", "pub:auth:v1:live:session"),
        ("SET", "campaigns:cache:x", "1"),
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
