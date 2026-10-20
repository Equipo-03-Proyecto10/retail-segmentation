"""redis/customers.acl: one line, no stream selector, and what it denies."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import redis

ACL = Path(__file__).resolve().parents[1] / "redis/customers.acl"
EXPECTED = (
    "user svc_customers on {{password}} resetchannels -@all +@connection +@read"
    " +@write -@dangerous -scan -randomkey ~customers:rl:*"
    " (%R~pub:auth:v1:live:* +exists)"
)


def test_the_file_is_one_line_without_a_stream_selector():
    lines = ACL.read_text().splitlines()
    assert lines == [EXPECTED]
    assert "stream" not in lines[0]
    assert "xadd" not in lines[0].lower()


def test_dryrun_denies_streams_and_session_keys():
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    admin = redis.Redis.from_url(url, decode_responses=True)
    rules = ACL.read_text().strip().replace("{{password}}", ">pw").split()
    user = "svc_customers_acltest"
    # The user line is re-created under a throwaway name, so a shared Redis is
    # left as found.
    admin.execute_command("ACL", "DELUSER", user)
    try:
        arguments = [r for r in _split(" ".join(rules[2:]))]
        admin.execute_command("ACL", "SETUSER", user, *arguments)
        dry = lambda *c: admin.execute_command("ACL", "DRYRUN", user, *c)  # noqa: E731
        assert dry("EXISTS", "pub:auth:v1:live:abc") == "OK"
        assert dry("INCR", "customers:rl:1:2") == "OK"
        assert dry("XADD", "stream:sales.v1.recorded", "*", "a", "b") != "OK"
        assert dry("GET", "auth:session:x") != "OK"
        assert dry("GET", "pub:auth:v1:live:abc") != "OK"
        assert dry("DEL", "pub:auth:v1:live:abc") != "OK"
    finally:
        admin.execute_command("ACL", "DELUSER", user)
        admin.close()


def _split(text: str) -> list[str]:
    import re

    return re.findall(r"\([^)]*\)|\S+", text)
