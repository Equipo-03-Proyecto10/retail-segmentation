"""F8-02: the CSV transport adapter over the row-level ingestion service.

Row content is written straight to tmp_path -- AGENTS.md gitignores *.csv
repo-wide, so a committed fixture file is not an option here. `ingest` is a
stub in every test: this file is about parsing, contract-version and header
checks, and the received/accepted/rejected reconciliation, not about
web.services.ingestion's own business rules (covered in
tests/test_ingestion_service.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from web.services.ingestion import RowRejected
from web.services.sales_csv import (
    CONTRACT_VERSION,
    RowRejection,
    UnsupportedContractVersion,
    load_sales_csv,
)

_HEADER = (
    "transaction_id,customer_id,store_id,channel_id,occurred_at,"
    "product_id,quantity,unit_price"
)


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text("\n".join([_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def _accept_everything(connection, row) -> None:
    return None


def test_an_unsupported_contract_version_is_refused_before_the_file_is_opened(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does-not-exist.csv"

    with pytest.raises(UnsupportedContractVersion):
        load_sales_csv(
            object(),
            missing_path,
            contract_version=CONTRACT_VERSION + 1,
            ingest=_accept_everything,
        )


def test_a_header_that_does_not_match_the_contract_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sales.csv"
    path.write_text("wrong,header\n1,2\n", encoding="utf-8")

    with pytest.raises(UnsupportedContractVersion):
        load_sales_csv(
            object(), path, contract_version=CONTRACT_VERSION, ingest=_accept_everything
        )


def test_a_leading_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    """#287: Excel's "CSV UTF-8" export prefixes a BOM; opening with plain
    utf-8 folded it into the first field name and failed the header check."""
    path = tmp_path / "sales.csv"
    path.write_bytes(
        f"﻿{_HEADER}\nTXN-1,cust-1,1,1,2026-01-15T10:00:00+00:00,1,2,9.99\n".encode()
    )

    report = load_sales_csv(
        object(), path, contract_version=CONTRACT_VERSION, ingest=_accept_everything
    )

    assert report.received == 1
    assert report.accepted == 1


def test_a_file_that_is_not_valid_utf8_is_a_typed_refusal(tmp_path: Path) -> None:
    """#287: a raw UnicodeDecodeError must not escape the loader."""
    path = tmp_path / "sales.csv"
    path.write_bytes(_HEADER.encode("utf-8") + b"\n" + b"\xff\xfe not utf-8\n")

    with pytest.raises(UnsupportedContractVersion, match="utf-8"):
        load_sales_csv(
            object(), path, contract_version=CONTRACT_VERSION, ingest=_accept_everything
        )


def test_four_rows_two_valid_two_invalid_report_4_2_2(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        [
            "TXN-1,cust-1,1,1,2026-01-15T10:00:00+00:00,1,2,9.99",
            "TXN-2,cust-1,1,1,2026-01-15T10:00:00+00:00,1,bad-quantity,9.99",
            "TXN-3,cust-1,1,1,2026-01-15T10:00:00+00:00,1,1,5.00",
            "TXN-4,cust-1,1,1,not-a-timestamp,1,1,5.00",
        ],
    )

    report = load_sales_csv(
        object(), path, contract_version=CONTRACT_VERSION, ingest=_accept_everything
    )

    assert report.received == 4
    assert report.accepted == 2
    assert report.rejected == 2
    assert report.received == report.accepted + report.rejected


def test_a_rejection_carries_its_row_number_and_reason(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        [
            "TXN-1,cust-1,1,1,2026-01-15T10:00:00+00:00,1,2,9.99",
            "TXN-2,cust-1,1,1,2026-01-15T10:00:00+00:00,1,bad-quantity,9.99",
        ],
    )

    report = load_sales_csv(
        object(), path, contract_version=CONTRACT_VERSION, ingest=_accept_everything
    )

    assert len(report.rejections) == 1
    rejection = report.rejections[0]
    assert rejection.row_number == 2
    assert "malformed" in rejection.reason


def test_a_business_rejection_from_ingest_is_counted_the_same_way(
    tmp_path: Path,
) -> None:
    path = _write_csv(
        tmp_path,
        [
            "TXN-1,cust-1,1,1,2026-01-15T10:00:00+00:00,1,2,9.99",
            "TXN-2,cust-1,1,1,2026-01-15T10:00:00+00:00,1,1,5.00",
        ],
    )

    def _reject_second(connection, row) -> None:
        if row.source_transaction_id == "TXN-2":
            raise RowRejected("unknown product")

    report = load_sales_csv(
        object(), path, contract_version=CONTRACT_VERSION, ingest=_reject_second
    )

    assert report.received == 2
    assert report.accepted == 1
    assert report.rejected == 1
    assert report.rejections[0] == RowRejection(2, "unknown product")
