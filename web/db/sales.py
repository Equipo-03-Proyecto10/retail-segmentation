"""Data access for sales ingestion (F8-02). Every statement is
parameterized; see web/services/ingestion.py for the business rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from psycopg import Connection


@dataclass(frozen=True)
class TransactionHeader:
    transaction_id: int
    source_transaction_id: str
    customer_id: str
    store_id: int
    channel_id: int
    occurred_at: datetime


def get_transaction_by_source_id(
    connection: Connection, source_transaction_id: str
) -> TransactionHeader | None:
    """Return the transaction a source identifier already maps to, if any."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT transaction_id, source_transaction_id, customer_id,
                   store_id, channel_id, occurred_at
            FROM transaction
            WHERE source_transaction_id = %s
            """,
            (source_transaction_id,),
        )
        row = cursor.fetchone()
    return TransactionHeader(*row) if row else None


def insert_transaction(
    connection: Connection,
    *,
    source_transaction_id: str,
    customer_id: str,
    store_id: int,
    channel_id: int,
    occurred_at: datetime,
) -> int:
    """Insert a new transaction header with total 0; the first line's insert
    and the immediate recompute bring it to the correct value before commit."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO transaction
                (source_transaction_id, customer_id, store_id, channel_id,
                 occurred_at, total)
            VALUES (%s, %s, %s, %s, %s, 0)
            RETURNING transaction_id
            """,
            (source_transaction_id, customer_id, store_id, channel_id, occurred_at),
        )
        return cursor.fetchone()[0]


def insert_transaction_line(
    connection: Connection,
    *,
    transaction_id: int,
    product_id: int,
    quantity: int,
    unit_price: Decimal,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO transaction_line
                (transaction_id, product_id, quantity, unit_price)
            VALUES (%s, %s, %s, %s)
            """,
            (transaction_id, product_id, quantity, unit_price),
        )


def recompute_total(connection: Connection, transaction_id: int) -> None:
    """Derive the header total from persisted lines (ADR-0020: the source
    never supplies a second total that could disagree)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE transaction
            SET total = (
                SELECT COALESCE(SUM(quantity * unit_price), 0)
                FROM transaction_line
                WHERE transaction_id = %s
            )
            WHERE transaction_id = %s
            """,
            (transaction_id, transaction_id),
        )
