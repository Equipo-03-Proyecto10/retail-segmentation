"""F8-02: row-level sales ingestion (ADR-0020).

A scripted fake connection, in the style of
tests/test_single_administrator.py: `execute` answers by looking at the SQL
text rather than at call order, because the service's own business logic
decides which question it asks next.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from psycopg.errors import (
    DataError,
    ForeignKeyViolation,
    InvalidTextRepresentation,
    NumericValueOutOfRange,
    StringDataRightTruncation,
    UniqueViolation,
)

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
        inactive_product_ids: set[int] | None = None,
        registered_on: date = date(2025, 1, 1),
        inventory: dict[tuple[int, int], int] | None = None,
        headers: dict[str, dict] | None = None,
        simulate_concurrent_header: bool = False,
    ) -> None:
        self.customer_ids = customer_ids or {_CUSTOMER_1}
        self.store_ids = store_ids or {1, 2}
        self.channel_ids = channel_ids or {1}
        self.product_ids = product_ids or {1, 2}
        self.inactive_product_ids = inactive_product_ids or set()
        self.registered_on = registered_on
        self.inventory = {(1, 1): 10, (1, 2): 10} if inventory is None else inventory
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
        self._uncommitted_lines: list[tuple[dict, int]] = []
        self._uncommitted_stock: list[tuple[tuple[int, int], int]] = []
        self.statements: list[str] = []

    def commit(self) -> None:
        self.commits += 1
        self._uncommitted_headers.clear()
        self._uncommitted_lines.clear()
        self._uncommitted_stock.clear()

    def rollback(self) -> None:
        self.rollbacks += 1
        for source_transaction_id in self._uncommitted_headers:
            del self.headers[source_transaction_id]
        for header, product_id in reversed(self._uncommitted_lines):
            header["lines"].pop(product_id, None)
        for inventory_key, quantity in reversed(self._uncommitted_stock):
            self.inventory[inventory_key] = quantity
        self._uncommitted_headers.clear()
        self._uncommitted_lines.clear()
        self._uncommitted_stock.clear()

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
        c.statements.append(text)

        if text.startswith("SELECT c.registered_on"):
            product_id, customer_id = parameters
            self.row = (
                None
                if customer_id not in c.customer_ids
                else (
                    c.registered_on,
                    (
                        None
                        if product_id not in c.product_ids
                        and product_id != "OUT_OF_RANGE"
                        else product_id not in c.inactive_product_ids
                    ),
                )
            )
        elif "FROM transaction WHERE source_transaction_id" in text:
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
            c._uncommitted_lines.append((header, product_id))
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
        elif text.startswith("UPDATE inventory"):
            quantity, store_id, product_id, minimum_quantity = parameters
            assert quantity == minimum_quantity
            inventory_key = (store_id, product_id)
            available = c.inventory.get(inventory_key)
            if available is not None and available >= quantity:
                c._uncommitted_stock.append((inventory_key, available))
                c.inventory[inventory_key] = available - quantity
                self.row = (available - quantity,)
            else:
                self.row = None
        elif text.startswith("SELECT quantity_on_hand"):
            store_id, product_id = parameters
            available = c.inventory.get((store_id, product_id))
            self.row = None if available is None else (available,)
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
    assert connection.inventory[(1, 1)] == 8
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_header_lock_is_acquired_before_the_inventory_lock() -> None:
    """Keep #355's header serialization ahead of the stock row lock.

    Every ingestion call handles one line, so this order prevents a future
    multi-line or retry path from taking the two shared locks in reverse order.
    """
    connection = _Connection()

    ingest_row(connection, _row())

    header_lock = next(
        index
        for index, statement in enumerate(connection.statements)
        if "FROM transaction WHERE source_transaction_id" in statement
    )
    inventory_lock = next(
        index
        for index, statement in enumerate(connection.statements)
        if statement.startswith("UPDATE inventory")
    )
    assert header_lock < inventory_lock


def test_a_second_line_for_the_same_source_id_is_appended_and_total_recomputed() -> (
    None
):
    connection = _Connection()
    ingest_row(connection, _row(product_id=1, quantity=2, unit_price=Decimal("9.99")))

    ingest_row(connection, _row(product_id=2, quantity=1, unit_price=Decimal("5.00")))

    header = connection.headers["TXN-1"]
    assert set(header["lines"]) == {1, 2}
    assert header["total"] == Decimal("24.98")
    assert connection.inventory == {(1, 1): 8, (1, 2): 9}
    assert connection.commits == 2


def test_insufficient_stock_rolls_back_the_new_header_and_line() -> None:
    connection = _Connection(inventory={(1, 1): 1})

    with pytest.raises(RowRejected, match="insufficient stock"):
        ingest_row(connection, _row(quantity=2))

    assert connection.headers == {}
    assert connection.inventory == {(1, 1): 1}
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_missing_stock_row_rolls_back_the_new_header_and_line() -> None:
    connection = _Connection(inventory={(1, 2): 10})

    with pytest.raises(RowRejected, match="no inventory"):
        ingest_row(connection, _row())

    assert connection.headers == {}
    assert connection.inventory == {(1, 2): 10}
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_a_failure_after_decrement_rolls_back_stock_header_and_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()

    def fail_recompute(*_args, **_kwargs) -> None:
        raise RuntimeError("total update failed")

    monkeypatch.setattr("web.services.ingestion.sales.recompute_total", fail_recompute)

    with pytest.raises(RuntimeError, match="total update failed"):
        ingest_row(connection, _row())

    assert connection.headers == {}
    assert connection.inventory[(1, 1)] == 10
    assert connection.commits == 0
    assert connection.rollbacks == 1


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


def test_zero_unit_price_is_rejected() -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="positive"):
        ingest_row(connection, _row(unit_price=Decimal("0.00")))

    assert connection.headers == {}


def test_a_unit_price_with_more_than_two_decimals_is_rejected() -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="2 decimal"):
        ingest_row(connection, _row(unit_price=Decimal("10.999")))

    assert connection.headers == {}


def test_a_future_sale_is_rejected() -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="future"):
        ingest_row(
            connection,
            _row(occurred_at=datetime.now(UTC) + timedelta(minutes=1)),
        )

    assert connection.headers == {}


def test_a_sale_before_customer_registration_is_rejected() -> None:
    connection = _Connection(registered_on=date(2026, 1, 16))

    with pytest.raises(RowRejected, match="registration"):
        ingest_row(connection, _row())

    assert connection.headers == {}


def test_a_sale_of_an_inactive_product_is_rejected() -> None:
    connection = _Connection(inactive_product_ids={1})

    with pytest.raises(RowRejected, match="inactive"):
        ingest_row(connection, _row())

    assert connection.headers == {}


@pytest.mark.parametrize("source_id", ["=2+3", "+SUM(A1:A2)", "@cmd", "-1"])
def test_a_spreadsheet_formula_like_transaction_id_is_rejected(
    source_id: str,
) -> None:
    connection = _Connection()

    with pytest.raises(RowRejected, match="spreadsheet formula"):
        ingest_row(connection, _row(source_transaction_id=source_id))

    assert connection.headers == {}


@pytest.mark.parametrize("unit_price", [Decimal("NaN"), Decimal("Infinity")])
def test_a_non_finite_unit_price_is_rejected_not_a_crash(
    unit_price: Decimal,
) -> None:
    """#286: comparing a NaN Decimal with `<` raises InvalidOperation instead
    of returning a bool, which used to escape as an unhandled 500."""
    connection = _Connection()

    with pytest.raises(RowRejected, match="finite"):
        ingest_row(connection, _row(unit_price=unit_price))

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


# ---------- #334: PostgreSQL's own text never reaches the administrator ----------

# Real server messages, including the CONTEXT/DETAIL lines psycopg keeps in
# str(error) -- what the rejection report used to show verbatim.
_SERVER_TEXT = {
    InvalidTextRepresentation: 'invalid input syntax for type uuid: "x"\n'
    "CONTEXT:  unnamed portal parameter $2 = '...'",
    StringDataRightTruncation: "value too long for type character varying(64)",
    NumericValueOutOfRange: "numeric field overflow\nDETAIL:  A field with "
    "precision 10, scale 2 must round to an absolute value less than 10^8.",
    DataError: "some other data error",
}


@pytest.mark.parametrize(
    ("error_type", "reason"),
    [
        (
            InvalidTextRepresentation,
            "malformed row: customer_id is not a valid identifier",
        ),
        (StringDataRightTruncation, "malformed row: transaction_id is too long"),
        (
            NumericValueOutOfRange,
            "malformed row: store_id or channel_id is out of range",
        ),
        (DataError, "malformed row: a value is not in the expected format"),
    ],
)
def test_a_header_value_postgresql_refuses_is_named_in_business_terms(
    monkeypatch: pytest.MonkeyPatch, error_type: type[DataError], reason: str
) -> None:
    def _refuse(*_args, **_kwargs):
        raise error_type(_SERVER_TEXT[error_type])

    monkeypatch.setattr("web.db.sales.insert_transaction", _refuse)

    with pytest.raises(RowRejected) as rejection:
        ingest_row(_Connection(), _row())

    assert str(rejection.value) == reason


@pytest.mark.parametrize(
    ("error_type", "reason"),
    [
        (
            NumericValueOutOfRange,
            "malformed row: product_id, quantity or unit_price is out of range",
        ),
        (DataError, "malformed row: a value is not in the expected format"),
    ],
)
def test_a_line_value_postgresql_refuses_is_named_in_business_terms(
    monkeypatch: pytest.MonkeyPatch, error_type: type[DataError], reason: str
) -> None:
    def _refuse(*_args, **_kwargs):
        raise error_type(_SERVER_TEXT[error_type])

    monkeypatch.setattr("web.db.sales.insert_transaction_line", _refuse)

    with pytest.raises(RowRejected) as rejection:
        ingest_row(_Connection(), _row())

    assert str(rejection.value) == reason
