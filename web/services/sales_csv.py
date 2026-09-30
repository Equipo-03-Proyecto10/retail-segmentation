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
from typing import TypeVar

from psycopg import Connection

from web.db.sales_loads import insert_sales_load
from web.services.ingestion import RowRejected, SalesRow, ingest_row

CONTRACT_VERSION = 1

_T = TypeVar("_T")

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
    """The requested contract version, the file's header, or its encoding is
    not one this adapter understands."""


@dataclass(frozen=True)
class RowRejection:
    line_number: int
    reason: str


@dataclass(frozen=True)
class LoadReport:
    received: int
    accepted: int
    rejected: int
    rejections: tuple[RowRejection, ...]


def _field(
    raw: dict[str, str], name: str, parse: Callable[[str], _T], expected: str
) -> _T:
    """Parse one column, naming the column rather than quoting Python's error
    text ("invalid literal for int() with base 10", "[<class
    'decimal.ConversionSyntax'>]") to the administrator (#334)."""
    try:
        return parse(raw[name])
    except (ValueError, InvalidOperation, TypeError) as error:
        raise RowRejected(f"malformed row: {name} must be {expected}") from error


def _parse_row(raw: dict[str, str]) -> SalesRow:
    if None in raw:
        raise RowRejected("row has more columns than the header")
    if any(value is None for value in raw.values()):
        raise RowRejected("row has fewer columns than the header")
    occurred_at = _field(
        raw, "occurred_at", datetime.fromisoformat, "an ISO 8601 date and time"
    )
    if occurred_at.tzinfo is None:
        raise RowRejected("malformed row: occurred_at must include a UTC offset")
    return SalesRow(
        source_transaction_id=raw["transaction_id"].strip(),
        customer_id=raw["customer_id"].strip(),
        store_id=_field(raw, "store_id", int, "a whole number"),
        channel_id=_field(raw, "channel_id", int, "a whole number"),
        occurred_at=occurred_at,
        product_id=_field(raw, "product_id", int, "a whole number"),
        quantity=_field(raw, "quantity", int, "a whole number"),
        unit_price=_field(raw, "unit_price", Decimal, "a decimal number"),
    )


# utf-8-sig accepts a leading byte-order mark (as Excel's "CSV UTF-8" export
# writes one) and is otherwise identical to utf-8.
_ENCODING = "utf-8-sig"
_DECODE_CHUNK = 64 * 1024


def _refuse_undecodable(path: Path) -> None:
    """Decode the whole file once, a chunk at a time, before any row is ingested.

    Rows commit one at a time, and the reader decodes as it goes: a bad byte
    past the first buffer would stop the load after the rows before it were
    written, with no report of the rows after it (#287). This pass holds one
    chunk, never the whole file.
    """
    try:
        with path.open(newline="", encoding=_ENCODING) as handle:
            while handle.read(_DECODE_CHUNK):
                pass
    except UnicodeDecodeError as error:
        # Not the codec's own text: its byte position is relative to one
        # decoded chunk, not the file, and the administrator reads this.
        raise UnsupportedContractVersion(
            'the file is not valid utf-8 text; save it as "CSV UTF-8" and '
            "upload it again"
        ) from error


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
    _refuse_undecodable(path)

    received = 0
    rejections: list[RowRejection] = []
    with path.open(newline="", encoding=_ENCODING) as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or tuple(reader.fieldnames) != _EXPECTED_HEADER:
            raise UnsupportedContractVersion(
                f"the header does not match contract version {CONTRACT_VERSION}"
            )
        # start=2: the header occupies line 1, so the first data row is
        # line 2 -- the administrator's rejection report names file line
        # numbers (#334), not record indexes.
        for line_number, raw in enumerate(reader, start=2):
            received += 1
            try:
                ingest(connection, _parse_row(raw))
            except RowRejected as error:
                rejections.append(RowRejection(line_number, str(error)))

    return LoadReport(
        received=received,
        accepted=received - len(rejections),
        rejected=len(rejections),
        rejections=tuple(rejections),
    )


def load_and_record_sales_csv(
    connection: Connection,
    path: Path,
    *,
    filename: str,
    contract_version: int,
    loaded_by: str | None,
    ingest: Callable[[Connection, SalesRow], None] = ingest_row,
) -> tuple[LoadReport, int]:
    """Load a file and persist the attempt as a sales_load row, so the
    rejection report stays retrievable after the page is left (AC 3).

    Returns the report and the new load_id. Each sales row has already
    committed or rolled back on its own inside ingest_row by the time the
    load is recorded; the load and its rejection rows then commit together
    in one transaction of their own. Wrapping the whole call in an outer
    transaction would break that: the first statement a rejected row makes
    fail would leave the shared transaction aborted for every row after it.
    """
    report = load_sales_csv(
        connection, path, contract_version=contract_version, ingest=ingest
    )
    load_id = insert_sales_load(
        connection,
        filename=filename,
        contract_version=contract_version,
        received_count=report.received,
        accepted_count=report.accepted,
        rejected_count=report.rejected,
        loaded_by=loaded_by,
        rejections=[(r.line_number, r.reason) for r in report.rejections],
    )
    return report, load_id
