"""Parameterized PostgreSQL operations for sales-service."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql


class StockRefusal(Exception):
    """A stock function refusal paired with its original request line."""

    def __init__(self, index: int, error: psycopg.Error):
        super().__init__(str(error))
        self.index = index
        self.error = error


def _money(value: Decimal) -> str:
    return format(value, ".2f")


def _sale(row: tuple[Any, ...], lines: list[tuple[Any, ...]]) -> dict[str, Any]:
    return {
        "sale_id": row[0],
        "customer_id": str(row[1]),
        "store_id": row[2],
        "channel_id": row[3],
        "occurred_at": row[4],
        "total": _money(row[5]),
        "lines": [
            {
                "product_id": line[0],
                "quantity": line[1],
                "unit_price": _money(line[2]),
            }
            for line in lines
        ],
    }


def get_sale(connection: psycopg.Connection, sale_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT transaction_id, customer_id, store_id, channel_id,"
        " occurred_at, total FROM public.transaction WHERE transaction_id = %s",
        (sale_id,),
    ).fetchone()
    if row is None:
        return None
    lines = connection.execute(
        "SELECT product_id, quantity, unit_price FROM public.transaction_line"
        " WHERE transaction_id = %s ORDER BY product_id",
        (sale_id,),
    ).fetchall()
    return _sale(row, lines)


def find_capture(
    connection: psycopg.Connection, source_id: str
) -> tuple[dict[str, Any], bytes] | None:
    row = connection.execute(
        "SELECT t.transaction_id, c.request_sha256 FROM public.transaction AS t"
        " JOIN sales.sale_capture AS c USING (transaction_id)"
        " WHERE t.source_transaction_id = %s",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    sale = get_sale(connection, row[0])
    assert sale is not None
    return sale, bytes(row[1])


def customer_registration(
    connection: psycopg.Connection, customer_id: str
) -> Any | None:
    row = connection.execute(
        "SELECT registered_on FROM api_customers.customer_ref_v1"
        " WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()
    return None if row is None else row[0]


def products(connection: psycopg.Connection, product_ids: list[int]) -> dict[int, bool]:
    rows = connection.execute(
        "SELECT product_id, is_active FROM api_products.product_ref_v1"
        " WHERE product_id = ANY(%s)",
        (product_ids,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def insert_sale(
    connection: psycopg.Connection,
    *,
    source_id: str,
    customer_id: str,
    store_id: int,
    channel_id: int,
    occurred_at: datetime,
    total: Decimal,
    lines: list[dict[str, Any]],
    digest: bytes,
    recorded_by: str,
) -> int:
    row = connection.execute(
        "INSERT INTO public.transaction"
        " (source_transaction_id, customer_id, store_id, channel_id,"
        " occurred_at, total, data_origin)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING transaction_id",
        (
            source_id,
            customer_id,
            store_id,
            channel_id,
            occurred_at,
            total,
            "OBSERVED",
        ),
    ).fetchone()
    sale_id = row[0]
    for line in lines:
        connection.execute(
            "INSERT INTO public.transaction_line"
            " (transaction_id, product_id, quantity, unit_price)"
            " VALUES (%s, %s, %s, %s)",
            (
                sale_id,
                line["product_id"],
                line["quantity"],
                line["unit_price"],
            ),
        )
    indexed = sorted(enumerate(lines), key=lambda item: item[1]["product_id"])
    for index, line in indexed:
        try:
            connection.execute(
                "SELECT api_stock.consume_v1(%s::smallint, %s, %s)",
                (store_id, line["product_id"], line["quantity"]),
            ).fetchone()
        except psycopg.errors.CheckViolation as error:
            raise StockRefusal(index, error) from error
    connection.execute(
        "INSERT INTO sales.sale_capture"
        " (transaction_id, request_sha256, recorded_by) VALUES (%s, %s, %s)",
        (sale_id, digest, recorded_by),
    )
    return sale_id


def list_sales(
    connection: psycopg.Connection,
    *,
    customer_id: str | None,
    store_id: int | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    params = {
        "customer_id": customer_id,
        "store_id": store_id,
        "occurred_from": occurred_from,
        "occurred_to": occurred_to,
        "limit": limit,
        "offset": offset,
    }
    where = sql.SQL(
        " WHERE (%(customer_id)s::uuid IS NULL OR customer_id = %(customer_id)s)"
        " AND (%(store_id)s::smallint IS NULL OR store_id = %(store_id)s)"
        " AND (%(occurred_from)s::timestamptz IS NULL"
        " OR occurred_at >= %(occurred_from)s)"
        " AND (%(occurred_to)s::timestamptz IS NULL"
        " OR occurred_at < %(occurred_to)s)"
    )
    total = connection.execute(
        sql.SQL("SELECT count(*) FROM public.transaction {} ").format(where), params
    ).fetchone()[0]
    rows = connection.execute(
        sql.SQL(
            "SELECT transaction_id FROM public.transaction {}"
            " ORDER BY occurred_at DESC, transaction_id DESC"
            " LIMIT %(limit)s OFFSET %(offset)s"
        ).format(where),
        params,
    ).fetchall()
    sales = [get_sale(connection, row[0]) for row in rows]
    return [sale for sale in sales if sale is not None], total


def channels(connection: psycopg.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT channel_id, name FROM public.channel ORDER BY channel_id"
    ).fetchall()
    return [{"channel_id": row[0], "name": row[1]} for row in rows]
