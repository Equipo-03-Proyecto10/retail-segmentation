"""Row-level sales ingestion (ADR-0020). No web framework or transport
parsing here -- a separate adapter turns one record into a SalesRow and
calls ingest_row once per row, so a later adapter can replace the transport
without touching validation or persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from psycopg import Connection
from psycopg.errors import DataError, ForeignKeyViolation, UniqueViolation

from web.db import sales
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


# Maps the FK constraints a header or line insert can violate to the entity
# name the rejection message names. Existence is left to the schema's own FK
# constraints (ADR-0020's insert-then-translate pattern, matching
# web/services/catalog.py's _refusal) rather than re-checked here with a
# SELECT per referenced id.
_FK_ENTITY_BY_CONSTRAINT = {
    "transaction_customer_id_fkey": "customer",
    "transaction_store_id_fkey": "store",
    "transaction_channel_id_fkey": "channel",
    "transaction_line_product_id_fkey": "product",
}


def _unknown_reference(error: ForeignKeyViolation) -> RowRejected:
    entity = _FK_ENTITY_BY_CONSTRAINT.get(error.diag.constraint_name, "reference")
    return RowRejected(f"unknown {entity}")


@atomic
def ingest_row(connection: Connection, row: SalesRow) -> None:
    """Validate and persist one sales row, or raise RowRejected."""
    if not row.source_transaction_id or not row.source_transaction_id.strip():
        raise RowRejected("source transaction id is required")
    if row.quantity <= 0:
        raise RowRejected("quantity must be positive")
    if row.unit_price < 0:
        raise RowRejected("unit price cannot be negative")

    header = sales.get_transaction_by_source_id(connection, row.source_transaction_id)
    if header is None:
        try:
            transaction_id = sales.insert_transaction(
                connection,
                source_transaction_id=row.source_transaction_id,
                customer_id=row.customer_id,
                store_id=row.store_id,
                channel_id=row.channel_id,
                occurred_at=row.occurred_at,
            )
        except ForeignKeyViolation as error:
            raise _unknown_reference(error) from error
        except UniqueViolation as error:
            raise RowRejected(
                f"duplicate: {row.source_transaction_id} was created concurrently"
            ) from error
        except DataError as error:
            raise RowRejected(f"malformed row: {error}") from error
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
    except ForeignKeyViolation as error:
        raise _unknown_reference(error) from error
    except DataError as error:
        raise RowRejected(f"malformed row: {error}") from error

    sales.recompute_total(connection, transaction_id)
