"""What a file alone can get wrong, read from one row's cells (ADR-0070 section 4).

A chunk carries a row as positional text cells and never types them. This module
turns the eight cells into a typed row, or refuses the row with its own code,
field and sentence: the cell count, a blank or mistyped cell, a date with no
offset, a spreadsheet formula in the id, an id too long to prefix. Everything
after that is the rule of a sale, which `salerules.py` holds and
`POST /v1/sales` calls too; `from_api_error` only gives its refusals the words of
a row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from mosaiq_kit import ApiError

# The CSV contract of the web's page, version 1 (ADR-0020): the eight columns,
# in order. A refusal names one of them as its field.
COLUMNS = (
    "transaction_id",
    "customer_id",
    "store_id",
    "channel_id",
    "occurred_at",
    "product_id",
    "quantity",
    "unit_price",
)
# `svc-sales:` is the namespace of ADR-0039; `imp:` keeps an imported id apart
# from a phone sale's UUID, and leaves 50 characters of a 64-character column.
IMPORT_PREFIX = "svc-sales:imp:"
MAX_ID_LENGTH = 50
FORMULA_PREFIXES = ("=", "+", "-", "@")
_WHOLE = re.compile(r"[+-]?[0-9]+")
SMALLINT = (-32768, 32767)
INTEGER = (-2147483648, 2147483647)


class Rejection(Exception):  # noqa: N818 - a row's outcome, not a program error
    """A row refused, with what the report shows."""

    def __init__(
        self,
        code: str,
        field: str | None,
        message: str,
        duplicate_of: str | None = None,
        first_line: int | None = None,
        sale_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message
        self.duplicate_of = duplicate_of
        self.first_line = first_line
        self.sale_id = sale_id


@dataclass(frozen=True)
class Row:
    """One row, typed. `cells` stays so a sentence can quote the cell at fault."""

    line: int
    cells: tuple[str, ...]
    source_id: str
    customer_id: str
    store_id: int
    channel_id: int
    occurred_at: datetime
    product_id: int
    quantity: int
    unit_price: Decimal

    def cell(self, field: str) -> str:
        return self.cells[COLUMNS.index(field)]

    def sale_body(self) -> dict[str, Any]:
        """The row as the one-line sale `POST /v1/sales` would receive."""
        return {
            "customer_id": self.customer_id,
            "store_id": self.store_id,
            "channel_id": self.channel_id,
            "occurred_at": self.cell("occurred_at").strip(),
            "lines": [
                {
                    "product_id": self.product_id,
                    "quantity": self.quantity,
                    "unit_price": self.cell("unit_price").strip(),
                }
            ],
        }


def quote(cell: str) -> str:
    """At most 64 characters of a cell, with control characters removed, so a
    sentence can show what was read without carrying a formula or a escape
    sequence to whoever reads the report (RN-37)."""
    cleaned = "".join(ch for ch in cell if ch.isprintable())
    return cleaned[:64]


def rejection(
    line: int, code: str, field: str | None, text: str, cell: str | None = None
) -> Rejection:
    sentence = f"Line {line}: {text}"
    if cell is not None:
        sentence += f'; the cell holds "{quote(cell)}"'
    return Rejection(code, field, sentence + ".")


def _whole(row_line: int, cells: list[str], field: str, limits: tuple[int, int]) -> int:
    cell = cells[COLUMNS.index(field)]
    text = cell.strip()
    if not _WHOLE.fullmatch(text):
        raise rejection(
            row_line, "invalid_value", field, f"{field} must be a whole number", cell
        )
    value = int(text)
    if not limits[0] <= value <= limits[1]:
        raise rejection(
            row_line, "invalid_value", field, f"{field} is out of range", cell
        )
    return value


def parse(line: int, cells: list[str]) -> Row:
    """The typed row, or the `Rejection` a file alone can cause."""
    if len(cells) != len(COLUMNS):
        raise rejection(
            line,
            "wrong_cell_count",
            None,
            f"the row has {len(cells)} cells; a row has exactly {len(COLUMNS)}",
        )
    source = cells[0].strip()
    if not source:
        raise rejection(
            line, "invalid_value", "transaction_id", "transaction_id is blank", cells[0]
        )
    if source.startswith(FORMULA_PREFIXES):
        raise rejection(
            line,
            "formula_like_id",
            "transaction_id",
            "transaction_id starts with =, +, - or @, which a spreadsheet would"
            " evaluate",
            cells[0],
        )
    if len(source) > MAX_ID_LENGTH:
        raise rejection(
            line,
            "id_too_long",
            "transaction_id",
            f"transaction_id is longer than {MAX_ID_LENGTH} characters",
            cells[0],
        )
    try:
        customer = str(UUID(cells[1].strip()))
    except ValueError:
        raise rejection(
            line,
            "invalid_value",
            "customer_id",
            "customer_id must be a UUID",
            cells[1],
        ) from None
    store_id = _whole(line, cells, "store_id", SMALLINT)
    channel_id = _whole(line, cells, "channel_id", SMALLINT)
    occurred = cells[4].strip()
    try:
        occurred_at = datetime.fromisoformat(occurred)
    except ValueError:
        raise rejection(
            line,
            "invalid_value",
            "occurred_at",
            "occurred_at must be an ISO 8601 date and time",
            cells[4],
        ) from None
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise rejection(
            line,
            "missing_offset",
            "occurred_at",
            "occurred_at must carry a UTC offset, as in 2026-10-02T14:03:00-06:00",
            cells[4],
        )
    product_id = _whole(line, cells, "product_id", INTEGER)
    quantity = _whole(line, cells, "quantity", (-(2**63), 2**63))
    try:
        price = Decimal(cells[7].strip())
    except InvalidOperation:
        raise rejection(
            line,
            "invalid_value",
            "unit_price",
            "unit_price must be a decimal number",
            cells[7],
        ) from None
    return Row(
        line=line,
        cells=tuple(cells),
        source_id=source,
        customer_id=customer,
        store_id=store_id,
        channel_id=channel_id,
        occurred_at=occurred_at,
        product_id=product_id,
        quantity=quantity,
        unit_price=price,
    )


# What each refusal of the sale rule says about a row: the column it concerns
# and the sentence. The codes are the ones POST /v1/sales answers with.
_SALE_REFUSALS = {
    "invalid_quantity": ("quantity", "quantity must be a positive whole number"),
    "invalid_price": (
        "unit_price",
        "unit_price must be positive, with at most two decimals",
    ),
    "future_dated": ("occurred_at", "occurred_at cannot be in the future"),
    "before_registration": (
        "occurred_at",
        "occurred_at is dated before the customer registered",
    ),
    "unknown_customer": ("customer_id", "customer_id names no customer"),
    "unknown_store": ("store_id", "store_id names no store"),
    "unknown_channel": ("channel_id", "channel_id names no channel"),
    "unknown_product": ("product_id", "product_id names no product"),
    "inactive_product": (
        "product_id",
        "product_id names a product that is no longer active",
    ),
    "stock_unavailable": ("quantity", "the store does not hold that many units"),
    "no_stock_record": (
        "product_id",
        "the store holds no stock record for that product",
    ),
}


def from_api_error(row: Row, error: ApiError) -> Rejection:
    """A refusal of the sale rule, in the words of a row. A code the rule can
    give a one-line sale that no file column maps to is reported as a value the
    column cannot hold."""
    known = _SALE_REFUSALS.get(error.code)
    if known is None:
        field = "unit_price"
        return rejection(
            row.line,
            "invalid_value",
            field,
            "the sale is larger than a sale can hold",
            row.cell(field),
        )
    field, text = known
    return rejection(row.line, error.code, field, text, row.cell(field))
