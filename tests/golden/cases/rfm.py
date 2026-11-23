"""Raw R, F and M, and the scores, through the web's RFM read (F20-11, ADR-0045, E-17).

`score_rfm_rules` and `read_rfm_inputs` in web/db/segments.py, which read the net
units of `public.net_sale` (ADR-0062). The cases are:

- every seeded customer, at the seed's anchor: the instant of the last net
  purchase, the count, the net total, the three quintile scores and the label;
- synthetic customers, added inside their own rolled-back case, whose sales are
  cancelled or partly returned as sales-service records them (F21-01): no
  purchase at all, one sale, a cancelled latest sale, a wholly cancelled one, a
  partly returned one, a returned one that leaves an earlier one, and one older
  than the window. Only their raw values are recorded: a quintile depends on the
  whole population, which a synthetic customer would move.

Every sale is one line. A reversal is written as svc_sales, which alone may write
`returned_quantity`, so this port is captured on a connection that can switch to
that role (the database owner); the web's own reads then run as `retail_app`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import psycopg

from tests.golden.cases import Case, Context, accepted, one
from web.db.segments import read_rfm_inputs, score_rfm_rules

MICROSECOND = timedelta(microseconds=1)
# Synthetic customers: ids no seeded row uses.
SYNTHETIC = "00000000-0000-0000-0000-00000000c{n:03d}"

# What a synthetic customer bought: (days before the anchor, units, unit price,
# whether the sale is cancelled whole, units returned). `window` stands for the
# run's window and is replaced when the case is built.
SCENARIOS: list[tuple[str, list[dict[str, Any]]]] = [
    ("no-purchase", []),
    ("one-sale", [{"days": 5, "units": 2, "price": "30.00"}]),
    (
        "cancelled-latest-sale",
        [
            {"days": 5, "units": 2, "price": "30.00"},
            {"days": 2, "units": 1, "price": "50.00", "cancelled": True},
        ],
    ),
    (
        "only-sale-cancelled",
        [{"days": 5, "units": 2, "price": "30.00", "cancelled": True}],
    ),
    (
        "partly-returned-sale",
        [{"days": 3, "units": 4, "price": "10.00", "returned": 1}],
    ),
    (
        "returned-latest-sale-leaves-an-earlier-one",
        [
            {"days": 10, "units": 1, "price": "20.00"},
            {"days": 3, "units": 2, "price": "15.00", "returned": 2},
        ],
    ),
    ("older-than-the-window", [{"days": "window+10", "units": 1, "price": "9.99"}]),
]


class Ledger:
    """One connection, switched between sales-service's role and the web's."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self.cur = connection.cursor()
        superuser = one(connection, "SELECT current_setting('is_superuser')")
        if superuser != "on":
            raise SystemExit(
                "the rfm port records reversals as svc_sales: run the capture with "
                "--database-url of the database owner"
            )
        self.user = one(connection, "SELECT min(user_id::text) FROM public.app_user")
        self.store = one(connection, "SELECT min(store_id) FROM public.store")
        self.product = one(
            connection,
            "SELECT min(product_id) FROM public.product"
            " WHERE is_active AND list_price > 0",
        )

    def as_service(self) -> None:
        self.cur.execute("RESET ROLE")
        self.cur.execute("SET LOCAL SESSION AUTHORIZATION svc_sales")

    def as_web(self) -> None:
        self.cur.execute("RESET ROLE")
        self.cur.execute("RESET SESSION AUTHORIZATION")
        self.cur.execute("SET LOCAL ROLE retail_app")

    def customer(self, customer_id: str, context: Context) -> None:
        self.as_web()
        self.cur.execute(
            "INSERT INTO customer (customer_id, name, registration_channel_id,"
            " registered_on) VALUES (%s, %s, 1, %s)",
            (
                customer_id,
                "Golden RFM " + customer_id[-4:],
                context.before_anchor(800).date(),
            ),
        )

    def sale(self, customer_id: str, at, units: int, price: str) -> int:
        """A one-line sale as sales-service writes it, without the stock it
        consumes: the stock is not what this port reads."""
        self.as_service()
        self.cur.execute(
            "INSERT INTO public.transaction (source_transaction_id, customer_id,"
            " store_id, channel_id, occurred_at, total, data_origin)"
            " VALUES (%s, %s, %s, 1, %s, %s, 'OBSERVED') RETURNING transaction_id",
            (
                "svc-sales:" + str(uuid.uuid4()),
                customer_id,
                self.store,
                at,
                Decimal(price) * units,
            ),
        )
        (sale_id,) = self.cur.fetchone()
        self.cur.execute(
            "INSERT INTO public.transaction_line"
            " (transaction_id, product_id, quantity, unit_price)"
            " VALUES (%s, %s, %s, %s)",
            (sale_id, self.product, units, price),
        )
        self.cur.execute(
            "INSERT INTO sales.sale_capture (transaction_id, request_sha256,"
            " recorded_by) VALUES (%s, %s, %s)",
            (sale_id, bytes(32), self.user),
        )
        return sale_id

    def reverse(self, sale_id: int, kind: str, units: int) -> None:
        self.as_service()
        self.cur.execute(
            "INSERT INTO sales.sale_reversal (transaction_id, kind, reason,"
            " idempotency_key, request_sha256, recorded_by)"
            " VALUES (%s, %s, %s, gen_random_uuid(), %s, %s) RETURNING reversal_id",
            (
                sale_id,
                kind,
                "golden" if kind == "CANCELLATION" else None,
                bytes(32),
                self.user,
            ),
        )
        (reversal_id,) = self.cur.fetchone()
        self.cur.execute(
            "INSERT INTO sales.sale_reversal_line (reversal_id, transaction_id,"
            " product_id, quantity, reason, resellable)"
            " VALUES (%s, %s, %s, %s, %s, false)",
            (
                reversal_id,
                sale_id,
                self.product,
                units,
                None if kind == "CANCELLATION" else "golden",
            ),
        )
        self.cur.execute(
            "UPDATE public.transaction_line"
            " SET returned_quantity = returned_quantity + %s"
            " WHERE transaction_id = %s AND product_id = %s",
            (units, sale_id, self.product),
        )


