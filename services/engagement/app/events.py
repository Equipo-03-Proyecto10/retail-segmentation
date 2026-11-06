"""The two MongoDB collections of engagement-service (ADR-0038).

`engagement_event` holds one document per interaction (eight kinds, none a
sale), recorded once per `client_event_id` and voided, never deleted. A
document of a relayed kind also carries its own outbox, `relay_pending`
(ADR-0075). `engagement_daily` is its rollup
by store, UTC day and type. This module only reads and writes documents: the
database, its user, the validator and the indexes are created by
`mongo/init.js`, and the service refuses to become ready without them.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mosaiq_kit import ApiError
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.errors import (
    ConnectionFailure,
    DuplicateKeyError,
    ExecutionTimeout,
    OperationFailure,
)

LOGGER = logging.getLogger(__name__)

DATABASE = "engagement"
EVENTS = "engagement_event"
DAILY = "engagement_daily"
# /v1 records and reads only these two; /v2 all eight (#798).
TYPES = ("QR_SCAN", "RECO_CLICK")
ALL_TYPES = (
    "QR_SCAN",
    "RECO_CLICK",
    "STORE_VISIT",
    "RECO_IMPRESSION",
    "CAMPAIGN_EXPOSURE",
    "CART_ADD",
    "CART_REMOVE",
    "NAVIGATION",
)
# The kinds relayed on stream:engagement.v1.interaction_recorded (ADR-0075).
RELAYED_TYPES = ("RECO_IMPRESSION", "RECO_CLICK", "CAMPAIGN_EXPOSURE")
# The fields a kind may carry beyond the common ones, as mongo/init.js's
# validator requires them.
KIND_FIELDS = {
    "QR_SCAN": ("product_id",),
    "RECO_CLICK": ("product_id", "label_code", "rank"),
    "RECO_IMPRESSION": ("product_id", "label_code", "rank"),
    "STORE_VISIT": (),
    "CAMPAIGN_EXPOSURE": ("campaign_id",),
    "CART_ADD": ("product_id", "quantity"),
    "CART_REMOVE": ("product_id", "quantity"),
    "NAVIGATION": ("screen",),
}
# What the relay's query repeats so that the partial index serves it.
RELAY_PENDING = {"relay_pending.0": {"$exists": True}}

# The most MongoDB may take to answer that it cannot be reached (ADR-0038).
SERVER_SELECTION_TIMEOUT_MS = 2000

# What mongo/init.js creates, as the service reads it back. Index name, key and
# uniqueness; init.js is the one definition, this is its mirror for readiness.
EVENT_INDEXES = {
    "client_event_id_1": ([("client_event_id", ASCENDING)], True, None),
    "store_id_1_occurred_at_-1": (
        [("store_id", ASCENDING), ("occurred_at", DESCENDING)],
        False,
        None,
    ),
    "type_1_occurred_at_-1": (
        [("type", ASCENDING), ("occurred_at", DESCENDING)],
        False,
        None,
    ),
    "customer_id_1_occurred_at_-1": (
        [("customer_id", ASCENDING), ("occurred_at", DESCENDING)],
        False,
        None,
    ),
    # The relay's partial index (ADR-0075).
    "relay_pending_occurred_at_1": ([("occurred_at", ASCENDING)], False, RELAY_PENDING),
}
DAILY_INDEXES = {
    "store_id_1_day_1_type_1": (
        [("store_id", ASCENDING), ("day", ASCENDING), ("type", ASCENDING)],
        True,
        None,
    ),
}

# MongoDB error codes that mean "this database cannot be used by the service":
# Unauthorized and AuthenticationFailed.
UNUSABLE_CODES = {13, 18}


def database_unavailable() -> ApiError:
    return ApiError(503, "database_unavailable", "The service database is unavailable.")


@contextmanager
def mongo_guard() -> Iterator[None]:
    """Answer 503 database_unavailable for every way MongoDB cannot be used.

    The kit's pool covers PostgreSQL only, so the service maps a refused
    connection, a server-selection timeout and a refused login itself, and
    never lets one reach the caller as a 500.
    """

    try:
        yield
    except (ConnectionFailure, ExecutionTimeout) as error:
        LOGGER.warning("MongoDB unavailable: %r", error)
        raise database_unavailable() from None
    except OperationFailure as error:
        if error.code in UNUSABLE_CODES:
            LOGGER.error("MongoDB refused the service's credentials: %r", error)
            raise database_unavailable() from None
        raise


def utc_day(moment: datetime) -> str:
    """The UTC calendar day of a moment, as YYYY-MM-DD (ADR-0038)."""

    utc = moment.astimezone(UTC)
    return f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}"


def to_millis(moment: datetime) -> datetime:
    """A moment cut to the millisecond BSON keeps, in UTC."""

    utc = moment.astimezone(UTC)
    return utc.replace(microsecond=utc.microsecond // 1000 * 1000)


def event_json_v2(document: dict[str, Any]) -> dict[str, Any]:
    """An engagement_event document as the contract's EngagementEventV2.

    Every field of the eight shapes is present, null where the kind has none.
    """

    v1 = event_json(document)
    return {
        **{key: v1[key] for key in ("event_id", "client_event_id", "type")},
        "store_id": document["store_id"],
        # The order of engagement.v2.xsd's sequences; JSON ignores it.
        "product_id": document.get("product_id"),
        "campaign_id": document.get("campaign_id"),
        "customer_id": document.get("customer_id"),
        "label_code": document.get("label_code"),
        "rank": document.get("rank"),
        "quantity": document.get("quantity"),
        "screen": document.get("screen"),
        **{
            key: v1[key]
            for key in ("recorded_by", "occurred_at", "recorded_at", "voided")
        },
        "history": v1["history"],
    }


def event_json(document: dict[str, Any]) -> dict[str, Any]:
    """An engagement_event document as the contract's EngagementEvent."""

    return {
        "event_id": document["_id"],
        "client_event_id": document["client_event_id"],
        "type": document["type"],
        "store_id": document["store_id"],
        "product_id": document.get("product_id"),
        "customer_id": document.get("customer_id"),
        "label_code": document.get("label_code"),
        "rank": document.get("rank"),
        "recorded_by": document["recorded_by"],
        "occurred_at": as_utc(document["occurred_at"]),
        "recorded_at": as_utc(document["recorded_at"]),
        "voided": document["voided"],
        "history": [
            {
                "action": entry["action"],
                "reason": entry["reason"],
                "by": entry["by"],
                "at": as_utc(entry["at"]),
            }
            for entry in document["history"]
        ],
    }


