"""Data access for sales ingestion (F8-02). Every statement is
parameterized; see web/services/ingestion.py for the business rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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


@dataclass(frozen=True)
class SaleReferences:
    """Customer and product facts needed before accepting one sale row."""

    registered_on: date
    product_is_active: bool | None


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
            FOR UPDATE
            """,
            (source_transaction_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    (
        transaction_id,
        source_transaction_id,
        customer_id,
        store_id,
        channel_id,
        occurred_at,
    ) = row
    # customer_id comes back as uuid.UUID (psycopg's default loader for the
    # UUID column type), not str; normalizing here keeps this dataclass's
    # own type hint true so a caller can compare it against a CSV-sourced
    # str without UUID.__eq__ silently returning NotImplemented/unequal.
    return TransactionHeader(
        transaction_id=transaction_id,
        source_transaction_id=source_transaction_id,
        customer_id=str(customer_id),
        store_id=store_id,
        channel_id=channel_id,
        occurred_at=occurred_at,
    )


def get_sale_references(
    connection: Connection, *, customer_id: str, product_id: int
) -> SaleReferences | None:
    """Return registration and product-state facts for one sale row.

    The customer is the required side of the join: ``None`` means that the
    customer does not exist, while a ``None`` product state distinguishes an
    unknown product from an inactive one.  Keeping this read in ``web.db``
    preserves the service/SQL boundary from ADR-0003 and keeps both values
    from being trusted as client input.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.registered_on, p.is_active
            FROM customer AS c
            LEFT JOIN product AS p ON p.product_id = %s
            WHERE c.customer_id = %s
            """,
            (product_id, customer_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    registered_on, product_is_active = row
    return SaleReferences(registered_on, product_is_active)


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