def _microseconds(context: Context, instant) -> int | None:
    if instant is None:
        return None
    return (context.anchor - instant) // MICROSECOND


def _raw(context: Context, found) -> dict[str, Any]:
    """The web's raw values for one customer; none at all is a customer with no
    net purchase in the window (RN-21)."""
    return {
        "last_purchase_before_anchor_us": _microseconds(
            context, found.last_purchase_at
        ),
        "frequency": found.frequency,
        "monetary": None if found.monetary is None else str(found.monetary),
    }


def _seeded(window_days: int, context: Context) -> Case:
    def run(connection: psycopg.Connection) -> dict[str, Any]:
        rows = score_rfm_rules(connection, window_days)
        return accepted(
            customers=[
                {
                    "customer_id": row.customer_id,
                    **_raw(context, row),
                    "r_score": row.r_score,
                    "f_score": row.f_score,
                    "m_score": row.m_score,
                    "label_code": row.label_code,
                    "via_fallback": row.via_fallback,
                }
                for row in sorted(rows, key=lambda row: row.customer_id)
            ]
        )

    return Case(
        "seeded-customers",
        {"call": "score_rfm_rules", "window_days": window_days},
        run,
    )


def _synthetic(
    number: int,
    case_id: str,
    sales: list[dict[str, Any]],
    window_days: int,
    context: Context,
) -> Case:
    customer_id = SYNTHETIC.format(n=number)
    resolved = [
        {
            **sale,
            "days": window_days + 10 if sale["days"] == "window+10" else sale["days"],
        }
        for sale in sales
    ]

    def run(connection: psycopg.Connection) -> dict[str, Any]:
        ledger = Ledger(connection)
        ledger.customer(customer_id, context)
        for sale in resolved:
            sale_id = ledger.sale(
                customer_id,
                context.before_anchor(sale["days"]),
                sale["units"],
                sale["price"],
            )
            if sale.get("cancelled"):
                ledger.reverse(sale_id, "CANCELLATION", sale["units"])
            elif sale.get("returned"):
                ledger.reverse(sale_id, "RETURN", sale["returned"])
        ledger.as_web()
        found = {
            row.customer_id: row for row in read_rfm_inputs(connection, window_days)
        }[customer_id]
        return accepted(**_raw(context, found))

    return Case(
        case_id,
        {
            "customer_id": customer_id,
            "window_days": window_days,
            "sales": resolved,
        },
        run,
    )


def build(connection: psycopg.Connection, context: Context) -> list[Case]:
    window_days = one(
        connection,
        "SELECT window_days FROM segmentation_run WHERE method = 'RFM_RULES'"
        " AND scope = 'FULL' ORDER BY run_id DESC LIMIT 1",
    )
    return [_seeded(window_days, context)] + [
        _synthetic(number, case_id, sales, window_days, context)
        for number, (case_id, sales) in enumerate(SCENARIOS, start=1)
    ]


def as_of(context: Context) -> dict[str, Any]:
    return {"anchor": context.anchor}
