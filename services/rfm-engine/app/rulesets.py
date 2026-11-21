"""Rule sets as an analyst governs them: a draft, a publication, a new version.

A rule set is a DRAFT until it is published, and edited only then. A PUBLISHED one is
frozen, by this module and by the table's own guard (BOUNDARY rfm-engine), so no
route and no role can rewrite what a profile was scored against. A change to one is
a new version: a row of its own, whose parent is the version it changes (F22-09,
ADR-0064). Every change is a domain event, recorded in the transaction that makes it
and naming its author (ADR-0072).

Every function takes the request's connection and raises ``ApiError``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

import psycopg
from flask import g
from mosaiq_kit import ApiError
from psycopg import Connection
from psycopg.errors import UniqueViolation

LOGGER = logging.getLogger(__name__)

DRAFT = "DRAFT"
PUBLISHED = "PUBLISHED"
TREATMENTS = ("DEDUCT", "KEEP")

# Whole statements, never built from pieces: every value is bound.
COLUMNS_LIST = (
    "SELECT rule_set_id, name, window_days, recency_boundaries, frequency_boundaries,"
    " monetary_boundaries, fallback_label_code, cancelled_sales, returned_sales,"
    " state, version_no, parent_rule_set_id, published_at, created_at, created_by"
    " FROM rfm.rule_set"
    " WHERE (%(published)s::boolean IS NULL OR (state = 'PUBLISHED') = %(published)s)"
    " ORDER BY rule_set_id DESC LIMIT %(limit)s OFFSET %(offset)s"
)
COUNT = (
    "SELECT count(*) FROM rfm.rule_set"
    " WHERE (%(published)s::boolean IS NULL OR (state = 'PUBLISHED') = %(published)s)"
)
COLUMNS_ONE = (
    "SELECT rule_set_id, name, window_days, recency_boundaries, frequency_boundaries,"
    " monetary_boundaries, fallback_label_code, cancelled_sales, returned_sales,"
    " state, version_no, parent_rule_set_id, published_at, created_at, created_by"
    " FROM rfm.rule_set WHERE rule_set_id = %s"
)
LOCK_ONE = (
    "SELECT state, version_no FROM rfm.rule_set WHERE rule_set_id = %s FOR UPDATE"
)
BANDS = (
    "SELECT rule_set_id, label_code, r_min, r_max, f_min, f_max, m_min, m_max"
    " FROM rfm.rule_set_band WHERE rule_set_id = ANY(%s)"
    " ORDER BY rule_set_id, band_no"
)
INSERT = (
    "INSERT INTO rfm.rule_set (name, window_days, recency_boundaries,"
    " frequency_boundaries, monetary_boundaries, fallback_label_code,"
    " cancelled_sales, returned_sales, version_no, parent_rule_set_id, created_by)"
    " VALUES (%s, %s, %s, %s, %s::numeric(12,2)[], %s, %s, %s, %s, %s, %s)"
    " RETURNING rule_set_id"
)
UPDATE = (
    "UPDATE rfm.rule_set SET name = %s, window_days = %s, recency_boundaries = %s,"
    " frequency_boundaries = %s, monetary_boundaries = %s::numeric(12,2)[],"
    " fallback_label_code = %s, cancelled_sales = %s, returned_sales = %s"
    " WHERE rule_set_id = %s"
)
PUBLISH = (
    "UPDATE rfm.rule_set SET state = 'PUBLISHED', published_at = now()"
    " WHERE rule_set_id = %s"
)
SUCCESSOR = "SELECT 1 FROM rfm.rule_set WHERE parent_rule_set_id = %s"
DELETE_BANDS = "DELETE FROM rfm.rule_set_band WHERE rule_set_id = %s"
INSERT_BAND = (
    "INSERT INTO rfm.rule_set_band (rule_set_id, band_no, label_code, r_min, r_max,"
    " f_min, f_max, m_min, m_max) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
PARTITION = "SELECT uncovered, overlapped FROM rfm.rule_set_partition(%s)"
RECORD = (
    "SELECT created FROM api_audit.record_event_v1("
    "%s, 'CRITICAL_ACTION', 'SUCCESS', now(), 'USER', %s, %s, NULL::smallint,"
    " 'rule_set', %s, NULL::bigint, %s, %s, %s::jsonb)"
)


@dataclass(frozen=True, slots=True)
class Definition:
    """The part of a rule set an analyst writes, checked."""

    name: str
    window_days: int
    recency: list[int]
    frequency: list[int]
    monetary: list[str]
    bands: list[dict[str, Any]]
    fallback_label_code: str
    cancelled_sales: str
    returned_sales: str


def _invalid(message: str, field: str, code: str) -> ApiError:
    return ApiError(422, "invalid_body", message, [{"field": field, "code": code}])


def not_found() -> ApiError:
    return ApiError(
        404, "rule_set_not_found", "No rule set has the requested rule_set_id."
    )


def published() -> ApiError:
    return ApiError(
        409,
        "rule_set_published",
        "A published rule set is never edited: create a new version with "
        "POST /v1/rfm-rule-sets/{rule_set_id}/versions.",
    )


def _non_decreasing(values: list[Any]) -> bool:
    return all(a <= b for a, b in zip(values, values[1:], strict=False))


def check(body: dict[str, Any]) -> Definition:
    """The monotonic boundaries, the ranges of each band and the supported treatment.

    The schema already fixed the shapes and the bounds; these are the orderings it
    cannot say. That the bands partition the score space is the database's to count
    (``rfm.rule_set_partition``), once they are stored.
    """
    recency = [int(v) for v in body["recency_boundaries"]]
    frequency = [int(v) for v in body["frequency_boundaries"]]
    try:
        monetary = [Decimal(str(v)) for v in body["monetary_boundaries"]]
    except InvalidOperation:
        raise _invalid(
            "monetary_boundaries must be amounts.", "monetary_boundaries", "not_money"
        ) from None
    if not _non_decreasing(recency):
        raise _invalid(
            "Recency boundaries must not decrease.",
            "recency_boundaries",
            "not_monotonic",
        )
    if not _non_decreasing(frequency[::-1]):
        raise _invalid(
            "Frequency boundaries must not increase.",
            "frequency_boundaries",
            "not_monotonic",
        )
    if not _non_decreasing(monetary[::-1]) or monetary[3] < 0:
        raise _invalid(
            "Monetary boundaries must not increase and may not be negative.",
            "monetary_boundaries",
            "not_monotonic",
        )
    bands = list(body["bands"])
    for band in bands:
        if not (
            band["r_min"] <= band["r_max"]
            and band["f_min"] <= band["f_max"]
            and band["m_min"] <= band["m_max"]
        ):
            raise _invalid(
                "A band's minimum cannot exceed its maximum.", "bands", "band_range"
            )
    cancelled = body.get("cancelled_sales", "DEDUCT")
    returned = body.get("returned_sales", "DEDUCT")
    if cancelled != returned:
        raise _invalid(
            "Cancelled and returned sales count the same way: the sales contract "
            "does not yet say which of the two took a unit away.",
            "returned_sales",
            "treatment_unsupported",
        )
    return Definition(
        name=body["name"],
        window_days=body["window_days"],
        recency=recency,
        frequency=frequency,
        monetary=[f"{v:.2f}" for v in monetary],
        bands=bands,
        fallback_label_code=body["fallback_label_code"],
        cancelled_sales=cancelled,
        returned_sales=returned,
    )


def _store_bands(connection: Connection, rule_set_id: int, bands: list[Any]) -> None:
    for number, band in enumerate(bands, start=1):
        connection.execute(
            INSERT_BAND,
            (
                rule_set_id,
                number,
                band["label_code"],
                band["r_min"],
                band["r_max"],
                band["f_min"],
                band["f_max"],
                band["m_min"],
                band["m_max"],
            ),
        )


def _partition(connection: Connection, rule_set_id: int) -> None:
    """RN-46: every triple of scores sits in exactly one band."""
    row = connection.execute(PARTITION, (rule_set_id,)).fetchone()
    assert row is not None
    uncovered, overlapped = row
    if overlapped:
        raise _invalid(
            f"{overlapped} of the 125 score triples sit in more than one band.",
            "bands",
            "bands_overlap",
        )
    if uncovered:
        raise _invalid(
            f"{uncovered} of the 125 score triples sit in no band.",
            "bands",
            "bands_gap",
        )


def record(
    connection: Connection,
    principal: dict[str, Any],
    action: str,
    rule_set_id: int,
    version_no: int,
) -> None:
    """Append the audit fact in the transaction of the change, or fail it closed."""
    try:
        connection.execute(
            RECORD,
            (
                str(uuid4()),
                str(principal["sub"]),
                principal["role"],
                str(rule_set_id),
                version_no,
                getattr(g, "correlation_id", None),
                json.dumps({"action": action}),
            ),
        )
    except psycopg.Error as error:
        LOGGER.error("rule set audit failed: sqlstate=%s", error.sqlstate)
        raise ApiError(
            503, "database_unavailable", "The service database is unavailable."
        ) from None


def _taken(error: UniqueViolation) -> ApiError:
    constraint = error.diag.constraint_name
    if constraint == "rule_set_one_successor":
        return ApiError(
            409,
            "rule_set_version_exists",
            "This rule set already has a newer version; edit that draft.",
        )
    if constraint == "rule_set_name_key":
        return ApiError(
            409, "rule_set_name_taken", "A rule set with this name already exists."
        )
    raise error


def create(
    connection: Connection,
    principal: dict[str, Any],
    definition: Definition,
    *,
    parent: int | None = None,
    version_no: int = 1,
) -> int:
    """Store a DRAFT, with its bands, and record who made it."""
    try:
        with connection.transaction():
            row = connection.execute(
                INSERT,
                (
                    definition.name,
                    definition.window_days,
                    definition.recency,
                    definition.frequency,
                    definition.monetary,
                    definition.fallback_label_code,
                    definition.cancelled_sales,
                    definition.returned_sales,
                    version_no,
                    parent,
                    str(principal["sub"]),
                ),
            ).fetchone()
            assert row is not None
            rule_set_id = int(row[0])
            _store_bands(connection, rule_set_id, definition.bands)
            _partition(connection, rule_set_id)
    except UniqueViolation as error:
        raise _taken(error) from None
    record(
        connection,
        principal,
        "RULE_SET_CREATED" if parent is None else "RULE_SET_VERSIONED",
        rule_set_id,
        version_no,
    )
    return rule_set_id


def edit(
    connection: Connection,
    principal: dict[str, Any],
    rule_set_id: int,
    definition: Definition,
) -> None:
    """Replace a DRAFT's definition. A published rule set is refused, never edited."""
    row = connection.execute(LOCK_ONE, (rule_set_id,)).fetchone()
    if row is None:
        raise not_found()
    if row[0] == PUBLISHED:
        raise published()
    try:
        with connection.transaction():
            connection.execute(
                UPDATE,
                (
                    definition.name,
                    definition.window_days,
                    definition.recency,
                    definition.frequency,
                    definition.monetary,
                    definition.fallback_label_code,
                    definition.cancelled_sales,
                    definition.returned_sales,
                    rule_set_id,
                ),
            )
            connection.execute(DELETE_BANDS, (rule_set_id,))
            _store_bands(connection, rule_set_id, definition.bands)
            _partition(connection, rule_set_id)
    except UniqueViolation as error:
        raise _taken(error) from None
    record(connection, principal, "RULE_SET_EDITED", rule_set_id, int(row[1]))


