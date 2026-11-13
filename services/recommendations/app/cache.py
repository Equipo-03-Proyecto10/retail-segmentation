"""The result cache on redis-cache (ADR-0037), invalidated through a key index.

A result lives at ``recommendations:cache:{customer_id}:{run_id}:{limit}`` for
120 s, keyed by the run behind the customer's open label, so a new run is a new
key. The entry carries the id of the decision recorded for it, so a cached answer
is still attributable to a stored decision (ADR-0067). Each key is also added to
an index set under the same prefix. An event on any of the six streams renames
the index away and deletes what it named, so no SCAN, KEYS or RANDOMKEY is ever
needed (the ACL forbids them). Any Redis failure is a miss: the result is
recomputed from the views. Each entry carries
its pre-computation generation; invalidation replaces the generation even when
the index was evicted, so an in-flight old result cannot become a fresh hit.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any, NamedTuple

import redis

from .config import CACHE_TTL_SECONDS

LOGGER = logging.getLogger(__name__)
PREFIX = "recommendations:cache:"
INDEX = PREFIX + "index"
GENERATION = PREFIX + "generation"


def result_key(customer_id: str, run_id: int | None, limit: int) -> str:
    return f"{PREFIX}{customer_id}:{'none' if run_id is None else run_id}:{limit}"


class Cached(NamedTuple):
    """A cached answer and the decision recorded for it (ADR-0067)."""

    value: dict[str, Any]
    decision_id: str


class ResultCache:
    def __init__(self, client: redis.Redis | None):
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def generation(self) -> str | None:
        """An eviction creates a new token, never a previous generation again."""
        if self.client is None:
            return None
        try:
            self.client.set(GENERATION, secrets.token_hex(16), nx=True)
            return self.client.get(GENERATION)
        except redis.RedisError as error:
            LOGGER.warning("Cache generation read failed: %r", error)
            return None

    def get(self, key: str, generation: str | None) -> Cached | None:
        if self.client is None or generation is None:
            return None
        try:
            raw = self.client.get(key)
        except redis.RedisError as error:
            LOGGER.warning("Cache read failed: %r", error)
            return None
        if raw is None:
            return None
        try:
            cached = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(cached, dict) or cached.get("generation") != generation:
            return None
        value, decision_id = cached.get("value"), cached.get("decision_id")
        # An entry with no decision was written before decisions were recorded:
        # serving it would serve an answer no stored decision explains.
        if not isinstance(value, dict) or not isinstance(decision_id, str):
            return None
        return Cached(value, decision_id)

    def put(
        self,
        key: str,
        value: dict[str, Any],
        generation: str | None,
        decision_id: str,
    ) -> None:
        if self.client is None or generation is None:
            return
        try:
            # One round trip, not a transaction: ADR-0037 keeps MULTI for
            # auth alone. Atomicity buys nothing here. An invalidation that
            # lands between these writes leaves a key whose generation get()
            # already refuses, which MULTI would not prevent either, and the
            # key expires on its own.
            pipe = self.client.pipeline(transaction=False)
            pipe.set(
                key,
                json.dumps(
                    {
                        "generation": generation,
                        "value": value,
                        "decision_id": decision_id,
                    },
                    separators=(",", ":"),
                ),
                ex=CACHE_TTL_SECONDS,
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
        # Fence computations already in progress, and cached results whose
        # index was evicted. They may finish writing, but no later read accepts
        # their old generation. This key may itself be evicted safely.
        self.client.set(GENERATION, secrets.token_hex(16))
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
