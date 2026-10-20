"""Every SQL statement of products-service. All of them are parameterized."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql

COLUMNS = sql.SQL(", ").join(
    sql.Identifier(name)
    for name in (
        "product_id",
        "sku",
        "name",
        "category_id",
        "list_price",
        "image_path",
        "is_active",
    )
)
_Q_FILTER = "(strpos(lower(name), lower(%s)) > 0 OR strpos(lower(sku), lower(%s)) > 0)"
EDITABLE = ("name", "category_id", "list_price", "is_active")


def _product(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "product_id": row[0],
        "sku": row[1],
        "name": row[2],
        "category_id": row[3],
        "list_price": f"{row[4]:.2f}",
        "is_active": row[6],
        "image_path": row[5],
    }


def get_product(connection: psycopg.Connection, product_id: int) -> dict | None:
    row = connection.execute(
        sql.SQL("SELECT {} FROM public.product WHERE product_id = %s").format(COLUMNS),
        (product_id,),
    ).fetchone()
    return None if row is None else _product(row)


def list_products(
    connection: psycopg.Connection,
    *,
    q: str | None,
    sku: str | None,
    category_id: int | None,
    active: bool | None,
    ids: list[int] | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """One page and the filtered total. ``ids`` is a single ``= ANY(%s)``."""

    conditions: list[sql.Composable] = []
    params: list[Any] = []
    if q is not None:
        conditions.append(sql.SQL(_Q_FILTER))
        params += [q, q]
    if sku is not None:
        conditions.append(sql.SQL("sku = %s"))
        params.append(sku)
    if category_id is not None:
        conditions.append(sql.SQL("category_id = %s"))
        params.append(category_id)
    if active is not None:
        conditions.append(sql.SQL("is_active = %s"))
        params.append(active)
    if ids is not None:
        conditions.append(sql.SQL("product_id = ANY(%s)"))
        params.append(ids)
    where = (
        sql.SQL(" WHERE {}").format(sql.SQL(" AND ").join(conditions))
        if conditions
        else sql.SQL("")
    )
    query = sql.SQL(
        "SELECT {columns}, count(*) OVER () FROM public.product{where} "
        "ORDER BY product_id LIMIT %s OFFSET %s"
    ).format(columns=COLUMNS, where=where)
    rows = connection.execute(query, [*params, limit, offset]).fetchall()
    if rows:
        return [_product(row) for row in rows], int(rows[0][7])
    if offset == 0:
        return [], 0
    # A page past the end: the window count has no row to ride on.
    count = connection.execute(
        sql.SQL("SELECT count(*) FROM public.product{where}").format(where=where),
        params,
    ).fetchone()
    return [], int(count[0])


def create_product(connection: psycopg.Connection, body: dict[str, Any]) -> dict:
    row = connection.execute(
        sql.SQL(
            "INSERT INTO public.product "
            "(product_id, sku, name, category_id, list_price) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING {}"
        ).format(COLUMNS),
        (
            body["product_id"],
            body["sku"],
            body["name"],
            body["category_id"],
            Decimal(body["list_price"]),
        ),
    ).fetchone()
    return _product(row)


def update_product(
    connection: psycopg.Connection, product_id: int, body: dict[str, Any]
) -> dict | None:
    assignments = []
    params: list[Any] = []
    for column in EDITABLE:
        if column in body:
            assignments.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
            value = body[column]
            params.append(Decimal(value) if column == "list_price" else value)
    query = sql.SQL(
        "UPDATE public.product SET {assignments} "
        "WHERE product_id = %s RETURNING {columns}"
    ).format(assignments=sql.SQL(", ").join(assignments), columns=COLUMNS)
    row = connection.execute(query, [*params, product_id]).fetchone()
    return None if row is None else _product(row)