def version(
    connection: Connection,
    principal: dict[str, Any],
    rule_set_id: int,
    definition: Definition,
) -> int:
    """The next version of a PUBLISHED rule set, as a DRAFT with the changes."""
    row = connection.execute(LOCK_ONE, (rule_set_id,)).fetchone()
    if row is None:
        raise not_found()
    if row[0] != PUBLISHED:
        raise ApiError(
            409,
            "rule_set_not_published",
            "Only a published rule set has versions; edit this draft instead.",
        )
    if connection.execute(SUCCESSOR, (rule_set_id,)).fetchone() is not None:
        raise ApiError(
            409,
            "rule_set_version_exists",
            "This rule set already has a newer version; edit that draft.",
        )
    return create(
        connection,
        principal,
        definition,
        parent=rule_set_id,
        version_no=int(row[1]) + 1,
    )


def publish(
    connection: Connection, principal: dict[str, Any], rule_set_id: int
) -> None:
    """Freeze a DRAFT. One way: nothing here moves a rule set back."""
    row = connection.execute(LOCK_ONE, (rule_set_id,)).fetchone()
    if row is None:
        raise not_found()
    if row[0] == PUBLISHED:
        raise published()
    _partition(connection, rule_set_id)
    connection.execute(PUBLISH, (rule_set_id,))
    record(connection, principal, "RULE_SET_PUBLISHED", rule_set_id, int(row[1]))


