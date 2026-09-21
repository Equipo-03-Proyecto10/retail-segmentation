"""F8-02: row-level sales ingestion (ADR-0020).

A scripted fake connection, in the style of
tests/test_single_administrator.py: `execute` answers by looking at the SQL
text rather than at call order, because the service's own business logic
decides which question it asks next.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from psycopg.errors import UniqueViolation

from web.services.ingestion import RowRejected, SalesRow, ingest_row

_OCCURRED_AT = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)


def _customer_row(customer_id: str) -> tuple:
    """The column order get_customer selects; only customer_id matters here."""
    return (customer_id, None, "Demo Customer", None, None, 1, None, date(2024, 1, 1))


def _store_row(store_id: int) -> tuple:
    return (store_id, "Demo Store", "City", "State", True)


def _channel_row(channel_id: int) -> tuple:
    return (channel_id, "Demo Channel")


def _product_row(product_id: int) -> tuple:
    return (
        product_id,
        f"SKU-{product_id}",
        "Demo Product",
        1,
        Decimal("10.00"),
        None,
        True,
    )


def _row(**overrides) -> SalesRow:
    fields = {
        "source_transaction_id": "TXN-1",
        "customer_id": "cust-1",
        "store_id": 1,
        "channel_id": 1,
        "occurred_at": _OCCURRED_AT,
        "product_id": 1,
        "quantity": 2,
        "unit_price": Decimal("9.99"),
    }
    fields.update(overrides)
    return SalesRow(**fields)


class _DuplicateLine(UniqueViolation):
    """A UniqueViolation on (transaction_id, product_id), hand-built the same
    way tests/test_single_administrator.py builds _IndexViolation."""

    def __init__(self) -> None:
        super().__init__("duplicate key value violates unique constraint")


class _Connection:
    def __init__(
        self,
        *,
        customer_ids: set[str] | None = None,
        store_ids: set[int] | None = None,
        channel_ids: set[int] | None = None,
        product_ids: set[int] | None = None,
        headers: dict[str, dict] | None = None,
    ) -> None:
        self.customer_ids = customer_ids or {"cust-1"}
        self.store_ids = store_ids or {1, 2}
        self.channel_ids = channel_ids or {1}
        self.product_ids = product_ids or {1, 2}
        # source_transaction_id -> {transaction_id, customer_id, store_id,
        # channel_id, occurred_at, lines: {product_id: (qty, price)}}
        self.headers = headers or {}
        self.next_id = 100
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def _header_by_transaction_id(self, transaction_id: int) -> dict | None:
        for header in self.headers.values():
            if header["transaction_id"] == transaction_id:
                return header
        return None


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.row: tuple | None = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exception) -> None:
        return None

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        c = self.connection
        text = " ".join(statement.split())

        if "FROM customer WHERE" in text:
            self.row = (
                _customer_row(parameters[0])
                if parameters[0] in c.customer_ids
                else None
            )
        elif "FROM store WHERE" in text:
            self.row = (
                _store_row(parameters[0]) if parameters[0] in c.store_ids else None
            )
        elif "FROM channel WHERE" in text:
            self.row = (
                _channel_row(parameters[0]) if parameters[0] in c.channel_ids else None
            )
        elif "FROM product WHERE" in text:
            self.row = (
                _product_row(parameters[0]) if parameters[0] in c.product_ids else None
            )
        elif "FROM transaction WHERE source_transaction_id" in text:
            header = c.headers.get(parameters[0])
            self.row = (
                None
                if header is None
                else (
                    header["transaction_id"],
                    parameters[0],
                    header["customer_id"],
                    header["store_id"],
                    header["channel_id"],
                    header["occurred_at"],
                )
            )
        elif text.startswith("INSERT INTO transaction_line"):
            transaction_id, product_id, quantity, unit_price = parameters
            header = c._header_by_transaction_id(transaction_id)
            if product_id in header["lines"]:
                raise _DuplicateLine()
            header["lines"][product_id] = (quantity, unit_price)
            self.row = None
        elif text.startswith("INSERT INTO transaction"):
            source_transaction_id, customer_id, store_id, channel_id, occurred_at = (
                parameters
            )
            transaction_id = c.next_id
            c.next_id += 1
            c.headers[source_transaction_id] = {
                "transaction_id": transaction_id,
                "customer_id": customer_id,
                "store_id": store_id,
                "channel_id": channel_id,
                "occurred_at": occurred_at,
                "total": Decimal("0"),
                "lines": {},
            }
            self.row = (transaction_id,)
        elif text.startswith("UPDATE transaction SET total"):
            transaction_id = parameters[0]
            header = c._header_by_transaction_id(transaction_id)
            header["total"] = sum(
                (qty * price for qty, price in header["lines"].values()), Decimal("0")
            )
            self.row = None
        else:  # pragma: no cover - the service asks nothing else
            raise AssertionError(f"unexpected statement: {text}")

    def fetchone(self) -> tuple | None:
        return self.row


# ---------- accepted rows ----------


def test_a_row_with_a_new_source_id_creates_a_transaction_and_a_line() -> None:
    connection = _Connection()

    ingest_row(connection, _row())

    header = connection.headers["TXN-1"]
    assert header["lines"][1] == (2, Decimal("9.99"))
    assert header["total"] == Decimal("19.98")
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_a_second_line_for_the_same_source_id_is_appended_and_total_recomputed() -> (
    None
):
    connection = _Connection()
    ingest_row(connection, _row(product_id=1, quantity=2, unit_price=Decimal("9.99")))

    ingest_row(connection, _row(product_id=2, quantity=1, unit_price=Decimal("5.00")))

    header = connection.headers["TXN-1"]
    assert set(header["lines"]) == {1, 2}
    assert header["total"] == Decimal("24.98")
    assert connection.commits == 2


# ---------- field-level rejections (no database call at all) ----------


def test_a_missing_source_transaction_id_is_rejected() -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="source transaction id"):
        ingest_row(connection, _row(source_transaction_id=""))

    assert connection.headers == {}
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_non_positive_quantity_is_rejected() -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="quantity"):
        ingest_row(connection, _row(quantity=0))

    assert connection.headers == {}


def test_negative_unit_price_is_rejected() -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="unit price"):
        ingest_row(connection, _row(unit_price=Decimal("-0.01")))

    assert connection.headers == {}


# ---------- foreign-key rejections ----------


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"customer_id": "ghost"}, "customer"),
        ({"store_id": 99}, "store"),
        ({"channel_id": 99}, "channel"),
        ({"product_id": 99}, "product"),
    ],
)
def test_an_unknown_customer_store_channel_or_product_is_rejected(
    overrides: dict, expected: str
) -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match=expected):
        ingest_row(connection, _row(**overrides))

    assert connection.headers == {}


# ---------- header consistency and duplicates ----------


def test_a_row_disagreeing_with_the_accepted_header_is_rejected() -> None:
    connection = _Connection(
        headers={
            "TXN-1": {
                "transaction_id": 1,
                "customer_id": "cust-1",
                "store_id": 1,
                "channel_id": 1,
                "occurred_at": _OCCURRED_AT,
                "total": Decimal("19.98"),
                "lines": {1: (2, Decimal("9.99"))},
            }
        }
    )

    with pytest.raises(RowRejected, match="disagrees with the accepted header"):
        ingest_row(connection, _row(product_id=2, store_id=2))

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_a_repeated_product_line_for_the_same_source_id_is_rejected_as_duplicate() -> (
    None
):
    connection = _Connection(
        headers={
            "TXN-1": {
                "transaction_id": 1,
                "customer_id": "cust-1",
                "store_id": 1,
                "channel_id": 1,
                "occurred_at": _OCCURRED_AT,
                "total": Decimal("19.98"),
                "lines": {1: (2, Decimal("9.99"))},
            }
        }
    )

    with pytest.raises(RowRejected, match="duplicate"):
        ingest_row(connection, _row(product_id=1))

    assert connection.rollbacks == 1
    # The line already there is untouched by the failed re-insert attempt.
    assert connection.headers["TXN-1"]["lines"] == {1: (2, Decimal("9.99"))}
