"""Row-level sales ingestion (ADR-0020). No web framework or transport
parsing here -- a separate adapter turns one record into a SalesRow and
calls ingest_row once per row, so a later adapter can replace the transport
without touching validation or persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from psycopg import Connection
from psycopg.errors import (
    DataError,
    ForeignKeyViolation,
    InvalidTextRepresentation,
    NumericValueOutOfRange,
    StringDataRightTruncation,
    UniqueViolation,
)

from web.db import sales
from web.db.inventory import StockUnavailable, decrement_stock
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


# What a DataError on each insert can only be about, in the administrator's
# terms. PostgreSQL's own text ("smallint out of range", "invalid input syntax
# for type uuid" followed by a CONTEXT line) is never passed on: the rejection
# report is read by the administrator, not a developer (#334).
_HEADER_DATA_ERRORS: dict[type[DataError], str] = {
    InvalidTextRepresentation: "customer_id is not a valid identifier",
    StringDataRightTruncation: "transaction_id is too long",
    NumericValueOutOfRange: "store_id or channel_id is out of range",
}
_LINE_DATA_ERRORS: dict[type[DataError], str] = {
    NumericValueOutOfRange: "product_id, quantity or unit_price is out of range",
}

_CENT = Decimal("0.01")
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _is_spreadsheet_formula(value: str) -> bool:
    """Recognize prefixes spreadsheet applications evaluate as formulas."""
    return value.lstrip().startswith(_FORMULA_PREFIXES)


def _malformed(error: DataError, reasons: dict[type[DataError], str]) -> RowRejected:
    for error_type, reason in reasons.items():
        if isinstance(error, error_type):
            return RowRejected(f"malformed row: {reason}")
    return RowRejected("malformed row: a value is not in the expected format")


@atomic
def ingest_row(connection: Connection, row: SalesRow) -> None:
    """Validate and persist one sales row, or raise RowRejected."""
    if not isinstance(row.source_transaction_id, str):
        raise RowRejected("source transaction id is required")
    source_transaction_id = row.source_transaction_id.strip()
    if not source_transaction_id:
        raise RowRejected("source transaction id is required")
    if _is_spreadsheet_formula(source_transaction_id):
        raise RowRejected("source transaction id cannot be a spreadsheet formula")
    if row.occurred_at.tzinfo is None:
        raise RowRejected("occurred_at must include a UTC offset")
    if row.occurred_at > datetime.now(UTC):
        raise RowRejected("occurred_at cannot be in the future")
    if row.quantity <= 0:
        raise RowRejected("quantity must be positive")
    if not row.unit_price.is_finite():
        raise RowRejected("unit price must be a finite number")
    if row.unit_price <= 0:
        raise RowRejected("unit price must be positive")
    try:
        has_more_than_two_decimals = row.unit_price.quantize(_CENT) != row.unit_price
    except InvalidOperation as error:
        raise RowRejected("unit price must have at most 2 decimal places") from error
    if has_more_than_two_decimals:
        raise RowRejected("unit price must have at most 2 decimal places")

    try:
        references = sales.get_sale_references(
            connection,
            customer_id=row.customer_id,
            product_id=row.product_id,
        )
    except DataError as error:
        raise _malformed(error, _HEADER_DATA_ERRORS) from error
    if references is None:
        raise RowRejected("unknown customer")
    if row.occurred_at.date() < references.registered_on:
        raise RowRejected("sale occurred before customer registration")
    if references.product_is_active is None:
        raise RowRejected("unknown product")
    if not references.product_is_active:
        raise RowRejected("product is inactive")

    header = sales.get_transaction_by_source_id(connection, source_transaction_id)
    if header is None:
        try:
            transaction_id = sales.insert_transaction(
                connection,
                source_transaction_id=source_transaction_id,
                customer_id=row.customer_id,
                store_id=row.store_id,
                channel_id=row.channel_id,
                occurred_at=row.occurred_at,
            )
        except ForeignKeyViolation as error:
            raise _unknown_reference(error) from error
        except UniqueViolation as error:
            raise RowRejected(
                f"duplicate: {source_transaction_id} was created concurrently"
            ) from error
        except DataError as error:
            raise _malformed(error, _HEADER_DATA_ERRORS) from error
    else:
        if (
            header.customer_id != row.customer_id
            or header.store_id != row.store_id
            or header.channel_id != row.channel_id
            or header.occurred_at != row.occurred_at
        ):
            raise RowRejected(
                f"row disagrees with the accepted header for "
                f"{source_transaction_id}"
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
            f"duplicate: {source_transaction_id} already has a line for "
            f"product {row.product_id}"
        ) from error
    except ForeignKeyViolation as error:
        raise _unknown_reference(error) from error
    except DataError as error:
        raise _malformed(error, _LINE_DATA_ERRORS) from error

    try:
        decrement_stock(
            connection,
            store_id=row.store_id,
            product_id=row.product_id,
            quantity=row.quantity,
        )
    except StockUnavailable as error:
        if error.available is None:
            raise RowRejected(
                f"no inventory for store {error.store_id} and product "
                f"{error.product_id}"
            ) from error
        raise RowRejected(
            f"insufficient stock for store {error.store_id} and product "
            f"{error.product_id}: {error.available} available"
        ) from error

    sales.recompute_total(connection, transaction_id)
