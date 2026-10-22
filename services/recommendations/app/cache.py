"""The result cache on redis-cache (ADR-0037), invalidated through a key index.

A result lives at ``recommendations:cache:{customer_id}:{run_id}:{limit}`` for
120 s, keyed by the run behind the customer's open label, so a new run is a new
key. Each key is also added to an index set under the same prefix. An event on
any of the six streams renames the index away and deletes what it named, so no
SCAN, KEYS or RANDOMKEY is ever needed (the ACL forbids them). Any Redis
failure is a miss: the result is recomputed from the views.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

import redis

from .config import CACHE_TTL_SECONDS

LOGGER = logging.getLogger(__name__)
PREFIX = "recommendations:cache:"
INDEX = PREFIX + "index"


def result_key(customer_id: str, run_id: int | None, limit: int) -> str:
    return f"{PREFIX}{customer_id}:{'none' if run_id is None else run_id}:{limit}"


class ResultCache:
    def __init__(self, client: redis.Redis | None):
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def get(self, key: str) -> dict[str, Any] | None:
        if self.client is None:
            return None
        try:
            raw = self.client.get(key)
        except redis.RedisError as error:
            LOGGER.warning("Cache read failed: %r", error)
            return None
        return None if raw is None else json.loads(raw)

    def put(self, key: str, value: dict[str, Any]) -> None:
        if self.client is None:
            return
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.set(
                key, json.dumps(value, separators=(",", ":")), ex=CACHE_TTL_SECONDS
            )
            pipe.sadd(INDEX, key)
            # The index outlives every key it names, and no longer.
            pipe.expire(INDEX, CACHE_TTL_SECONDS)
            pipe.execute()
        except redis.RedisError as error:
            LOGGER.warning("Cache write failed: %r", error)

    def invalidate(self) -> None:
        """Delete every cached result. Raises on a Redis failure, so the
        consumer redelivers the event."""
        if self.client is None:
            return
        # Renamed first, so a result stored meanwhile goes to a fresh index
        # instead of being lost from one that is being emptied.
        doomed = f"{PREFIX}index-old:{secrets.token_hex(8)}"
        try:
            self.client.rename(INDEX, doomed)
        except redis.ResponseError as error:
            if "no such key" in str(error).lower():
                return
            raise
        keys = list(self.client.smembers(doomed))
        self.client.delete(*keys, doomed)
