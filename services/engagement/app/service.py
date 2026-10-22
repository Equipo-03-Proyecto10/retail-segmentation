"""The engagement rule: an event is recorded once, attributed, voided never
deleted, and counted (RN-55).

Events and their rollup live in MongoDB; PostgreSQL is only asked whether the
store, product and customer an event names exist. Every /v1 operation that
reaches an unreachable database answers 503 database_unavailable.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, Response, request
from lxml import etree
from mosaiq_kit import ApiError, Kit, Page, Representation, configure_logging

from .events import (
    TYPES,
    EventStore,
    event_json,
    to_millis,
    utc_day,
)
from .live import LiveCounters
from .refs import check_references

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"
NAMESPACE = "https://mosaiq.maxthecoder.online/xml/engagement/v1"
COMMON_NAMESPACE = "https://mosaiq.maxthecoder.online/xml/common/v1"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
XSI_TYPES = {"QR_SCAN": "QrScanEvent", "RECO_CLICK": "RecoClickEvent"}
# A device clock may run ahead of the server's by this much (ADR-0038).
FUTURE_TOLERANCE = timedelta(minutes=5)
GROUPS = ("store", "day", "type")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_INTEGER = re.compile(r"-?\d{1,10}")
_UUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def invalid_query(message: str, field: str, code: str) -> ApiError:
    return ApiError(422, "invalid_query", message, [{"field": field, "code": code}])


def parse_instant(text: str) -> datetime | None:
    """An RFC 3339 timestamp with an offset, in UTC; None when it is not one."""

    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return to_millis(moment)


def query_instant(name: str) -> datetime | None:
    raw = request.args.get(name)
    if raw is None:
        return None
    moment = parse_instant(raw)
    if moment is None:
        raise invalid_query(
            f"{name} is not an RFC 3339 timestamp.", name, "invalid_format"
        )
    return moment


def query_integer(name: str) -> int | None:
    raw = request.args.get(name)
    if raw is None:
        return None
    if not _INTEGER.fullmatch(raw) or not -(2**31) <= int(raw) < 2**31:
        raise invalid_query(f"{name} is not a 32-bit integer.", name, "invalid_format")
    return int(raw)


def query_day(name: str) -> date | None:
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        if not _DAY.fullmatch(raw):
            raise ValueError(raw)
        return date.fromisoformat(raw)
    except ValueError:
        raise invalid_query(
            f"{name} is not a YYYY-MM-DD date.", name, "invalid_format"
        ) from None


def event_representation(document: dict[str, Any], status: int = 200) -> Representation:
    return Representation(
        "EngagementEvent",
        event_json(document),
        status=status,
        xsi_type=XSI_TYPES[document["type"]],
    )


def type_the_page(response: Response) -> Response:
    """Give each EngagementEvent of a listed page its xsi:type.

    The kit renders a page's items alike; the contract's XML makes them a
    polymorphic type (QrScanEvent or RecoClickEvent), so the discriminator is
    added to the rendered page.
    """

    if (
        request.endpoint != "mosaiq_operation_listEngagementEvents"
        or response.status_code != 200
        or response.mimetype != "application/xml"
    ):
        return response
    root = etree.fromstring(response.get_data())
    typed = etree.Element(
        root.tag,
        nsmap={None: NAMESPACE, "common": COMMON_NAMESPACE, "xsi": XSI_NAMESPACE},
    )
    for child in list(root):
        if child.tag == f"{{{NAMESPACE}}}EngagementEvent":
            kind = child.findtext(f"{{{NAMESPACE}}}type")
            child.set(f"{{{XSI_NAMESPACE}}}type", XSI_TYPES[kind or "QR_SCAN"])
        typed.append(child)
    response.set_data(etree.tostring(typed, xml_declaration=True, encoding="UTF-8"))
    return response


def create_app(
    *, events: EventStore | None = None, live: LiveCounters | None = None
) -> Flask:
    app = Flask(__name__)
    configure_logging()
    holder: dict[str, Kit] = {}
    store = events if events is not None else EventStore.from_environment()
    counters = live if live is not None else LiveCounters.from_environment()

    kit = Kit(
        app,
        service="engagement",
        suffix="engagement",
        contracts_dir=CONTRACTS_DIR,
        ready_checks=[store.ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit
    app.extensions["engagement_store"] = store
    app.extensions["engagement_live"] = counters
    app.after_request(type_the_page)

    @kit.operation("createEngagementEvent")
    def create_event() -> Representation:
        body = kit.body
        occurred_at = parse_instant(body["occurred_at"])
        if occurred_at is None:
            raise ApiError(
                422,
                "invalid_body",
                "The parsed body violates the request schema.",
                [{"field": "occurred_at", "code": "invalid_format"}],
            )
        now = to_millis(datetime.now(UTC))
        if occurred_at > now + FUTURE_TOLERANCE:
            raise ApiError(
                422,
                "occurred_in_future",
                "occurred_at is more than five minutes ahead of the server.",
                [{"field": "occurred_at", "code": "in_future"}],
            )
        customer_id = body.get("customer_id")
        if customer_id is not None:
            customer_id = customer_id.lower()
        with kit.db.connection() as connection:
            check_references(
                connection, body["store_id"], body["product_id"], customer_id
            )
        document: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "client_event_id": body["client_event_id"].lower(),
            "type": body["type"],
            "store_id": body["store_id"],
            "product_id": body["product_id"],
        }
        if customer_id is not None:
            document["customer_id"] = customer_id
        if body["type"] == "RECO_CLICK":
            document["label_code"] = body["label_code"]
            document["rank"] = body["rank"]
        document |= {
            "recorded_by": str(kit.principal["sub"]),
            "occurred_at": occurred_at,
            "recorded_at": now,
            "voided": False,
            "history": [],
        }
        stored, created = store.insert(document)
        if not created:
            return event_representation(stored, status=200)
        count_it(stored, 1)
        return event_representation(stored, status=201)

    def count_it(document: dict[str, Any], delta: int) -> None:
        """Move the day's rollup, then its live counter, by one event."""

        day = utc_day(document["occurred_at"])
        total = store.add_to_rollup(document["store_id"], day, document["type"], delta)
        counters.count(document["store_id"], day, document["type"], delta, total)

    @kit.operation("listEngagementEvents")
    def list_events() -> Page:
        query: dict[str, Any] = {}
        kind = request.args.get("type")
        if kind is not None:
            if kind not in TYPES:
                raise invalid_query(
                    "type must be QR_SCAN or RECO_CLICK.", "type", "unknown_value"
                )
            query["type"] = kind
        store_id = query_integer("store_id")
        if store_id is not None:
            query["store_id"] = store_id
        customer_id = request.args.get("customer_id")
        if customer_id is not None:
            if not _UUID.fullmatch(customer_id):
                raise invalid_query(
                    "customer_id is not a UUID.", "customer_id", "invalid_format"
                )
            query["customer_id"] = customer_id.lower()
        voided = request.args.get("voided")
        if voided is not None:
            if voided not in ("true", "false"):
                raise invalid_query(
                    "voided must be true or false.", "voided", "invalid_format"
                )
            query["voided"] = voided == "true"
        first, last = query_instant("from"), query_instant("to")
        if first is not None and last is not None and last < first:
            raise invalid_query("to is before from.", "to", "before_from")
        if first is not None or last is not None:
            query["occurred_at"] = {
                **({"$gte": first} if first is not None else {}),
                **({"$lte": last} if last is not None else {}),
            }
        documents, total = store.find(query, (kit.page - 1) * kit.size, kit.size)
        return Page(
            "EngagementEvents",
            "EngagementEvent",
            "engagement_events",
            [event_json(document) for document in documents],
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("getEngagementEvent")
    def get_event(event_id: Any) -> Representation:
        document = store.get(str(event_id))
        if document is None:
            raise ApiError(404, "event_not_found", f"Event {event_id} was not found.")
        return event_representation(document)

    @kit.operation("voidEngagementEvent")
    def void_event(event_id: Any) -> Representation:
        reason = kit.body["reason"]
        if not reason.strip():
            raise ApiError(
                422,
                "invalid_body",
                "The parsed body violates the request schema.",
                [{"field": "reason", "code": "too_short"}],
            )
        document = store.void(
            str(event_id),
            reason,
            str(kit.principal["sub"]),
            to_millis(datetime.now(UTC)),
        )
        count_it(document, -1)
        return event_representation(document)

    @kit.operation("getEngagementSummary")
    def get_summary() -> Representation:
        group_by = request.args.get("group_by")
        if group_by not in GROUPS:
            raise invalid_query(
                "group_by must be store, day or type.", "group_by", "unknown_group_by"
            )
        first, last = query_day("from"), query_day("to")
        if first is not None and last is not None and last < first:
            raise invalid_query("to is before from.", "to", "before_from")
        store_id = query_integer("store_id")
        today = datetime.now(UTC).date()
        groups = None
        if store_id is not None and first == today and last == today:
            groups = today_from_counters(group_by, store_id, today)
        if groups is None:
            groups = store.summarize(
                group_by,
                first.isoformat() if first else None,
                last.isoformat() if last else None,
                store_id,
            )
        return Representation(
            "EngagementSummary",
            {"from": first, "to": last, "group_by": group_by, "groups": groups},
        )

    def today_from_counters(
        group_by: str, store_id: int, today: date
    ) -> list[dict[str, Any]] | None:
        """One store, today: the live counters, else None (read MongoDB)."""

        day = today.isoformat()
        read = counters.read(store_id, day, TYPES)
        if read is None:
            return None
        # A counter that is missing is read from the rollup it caches.
        counts = {
            kind: (
                read[kind]
                if read[kind] is not None
                else store.daily_count(store_id, day, kind)
            )
            for kind in TYPES
        }
        if group_by == "type":
            rows = [(kind, counts[kind]) for kind in TYPES]
        elif group_by == "store":
            rows = [(str(store_id), sum(counts.values()))]
        else:
            rows = [(day, sum(counts.values()))]
        return [{"key": key, "count": count} for key, count in rows if count > 0]

    return app
