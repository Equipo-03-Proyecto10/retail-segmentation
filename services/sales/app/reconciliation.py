"""Reconciling an import's sales with the sales already recorded, and the
quality report of an import (F26-09, ADR-0070 section 5).

A *case* is a pair: a sale the import made and an earlier sale that looks the
same, with the same customer, store, instant and lines, under a different
source id. The service only ever lists such pairs. It changes no sale: a
person settles each case once, as `DISTINCT` (two real sales) or `DUPLICATE`
(one sale loaded twice), and the decision keeps its author and its moment
(`sales.reconciliation_case`, written once by the database's own trigger).
Cancelling a duplicate sale stays what ADR-0062 says it is, a reversal.

Everything here is parameterized SQL over `sales.reconciliation_case`,
`sales.sale_capture`, `sales.import_row_outcome` and the sale tables.
"""

from __future__ import annotations

from typing import Any

import psycopg
from mosaiq_kit import ApiError
from psycopg import sql

RESOLUTIONS = ("DISTINCT", "DUPLICATE")
STATUSES = ("OPEN", "RESOLVED")
FINISHED = ("COMPLETED", "CANCELLED", "EXPIRED")

# The two sales are compared by their lines, each side as one sorted value.
_FIND = (
    "INSERT INTO sales.reconciliation_case"
    " (import_id, imported_transaction_id, matched_transaction_id)"
    " SELECT c.import_id, a.transaction_id, b.transaction_id"
    " FROM sales.sale_capture AS c"
    " JOIN public.transaction AS a ON a.transaction_id = c.transaction_id"
    " JOIN public.transaction AS b"
    "   ON b.customer_id = a.customer_id AND b.store_id = a.store_id"
    "  AND b.occurred_at = a.occurred_at AND b.transaction_id < a.transaction_id"
    " WHERE c.import_id = %s"
    " AND (SELECT array_agg(l.product_id || ':' || l.quantity || ':' || l.unit_price"
    "      ORDER BY l.product_id) FROM public.transaction_line AS l"
    "      WHERE l.transaction_id = a.transaction_id)"
    "   = (SELECT array_agg(l.product_id || ':' || l.quantity || ':' || l.unit_price"
    "      ORDER BY l.product_id) FROM public.transaction_line AS l"
    "      WHERE l.transaction_id = b.transaction_id)"
    " ORDER BY a.transaction_id, b.transaction_id"
    " ON CONFLICT (imported_transaction_id, matched_transaction_id) DO NOTHING"
    " RETURNING case_id"
)

_CASE = sql.SQL(
    "SELECT k.case_id, k.import_id, k.imported_transaction_id,"
    " a.source_transaction_id, k.matched_transaction_id, b.source_transaction_id,"
    " a.customer_id, a.store_id, a.occurred_at, a.total, k.found_at,"
    " k.resolution, k.resolved_by, k.resolved_at, k.note"
    " FROM sales.reconciliation_case AS k"
    " JOIN public.transaction AS a ON a.transaction_id = k.imported_transaction_id"
    " JOIN public.transaction AS b ON b.transaction_id = k.matched_transaction_id"
)


def case_not_found(import_id: int, case_id: int) -> ApiError:
    return ApiError(
        404,
        "sale_reconciliation_case_not_found",
        f"Reconciliation case {case_id} of import {import_id} was not found.",
    )


def not_finished(import_id: int, status: str) -> ApiError:
    return ApiError(
        409,
        "import_not_finished",
        f"Import {import_id} is {status}; reconcile it once it has finished.",
        [{"field": "status", "code": "not_finished"}],
    )


def already_settled(case_id: int, resolution: str) -> ApiError:
    return ApiError(
        409,
        "case_resolved",
        f"Reconciliation case {case_id} was already settled as {resolution}"
        " and cannot be settled again.",
        [{"field": "resolution", "code": "already_settled"}],
    )


def _case(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "case_id": row[0],
        "import_id": row[1],
        "imported_sale_id": row[2],
        "imported_source_id": row[3],
        "matched_sale_id": row[4],
        "matched_source_id": row[5],
        "customer_id": str(row[6]),
        "store_id": row[7],
        "occurred_at": row[8],
        "total": row[9],
        "found_at": row[10],
        "resolution": row[11],
        "resolved_by": None if row[12] is None else str(row[12]),
        "resolved_at": row[13],
        "note": row[14],
    }


