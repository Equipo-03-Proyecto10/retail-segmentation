"""The rule of a sale, as one module (ADR-0039, ADR-0070 section 4).

`POST /v1/sales` and the row seam of a sales import both call it, so a row of a
file and a phone sale are judged by the same five ported checks (future date,
registration date, active product, positive price, two decimals), the same
quantity and total limits, and the same mapping of the database's refusals to
stable codes. There is no second copy: the import worker imports these functions
and adds only what a file alone can get wrong (cells, ids), in `rows.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from mosaiq_kit import ApiError

from . import store

# The columns a sale lands in: transaction_line.quantity INT,
# transaction_line.unit_price NUMERIC(10,2), transaction.total NUMERIC(12,2).
# Past them PostgreSQL would answer 22003, so they are refused first.
MAX_QUANTITY = 2_147_483_647
MAX_PRICE = Decimal("99999999.99")
MAX_TOTAL = Decimal("9999999999.99")


def error(status: int, code: str, message: str, field: str, detail: str) -> ApiError:
    return ApiError(status, code, message, [{"field": field, "code": detail}])


def line_error(
    status: int, code: str, message: str, index: int, member: str, detail: str
) -> ApiError:
    return error(status, code, message, f"lines[{index}].{member}", detail)


def parse_timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise error(
            422,
            "invalid_body" if field == "occurred_at" else "invalid_query",
            (
                "The sale violates its schema."
                if field == "occurred_at"
                else "A filter is malformed or the range is reversed."
            ),
            field,
            "invalid_format",
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise error(
            422,
            "invalid_body" if field == "occurred_at" else "invalid_query",
            (
                "The sale violates its schema."
                if field == "occurred_at"
                else "A filter is malformed or the range is reversed."
            ),
            field,
            "missing_offset",
        )
    return parsed


def validate_sale_rules(body: dict[str, Any]) -> tuple[datetime, list[dict[str, Any]]]:
    lines = body.get("lines") or []
    if not lines:
        raise error(
            422, "no_lines", "A sale needs at least one line.", "lines", "empty"
        )
    seen: set[int] = set()
    normalized = []
    for index, line in enumerate(lines):
        product_id = line["product_id"]
        if product_id in seen:
            raise line_error(
                409,
                "duplicate_product_line",
                "A product appears on more than one line.",
                index,
                "product_id",
                "duplicate",
            )
        seen.add(product_id)
        quantity = line["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise line_error(
                422,
                "invalid_quantity",
                "A quantity is not a positive whole number.",
                index,
                "quantity",
                "not_positive",
            )
        if quantity > MAX_QUANTITY:
            raise line_error(
                422,
                "invalid_quantity",
                "A quantity is larger than a sale line can hold.",
                index,
                "quantity",
                "out_of_range",
            )
        try:
            price = Decimal(str(line["unit_price"]))
        except (InvalidOperation, ValueError):
            price = Decimal("NaN")
        if not price.is_finite() or price <= 0 or price.as_tuple().exponent < -2:
            raise line_error(
                422,
                "invalid_price",
                "A unit price is not a positive amount with two decimals.",
                index,
                "unit_price",
                "invalid",
            )
        if price > MAX_PRICE:
            raise line_error(
                422,
                "invalid_price",
                "A unit price is larger than a sale line can hold.",
                index,
                "unit_price",
                "out_of_range",
            )
        normalized.append(
            {"product_id": product_id, "quantity": quantity, "unit_price": price}
        )
    total = sum(
        (line["unit_price"] * line["quantity"] for line in normalized), Decimal(0)
    )
    if total > MAX_TOTAL:
        raise error(
            422,
            "invalid_body",
            "The sale's total is larger than a sale can hold.",
            "lines",
            "out_of_range",
        )
    occurred_at = parse_timestamp(body["occurred_at"], "occurred_at")
    if occurred_at > datetime.now(UTC):
        raise error(
            422,
            "future_dated",
            "The sale is dated in the future.",
            "occurred_at",
            "future",
        )
    return occurred_at, normalized


def validate_sale_references(
    connection: psycopg.Connection,
    body: dict[str, Any],
    occurred_at: datetime,
    lines: list[dict[str, Any]],
) -> None:
    registered_on = store.customer_registration(connection, body["customer_id"])
    if registered_on is None:
        raise error(
            422,
            "unknown_customer",
            "The customer does not exist.",
            "customer_id",
            "unknown",
        )
    if occurred_at.date() < registered_on:
        raise error(
            422,
            "before_registration",
            "The sale is dated before the customer registered.",
            "occurred_at",
            "before_registration",
        )
    states = store.products(connection, [line["product_id"] for line in lines])
    for index, line in enumerate(lines):
        state = states.get(line["product_id"])
        if state is None:
            raise line_error(
                422,
                "unknown_product",
                "A product does not exist.",
                index,
                "product_id",
                "unknown",
            )
        if not state:
            raise line_error(
                422,
                "inactive_product",
                "A product is not active.",
                index,
                "product_id",
                "inactive",
            )


def constraint(exc: psycopg.Error) -> str:
    return exc.diag.constraint_name or ""


def database_refusal(
    exc: psycopg.Error, lines: list[dict[str, Any]], line_index: int | None = None
) -> ApiError:
    """The stable code for a refusal the database made, or the error itself."""
    name = constraint(exc)
    if name in {"no_stock_record", "stock_unavailable"}:
        index = line_index if line_index is not None else 0
        if name == "no_stock_record":
            return line_error(
                422,
                "no_stock_record",
                "The store has no stock record for a product.",
                index,
                "product_id",
                "no_stock_record",
            )
        return line_error(
            409,
            "stock_unavailable",
            "The store does not hold enough stock for a line.",
            index,
            "quantity",
            "insufficient_stock",
        )
    foreign = {
        "transaction_store_id_fkey": (
            "unknown_store",
            "The store does not exist.",
            "store_id",
        ),
        "transaction_channel_id_fkey": (
            "unknown_channel",
            "The channel does not exist.",
            "channel_id",
        ),
    }
    if name in foreign:
        code, message, field = foreign[name]
        return error(422, code, message, field, "unknown")
    raise exc
