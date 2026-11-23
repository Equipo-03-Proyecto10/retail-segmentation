"""The current RFM profiles exposed by the read operations (F22-19, RN-65).

Reads return only the values left by a consumer or scheduled recomputation. A
registered customer without such a row is represented as ``pending``; reading never
computes or stores a profile.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from mosaiq_kit import ApiError, money
from psycopg import Connection

STATES = ("scored", "unassigned", "pending")
BIGINT_MAX = 2**63 - 1

_LIST = (
    "WITH matches AS ("
    " SELECT c.customer_id, COALESCE(p.state, 'pending') AS state, p.rule_set_id,"
    " p.r_days, p.f_count, p.m_total, p.r_score, p.f_score, p.m_score,"
    " p.label_code, p.via_fallback, p.computed_at, p.next_change_at"
    " FROM api_customers.customer_ref_v1 AS c"
    " LEFT JOIN rfm.customer_profile AS p ON p.customer_id = c.customer_id"
    " WHERE ((%(state)s = 'pending' AND p.customer_id IS NULL)"
    "     OR (%(state)s IS DISTINCT FROM 'pending' AND p.customer_id IS NOT NULL"
    "         AND (%(state)s::text IS NULL OR p.state = %(state)s::text)))"
    " AND (%(label)s::text IS NULL OR p.label_code = %(label)s::text)"
    " AND (%(rule_set)s::bigint IS NULL OR p.rule_set_id = %(rule_set)s::bigint)"
    "), page_rows AS ("
    " SELECT * FROM matches ORDER BY customer_id"
    " LIMIT %(limit)s OFFSET %(offset)s"
    ")"
    " SELECT p.customer_id, p.state, p.rule_set_id, p.r_days, p.f_count,"
    " p.m_total, p.r_score, p.f_score, p.m_score, p.label_code,"
    " p.via_fallback, p.computed_at, p.next_change_at, totals.total"
    " FROM (SELECT count(*) AS total FROM matches) AS totals"
    " LEFT JOIN page_rows AS p ON true ORDER BY p.customer_id"
)
_ONE = (
    "SELECT c.customer_id, COALESCE(p.state, 'pending'), p.rule_set_id, p.r_days,"
    " p.f_count, p.m_total, p.r_score, p.f_score, p.m_score, p.label_code,"
    " p.via_fallback, p.computed_at, p.next_change_at"
    " FROM api_customers.customer_ref_v1 AS c"
    " LEFT JOIN rfm.customer_profile AS p ON p.customer_id = c.customer_id"
    " WHERE c.customer_id = %s"
)


def filters(args: Any) -> dict[str, Any]:
    """Validate the three filters declared by ``listRfmProfiles``."""
    details: list[dict[str, str]] = []
    state = args.get("state")
    if state is not None and state not in STATES:
        details.append({"field": "state", "code": "unknown_value"})

    label_code = args.get("label_code")
    if label_code is not None and "\x00" in label_code:
        details.append({"field": "label_code", "code": "invalid_format"})

    raw_rule_set = args.get("rule_set_id")
    rule_set_id: int | None = None
    if raw_rule_set is not None:
        if re.fullmatch(r"[0-9]+", raw_rule_set) is None:
            details.append({"field": "rule_set_id", "code": "invalid_type"})
        else:
            digits = raw_rule_set.lstrip("0") or "0"
            if len(digits) > len(str(BIGINT_MAX)):
                details.append({"field": "rule_set_id", "code": "out_of_range"})
            else:
                value = int(digits)
                if not 1 <= value <= BIGINT_MAX:
                    details.append({"field": "rule_set_id", "code": "out_of_range"})
                else:
                    rule_set_id = value

    if details:
        raise ApiError(422, "invalid_query", "A filter is malformed.", details)
    return {
        "state": state,
        "label_code": label_code,
        "rule_set_id": rule_set_id,
    }


def _profile(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "customer_id": str(row[0]),
        "state": row[1],
        "rule_set_id": row[2],
        "r_days": row[3],
        "f_count": row[4],
        "m_total": None if row[5] is None else money(row[5]),
        "r_score": row[6],
        "f_score": row[7],
        "m_score": row[8],
        "label_code": row[9],
        "via_fallback": row[10],
        "computed_at": row[11],
        "next_change_at": row[12],
    }


def listing(
    connection: Connection,
    *,
    state: str | None,
    label_code: str | None,
    rule_set_id: int | None,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """Stored profiles, except that pending customers appear only when requested."""
    values = {
        "state": state,
        "label": label_code,
        "rule_set": rule_set_id,
        "limit": size,
        "offset": (page - 1) * size,
    }
    rows = connection.execute(_LIST, values).fetchall()
    total = int(rows[0][13]) if rows else 0
    return [_profile(row) for row in rows if row[0] is not None], total


def one(connection: Connection, customer_id: UUID) -> dict[str, Any]:
    row = connection.execute(_ONE, (customer_id,)).fetchone()
    if row is None:
        raise ApiError(404, "customer_not_found", "The customer does not exist.")
    return _profile(row)