def as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def days(first: date, last: date) -> Iterator[date]:
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


class EventStore:
    """The service's reads and writes of engagement_event and engagement_daily."""

    def __init__(self, client: MongoClient | None, database: str = DATABASE):
        self.client = client
        self.database_name = database

    @classmethod
    def from_environment(cls) -> EventStore:
        uri = os.environ.get("MONGO_URI")
        if not uri:
            return cls(None)
        client: MongoClient = MongoClient(
            uri,
            serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
            connectTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
            socketTimeoutMS=5000,
            maxPoolSize=10,
            tz_aware=True,
            uuidRepresentation="standard",
            appname="engagement-service",
        )
        return cls(client, client.get_default_database(default=DATABASE).name)

    def database(self) -> Database:
        if self.client is None:
            raise database_unavailable()
        return self.client[self.database_name]

    # -- readiness ---------------------------------------------------------

    def ready(self) -> bool:
        """True when both collections, the validator and every index exist.

        The service only looks: it never creates what is missing, because the
        shape is mongo/init.js's to own.
        """

        with mongo_guard():
            database = self.database()
            infos = {
                info["name"]: info
                for info in database.list_collections()
                if info["name"] in (EVENTS, DAILY)
            }
            if set(infos) != {EVENTS, DAILY}:
                return False
            schema = (
                infos[EVENTS].get("options", {}).get("validator", {}).get("$jsonSchema")
            )
            # An older validator would refuse every insert of this version:
            # init.js has not been applied yet (expand before deploy, ADR-0075).
            if not schema or "relay_pending" not in schema.get("properties", {}):
                return False
            return self._indexes_match(
                database[EVENTS], EVENT_INDEXES
            ) and self._indexes_match(database[DAILY], DAILY_INDEXES)

    @staticmethod
    def _indexes_match(collection: Any, wanted: dict[str, Any]) -> bool:
        found = {
            index["name"]: (
                list(index["key"].items()),
                bool(index.get("unique")),
                index.get("partialFilterExpression"),
            )
            for index in collection.list_indexes()
            if index["name"] != "_id_"
        }
        return found == {
            name: (list(key), unique, partial)
            for name, (key, unique, partial) in wanted.items()
        }

    # -- events ------------------------------------------------------------

    def insert(self, document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Record an event once. Returns the stored event and whether it is new.

        A second insert of the same client_event_id hits the unique index. When
        the stored event has the same type, store and product it is a replay
        and nothing is written; otherwise the id is being reused.
        """

        with mongo_guard():
            events = self.database()[EVENTS]
            try:
                events.insert_one(document)
            except DuplicateKeyError:
                stored = events.find_one(
                    {"client_event_id": document["client_event_id"]}
                )
                if stored is None:
                    raise
                same = all(
                    stored.get(field) == document.get(field)
                    for field in ("type", "store_id", "product_id")
                )
                if not same:
                    raise ApiError(
                        409,
                        "client_event_conflict",
                        "This client_event_id was already used with a different"
                        " type, store or product.",
                    ) from None
                return stored, False
            return document, True

    def get(
        self, event_id: str, types: tuple[str, ...] = ALL_TYPES
    ) -> dict[str, Any] | None:
        with mongo_guard():
            return self.database()[EVENTS].find_one(
                {"_id": event_id, "type": {"$in": list(types)}}
            )

    def void(
        self,
        event_id: str,
        reason: str,
        by: str,
        at: datetime,
        types: tuple[str, ...] = ALL_TYPES,
    ) -> dict[str, Any]:
        """Void a live event; the one that voids it is the one that counts.

        An event of a relayed kind gets its VOIDED notice in the same
        single-document update (ADR-0075). An event whose kind is not in
        `types` (a /v2 kind voided through /v1) is not found.
        """

        entry = {"action": "VOIDED", "reason": reason, "by": by, "at": at}
        relayed = [kind for kind in types if kind in RELAYED_TYPES]
        quiet = [kind for kind in types if kind not in RELAYED_TYPES]
        with mongo_guard():
            events = self.database()[EVENTS]
            matched = 0
            for kinds, push in (
                (relayed, {"history": entry, "relay_pending": "VOIDED"}),
                (quiet, {"history": entry}),
            ):
                if matched or not kinds:
                    continue
                matched = events.update_one(
                    {"_id": event_id, "voided": False, "type": {"$in": kinds}},
                    {"$set": {"voided": True}, "$push": push},
                ).matched_count
            stored = events.find_one({"_id": event_id, "type": {"$in": list(types)}})
        if matched == 0:
            if stored is None:
                raise ApiError(
                    404, "event_not_found", f"Event {event_id} was not found."
                )
            raise ApiError(409, "already_voided", "The event is already voided.")
        assert stored is not None
        return stored

    def find(
        self, query: dict[str, Any], skip: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        with mongo_guard():
            events = self.database()[EVENTS]
            total = events.count_documents(query)
            cursor = (
                events.find(query)
                .sort("occurred_at", DESCENDING)
                .skip(skip)
                .limit(limit)
            )
            return list(cursor), total

    # -- rollup ------------------------------------------------------------

    def add_to_rollup(self, store_id: int, day: str, kind: str, delta: int) -> int:
        """Add delta to the day's count and return the count after it."""

        with mongo_guard():
            daily = self.database()[DAILY]
            key = {"store_id": store_id, "day": day, "type": kind}
            for attempt in range(3):
                try:
                    updated = daily.find_one_and_update(
                        key,
                        {"$inc": {"count": delta}},
                        upsert=True,
                        return_document=ReturnDocument.AFTER,
                    )
                except DuplicateKeyError:
                    # Two first events of the day upserted together; the loser
                    # retries and finds the winner's document.
                    if attempt == 2:
                        raise
                    continue
                return int(updated["count"])
        raise AssertionError("unreachable")

    def daily_count(self, store_id: int, day: str, kind: str) -> int:
        with mongo_guard():
            found = self.database()[DAILY].find_one(
                {"store_id": store_id, "day": day, "type": kind}
            )
        return int(found["count"]) if found else 0

    def summarize(
        self,
        group_by: str,
        first: str | None,
        last: str | None,
        store_id: int | None,
        types: tuple[str, ...] = ALL_TYPES,
    ) -> list[dict[str, Any]]:
        """Count the events not voided, grouped, from the daily rollup.

        Only the kinds in `types` count: /v1 sums its two, /v2 all eight.
        """

        match: dict[str, Any] = {"count": {"$gt": 0}, "type": {"$in": list(types)}}
        if first is not None or last is not None:
            match["day"] = {
                **({"$gte": first} if first is not None else {}),
                **({"$lte": last} if last is not None else {}),
            }
        if store_id is not None:
            match["store_id"] = store_id
        field = {"store": "$store_id", "day": "$day", "type": "$type"}[group_by]
        pipeline = [
            {"$match": match},
            {"$group": {"_id": field, "count": {"$sum": "$count"}}},
            {"$sort": {"_id": ASCENDING}},
        ]
        with mongo_guard():
            rows = list(self.database()[DAILY].aggregate(pipeline))
        return [{"key": str(row["_id"]), "count": int(row["count"])} for row in rows]

    # -- relay (ADR-0075) --------------------------------------------------

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """Events with a notice still to relay, oldest first.

        The filter repeats the partial index's, so the scan reads that index
        and nothing else.
        """

        with mongo_guard():
            cursor = (
                self.database()[EVENTS]
                .find(RELAY_PENDING)
                .sort("occurred_at", ASCENDING)
                .limit(limit)
            )
            return list(cursor)

    def relayed(self, event_id: str, notices: list[str]) -> None:
        """Remove the published notices; a notice added since stays."""

        with mongo_guard():
            self.database()[EVENTS].update_one(
                {"_id": event_id},
                {"$pull": {"relay_pending": {"$in": notices}}},
            )
