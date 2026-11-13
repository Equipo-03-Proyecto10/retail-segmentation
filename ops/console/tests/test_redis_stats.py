"""The oldest unacknowledged entry's age, from XPENDING, XRANGE and TIME (#764)."""

from __future__ import annotations

import redis
from app.redis_stats import Group, inspect

NOW_MS = 1_700_000_100_000


class FakeRedis:
    """One stream with a group per case; only the commands the console runs."""

    def __init__(self, groups, pending, entries):
        self.groups = groups
        self.pending_by_group = pending
        self.entries = entries
        self.calls: list[str] = []

    def info(self, section):
        return {"used_memory_human": "1M", "maxmemory_human": "64M"}

    def time(self):
        return NOW_MS // 1000, (NOW_MS % 1000) * 1000

    def scan_iter(self, match, count, _type):
        return iter([b"stream:sales.v1.recorded"])

    def xinfo_groups(self, stream):
        return self.groups

    def xpending(self, stream, group):
        self.calls.append("xpending")
        ids = self.pending_by_group.get(group, [])
        if not ids:
            return {"pending": 0, "min": None, "max": None, "consumers": []}
        return {"pending": len(ids), "min": ids[0], "max": ids[-1], "consumers": []}

    def xrange(self, stream, min, max, count):
        self.calls.append("xrange")
        assert min.startswith("(")
        after = tuple(int(p) for p in min[1:].split("-"))
        later = [e for e in self.entries if tuple(map(int, e.split("-"))) > after]
        return [(e.encode(), {b"event_id": b"x"}) for e in later[:count]]


def _group(name, last, lag, pending=0):
    return {
        "name": name.encode(),
        "last-delivered-id": last,
        "lag": lag,
        "pending": pending,
    }


def test_age_is_the_oldest_pending_entry_when_one_is_pending():
    client = FakeRedis(
        [_group("rfm-engine", b"1700000098000-0", 1, pending=2)],
        {"rfm-engine": [b"1700000090000-0", b"1700000098000-0"]},
        ["1700000090000-0", "1700000098000-0", "1700000099000-0"],
    )
    (group,) = inspect("redis-state", client).groups
    assert group.oldest_unacked_ms == 10_000
    assert group.oldest_unacked_label == "10.0 s"
    assert "xrange" not in client.calls


def test_age_is_the_first_undelivered_entry_when_nothing_is_pending():
    client = FakeRedis(
        [_group("recommendations", b"1700000090000-0", 2)],
        {},
        ["1700000090000-0", "1700000099650-0", "1700000099900-0"],
    )
    (group,) = inspect("redis-state", client).groups
    assert group.oldest_unacked_ms == 350
    assert group.oldest_unacked_label == "350 ms"


def test_no_age_when_the_group_has_acknowledged_everything():
    client = FakeRedis(
        [_group("recommendations", b"1700000099900-0", 0)],
        {},
        ["1700000099900-0"],
    )
    (group,) = inspect("redis-state", client).groups
    assert group.oldest_unacked_ms is None
    assert group.oldest_unacked_label == "—"
    assert client.calls == ["xpending"]


def test_an_unknown_lag_reads_the_stream():
    client = FakeRedis(
        [_group("recommendations", b"1700000090000-0", None)],
        {},
        ["1700000090000-0", "1700000040000-1"],
    )
    client.entries = ["1700000090000-0", "1700000095000-0"]
    (group,) = inspect("redis-state", client).groups
    assert group.oldest_unacked_ms == 5_000
    assert "xrange" in client.calls


def test_labels_long_ages_in_minutes():
    assert Group("s", "g", 0, 0, 3 * 60_000 + 5).oldest_unacked_label == "3 min"


def test_a_refused_command_marks_the_instance_down():
    class Refusing(FakeRedis):
        def xpending(self, stream, group):
            raise redis.ResponseError("NOPERM")

    client = Refusing([_group("g", b"0-0", 1)], {}, [])
    instance = inspect("redis-state", client)
    assert not instance.up
    assert instance.error == "ResponseError"
