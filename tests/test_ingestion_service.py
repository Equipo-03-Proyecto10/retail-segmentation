"""F8-02: row-level sales ingestion (ADR-0020).

A scripted fake connection, in the style of
tests/test_single_administrator.py: `execute` answers by looking at the SQL
text rather than at call order, because the service's own business logic
decides which question it asks next.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from psycopg.errors import DataError, ForeignKeyViolation, UniqueViolation

from web.services.ingestion import RowRejected, SalesRow, ingest_row

_OCCURRED_AT = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
_CUSTOMER_1 = "00000000-0000-0000-0000-000000000001"


def _row(**overrides) -> SalesRow:
    fields = {
        "source_transaction_id": "TXN-1",
        "customer_id": _CUSTOMER_1,
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


class _MissingReference(ForeignKeyViolation):
    """A ForeignKeyViolation naming a constraint, hand-built the same way
    tests/test_single_administrator.py's _IndexViolation stands in for a
    UniqueViolation: psycopg builds `diag` from the server's error fields and
    exposes `constraint_name` read-only, so it cannot be assembled directly."""

    def __init__(self, constraint_name: str) -> None:
        super().__init__("insert or update on table violates foreign key constraint")
        self._constraint_name = constraint_name

    @property
    def diag(self) -> SimpleNamespace:  # type: ignore[override]
        return SimpleNamespace(constraint_name=self._constraint_name)


class _OutOfRange(DataError):
    """A DataError such as a SMALLINT/NUMERIC overflow at insert time."""

    def __init__(self) -> None:
        super().__init__("numeric field overflow")


class _ConcurrentHeader(UniqueViolation):
    """A UniqueViolation on transaction.source_transaction_id, racing a
    second, brand-new header insert for the same source id."""

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
        simulate_concurrent_header: bool = False,
    ) -> None:
        self.customer_ids = customer_ids or {_CUSTOMER_1}
        self.store_ids = store_ids or {1, 2}
        self.channel_ids = channel_ids or {1}
        self.product_ids = product_ids or {1, 2}
        # source_transaction_id -> {transaction_id, customer_id, store_id,
        # channel_id, occurred_at, lines: {product_id: (qty, price)}}
        self.headers = headers or {}
        self.simulate_concurrent_header = simulate_concurrent_header
        self.next_id = 100
        self.commits = 0
        self.rollbacks = 0
        # Keys inserted since the last commit: visible to a later statement
        # in the same not-yet-committed transaction (read-your-own-writes),
        # but undone on rollback -- matching what a real ROLLBACK does to an
        # uncommitted INSERT.
        self._uncommitted_headers: set[str] = set()

    def commit(self) -> None:
        self.commits += 1
        self._uncommitted_headers.clear()

    def rollback(self) -> None:
        self.rollbacks += 1
        for source_transaction_id in self._uncommitted_headers:
            del self.headers[source_transaction_id]
        self._uncommitted_headers.clear()

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

        if "FROM transaction WHERE source_transaction_id" in text:
            header = c.headers.get(parameters[0])
            self.row = (
                None
                if header is None
                else (
                    header["transaction_id"],
                    parameters[0],
                    # A real UUID column comes back from psycopg as
                    # uuid.UUID, not str -- this is what
                    # get_transaction_by_source_id's own normalization
                    # (web/db/sales.py) is exercised against.
                    uuid.UUID(header["customer_id"]),
                    header["store_id"],
                    header["channel_id"],
                    header["occurred_at"],
                )
            )
        elif text.startswith("INSERT INTO transaction_line"):
            transaction_id, product_id, quantity, unit_price = parameters
            if product_id == "OUT_OF_RANGE":
                raise _OutOfRange()
            if product_id not in c.product_ids:
                raise _MissingReference("transaction_line_product_id_fkey")
            header = c._header_by_transaction_id(transaction_id)
            if product_id in header["lines"]:
                raise _DuplicateLine()
            header["lines"][product_id] = (quantity, unit_price)
            self.row = None
        elif text.startswith("INSERT INTO transaction"):
            source_transaction_id, customer_id, store_id, channel_id, occurred_at = (
                parameters
            )
            if c.simulate_concurrent_header:
                raise _ConcurrentHeader()
            if customer_id not in c.customer_ids:
                raise _MissingReference("transaction_customer_id_fkey")
            if store_id == "OUT_OF_RANGE":
                raise _OutOfRange()
            if store_id not in c.store_ids:
                raise _MissingReference("transaction_store_id_fkey")
            if channel_id not in c.channel_ids:
                raise _MissingReference("transaction_channel_id_fkey")
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
            c._uncommitted_headers.add(source_transaction_id)
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
                "customer_id": _CUSTOMER_1,
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


def test_a_row_with_a_different_customer_for_the_same_header_is_rejected() -> None:
    """Regression: header.customer_id (a uuid.UUID) and row.customer_id (a
    str) must compare equal by value, not by identity/type -- see
    web/db/sales.py's get_transaction_by_source_id normalization."""
    other_customer = "00000000-0000-0000-0000-000000000002"
    connection = _Connection(
        customer_ids={_CUSTOMER_1, other_customer},
        headers={
            "TXN-1": {
                "transaction_id": 1,
                "customer_id": _CUSTOMER_1,
                "store_id": 1,
                "channel_id": 1,
                "occurred_at": _OCCURRED_AT,
                "total": Decimal("19.98"),
                "lines": {1: (2, Decimal("9.99"))},
            }
        },
    )

    with pytest.raises(RowRejected, match="disagrees with the accepted header"):
        ingest_row(connection, _row(customer_id=other_customer))


def test_a_repeated_product_line_for_the_same_source_id_is_rejected_as_duplicate() -> (
    None
):
    connection = _Connection(
        headers={
            "TXN-1": {
                "transaction_id": 1,
                "customer_id": _CUSTOMER_1,
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


# ---------- out-of-range values don't escape as unhandled exceptions ----------


def test_a_store_id_out_of_range_is_rejected_not_raised_uncaught() -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="malformed row"):
        ingest_row(connection, _row(store_id="OUT_OF_RANGE"))

    assert connection.headers == {}
    assert connection.rollbacks == 1


def test_a_product_id_out_of_range_on_the_line_insert_is_rejected() -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="malformed row"):
        ingest_row(connection, _row(product_id="OUT_OF_RANGE"))

    assert connection.rollbacks == 1


def test_a_source_transaction_id_created_concurrently_is_rejected() -> None:
    """Regression: a UniqueViolation racing a brand-new header insert used to
    escape ingest_row uncaught instead of being translated to RowRejected."""
    connection = _Connection(simulate_concurrent_header=True)

    with pytest.raises(RowRejected, match="created concurrently"):
        ingest_row(connection, _row())
