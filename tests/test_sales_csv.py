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
from unittest.mock import Mock

import pytest

from web.services.ingestion import RowRejected
from web.services.sales_csv import (
    CONTRACT_VERSION,
    RowRejection,
    UnsupportedContractVersion,
    load_and_record_sales_csv,
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


def test_a_bad_byte_deep_in_the_file_refuses_it_before_any_row_is_ingested(
    tmp_path: Path,
) -> None:
    """#287: the reader decodes as it goes and rows commit one at a time, so a
    bad byte past the first buffer must not leave the rows before it written."""
    row = "TXN-{},cust-1,1,1,2026-01-15T10:00:00+00:00,1,2,9.99\n"
    body = "".join(row.format(number) for number in range(2000))
    path = tmp_path / "sales.csv"
    path.write_bytes(f"{_HEADER}\n{body}".encode() + b"\xff\n")
    ingested = []

    with pytest.raises(UnsupportedContractVersion, match="utf-8"):
        load_sales_csv(
            object(),
            path,
            contract_version=CONTRACT_VERSION,
            ingest=lambda _connection, sale: ingested.append(sale),
        )

    assert ingested == []


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
    assert rejection.line_number == 3
    assert "malformed" in rejection.reason


def test_a_short_row_is_rejected_and_the_load_continues(tmp_path: Path) -> None:
    """#286: a row with fewer columns than the header must not abort the load
    -- datetime.fromisoformat(None) used to raise an uncaught TypeError,
    losing every row after it."""
    path = _write_csv(
        tmp_path,
        [
            "TXN-1,cust-1,1,1,2026-01-15T10:00:00+00:00,1,2,9.99",
            "TXN-2,cust-1,1",
            "TXN-3,cust-1,1,1,2026-01-15T10:00:00+00:00,1,1,5.00",
        ],
    )

    report = load_sales_csv(
        object(), path, contract_version=CONTRACT_VERSION, ingest=_accept_everything
    )

    assert report.received == 3
    assert report.accepted == 2
    assert report.rejected == 1
    rejection = report.rejections[0]
    assert rejection.line_number == 3
    assert "fewer columns" in rejection.reason


def test_a_row_with_an_extra_column_is_rejected_and_the_load_continues(
    tmp_path: Path,
) -> None:
    path = _write_csv(
        tmp_path,
        [
            "TXN-1,cust-1,1,1,2026-01-15T10:00:00+00:00,1,2,9.99,unexpected",
            "TXN-2,cust-1,1,1,2026-01-15T10:00:00+00:00,1,1,5.00",
        ],
    )

    report = load_sales_csv(
        object(), path, contract_version=CONTRACT_VERSION, ingest=_accept_everything
    )

    assert report.received == 2
    assert report.accepted == 1
    assert report.rejected == 1
    assert report.rejections == (
        RowRejection(2, "row has more columns than the header"),
    )


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
    assert report.rejections[0] == RowRejection(3, "unknown product")


# ---------- #334: the administrator reads these reasons ----------


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("store_id", "abc", "malformed row: store_id must be a whole number"),
        ("channel_id", "1.5", "malformed row: channel_id must be a whole number"),
        ("product_id", "", "malformed row: product_id must be a whole number"),
        ("quantity", "two", "malformed row: quantity must be a whole number"),
        ("unit_price", "ten", "malformed row: unit_price must be a decimal number"),
        (
            "occurred_at",
            "yesterday",
            "malformed row: occurred_at must be an ISO 8601 date and time",
        ),
        (
            "occurred_at",
            "2026-01-15T10:00:00",
            "malformed row: occurred_at must include a UTC offset",
        ),
    ],
)
def test_a_malformed_value_is_named_by_its_column_not_by_python(
    tmp_path: Path, column: str, value: str, reason: str
) -> None:
    fields = {
        "transaction_id": "TXN-1",
        "customer_id": "cust-1",
        "store_id": "1",
        "channel_id": "1",
        "occurred_at": "2026-01-15T10:00:00+00:00",
        "product_id": "1",
        "quantity": "2",
        "unit_price": "9.99",
    }
    fields[column] = value
    path = _write_csv(tmp_path, [",".join(fields.values())])

    report = load_sales_csv(
        object(), path, contract_version=CONTRACT_VERSION, ingest=_accept_everything
    )

    assert report.rejections == (RowRejection(2, reason),)


def test_a_file_that_is_not_utf8_is_refused_in_words_not_codec_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sales.csv"
    path.write_bytes(_HEADER.encode("utf-8") + b"\n" + b"\xff\xfe not utf-8\n")

    with pytest.raises(UnsupportedContractVersion) as refusal:
        load_sales_csv(object(), path, contract_version=CONTRACT_VERSION)

    assert "CSV UTF-8" in str(refusal.value)
    assert "codec" not in str(refusal.value)
    assert "position" not in str(refusal.value)


def test_a_load_is_recorded_with_its_counts_and_every_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_csv(
        tmp_path,
        [
            "TXN-1,cust-1,1,1,2026-01-15T10:00:00+00:00,1,2,9.99",
            "TXN-2,cust-1,1,1,2026-01-15T10:00:00+00:00,1,bad,9.99",
            "TXN-3,cust-1,1,1,2026-01-15T10:00:00+00:00,1,1,5.00",
        ],
    )
    recorded: dict = {}

    def _insert(connection, **fields) -> int:
        recorded.update(fields)
        return 7

    monkeypatch.setattr("web.services.sales_csv.insert_sales_load", _insert)

    report, load_id = load_and_record_sales_csv(
        object(),
        path,
        filename="sales.csv",
        contract_version=CONTRACT_VERSION,
        loaded_by="11111111-1111-1111-1111-000000000001",
        ingest=_accept_everything,
    )

    assert load_id == 7
    assert (report.received, report.accepted, report.rejected) == (3, 2, 1)
    assert recorded == {
        "filename": "sales.csv",
        "contract_version": CONTRACT_VERSION,
        "received_count": 3,
        "accepted_count": 2,
        "rejected_count": 1,
        "loaded_by": "11111111-1111-1111-1111-000000000001",
        "rejections": [(3, "malformed row: quantity must be a whole number")],
    }


def test_a_refused_file_records_no_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sales.csv"
    path.write_text("wrong,header\n1,2\n", encoding="utf-8")
    insert = Mock()
    monkeypatch.setattr("web.services.sales_csv.insert_sales_load", insert)

    with pytest.raises(UnsupportedContractVersion):
        load_and_record_sales_csv(
            object(),
            path,
            filename="sales.csv",
            contract_version=CONTRACT_VERSION,
            loaded_by=None,
        )

    insert.assert_not_called()
