"""Row-level sales ingestion (ADR-0020). No web framework or transport
parsing here -- a separate adapter turns one record into a SalesRow and
calls ingest_row once per row, so a later adapter can replace the transport
without touching validation or persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from psycopg import Connection
from psycopg.errors import UniqueViolation

from web.db import channels, customers, products, sales, stores
from web.db.transactions import atomic


@dataclass(frozen=True)
class SalesRow:
    source_transaction_id: str
    customer_id: str
    store_id: int
    channel_id: int
    occurred_at: datetime
    product_id: int
    quantity: int
    unit_price: Decimal


class RowRejected(Exception):
    """One sales row failed validation or persistence; the load continues."""


@atomic
def ingest_row(connection: Connection, row: SalesRow) -> None:
    """Validate and persist one sales row, or raise RowRejected."""
    if not row.source_transaction_id or not row.source_transaction_id.strip():
        raise RowRejected("source transaction id is required")
    if row.quantity <= 0:
        raise RowRejected("quantity must be positive")
    if row.unit_price < 0:
        raise RowRejected("unit price cannot be negative")

    if customers.get_customer(connection, row.customer_id) is None:
        raise RowRejected(f"unknown customer {row.customer_id}")
    if stores.get_store(connection, row.store_id) is None:
        raise RowRejected(f"unknown store {row.store_id}")
    if channels.get_channel(connection, row.channel_id) is None:
        raise RowRejected(f"unknown channel {row.channel_id}")
    if products.get_product(connection, row.product_id) is None:
        raise RowRejected(f"unknown product {row.product_id}")

    header = sales.get_transaction_by_source_id(connection, row.source_transaction_id)
    if header is None:
        transaction_id = sales.insert_transaction(
            connection,
            source_transaction_id=row.source_transaction_id,
            customer_id=row.customer_id,
            store_id=row.store_id,
            channel_id=row.channel_id,
            occurred_at=row.occurred_at,
        )
    else:
        if (
            header.customer_id != row.customer_id
            or header.store_id != row.store_id
            or header.channel_id != row.channel_id
            or header.occurred_at != row.occurred_at
        ):
            raise RowRejected(
                f"row disagrees with the accepted header for "
                f"{row.source_transaction_id}"
            )
        transaction_id = header.transaction_id

    try:
        sales.insert_transaction_line(
            connection,
            transaction_id=transaction_id,
            product_id=row.product_id,
            quantity=row.quantity,
            unit_price=row.unit_price,
        )
    except UniqueViolation as error:
        raise RowRejected(
            f"duplicate: {row.source_transaction_id} already has a line for "
            f"product {row.product_id}"
        ) from error

    sales.recompute_total(connection, transaction_id)