def counts(connection: psycopg.Connection, import_id: int) -> dict[str, int]:
    found, resolved, distinct, duplicate = connection.execute(
        "SELECT count(*), count(resolution),"
        " count(*) FILTER (WHERE resolution = 'DISTINCT'),"
        " count(*) FILTER (WHERE resolution = 'DUPLICATE')"
        " FROM sales.reconciliation_case WHERE import_id = %s",
        (import_id,),
    ).fetchone()
    return {
        "found": found,
        "open": found - resolved,
        "distinct": distinct,
        "duplicate": duplicate,
    }


def run(connection: psycopg.Connection, import_id: int) -> dict[str, int]:
    """List the import's look-alike pairs. Pairs already listed are left as
    they are, settled or not, so running again only adds what is new."""
    added = len(connection.execute(_FIND, (import_id,)).fetchall())
    return {"import_id": import_id, "added": added, **counts(connection, import_id)}


def list_cases(
    connection: psycopg.Connection,
    import_id: int,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    where = sql.SQL(" WHERE k.import_id = %s")
    if status == "OPEN":
        where += sql.SQL(" AND k.resolution IS NULL")
    elif status == "RESOLVED":
        where += sql.SQL(" AND k.resolution IS NOT NULL")
    total = connection.execute(
        sql.SQL("SELECT count(*) FROM sales.reconciliation_case AS k{}").format(where),
        (import_id,),
    ).fetchone()[0]
    rows = connection.execute(
        sql.SQL("{}{} ORDER BY k.case_id LIMIT %s OFFSET %s").format(_CASE, where),
        (import_id, limit, offset),
    ).fetchall()
    return [_case(row) for row in rows], total


def get_case(
    connection: psycopg.Connection, import_id: int, case_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        sql.SQL("{} WHERE k.import_id = %s AND k.case_id = %s").format(_CASE),
        (import_id, case_id),
    ).fetchone()
    return None if row is None else _case(row)


def resolve(
    connection: psycopg.Connection,
    import_id: int,
    case_id: int,
    resolution: str,
    note: str | None,
    author: str,
) -> dict[str, Any]:
    """Settle a case once, as `author`. Settling it again as the same thing
    returns the decision that stands; as another thing, it is a conflict."""
    locked = connection.execute(
        "SELECT resolution FROM sales.reconciliation_case"
        " WHERE import_id = %s AND case_id = %s FOR UPDATE",
        (import_id, case_id),
    ).fetchone()
    if locked is None:
        raise case_not_found(import_id, case_id)
    if locked[0] is not None:
        if locked[0] != resolution:
            raise already_settled(case_id, locked[0])
    else:
        connection.execute(
            "UPDATE sales.reconciliation_case"
            " SET resolution = %s, resolved_by = %s, resolved_at = now(), note = %s"
            " WHERE case_id = %s",
            (resolution, author, note, case_id),
        )
    found = get_case(connection, import_id, case_id)
    assert found is not None
    return found


def quality(connection: psycopg.Connection, import_id: int) -> dict[str, Any] | None:
    """The report of one load: its counts, how many rows failed for each code,
    and where its look-alike pairs stand."""
    row = connection.execute(
        "SELECT i.import_id, i.status, i.total_rows, coalesce(c.received, 0),"
        " coalesce(o.accepted, 0), coalesce(o.rejected, 0), i.created_at,"
        " i.finished_at"
        " FROM sales.import AS i"
        " LEFT JOIN LATERAL (SELECT sum(row_count) AS received"
        "   FROM sales.import_chunk WHERE import_id = i.import_id) AS c ON true"
        " LEFT JOIN LATERAL ("
        "   SELECT count(*) FILTER (WHERE outcome = 'ACCEPTED') AS accepted,"
        "          count(*) FILTER (WHERE outcome = 'REJECTED') AS rejected"
        "   FROM sales.import_row_outcome WHERE import_id = i.import_id) AS o ON true"
        " WHERE i.import_id = %s",
        (import_id,),
    ).fetchone()
    if row is None:
        return None
    received, accepted, rejected = int(row[3]), int(row[4]), int(row[5])
    failures = connection.execute(
        "SELECT code, count(*) FROM sales.import_row_outcome"
        " WHERE import_id = %s AND outcome = 'REJECTED'"
        " GROUP BY code ORDER BY count(*) DESC, code",
        (import_id,),
    ).fetchall()
    return {
        "import_id": row[0],
        "status": row[1],
        "total_rows": row[2],
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "pending": received - accepted - rejected,
        "created_at": row[6],
        "finished_at": row[7],
        "failures": [{"code": code, "count": count} for code, count in failures],
        "reconciliation": counts(connection, import_id),
    }
