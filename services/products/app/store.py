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


# Commercial details (F21-10). Static statements with bound parameters only.

_DETAILS = """
SELECT p.product_id, p.sku, p.name, p.category_id, p.list_price, p.image_path,
       p.is_active,
       coalesce(s.commercial_status,
                CASE WHEN p.is_active THEN 'active' ELSE 'discontinued' END),
       b.barcode,
       (SELECT coalesce(jsonb_agg(jsonb_build_object('name', a.name,
                                                     'value', a.value)
                                  ORDER BY a.name), '[]'::jsonb)
          FROM products.product_attribute AS a
         WHERE a.product_id = p.product_id)
  FROM public.product AS p
  LEFT JOIN products.product_status AS s ON s.product_id = p.product_id
  LEFT JOIN products.product_barcode AS b ON b.product_id = p.product_id
 WHERE p.product_id = %s
"""
_SET_STATUS = """
INSERT INTO products.product_status (product_id, commercial_status)
VALUES (%s, %s)
ON CONFLICT (product_id) DO UPDATE
   SET commercial_status = EXCLUDED.commercial_status
 WHERE products.product_status.commercial_status
       IS DISTINCT FROM EXCLUDED.commercial_status
"""
_SET_BARCODE = """
INSERT INTO products.product_barcode (product_id, barcode)
VALUES (%s, %s)
ON CONFLICT (product_id) DO UPDATE
   SET barcode = EXCLUDED.barcode
 WHERE products.product_barcode.barcode IS DISTINCT FROM EXCLUDED.barcode
"""
_DELETE_OTHER_ATTRIBUTES = """
DELETE FROM products.product_attribute
 WHERE product_id = %s AND name <> ALL(%s::text[])
"""
_UPSERT_ATTRIBUTES = """
INSERT INTO products.product_attribute (product_id, name, value)
SELECT %s::integer, t.name, t.value
  FROM unnest(%s::text[], %s::text[]) AS t(name, value)
ON CONFLICT (product_id, name) DO UPDATE
   SET value = EXCLUDED.value
 WHERE products.product_attribute.value IS DISTINCT FROM EXCLUDED.value
"""
_DELETE_OTHER_RELATIONS = """
DELETE FROM products.product_relation
 WHERE product_id = %s AND relation_kind = %s
   AND related_product_id <> ALL(%s::integer[])
"""
_INSERT_RELATIONS = """
INSERT INTO products.product_relation (product_id, related_product_id, relation_kind)
SELECT %s::integer, t.related_product_id, %s::text
  FROM unnest(%s::integer[]) AS t(related_product_id)
ON CONFLICT DO NOTHING
"""
_MISSING_PRODUCTS = """
SELECT t.product_id
  FROM unnest(%s::integer[]) AS t(product_id)
 WHERE NOT EXISTS (SELECT 1 FROM public.product AS p
                    WHERE p.product_id = t.product_id)
 ORDER BY t.product_id
"""
_LIST_RELATIONS = """
SELECT related_product_id, relation_kind, count(*) OVER ()
  FROM products.product_relation
 WHERE product_id = %s AND (%s::text IS NULL OR relation_kind = %s::text)
 ORDER BY relation_kind, related_product_id
 LIMIT %s OFFSET %s
"""
_COUNT_RELATIONS = """
SELECT count(*)
  FROM products.product_relation
 WHERE product_id = %s AND (%s::text IS NULL OR relation_kind = %s::text)
"""


def get_details(connection: psycopg.Connection, product_id: int) -> dict | None:
    """The product with its status, barcode and attributes, in one statement
    so they come from one snapshot. ``image_path`` stays raw: the caller builds
    the photo URL from it."""

    row = connection.execute(_DETAILS, (product_id,)).fetchone()
    if row is None:
        return None
    return {
        **_product(row[:7]),
        "commercial_status": row[7],
        "barcode": row[8],
        "attributes": row[9],
    }


def product_exists(connection: psycopg.Connection, product_id: int) -> bool:
    row = connection.execute(
        "SELECT 1 FROM public.product WHERE product_id = %s", (product_id,)
    ).fetchone()
    return row is not None


def lock_product(connection: psycopg.Connection, product_id: int) -> bool:
    """Take the product's row, so two writers of its details queue. False when
    there is no such product."""

    row = connection.execute(
        "SELECT 1 FROM public.product WHERE product_id = %s FOR NO KEY UPDATE",
        (product_id,),
    ).fetchone()
    return row is not None


def set_status(connection: psycopg.Connection, product_id: int, status: str) -> None:
    """Upsert the status. The schema's trigger sets ``is_active`` to match."""

    connection.execute(_SET_STATUS, (product_id, status))


def set_barcode(connection: psycopg.Connection, product_id: int, barcode: str) -> None:
    connection.execute(_SET_BARCODE, (product_id, barcode))


def replace_attributes(
    connection: psycopg.Connection, product_id: int, attributes: list[dict[str, str]]
) -> None:
    """Make the product's attributes exactly this set, writing only the rows
    that differ so the audit trail records real changes."""

    names = [attribute["name"] for attribute in attributes]
    values = [attribute["value"] for attribute in attributes]
    connection.execute(_DELETE_OTHER_ATTRIBUTES, (product_id, names))
    connection.execute(_UPSERT_ATTRIBUTES, (product_id, names, values))


def missing_products(
    connection: psycopg.Connection, product_ids: list[int]
) -> list[int]:
    return [row[0] for row in connection.execute(_MISSING_PRODUCTS, (product_ids,))]


def replace_relations(
    connection: psycopg.Connection,
    product_id: int,
    kind: str,
    related_product_ids: list[int],
) -> None:
    connection.execute(_DELETE_OTHER_RELATIONS, (product_id, kind, related_product_ids))
    connection.execute(_INSERT_RELATIONS, (product_id, kind, related_product_ids))


def list_relations(
    connection: psycopg.Connection,
    product_id: int,
    kind: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """One page of a product's relations, by kind and then related id, and the
    filtered total."""

    rows = connection.execute(
        _LIST_RELATIONS, (product_id, kind, kind, limit, offset)
    ).fetchall()
    items = [{"related_product_id": row[0], "kind": row[1]} for row in rows]
    if rows:
        return items, int(rows[0][2])
    if offset == 0:
        return [], 0
    # A page past the end: the window count has no row to ride on.
    count = connection.execute(_COUNT_RELATIONS, (product_id, kind, kind)).fetchone()
    return [], int(count[0])
