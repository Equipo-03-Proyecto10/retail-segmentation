"""The read cache on redis-cache (ADR-0037), invalidated by generation.

Every entry lives under ``products:cache:<generation>:...`` with a ten minute
TTL. A change event replaces the generation token, so every older entry is
unreachable at once and expires on its own. That needs no SCAN or KEYS, which
the service's ACL forbids. Any Redis failure is a miss: the caller reads
PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Any

import redis

from .config import CACHE_TTL_SECONDS

LOGGER = logging.getLogger(__name__)
PREFIX = "products:cache:"
GENERATION_KEY = PREFIX + "generation"


class ReadCache:
    def __init__(self, client: redis.Redis | None):
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _generation(self) -> str:
        assert self.client is not None
        token = self.client.get(GENERATION_KEY)
        if token is None:
            self.client.set(GENERATION_KEY, secrets.token_hex(8), nx=True)
            token = self.client.get(GENERATION_KEY)
        return str(token)

    @staticmethod
    def item_key(product_id: int) -> str:
        return f"item:{product_id}"

    @staticmethod
    def list_key(parts: dict[str, Any]) -> str:
        canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"))
        return "list:" + hashlib.sha256(canonical.encode()).hexdigest()

    def generation(self) -> str | None:
        """The current token, read once per request before the database read.

        An entry is written under the token read before the query, so a change
        that lands in between leaves it unreachable instead of stale.
        """

        if self.client is None:
            return None
        try:
            return self._generation()
        except redis.RedisError as error:
            LOGGER.warning("Cache read failed: %r", error)
            return None

    def get(self, generation: str | None, name: str) -> Any | None:
        if self.client is None or generation is None:
            return None
        try:
            raw = self.client.get(f"{PREFIX}{generation}:{name}")
        except redis.RedisError as error:
            LOGGER.warning("Cache read failed: %r", error)
            return None
        return None if raw is None else json.loads(raw)

    def put(self, generation: str | None, name: str, value: Any) -> None:
        if self.client is None or generation is None:
            return
        try:
            self.client.set(
                f"{PREFIX}{generation}:{name}",
                json.dumps(value, separators=(",", ":")),
                ex=CACHE_TTL_SECONDS,
            )
        except redis.RedisError as error:
            LOGGER.warning("Cache write failed: %r", error)

    def invalidate(self) -> None:
        """Make every cached read unreachable. Raises on a Redis failure."""

        if self.client is None:
            return
        self.client.set(GENERATION_KEY, secrets.token_hex(8))
