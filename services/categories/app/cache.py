"""Tree reads cached on redis-cache under categories:cache:*.

Invalidation never scans: every key carries the current version number held in
one counter, and a change event increments the counter, so older keys are
simply never read again and expire by TTL. Every failure of redis-cache is a
miss, so reads fall through to PostgreSQL.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from mosaiq_kit.auth import service_redis

import redis

LOGGER = logging.getLogger(__name__)

PREFIX = "categories:cache:"
VERSION_KEY = PREFIX + "version"
TTL_SECONDS = 15 * 60


class TreeCache:
    def __init__(self, client: redis.Redis | None):
        self.client = client

    @classmethod
    def from_environment(cls) -> TreeCache:
        url = os.environ.get("REDIS_CACHE_URL")
        return cls(service_redis(url, "categories") if url else None)

    def _key(self, name: str) -> str:
        assert self.client is not None
        version = self.client.get(VERSION_KEY) or "0"
        return f"{PREFIX}{version}:{name}"

    def get(self, name: str) -> Any | None:
        if self.client is None:
            return None
        try:
            raw = self.client.get(self._key(name))
            return None if raw is None else json.loads(raw)
        except (redis.RedisError, ValueError):
            LOGGER.warning("redis-cache unavailable; reading PostgreSQL")
            return None

    def put(self, name: str, value: Any) -> None:
        if self.client is None:
            return
        try:
            self.client.set(self._key(name), json.dumps(value), ex=TTL_SECONDS)
        except redis.RedisError:
            LOGGER.warning("redis-cache unavailable; not caching")

    def invalidate(self) -> None:
        """Make every cached read stale. Raises when redis-cache is down."""

        if self.client is not None:
            self.client.incr(VERSION_KEY)

    def invalidate_quietly(self) -> None:
        try:
            self.invalidate()
        except redis.RedisError:
            LOGGER.warning("redis-cache unavailable; entries expire by TTL")
