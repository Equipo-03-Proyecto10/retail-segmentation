"""redis/engagement.acl: one line, the keys it may touch, and what it denies."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import redis

ACL = Path(__file__).resolve().parents[1] / "redis/engagement.acl"
SERVICES = Path(__file__).resolve().parents[2]
EXPECTED = (
    "user svc_engagement on {{password}} resetchannels -@all +@connection +@read"
    " +@write -@dangerous -scan -randomkey ~engagement:rl:* ~engagement:live:*"
    " (%R~pub:auth:v1:live:* +exists)"
)
LIVE = "engagement:live:1:20261013:QR_SCAN"


def test_the_file_is_one_line_with_the_common_rules_and_no_stream():
    lines = ACL.read_text().splitlines()
    assert lines == [EXPECTED]
    assert "stream" not in lines[0].lower() and "xadd" not in lines[0].lower()
    assert "~engagement:rl:*" in lines[0] and "~engagement:live:*" in lines[0]


def split(rules: str) -> list[str]:
    return re.findall(r"\([^)]*\)|\S+", rules)


def define(admin: redis.Redis, user: str, line: str) -> None:
    rules = line.strip().replace("{{password}}", ">pw").split(" ", 2)[2]
    admin.execute_command("ACL", "DELUSER", user)
    admin.execute_command("ACL", "SETUSER", user, *split(rules))


def test_dryrun_allows_the_counters_and_denies_streams_and_other_services():
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        if os.environ.get("MOSAIQ_REQUIRE_REDIS") == "1":
            pytest.fail(
                "TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping"
            )
        pytest.skip("TEST_REDIS_URL is not set")
    admin = redis.Redis.from_url(url, decode_responses=True)
    made = []
    try:
        define(admin, "svc_engagement_acltest", ACL.read_text())
        made.append("svc_engagement_acltest")

        def dry(user, *command):
            return admin.execute_command("ACL", "DRYRUN", user, *command)

        me = "svc_engagement_acltest"
        for command in (
            ("INCR", LIVE),
            ("DECR", LIVE),
            ("SET", LIVE, "3", "EX", "172800"),
            ("EXPIRE", LIVE, "172800", "NX"),
            ("MGET", LIVE, LIVE),
            ("GET", LIVE),
            ("INCR", "engagement:rl:1:2"),
        ):
            assert dry(me, *command) == "OK", command
        assert dry(me, "EXISTS", "pub:auth:v1:live:abc") == "OK"
        assert dry(me, "XADD", "stream:products.v1.changed", "*", "a", "b") != "OK"
        assert dry(me, "INCR", "customers:rl:1:2") != "OK"
        assert dry(me, "INCR", "engagement:cache:x") != "OK"
        assert dry(me, "GET", "auth:session:x") != "OK"
        assert dry(me, "DEL", "pub:auth:v1:live:abc") != "OK"
        assert dry(me, "KEYS", "*") != "OK"
        # No other service's user may count here.
        others = 0
        for path in sorted(SERVICES.glob("*/redis/*.acl")):
            if path.parent.parent.name == "engagement":
                continue
            line = path.read_text().splitlines()[0]
            user = re.match(r"user (svc_\w+)", line).group(1) + "_acltest"
            try:
                define(
                    admin,
                    user,
                    line.replace(re.match(r"user (svc_\w+)", line).group(1), user, 1),
                )
            except redis.ResponseError as error:
                # An older Redis than the instance's does not know +waitaof.
                assert "Unknown command" in str(error), error
                continue
            made.append(user)
            assert dry(user, "INCR", LIVE) != "OK", path
            others += 1
        assert others >= 2
    finally:
        for user in made:
            admin.execute_command("ACL", "DELUSER", user)
        admin.close()


def test_the_live_counters_work_under_the_acl_the_service_runs_with():
    """Signed in as the ACL user, a pipeline must not be a MULTI/EXEC."""

    from app.live import LiveCounters, counter_key

    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        if os.environ.get("MOSAIQ_REQUIRE_REDIS") == "1":
            pytest.fail(
                "TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids skipping"
            )
        pytest.skip("TEST_REDIS_URL is not set")
    admin = redis.Redis.from_url(url, decode_responses=True)
    user = "svc_engagement_livetest"
    key = counter_key(31, "2026-10-13", "QR_SCAN")
    try:
        define(admin, user, ACL.read_text())
        signed_in = redis.Redis.from_url(
            url, username=user, password="pw", decode_responses=True
        )
        live = LiveCounters(signed_in)
        live.count(31, "2026-10-13", "QR_SCAN", 1, stored=5)
        assert admin.get(key) == "5" and 0 < admin.ttl(key) <= 48 * 3600
        live.count(31, "2026-10-13", "QR_SCAN", 1, stored=6)
        live.count(31, "2026-10-13", "QR_SCAN", -1, stored=5)
        assert live.read(31, "2026-10-13", ("QR_SCAN",)) == {"QR_SCAN": 5}
        with pytest.raises(redis.ResponseError):
            signed_in.xadd("stream:products.v1.changed", {"a": "b"})
    finally:
        admin.delete(key)
        admin.execute_command("ACL", "DELUSER", user)
        admin.close()
