"""Data access for sales CSV upload attempts and their rejections (#334).

A load record persists past the HTTP response that created it (AC 3): the
administrator can leave the page and still retrieve the rejection report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

from web.db.transactions import atomic


@dataclass(frozen=True)
class SalesLoad:
    load_id: int
    filename: str
    contract_version: int
    received_count: int
    accepted_count: int
    rejected_count: int
    loaded_by: UUID | None
    loaded_by_name: str | None
    loaded_at: datetime


@dataclass(frozen=True)
class SalesLoadRejectionRow:
    line_number: int
    reason: str


@atomic
def insert_sales_load(
    connection: Connection[Any],
    *,
    filename: str,
    contract_version: int,
    received_count: int,
    accepted_count: int,
    rejected_count: int,
    loaded_by: UUID | str | None,
    rejections: list[tuple[int, str]],
) -> int:
    """Record one upload attempt and every rejected line in one transaction.

    The sales rows themselves are not part of it: each one has already
    committed or rolled back on its own inside ingest_row."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO sales_load
                (filename, contract_version, received_count, accepted_count,
                 rejected_count, loaded_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING load_id
            """,
            (
                filename,
                contract_version,
                received_count,
                accepted_count,
                rejected_count,
                str(loaded_by) if loaded_by else None,
            ),
        )
        load_id = cursor.fetchone()[0]

        if rejections:
            cursor.executemany(
                """
                INSERT INTO sales_load_rejection (load_id, line_number, reason)
                VALUES (%s, %s, %s)
                """,
                [(load_id, line_number, reason) for line_number, reason in rejections],
            )

    return load_id


def list_sales_loads(
    connection: Connection[Any], *, page: int, per_page: int
) -> tuple[list[SalesLoad], int]:
    """Return a page of upload attempts, most recent first, and the total."""
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT l.load_id, l.filename, l.contract_version, l.received_count,
                   l.accepted_count, l.rejected_count, l.loaded_by, u.name,
                   l.loaded_at
            FROM sales_load AS l
            LEFT JOIN app_user AS u ON u.user_id = l.loaded_by
            ORDER BY l.loaded_at DESC, l.load_id DESC
            LIMIT %s OFFSET %s
            """,
            (per_page, offset),
        )
        rows = cursor.fetchall()

        cursor.execute("SELECT count(*) FROM sales_load")
        total = cursor.fetchone()[0]

    return [SalesLoad(*row) for row in rows], total


def get_sales_load(connection: Connection[Any], load_id: int) -> SalesLoad | None:
    """Return one upload attempt by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT l.load_id, l.filename, l.contract_version, l.received_count,
                   l.accepted_count, l.rejected_count, l.loaded_by, u.name,
                   l.loaded_at
            FROM sales_load AS l
            LEFT JOIN app_user AS u ON u.user_id = l.loaded_by
            WHERE l.load_id = %s
            """,
            (load_id,),
        )
        row = cursor.fetchone()

    return SalesLoad(*row) if row else None


def list_sales_load_rejections(
    connection: Connection[Any], load_id: int
) -> list[SalesLoadRejectionRow]:
    """Return every rejected line for one load, ordered by line number --
    the file's own order, so the administrator can follow along in a
    spreadsheet."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT line_number, reason
            FROM sales_load_rejection
            WHERE load_id = %s
            ORDER BY line_number
            """,
            (load_id,),
        )
        return [SalesLoadRejectionRow(*row) for row in cursor.fetchall()]
