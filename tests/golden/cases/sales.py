"""Sales line outcomes, through `ingest_row` in web/services/ingestion.py.

Each case is one CSV line. Seed facts the cases stand on:
- customers `00000000-…-0000000000NN` registered 180 + NN days before the load;
- products 1 to 40 are active;
- inventory covers stores 1 to 30 × products 1 to 5, with
  10 + (store + product) % 90 units, so store 1 holds 12 of product 1 and has
  no row for product 6.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg

from tests.golden.cases import Case, Context, accepted, one, refused
from web.services.ingestion import RowRejected, SalesRow, ingest_row

CUSTOMER = "00000000-0000-0000-0000-000000000001"
FUTURE = "2099-01-01T12:00:00+00:00"
# Before any seeded customer's registration on a load from 2020-08 onwards.
LONG_AGO = "2020-01-01T12:00:00+00:00"


def _line(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "source_transaction_id": "GOLDEN-SALE-1",
        "customer_id": CUSTOMER,
        "store_id": 1,
        "channel_id": 1,
        "occurred_at": {"days_before_anchor": 1},
        "product_id": 1,
        "quantity": 1,
        "unit_price": "10.00",
    }
    line.update(overrides)
    return line


def _occurred_at(value: Any, context: Context) -> datetime:
    if isinstance(value, dict):
        return context.before_anchor(value["days_before_anchor"])
    return datetime.fromisoformat(value)


def _case(
    case_id: str,
    line: dict[str, Any],
    context: Context,
    *,
    setup: str | None = None,
    setup_params: tuple = (),
    service_differs: str | None = None,
) -> Case:
    row = SalesRow(
        source_transaction_id=line["source_transaction_id"],
        customer_id=line["customer_id"],
        store_id=line["store_id"],
        channel_id=line["channel_id"],
        occurred_at=_occurred_at(line["occurred_at"], context),
        product_id=line["product_id"],
        quantity=line["quantity"],
        unit_price=Decimal(line["unit_price"]),
    )

    def run(connection: psycopg.Connection) -> dict[str, Any]:
        if setup:
            with connection.cursor() as cursor:
                cursor.execute(setup, setup_params)
        try:
            ingest_row(connection, row)
        except RowRejected as error:
            return refused(error)
        return accepted(
            quantity_on_hand_after=one(
                connection,
                "SELECT quantity_on_hand FROM inventory"
                " WHERE store_id = %s AND product_id = %s",
                (row.store_id, row.product_id),
            )
        )

    recorded = dict(line)
    if setup:
        recorded["setup"] = setup
    return Case(case_id, recorded, run, service_differs)


def build(connection: psycopg.Connection, context: Context) -> list[Case]:
    return [
        _case("accepted-line", _line(), context),
        _case("future-dated", _line(occurred_at=FUTURE), context),
        _case("before-registration", _line(occurred_at=LONG_AGO), context),
        _case(
            "inactive-product",
            _line(product_id=2),
            context,
            setup="UPDATE product SET is_active = false WHERE product_id = 2",
        ),
        _case("zero-quantity", _line(quantity=0), context),
        _case("negative-quantity", _line(quantity=-1), context),
        _case("zero-price", _line(unit_price="0.00"), context),
        _case("negative-price", _line(unit_price="-1.00"), context),
        _case("three-decimal-price", _line(unit_price="10.005"), context),
        _case(
            "unknown-customer",
            _line(customer_id="ffffffff-ffff-ffff-ffff-ffffffffffff"),
            context,
        ),
        _case("unknown-product", _line(product_id=99999), context),
        _case("unknown-store", _line(store_id=999), context),
        _case("unknown-channel", _line(channel_id=99), context),
        _case("no-inventory-row", _line(product_id=6), context),
        _case("insufficient-stock", _line(quantity=13), context),
        _case(
            "service-namespace",
            _line(
                source_transaction_id="svc-sales:00000000-0000-0000-0000-000000000001"
            ),
            context,
            service_differs=(
                "sales-service writes the svc-sales: namespace itself (ADR-0039): "
                "a CSV line naming it is refused, a phone sale from svc_sales is "
                "not."
            ),
        ),
    ]


def as_of(context: Context) -> dict[str, Any]:
    return {"anchor": context.anchor.isoformat()}
