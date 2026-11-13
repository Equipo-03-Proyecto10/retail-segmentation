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


# ---- reversals (ADR-0062) ---------------------------------------------------


class ReversalRefusal(Exception):
    """A reversal the rules refuse, as the ApiError arguments the route raises."""

    def __init__(
        self, status: int, code: str, message: str, field: str, detail: str
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.detail = detail


class Replayed(Exception):
    """The Idempotency-Key was already recorded; nothing was written."""

    def __init__(self, reversal_id: int, digest: bytes) -> None:
        super().__init__(str(reversal_id))
        self.reversal_id = reversal_id
        self.digest = digest


_REVERSAL_COLUMNS = (
    "r.reversal_id, r.transaction_id, r.kind, r.reason, t.customer_id, t.store_id,"
    " r.recorded_at, r.recorded_by"
)


def find_reversal_by_key(
    connection: psycopg.Connection, key: str
) -> tuple[int, bytes] | None:
    row = connection.execute(
        "SELECT reversal_id, request_sha256 FROM sales.sale_reversal"
        " WHERE idempotency_key = %s",
        (key,),
    ).fetchone()
    return None if row is None else (row[0], bytes(row[1]))


def _reversals(
    connection: psycopg.Connection, rows: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    """Reversals as the contract shows them: each line's remaining units and the
    sale's net total are those *after that reversal*, so a replay or a later
    read returns what the first answer said, whatever was reversed since."""

    if not rows:
        return []
    ids = [row[0] for row in rows]
    lines: dict[int, list[dict[str, Any]]] = {}
    for line in connection.execute(
        "SELECT x.reversal_id, x.product_id, x.quantity, x.reason, x.resellable,"
        " l.unit_price, l.quantity - ("
        "   SELECT coalesce(sum(y.quantity), 0) FROM sales.sale_reversal_line AS y"
        "   WHERE y.transaction_id = x.transaction_id AND y.product_id = x.product_id"
        "     AND y.reversal_id <= x.reversal_id) AS remaining"
        " FROM sales.sale_reversal_line AS x"
        " JOIN public.transaction_line AS l"
        "   ON l.transaction_id = x.transaction_id AND l.product_id = x.product_id"
        " WHERE x.reversal_id = ANY(%s) ORDER BY x.reversal_id, x.product_id",
        (ids,),
    ):
        lines.setdefault(line[0], []).append(
            {
                "product_id": line[1],
                "quantity": line[2],
                "remaining_quantity": line[6],
                "unit_price": _money(line[5]),
                "reason": line[3],
                "resellable": line[4],
            }
        )
    net = dict(
        connection.execute(
            "SELECT r.reversal_id, coalesce(sum((l.quantity - coalesce(rv.returned, 0))"
            " * l.unit_price), 0)"
            " FROM sales.sale_reversal AS r"
            " JOIN public.transaction_line AS l ON l.transaction_id = r.transaction_id"
            " LEFT JOIN LATERAL ("
            "   SELECT sum(y.quantity) AS returned FROM sales.sale_reversal_line AS y"
            "   WHERE y.transaction_id = l.transaction_id"
            "     AND y.product_id = l.product_id AND y.reversal_id <= r.reversal_id"
            " ) AS rv ON true"
            " WHERE r.reversal_id = ANY(%s) GROUP BY r.reversal_id",
            (ids,),
        ).fetchall()
    )
    result = []
    for row in rows:
        own = lines.get(row[0], [])
        amount = sum(
            (Decimal(line["unit_price"]) * line["quantity"] for line in own),
            Decimal(0),
        )
        result.append(
            {
                "reversal_id": row[0],
                "sale_id": row[1],
                "kind": row[2],
                "reason": row[3],
                "customer_id": str(row[4]),
                "store_id": row[5],
                "recorded_at": row[6],
                "recorded_by": str(row[7]),
                "amount": _money(amount),
                "net_total": _money(net.get(row[0], Decimal(0))),
                "lines": own,
            }
        )
    return result


def get_reversal(
    connection: psycopg.Connection, reversal_id: int
) -> dict[str, Any] | None:
    rows = connection.execute(
        sql.SQL(
            "SELECT {} FROM sales.sale_reversal AS r"
            " JOIN public.transaction AS t ON t.transaction_id = r.transaction_id"
            " WHERE r.reversal_id = %s"
        ).format(sql.SQL(_REVERSAL_COLUMNS)),
        (reversal_id,),
    ).fetchall()
    found = _reversals(connection, rows)
    return found[0] if found else None


def list_reversals(
    connection: psycopg.Connection,
    *,
    sale_id: int | None,
    customer_id: str | None,
    store_id: int | None,
    kind: str | None,
    recorded_from: datetime | None,
    recorded_to: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    params = {
        "sale_id": sale_id,
        "customer_id": customer_id,
        "store_id": store_id,
        "kind": kind,
        "recorded_from": recorded_from,
        "recorded_to": recorded_to,
        "limit": limit,
        "offset": offset,
    }
    where = sql.SQL(
        " WHERE (%(sale_id)s::bigint IS NULL OR r.transaction_id = %(sale_id)s)"
        " AND (%(customer_id)s::uuid IS NULL OR t.customer_id = %(customer_id)s)"
        " AND (%(store_id)s::smallint IS NULL OR t.store_id = %(store_id)s)"
        " AND (%(kind)s::text IS NULL OR r.kind = %(kind)s)"
        " AND (%(recorded_from)s::timestamptz IS NULL"
        " OR r.recorded_at >= %(recorded_from)s)"
        " AND (%(recorded_to)s::timestamptz IS NULL"
        " OR r.recorded_at < %(recorded_to)s)"
    )
    join = sql.SQL(
        " FROM sales.sale_reversal AS r"
        " JOIN public.transaction AS t ON t.transaction_id = r.transaction_id"
    )
    total = connection.execute(
        sql.SQL("SELECT count(*){}{}").format(join, where), params
    ).fetchone()[0]
    rows = connection.execute(
        sql.SQL(
            "SELECT " + _REVERSAL_COLUMNS + "{}{}"
            " ORDER BY r.recorded_at DESC, r.reversal_id DESC"
            " LIMIT %(limit)s OFFSET %(offset)s"
        ).format(join, where),
        params,
    ).fetchall()
    return _reversals(connection, rows), total


def record_reversal(
    connection: psycopg.Connection,
    *,
    request: dict[str, Any],
    key: str,
    digest: bytes,
    recorded_by: str,
    window_open: Any,
) -> int:
    """Record one reversal in the caller's transaction, or refuse it whole.

    The order matters. The sold lines are locked first, in ascending product
    order, so two reversals of one sale queue and the units they see remaining
    are the units that remain. The key is looked up again after the lock, so a
    concurrent duplicate that waited for the first reversal replays it instead
    of being told the sale was already reversed. Then the header, the lines, the
    running totals and the stock restored, in ascending product order.
    """

    sale = connection.execute(
        "SELECT occurred_at, store_id FROM public.transaction"
        " WHERE transaction_id = %s",
        (request["sale_id"],),
    ).fetchone()
    if sale is None:
        raise ReversalRefusal(
            422, "unknown_sale", "The sale does not exist.", "sale_id", "unknown"
        )
    occurred_at, store_id = sale
    sold = {
        row[0]: (row[1], row[2], row[3])
        for row in connection.execute(
            "SELECT product_id, quantity, returned_quantity, unit_price"
            " FROM public.transaction_line WHERE transaction_id = %s"
            " ORDER BY product_id FOR UPDATE",
            (request["sale_id"],),
        )
    }
    earlier = find_reversal_by_key(connection, key)
    if earlier is not None:
        raise Replayed(*earlier)
    if not window_open(request["kind"], occurred_at):
        raise ReversalRefusal(
            409,
            "reversal_window_closed",
            "The time allowed for this kind of reversal has passed.",
            "kind",
            "window_closed",
        )
    if request["kind"] == "CANCELLATION":
        if connection.execute(
            "SELECT EXISTS (SELECT 1 FROM sales.sale_reversal"
            " WHERE transaction_id = %s)",
            (request["sale_id"],),
        ).fetchone()[0]:
            raise ReversalRefusal(
                409,
                "sale_already_reversed",
                "The sale already has a reversal, so it can no longer be"
                " cancelled as a whole.",
                "sale_id",
                "already_reversed",
            )
        lines = [
            {
                "product_id": product_id,
                "quantity": quantity,
                "reason": None,
                "resellable": True,
            }
            for product_id, (quantity, _returned, _price) in sold.items()
        ]
    else:
        lines = request["lines"]
        for index, line in enumerate(lines):
            known = sold.get(line["product_id"])
            if known is None:
                raise ReversalRefusal(
                    422,
                    "unknown_sale_line",
                    "A returned product is not on the sale.",
                    f"lines[{index}].product_id",
                    "not_on_sale",
                )
            if line["quantity"] > known[0] - known[1]:
                raise ReversalRefusal(
                    409,
                    "quantity_exceeds_remaining",
                    "A line returns more units than the sale still holds.",
                    f"lines[{index}].quantity",
                    "exceeds_remaining",
                )
    reversal_id = connection.execute(
        "INSERT INTO sales.sale_reversal"
        " (transaction_id, kind, reason, idempotency_key, request_sha256,"
        " recorded_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING reversal_id",
        (
            request["sale_id"],
            request["kind"],
            request["reason"],
            key,
            digest,
            recorded_by,
        ),
    ).fetchone()[0]
    ordered = sorted(enumerate(lines), key=lambda indexed: indexed[1]["product_id"])
    for _index, line in ordered:
        connection.execute(
            "INSERT INTO sales.sale_reversal_line"
            " (reversal_id, transaction_id, product_id, quantity, reason,"
            " resellable) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                reversal_id,
                request["sale_id"],
                line["product_id"],
                line["quantity"],
                line["reason"],
                line["resellable"],
            ),
        )
        connection.execute(
            "UPDATE public.transaction_line"
            " SET returned_quantity = returned_quantity + %s"
            " WHERE transaction_id = %s AND product_id = %s",
            (line["quantity"], request["sale_id"], line["product_id"]),
        )
    for index, line in ordered:
        if not line["resellable"]:
            continue
        try:
            connection.execute(
                "SELECT api_stock.restock_v1(%s::smallint, %s, %s, %s)",
                (store_id, line["product_id"], line["quantity"], reversal_id),
            ).fetchone()
        except psycopg.errors.CheckViolation as error:
            if (error.diag.constraint_name or "") != "no_stock_record":
                raise
            raise ReversalRefusal(
                422,
                "no_stock_record",
                "The store has no stock record for a product.",
                f"lines[{index}].product_id",
                "no_stock_record",
            ) from error
    return reversal_id