def _item(row: tuple[Any, ...], bands: list[tuple[Any, ...]]) -> dict[str, Any]:
    return {
        "rule_set_id": row[0],
        "name": row[1],
        "window_days": row[2],
        "recency_boundaries": list(row[3]),
        "frequency_boundaries": list(row[4]),
        "monetary_boundaries": [f"{value:.2f}" for value in row[5]],
        "bands": [
            {
                "label_code": band[1],
                "r_min": band[2],
                "r_max": band[3],
                "f_min": band[4],
                "f_max": band[5],
                "m_min": band[6],
                "m_max": band[7],
            }
            for band in bands
            if band[0] == row[0]
        ],
        "fallback_label_code": row[6],
        "cancelled_sales": row[7],
        "returned_sales": row[8],
        "state": row[9],
        "version_no": row[10],
        "parent_rule_set_id": row[11],
        "published": row[9] == PUBLISHED,
        "published_at": row[12],
        "created_at": row[13],
        "created_by": str(row[14]) if row[14] else None,
    }


# What the listing carries: the v1 RfmRuleSet, which has no state, version or
# treatment (contracts/rfm-engine, F22-03). `published` tells a draft from a rule
# set that is frozen; the full record is `GET /v1/rfm-rule-sets/{rule_set_id}`.
COMPACT = (
    "rule_set_id",
    "name",
    "window_days",
    "recency_boundaries",
    "frequency_boundaries",
    "monetary_boundaries",
    "bands",
    "fallback_label_code",
    "published",
    "created_at",
    "created_by",
)


def compact(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in COMPACT}


def listing(
    connection: Connection, *, published_only: bool | None, page: int, size: int
) -> tuple[list[dict[str, Any]], int]:
    values = {
        "published": published_only,
        "limit": size,
        "offset": (page - 1) * size,
    }
    total = connection.execute(COUNT, values).fetchone()
    rows = connection.execute(COLUMNS_LIST, values).fetchall()
    bands = connection.execute(BANDS, ([row[0] for row in rows],)).fetchall()
    return [_item(row, bands) for row in rows], int(total[0] if total else 0)


def one(connection: Connection, rule_set_id: int) -> dict[str, Any]:
    row = connection.execute(COLUMNS_ONE, (rule_set_id,)).fetchone()
    if row is None:
        raise not_found()
    bands = connection.execute(BANDS, ([rule_set_id],)).fetchall()
    return _item(row, bands)
