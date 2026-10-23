"""Rebuild engagement_daily from engagement_event for a range of UTC days.

A maintenance command (`python -m app rebuild-rollup --from D --to D`), never
an endpoint (ADR-0038). An event and its rollup are not written in one
transaction, so a crash between them leaves the rollup one short until this
runs; it also repairs any other drift.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from .events import DAILY, EVENTS, TYPES, EventStore, mongo_guard, utc_day
from .live import LiveCounters

LOGGER = logging.getLogger(__name__)


def day_bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """The half-open UTC interval that holds the days first..last."""

    start = datetime.combine(first, time.min, tzinfo=UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=UTC)
    return start, end


def rebuild(
    store: EventStore, live: LiveCounters, first: date, last: date
) -> dict[str, int]:
    """Recompute the days first..last (inclusive). Returns what it did."""

    if last < first:
        raise ValueError("--to is before --from")
    start, end = day_bounds(first, last)
    first_day, last_day = first.isoformat(), last.isoformat()
    in_range = {"day": {"$gte": first_day, "$lte": last_day}}
    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "voided": False,
                "occurred_at": {"$gte": start, "$lt": end},
            }
        },
        {
            "$group": {
                "_id": {
                    "store_id": "$store_id",
                    "type": "$type",
                    "day": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$occurred_at",
                            "timezone": "UTC",
                        }
                    },
                },
                "count": {"$sum": 1},
            }
        },
        {
            "$project": {
                "_id": 0,
                "store_id": "$_id.store_id",
                "day": "$_id.day",
                "type": "$_id.type",
                "count": 1,
            }
        },
        {
            "$merge": {
                "into": DAILY,
                "on": ["store_id", "day", "type"],
                "whenMatched": "replace",
                "whenNotMatched": "insert",
            }
        },
    ]
    with mongo_guard():
        database = store.database()
        # A (store, day, type) with no live event left is absent from the
        # aggregation, so its stale count is zeroed first; $merge then
        # replaces every group that still has events.
        zeroed = database[DAILY].update_many(in_range, {"$set": {"count": 0}})
        list(database[EVENTS].aggregate(pipeline))
        rebuilt = list(database[DAILY].find({**in_range, "count": {"$gt": 0}}))
    refreshed = refresh_live(live, rebuilt)
    LOGGER.info("rollup rebuilt for %s..%s", first_day, last_day)
    return {
        "rollup_documents_reset": zeroed.matched_count,
        "groups": len(rebuilt),
        "live_counters_set": refreshed,
    }


def refresh_live(live: LiveCounters, rollup: list[dict[str, Any]]) -> int:
    """Put the rebuilt count on every counter that is still alive (48 h)."""

    today = datetime.now(UTC)
    recent = {utc_day(today - timedelta(days=age)) for age in range(3)}
    count = 0
    for row in rollup:
        if row["day"] in recent and row["type"] in TYPES:
            live.set(row["store_id"], row["day"], row["type"], int(row["count"]))
            count += 1
    return count
