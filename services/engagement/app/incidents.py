"""Capture incidents: refused client writes recorded once and closed, never deleted."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from mosaiq_kit import ApiError
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from .events import EventStore, as_utc, mongo_guard

COLLECTION = "capture_incident"
STATUSES = ("OPEN", "ACKNOWLEDGED", "RESOLVED")
SOURCE_TYPES = ("SALE", "CHECK_IN", "ENGAGEMENT_EVENT", "CUSTOMER_WRITE")
INDEXES = {
    "client_incident_id_1": ([("client_incident_id", ASCENDING)], True, None),
    "source_type_1_source_operation_id_1": (
        [("source_type", ASCENDING), ("source_operation_id", ASCENDING)],
        True,
        None,
    ),
    "store_id_1_status_1_recorded_at_-1": (
        [
            ("store_id", ASCENDING),
            ("status", ASCENDING),
            ("recorded_at", DESCENDING),
        ],
        False,
        None,
    ),
}


def incident_json(document: dict[str, Any]) -> dict[str, Any]:
    """A strict stored document as the contract's CaptureIncident."""

    return {
        "incident_id": document["_id"],
        "client_incident_id": document["client_incident_id"],
        "source_type": document["source_type"],
        "source_operation_id": document["source_operation_id"],
        "source_correlation_id": document["source_correlation_id"],
        "store_id": document["store_id"],
        "refused_status": document["refused_status"],
        "refused_code": document["refused_code"],
        "attempts": document["attempts"],
        "occurred_at": as_utc(document["occurred_at"]),
        "line_count": document["line_count"],
        "reported_by": document["reported_by"],
        "recorded_at": as_utc(document["recorded_at"]),
        "status": document["status"],
        "history": [
            {
                "status": review["status"],
                "note": review["note"],
                "by": review["by"],
                "at": as_utc(review["at"]),
            }
            for review in document["history"]
        ],
    }


def _conflict() -> ApiError:
    return ApiError(
        409,
        "client_incident_conflict",
        "This client_incident_id or refused operation was already reported"
        " as a different incident.",
    )


class IncidentStore:
    """MongoDB operations for the capture_incident collection."""

    def __init__(self, events: EventStore):
        self.events = events

    def collection(self):
        return self.events.database()[COLLECTION]

    def ready(self) -> bool:
        with mongo_guard():
            database = self.events.database()
            info = next(
                (
                    item
                    for item in database.list_collections()
                    if item["name"] == COLLECTION
                ),
                None,
            )
            schema = (
                info.get("options", {}).get("validator", {}).get("$jsonSchema")
                if info
                else None
            )
            if not schema or schema.get("additionalProperties") is not False:
                return False
            return EventStore._indexes_match(database[COLLECTION], INDEXES)

    def insert(self, document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Insert once under either unique identity, safely under concurrency."""

        with mongo_guard():
            incidents = self.collection()
            try:
                incidents.insert_one(document)
                return document, True
            except DuplicateKeyError:
                matches = list(
                    incidents.find(
                        {
                            "$or": [
                                {"client_incident_id": document["client_incident_id"]},
                                {
                                    "source_type": document["source_type"],
                                    "source_operation_id": document[
                                        "source_operation_id"
                                    ],
                                },
                            ]
                        }
                    ).limit(2)
                )
        if len(matches) != 1:
            raise _conflict() from None
        stored = matches[0]
        same_operation = all(
            stored[field] == document[field]
            for field in ("source_type", "source_operation_id")
        )
        same_client_or_operation = (
            stored["client_incident_id"] == document["client_incident_id"]
            or same_operation
        )
        if not same_operation or not same_client_or_operation:
            raise _conflict() from None
        return stored, False

    def get(self, incident_id: str) -> dict[str, Any] | None:
        with mongo_guard():
            return self.collection().find_one({"_id": incident_id})

    def find(
        self, query: dict[str, Any], skip: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        with mongo_guard():
            incidents = self.collection()
            total = incidents.count_documents(query)
            documents = list(
                incidents.find(query)
                .sort("recorded_at", DESCENDING)
                .skip(skip)
                .limit(limit)
            )
        return documents, total

    def review(
        self, incident_id: str, status: str, note: str | None, by: str, at: datetime
    ) -> dict[str, Any]:
        previous = ["OPEN"] if status == "ACKNOWLEDGED" else ["OPEN", "ACKNOWLEDGED"]
        entry = {"status": status, "note": note, "by": by, "at": at}
        with mongo_guard():
            incidents = self.collection()
            stored = incidents.find_one_and_update(
                {"_id": incident_id, "status": {"$in": previous}},
                {"$set": {"status": status}, "$push": {"history": entry}},
                return_document=ReturnDocument.AFTER,
            )
            if stored is not None:
                return stored
            existing = incidents.find_one({"_id": incident_id})
        if existing is None:
            raise ApiError(
                404, "incident_not_found", "No capture incident has this id."
            )
        raise ApiError(
            409,
            "invalid_transition",
            "An incident only moves forward from OPEN to ACKNOWLEDGED to RESOLVED.",
            [{"field": "status", "code": "backward_move"}],
        )

    def summarize(
        self,
        group_by: str,
        first: date | None,
        last: date | None,
        store_id: int | None,
        allowed_stores: list[int] | None,
    ) -> list[dict[str, Any]]:
        match: dict[str, Any] = {}
        if store_id is not None:
            match["store_id"] = store_id
        elif allowed_stores is not None:
            match["store_id"] = {"$in": allowed_stores}
        if first is not None or last is not None:
            recorded: dict[str, datetime] = {}
            if first is not None:
                recorded["$gte"] = datetime.combine(first, datetime.min.time(), UTC)
            if last is not None:
                recorded["$lt"] = datetime.combine(
                    last + timedelta(days=1), datetime.min.time(), UTC
                )
            match["recorded_at"] = recorded
        expression: str | dict[str, Any] = {
            "store": {"$toString": "$store_id"},
            "day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$recorded_at"}},
            "source_type": "$source_type",
            "refused_code": "$refused_code",
        }[group_by]
        pipeline = [
            {"$match": match},
            {"$group": {"_id": expression, "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]
        with mongo_guard():
            rows = list(self.collection().aggregate(pipeline))
        return [{"key": row["_id"], "count": row["count"]} for row in rows]
