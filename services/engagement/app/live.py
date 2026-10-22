"""Live counters on redis-cache: engagement:live:{store_id}:{yyyymmdd}:{type}.

A counter caches one engagement_daily count and is rebuilt from it, so every
failure of redis-cache is a miss: writes go on, reads fall back to MongoDB
(ADR-0038, ADR-0037).
"""

from __future__ import annotations

import logging
import os

from mosaiq_kit.auth import service_redis

import redis

LOGGER = logging.getLogger(__name__)

TTL_SECONDS = 48 * 60 * 60


def counter_key(store_id: int, day: str, kind: str) -> str:
    """The key of one store, UTC day (YYYY-MM-DD) and type."""

    return f"engagement:live:{store_id}:{day.replace('-', '')}:{kind}"


class LiveCounters:
    def __init__(self, client: redis.Redis | None):
        self.client = client

    @classmethod
    def from_environment(cls) -> LiveCounters:
        url = os.environ.get("REDIS_CACHE_URL")
        return cls(service_redis(url, "engagement") if url else None)

    def count(
        self, store_id: int, day: str, kind: str, delta: int, stored: int
    ) -> None:
        """Count one event, then trust the rollup's number when the key was new.

        INCR (or DECR) returns delta itself when the key did not exist; the
        counter then holds the count findOneAndUpdate returned instead, so a
        counter that expired or was deleted comes back right.
        """

        if self.client is None:
            return
        key = counter_key(store_id, day, kind)
        try:
            pipe = self.client.pipeline(transaction=False)
            pipe.incr(key) if delta > 0 else pipe.decr(key)
            pipe.expire(key, TTL_SECONDS, nx=True)
            result, _ = pipe.execute()
            if result == delta:
                self.client.set(key, stored, ex=TTL_SECONDS)
        except redis.RedisError as error:
            LOGGER.warning("redis-cache unavailable; live counter skipped: %r", error)

    def set(self, store_id: int, day: str, kind: str, value: int) -> None:
        if self.client is None:
            return
        try:
            self.client.set(counter_key(store_id, day, kind), value, ex=TTL_SECONDS)
        except redis.RedisError as error:
            LOGGER.warning("redis-cache unavailable; live counter skipped: %r", error)

    def read(
        self, store_id: int, day: str, kinds: tuple[str, ...]
    ) -> dict[str, int | None] | None:
        """The counters of one store and day; a missing one is None.

        None for the whole answer means redis-cache cannot be asked.
        """

        if self.client is None:
            return None
        try:
            values = self.client.mget(
                [counter_key(store_id, day, kind) for kind in kinds]
            )
        except redis.RedisError as error:
            LOGGER.warning("redis-cache unavailable; reading MongoDB: %r", error)
            return None
        counts: dict[str, int | None] = {}
        for kind, value in zip(kinds, values, strict=True):
            try:
                counts[kind] = None if value is None else max(0, int(value))
            except ValueError:
                counts[kind] = None
        return counts
