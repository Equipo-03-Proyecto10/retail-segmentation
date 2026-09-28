"""CSV transport adapter over the row-level sales ingestion service
(ADR-0020). Parses one line at a time and calls ingest_row per record; never
buffers a whole file and never fails an entire load for one bad row."""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from psycopg import Connection

from web.services.ingestion import RowRejected, SalesRow, ingest_row

CONTRACT_VERSION = 1

# ADR-0020's first contract version. The CSV's own `transaction_id` column is
# the producer's identifier; it maps onto SalesRow.source_transaction_id, not
# the surrogate key the database generates for it.
_EXPECTED_HEADER = (
    "transaction_id",
    "customer_id",
    "store_id",
    "channel_id",
    "occurred_at",
    "product_id",
    "quantity",
    "unit_price",
)


class UnsupportedContractVersion(Exception):
    """The requested contract version, or the file's header, is not one this
    adapter understands."""


@dataclass(frozen=True)
class RowRejection:
    row_number: int
    reason: str


@dataclass(frozen=True)
class LoadReport:
    received: int
    accepted: int
    rejected: int
    rejections: tuple[RowRejection, ...]


def _parse_row(raw: dict[str, str]) -> SalesRow:
    try:
        occurred_at = datetime.fromisoformat(raw["occurred_at"])
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a UTC offset")
        return SalesRow(
            source_transaction_id=raw["transaction_id"].strip(),
            customer_id=raw["customer_id"].strip(),
            store_id=int(raw["store_id"]),
            channel_id=int(raw["channel_id"]),
            occurred_at=occurred_at,
            product_id=int(raw["product_id"]),
            quantity=int(raw["quantity"]),
            unit_price=Decimal(raw["unit_price"]),
        )
    except (KeyError, ValueError, InvalidOperation) as error:
        raise RowRejected(f"malformed row: {error}") from error


def load_sales_csv(
    connection: Connection,
    path: Path,
    *,
    contract_version: int,
    ingest: Callable[[Connection, SalesRow], None] = ingest_row,
) -> LoadReport:
    """Load a versioned sales file, one accepted or rejected row at a time."""
    if contract_version != CONTRACT_VERSION:
        raise UnsupportedContractVersion(
            f"contract version {contract_version} is not supported; this "
            f"adapter understands version {CONTRACT_VERSION}"
        )

    received = 0
    rejections: list[RowRejection] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or tuple(reader.fieldnames) != _EXPECTED_HEADER:
            raise UnsupportedContractVersion(
                f"the header does not match contract version {CONTRACT_VERSION}"
            )
        for row_number, raw in enumerate(reader, start=1):
            received += 1
            try:
                ingest(connection, _parse_row(raw))
            except RowRejected as error:
                rejections.append(RowRejection(row_number, str(error)))

    return LoadReport(
        received=received,
        accepted=received - len(rejections),
        rejected=len(rejections),
        rejections=tuple(rejections),
    )
