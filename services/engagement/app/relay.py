"""The relay of stream:engagement.v1.interaction_recorded (ADR-0075).

The outbox is `relay_pending` on each event document, written in the same
single-document insert or void as the fact. A thread of the service polls it
every second, publishes the notices through the kit's StreamPublisher (the
stream append, then WAITAOF) and only then `$pull`s them, so delivery is at
least once and a relay or Redis outage loses nothing. The request path never
appends to the stream; only the kit does (ADR-0044's guard).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date
from typing import Any

from mosaiq_kit import StreamPublisher

import redis

from .events import EVENTS, RELAYED_TYPES, EventStore, as_utc, mongo_guard
from .rollup import day_bounds

LOGGER = logging.getLogger(__name__)

STREAM = "stream:engagement.v1.interaction_recorded"
POLL_SECONDS = 1.0
BATCH = 100


def entry(document: dict[str, Any], notice: str) -> dict[str, Any]:
    """One stream entry: the fact, keyed by `<event_id>:<notice>`.

    Absent fields are None and left out by the publisher.
    """

    return {
        "event_id": f"{document['_id']}:{notice.lower()}",
        "v": 1,
        "notice": notice,
        "type": document["type"],
        "store_id": document["store_id"],
        "product_id": document.get("product_id"),
        "customer_id": document.get("customer_id"),
        "campaign_id": document.get("campaign_id"),
        "label_code": document.get("label_code"),
        "rank": document.get("rank"),
        "occurred_at": as_utc(document["occurred_at"]),
    }


def relay_redis(url: str) -> redis.Redis:
    """redis-state as svc_engagement, with room for WAITAOF's 2 s.

    The kit's request connection gives up after 200 ms, which is right for a
    liveness check and too short for an AOF wait, so the relay has its own.
    """

    client = redis.Redis.from_url(
        url,
        username="svc_engagement",
        password=os.environ.get("REDIS_ENGAGEMENT_PASSWORD"),
        socket_connect_timeout=1,
        socket_timeout=3,
    )
    return client


class InteractionRelay:
    def __init__(self, store: EventStore, publisher: StreamPublisher):
        self.store = store
        self.publisher = publisher
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_environment(cls, store: EventStore) -> InteractionRelay | None:
        url = os.environ.get("REDIS_STATE_URL")
        if not url or store.client is None:
            return None
        return cls(store, StreamPublisher(relay_redis(url), STREAM))

    def run_once(self) -> int:
        """Publish one batch of pending notices; returns how many."""

        documents = self.store.pending(BATCH)
        batch = [(document, list(document["relay_pending"])) for document in documents]
        entries = [
            entry(document, notice) for document, notices in batch for notice in notices
        ]
        if not entries:
            return 0
        self.publisher.publish(entries)
        for document, notices in batch:
            self.store.relayed(document["_id"], notices)
        return len(entries)

    def run_forever(self) -> None:
        outage = False
        while not self._stop.is_set():
            try:
                published = self.run_once()
            except redis.RedisError as error:
                if not outage:
                    LOGGER.warning("relay: redis-state unavailable: %r", error)
                    outage = True
                published = 0
            except Exception:
                LOGGER.exception("relay: pass failed; notices stay pending")
                published = 0
            else:
                if outage:
                    LOGGER.info("relay: redis-state reachable again")
                    outage = False
            # A full batch means more may be waiting: go on without sleeping.
            if published < BATCH:
                self._stop.wait(POLL_SECONDS)

    def start(self) -> threading.Thread:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self.run_forever, name="engagement-relay", daemon=True
            )
            self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()


def requeue(store: EventStore, first: date, last: date) -> int:
    """Queue every relayed event of the UTC days first..last again (ADR-0075).

    A maintenance command, never an endpoint: it brings a consumer that fell
    behind the stream's retention, or one deployed later, up to date. Each
    event gets RECORDED again, and VOIDED too when it is voided; consumers
    dedupe the redelivered entries by event_id. Returns how many events.
    """

    if last < first:
        raise ValueError("--to is before --from")
    start, end = day_bounds(first, last)
    with mongo_guard():
        result = store.database()[EVENTS].update_many(
            {
                "type": {"$in": list(RELAYED_TYPES)},
                "occurred_at": {"$gte": start, "$lt": end},
            },
            [
                {
                    "$set": {
                        "relay_pending": {
                            "$cond": [
                                "$voided",
                                ["RECORDED", "VOIDED"],
                                ["RECORDED"],
                            ]
                        }
                    }
                }
            ],
        )
    LOGGER.info("relay re-queued %d events", result.modified_count)
    return result.matched_count
