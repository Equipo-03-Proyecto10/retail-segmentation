"""Both Redis instances, read as the `ops_console` user (ADR-0055)."""

from __future__ import annotations

from dataclasses import dataclass, field

import redis


@dataclass(frozen=True)
class Group:
    stream: str
    group: str
    lag: int | None
    pending: int
    # Age of the oldest entry the group has not acknowledged, delivered or
    # not, on the Redis server's clock; None when nothing is unacknowledged.
    oldest_unacked_ms: int | None = None

    @property
    def oldest_unacked_label(self) -> str:
        age = self.oldest_unacked_ms
        if age is None:
            return "—"
        if age < 1000:
            return f"{age} ms"
        if age < 120_000:
            return f"{age / 1000:.1f} s"
        return f"{age // 60_000} min"


@dataclass(frozen=True)
class Instance:
    name: str
    up: bool
    used_memory: str | None = None
    max_memory: str | None = None
    error: str | None = None
    groups: list[Group] = field(default_factory=list)


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _entry_ms(entry_id: str) -> int:
    return int(entry_id.partition("-")[0])


def oldest_unacked_id(
    client: redis.Redis, stream: str, group: str, last_delivered: str, lag: int | None
) -> str | None:
    """The oldest entry `group` has not acknowledged.

    A pending entry was delivered, so it is older than any undelivered one;
    with nothing pending it is the first entry after `last-delivered-id`.
    A lag of None means Redis cannot count it, so the stream is read.
    """
    summary = client.xpending(stream, group)
    if summary and int(summary.get("pending") or 0) > 0:
        return _text(summary["min"])
    if lag == 0:
        return None
    rows = client.xrange(stream, min="(" + last_delivered, max="+", count=1)
    return _text(rows[0][0]) if rows else None


def inspect(name: str, client: redis.Redis) -> Instance:
    """Memory and, per `stream:*`, each consumer group's lag, pending count and
    the age of its oldest unacknowledged entry."""
    try:
        memory = client.info("memory")
        seconds, microseconds = client.time()
        now_ms = int(seconds) * 1000 + int(microseconds) // 1000
        groups = []
        for key in client.scan_iter(match="stream:*", count=100, _type="stream"):
            stream = _text(key)
            for row in client.xinfo_groups(stream):
                group = _text(row["name"])
                lag = row.get("lag")
                oldest = oldest_unacked_id(
                    client, stream, group, _text(row["last-delivered-id"]), lag
                )
                age = None if oldest is None else max(0, now_ms - _entry_ms(oldest))
                groups.append(
                    Group(
                        stream=stream,
                        group=group,
                        lag=lag,
                        pending=int(row.get("pending", 0)),
                        oldest_unacked_ms=age,
                    )
                )
    except redis.RedisError as exc:
        return Instance(name, False, error=type(exc).__name__)
    return Instance(
        name,
        True,
        used_memory=memory.get("used_memory_human"),
        max_memory=memory.get("maxmemory_human"),
        groups=sorted(groups, key=lambda g: (g.stream, g.group)),
    )
