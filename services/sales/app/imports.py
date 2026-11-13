"""The control state of a sales import (ADR-0070): opening one, staging its
chunks, reading its counts and its rejections, cancelling it.

Everything here is parameterized SQL over `sales.import`, `sales.import_chunk` and
`sales.import_row_outcome`, plus the contract's refusals. The rows themselves are
applied by the worker (`import_worker.py`), never inside a request.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import psycopg
from mosaiq_kit import ApiError
from psycopg.types.json import Jsonb

from .rows import COLUMNS
from .salerules import error

CONTRACT_VERSION = 1
CHUNK_BYTES = 60_000

IMPORT_COLUMNS = (
    "i.import_id, i.status, i.contract_version, i.total_rows, i.chunk_count,"
    " coalesce(c.chunks, 0), coalesce(c.received, 0), coalesce(o.accepted, 0),"
    " coalesce(o.rejected, 0), i.created_at, i.finished_at, i.created_by"
)
IMPORT_FROM = (
    " FROM sales.import AS i"
    " LEFT JOIN LATERAL (SELECT count(*) AS chunks, sum(row_count) AS received"
    "   FROM sales.import_chunk WHERE import_id = i.import_id) AS c ON true"
    " LEFT JOIN LATERAL ("
    "   SELECT count(*) FILTER (WHERE outcome = 'ACCEPTED') AS accepted,"
    "          count(*) FILTER (WHERE outcome = 'REJECTED') AS rejected"
    "   FROM sales.import_row_outcome WHERE import_id = i.import_id) AS o ON true"
)


def expiry_hours() -> int:
    try:
        value = int(os.environ.get("SALES_IMPORT_EXPIRY_HOURS", "24"))
    except ValueError:
        return 24
    return value if value >= 1 else 24


def _import(row: tuple[Any, ...]) -> dict[str, Any]:
    received = int(row[6])
    accepted, rejected = int(row[7]), int(row[8])
    return {
        "import_id": row[0],
        "status": row[1],
        "contract_version": row[2],
        "total_rows": row[3],
        "chunk_count": row[4],
        "next_sequence": row[5],
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "pending": received - accepted - rejected,
        "created_at": row[9],
        "finished_at": row[10],
        "created_by": str(row[11]),
    }


def expire_stale(connection: psycopg.Connection) -> None:
    """An import that received no chunk for 24 hours is EXPIRED, and the rows it
    staged and never applied are dropped (their count stays in `pending`)."""
    expired = connection.execute(
        "UPDATE sales.import SET status = 'EXPIRED', finished_at = now()"
        " WHERE status = 'RECEIVING'"
        " AND last_chunk_at < now() - make_interval(hours => %s)"
        " RETURNING import_id",
        (expiry_hours(),),
    ).fetchall()
    if expired:
        connection.execute(
            "UPDATE sales.import_chunk SET rows = NULL"
            " WHERE import_id = ANY(%s) AND status = 'PENDING'",
            ([row[0] for row in expired],),
        )


def get_import(
    connection: psycopg.Connection, import_id: int, *, lock: bool = False
) -> dict[str, Any] | None:
    if lock:
        # The row lock serializes a chunk, a cancel and the worker's status
        # check; the counts are read after it.
        locked = connection.execute(
            "SELECT 1 FROM sales.import WHERE import_id = %s FOR UPDATE",
            (import_id,),
        ).fetchone()
        if locked is None:
            return None
    row = connection.execute(
        "SELECT "
        + IMPORT_COLUMNS
        + IMPORT_FROM
        + " WHERE i.import_id = %s",  # noqa: S608
        (import_id,),
    ).fetchone()
    return None if row is None else _import(row)


def list_imports(
    connection: psycopg.Connection, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    total = connection.execute("SELECT count(*) FROM sales.import").fetchone()[0]
    rows = connection.execute(
        "SELECT "
        + IMPORT_COLUMNS
        + IMPORT_FROM  # noqa: S608
        + " ORDER BY i.created_at DESC, i.import_id DESC LIMIT %s OFFSET %s",
        (limit, offset),
    ).fetchall()
    return [_import(row) for row in rows], total


def canonical_create(body: dict[str, Any], sub: str) -> bytes:
    value = {
        "sub": sub,
        "contract_version": body["contract_version"],
        "header": list(body["header"]),
        "total_rows": body["total_rows"],
        "chunk_count": body["chunk_count"],
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


def check_create(body: dict[str, Any]) -> None:
    """What the schema leaves to the service: the version and the header names."""
    if body["contract_version"] != CONTRACT_VERSION:
        raise error(
            422,
            "invalid_body",
            "The import violates its schema: only contract version 1 is supported.",
            "contract_version",
            "unsupported_version",
        )
    for index, name in enumerate(body["header"]):
        if name != COLUMNS[index]:
            raise error(
                422,
                "invalid_body",
                "The import violates its schema: the header must be the eight"
                " columns of contract version 1, in order.",
                f"header[{index}]",
                "unexpected_column",
            )


def find_by_key(connection: psycopg.Connection, key: str) -> tuple[int, bytes] | None:
    row = connection.execute(
        "SELECT import_id, request_sha256 FROM sales.import"
        " WHERE idempotency_key = %s",
        (key,),
    ).fetchone()
    return None if row is None else (row[0], bytes(row[1]))


def newest_open(connection: psycopg.Connection) -> tuple[int, str] | None:
    row = connection.execute(
        "SELECT import_id, status FROM sales.import"
        " WHERE status IN ('RECEIVING', 'PROCESSING')"
        " ORDER BY created_at DESC, import_id DESC LIMIT 1"
    ).fetchone()
    return None if row is None else (row[0], row[1])


def in_progress(import_id: int, status: str) -> ApiError:
    return ApiError(
        409,
        "import_in_progress",
        f"Import {import_id} is still {status}; wait for it to complete, or"
        " cancel it, before opening another.",
    )


def create_import(
    connection: psycopg.Connection,
    *,
    key: str,
    digest: bytes,
    body: dict[str, Any],
    created_by: str,
) -> int:
    return connection.execute(
        "INSERT INTO sales.import (idempotency_key, request_sha256,"
        " contract_version, total_rows, chunk_count, created_by)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING import_id",
        (
            key,
            digest,
            body["contract_version"],
            body["total_rows"],
            body["chunk_count"],
            created_by,
        ),
    ).fetchone()[0]


def canonical_chunk(rows: list[dict[str, Any]]) -> bytes:
    """The rows by `line`, each with its cells in order, so a JSON and an XML
    retry compare equal."""
    value = [
        {"line": row["line"], "cells": list(row["cells"])}
        for row in sorted(rows, key=lambda item: item["line"])
    ]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


def check_lines(rows: list[dict[str, Any]], after: int) -> None:
    """Lines are unique and increasing within the chunk and after the last line of
    the chunk before it (ADR-0070 section 1)."""
    previous = after
    for index, row in enumerate(rows):
        if row["line"] <= previous:
            raise error(
                422,
                "invalid_body",
                "The chunk violates its schema: lines must be unique and increasing.",
                f"rows[{index}].line",
                "not_increasing",
            )
        previous = row["line"]


def stage_chunk(
    connection: psycopg.Connection,
    *,
    imp: dict[str, Any],
    sequence: int,
    digest: bytes,
    rows: list[dict[str, Any]],
) -> None:
    """Stage one chunk and, when it is the last, move the import to PROCESSING.

    The caller holds the import's row lock and has checked everything the
    contract refuses; this only writes."""
    payload = [{"line": row["line"], "cells": list(row["cells"])} for row in rows]
    lines = [row["line"] for row in rows]
    connection.execute(
        "INSERT INTO sales.import_chunk (import_id, sequence, request_sha256,"
        " row_count, first_line, last_line, rows) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            imp["import_id"],
            sequence,
            digest,
            len(rows),
            min(lines),
            max(lines),
            Jsonb(payload),
        ),
    )
    connection.execute(
        "UPDATE sales.import SET last_chunk_at = now(),"
        " status = CASE WHEN %s THEN 'PROCESSING' ELSE status END"
        " WHERE import_id = %s",
        (sequence == imp["chunk_count"] - 1, imp["import_id"]),
    )


def previous_last_line(
    connection: psycopg.Connection, import_id: int, sequence: int
) -> int:
    row = connection.execute(
        "SELECT last_line FROM sales.import_chunk"
        " WHERE import_id = %s AND sequence = %s",
        (import_id, sequence - 1),
    ).fetchone()
    return 1 if row is None else row[0]


def find_chunk(
    connection: psycopg.Connection, import_id: int, sequence: int
) -> bytes | None:
    row = connection.execute(
        "SELECT request_sha256 FROM sales.import_chunk"
        " WHERE import_id = %s AND sequence = %s",
        (import_id, sequence),
    ).fetchone()
    return None if row is None else bytes(row[0])


def cancel_import(connection: psycopg.Connection, import_id: int) -> None:
    """Cancel an import that is still RECEIVING or PROCESSING. Rows already
    accepted stay accepted (ADR-0020); the chunks not applied yet are dropped,
    and their rows stay in `pending`."""
    connection.execute(
        "UPDATE sales.import SET status = 'CANCELLED', finished_at = now()"
        " WHERE import_id = %s",
        (import_id,),
    )
    connection.execute(
        "UPDATE sales.import_chunk SET rows = NULL"
        " WHERE import_id = %s AND status = 'PENDING'",
        (import_id,),
    )


def list_rejections(
    connection: psycopg.Connection, import_id: int, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    total = connection.execute(
        "SELECT count(*) FROM sales.import_row_outcome"
        " WHERE import_id = %s AND outcome = 'REJECTED'",
        (import_id,),
    ).fetchone()[0]
    rows = connection.execute(
        "SELECT line, field, code, message, duplicate_of, first_line, sale_id"
        " FROM sales.import_row_outcome WHERE import_id = %s AND outcome = 'REJECTED'"
        " ORDER BY line LIMIT %s OFFSET %s",
        (import_id, limit, offset),
    ).fetchall()
    return [
        {
            "line": row[0],
            "field": row[1],
            "code": row[2],
            "message": row[3],
            "duplicate_of": row[4],
            "first_line": row[5],
            "sale_id": row[6],
        }
        for row in rows
    ], total


def not_found(import_id: int) -> ApiError:
    return ApiError(
        404, "sale_import_not_found", f"Sale import {import_id} was not found."
    )
