"""A phone sale is seen by the web with no web change (F14-18, ADR-0039).

sales-service writes a sale as svc_sales: the header in the `svc-sales:`
namespace, its lines, one api_stock.consume_v1 per line and its
sales.sale_capture row. The web's own reads, imported unchanged, must then
count it in the reports revenue summary and show its decrement in the stock
consultation. Everything runs in one transaction that is rolled back.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import psycopg
import pytest

from web.db.inventory import list_stock
from web.db.revenue_summary import read_revenue_summary

pytestmark = pytest.mark.postgres

# The guards read session_user, which SET ROLE cannot change, so the owner (a
# superuser on the test database) switches the session itself.
_AS_SVC_SALES = "SET LOCAL SESSION AUTHORIZATION svc_sales"
# The summary's window ends at the newest sale, which the phone sale becomes;
# ten years holds every seeded sale both times, so the count moves by exactly one.
WINDOW_DAYS = 3650


def _stock(connection: psycopg.Connection, store_id: int, product_id: int) -> int:
    rows, _ = list_stock(
        connection, store_id=store_id, search=None, page=1, per_page=10_000
    )
    (row,) = [row for row in rows if row.product_id == product_id]
    return row.quantity_on_hand


def test_a_phone_sale_is_counted_in_reports_and_decrements_the_stock_page(
    owner_url: str,
) -> None:
    with psycopg.connect(owner_url) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT i.store_id, i.product_id, p.list_price"
                    " FROM public.inventory AS i"
                    " JOIN public.product AS p USING (product_id)"
                    " WHERE p.is_active AND i.quantity_on_hand >= %s"
                    " ORDER BY i.store_id, i.product_id LIMIT 1",
                    (2,),
                )
                store_id, product_id, price = cur.fetchone()
                cur.execute(
                    "SELECT min(customer_id::text) FROM public.customer"
                    " WHERE registered_on <= current_date"
                )
                (customer_id,) = cur.fetchone()
                cur.execute("SELECT min(user_id::text) FROM public.app_user")
                (recorded_by,) = cur.fetchone()
            summary_before = read_revenue_summary(conn, WINDOW_DAYS)
            stock_before = _stock(conn, store_id, product_id)

            total = Decimal(price) * 2
            with conn.cursor() as cur:
                cur.execute(_AS_SVC_SALES)
                cur.execute(
                    "INSERT INTO public.transaction"
                    " (source_transaction_id, customer_id, store_id, channel_id,"
                    "  occurred_at, total, data_origin)"
                    " VALUES (%s, %s, %s, %s, now(), %s, %s) RETURNING transaction_id",
                    (
                        "svc-sales:" + str(uuid.uuid4()),
                        customer_id,
                        store_id,
                        1,
                        total,
                        "OBSERVED",
                    ),
                )
                (sale_id,) = cur.fetchone()
                cur.execute(
                    "INSERT INTO public.transaction_line"
                    " (transaction_id, product_id, quantity, unit_price)"
                    " VALUES (%s, %s, %s, %s)",
                    (sale_id, product_id, 2, price),
                )
                cur.execute(
                    "SELECT api_stock.consume_v1(%s::smallint, %s, %s)",
                    (store_id, product_id, 2),
                )
                cur.execute(
                    "INSERT INTO sales.sale_capture"
                    " (transaction_id, request_sha256, recorded_by)"
                    " VALUES (%s, %s, %s)",
                    (sale_id, bytes(32), recorded_by),
                )
                cur.execute("RESET SESSION AUTHORIZATION")
                cur.execute("SET LOCAL ROLE retail_app")

            summary_after = read_revenue_summary(conn, WINDOW_DAYS)
            assert summary_after.transactions == summary_before.transactions + 1
            assert summary_after.revenue == summary_before.revenue + total
            assert _stock(conn, store_id, product_id) == stock_before - 2
        finally:
            conn.rollback()
