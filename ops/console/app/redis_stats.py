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


def inspect(name: str, client: redis.Redis) -> Instance:
    """Memory and, per `stream:*`, each consumer group's lag and pending count."""
    try:
        memory = client.info("memory")
        groups = []
        for key in client.scan_iter(match="stream:*", count=100, _type="stream"):
            stream = _text(key)
            for row in client.xinfo_groups(stream):
                groups.append(
                    Group(
                        stream=stream,
                        group=_text(row["name"]),
                        lag=row.get("lag"),
                        pending=int(row.get("pending", 0)),
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
