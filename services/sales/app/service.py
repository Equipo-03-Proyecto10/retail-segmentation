"""HTTP operations for the atomic sales rule (ADR-0039)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import psycopg
from flask import Flask, request
from mosaiq_kit import (
    ApiError,
    Collection,
    Kit,
    OutboxRelay,
    Page,
    Representation,
    configure_logging,
)

import redis

from . import store

LOGGER = logging.getLogger(__name__)
STREAM = "stream:sales.v1.recorded"
CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
_INTEGER = re.compile(r"-?[0-9]+")
# The columns a sale lands in: transaction_line.quantity INT,
# transaction_line.unit_price NUMERIC(10,2), transaction.total NUMERIC(12,2).
# Past them PostgreSQL would answer 22003, so they are refused first.
MAX_QUANTITY = 2_147_483_647
MAX_PRICE = Decimal("99999999.99")
MAX_TOTAL = Decimal("9999999999.99")


def _error(status: int, code: str, message: str, field: str, detail: str) -> ApiError:
    return ApiError(status, code, message, [{"field": field, "code": detail}])


def _line_error(
    status: int, code: str, message: str, index: int, member: str, detail: str
) -> ApiError:
    return _error(status, code, message, f"lines[{index}].{member}", detail)


def _parse_timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise _error(
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
        raise _error(
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


def _validate_local(body: dict[str, Any]) -> tuple[datetime, list[dict[str, Any]]]:
    lines = body.get("lines") or []
    if not lines:
        raise _error(
            422, "no_lines", "A sale needs at least one line.", "lines", "empty"
        )
    seen: set[int] = set()
    normalized = []
    for index, line in enumerate(lines):
        product_id = line["product_id"]
        if product_id in seen:
            raise _line_error(
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
            raise _line_error(
                422,
                "invalid_quantity",
                "A quantity is not a positive whole number.",
                index,
                "quantity",
                "not_positive",
            )
        if quantity > MAX_QUANTITY:
            raise _line_error(
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
            raise _line_error(
                422,
                "invalid_price",
                "A unit price is not a positive amount with two decimals.",
                index,
                "unit_price",
                "invalid",
            )
        if price > MAX_PRICE:
            raise _line_error(
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
        raise _error(
            422,
            "invalid_body",
            "The sale's total is larger than a sale can hold.",
            "lines",
            "out_of_range",
        )
    occurred_at = _parse_timestamp(body["occurred_at"], "occurred_at")
    if occurred_at > datetime.now(UTC):
        raise _error(
            422,
            "future_dated",
            "The sale is dated in the future.",
            "occurred_at",
            "future",
        )
    return occurred_at, normalized


def _canonical(
    body: dict[str, Any], occurred_at: datetime, lines: list[dict[str, Any]], sub: str
) -> bytes:
    value = {
        "sub": sub,
        "customer_id": str(body["customer_id"]).lower(),
        "store_id": body["store_id"],
        "channel_id": body["channel_id"],
        "occurred_at": occurred_at.astimezone(UTC).isoformat(
            timespec="microseconds" if occurred_at.microsecond else "seconds"
        ),
        "lines": [
            {
                "product_id": line["product_id"],
                "quantity": line["quantity"],
                "unit_price": format(line["unit_price"], ".2f"),
            }
            for line in sorted(lines, key=lambda item: item["product_id"])
        ],
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).digest()


def _idempotency_key() -> str:
    raw = request.headers.get("Idempotency-Key")
    try:
        key = UUID(raw) if raw is not None else None
    except (ValueError, AttributeError):
        key = None
    if key is None:
        raise _error(
            400,
            "idempotency_key_required",
            "The Idempotency-Key header is missing or is not a UUID.",
            "Idempotency-Key",
            "required",
        )
    return str(key)


def _replay(kit: Kit, source_id: str, digest: bytes) -> Representation | None:
    with kit.db.connection() as connection:
        found = store.find_capture(connection, source_id)
    if found is None:
        return None
    sale, original = found
    if original != digest:
        raise _error(
            409,
            "idempotency_key_reused",
            "The Idempotency-Key was already used for a different sale.",
            "Idempotency-Key",
            "reused",
        )
    return Representation("Sale", sale, headers={"Idempotent-Replayed": "true"})


def _validate_references(
    connection: psycopg.Connection,
    body: dict[str, Any],
    occurred_at: datetime,
    lines: list[dict[str, Any]],
) -> None:
    registered_on = store.customer_registration(connection, body["customer_id"])
    if registered_on is None:
        raise _error(
            422,
            "unknown_customer",
            "The customer does not exist.",
            "customer_id",
            "unknown",
        )
    if occurred_at.date() < registered_on:
        raise _error(
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
            raise _line_error(
                422,
                "unknown_product",
                "A product does not exist.",
                index,
                "product_id",
                "unknown",
            )
        if not state:
            raise _line_error(
                422,
                "inactive_product",
                "A product is not active.",
                index,
                "product_id",
                "inactive",
            )


def _constraint(error: psycopg.Error) -> str:
    return error.diag.constraint_name or ""


def _database_refusal(
    error: psycopg.Error, lines: list[dict[str, Any]], line_index: int | None = None
) -> ApiError:
    constraint = _constraint(error)
    if constraint in {"no_stock_record", "stock_unavailable"}:
        index = line_index if line_index is not None else 0
        if constraint == "no_stock_record":
            return _line_error(
                422,
                "no_stock_record",
                "The store has no stock record for a product.",
                index,
                "product_id",
                "no_stock_record",
            )
        return _line_error(
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
    if constraint in foreign:
        code, message, field = foreign[constraint]
        return _error(422, code, message, field, "unknown")
    raise error


def _query_integer(name: str) -> int | None:
    raw = request.args.get(name)
    if raw is None:
        return None
    if _INTEGER.fullmatch(raw) is None:
        raise _error(
            422,
            "invalid_query",
            "A filter is malformed or the range is reversed.",
            name,
            "invalid_type",
        )
    value = int(raw)
    maximum = 2_147_483_647 if name == "customer_id" else 32767
    if value < 1 or value > maximum:
        raise _error(
            422,
            "invalid_query",
            "A filter is malformed or the range is reversed.",
            name,
            "out_of_range",
        )
    return value


def _query_uuid() -> str | None:
    raw = request.args.get("customer_id")
    if raw is None:
        return None
    try:
        return str(UUID(raw))
    except ValueError:
        raise _error(
            422,
            "invalid_query",
            "A filter is malformed or the range is reversed.",
            "customer_id",
            "invalid_format",
        ) from None


def _relay_client(url: str) -> redis.Redis:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    netloc = hostname + ((":" + str(parsed.port)) if parsed.port else "")
    return redis.Redis.from_url(
        urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, "")),
        username="svc_sales",
        password=os.environ.get("REDIS_SALES_PASSWORD"),
        socket_connect_timeout=1,
        socket_timeout=3,
        decode_responses=True,
    )


def create_app(*, start_relay: bool | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    configure_logging()
    holder: dict[str, Kit] = {}

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            row = connection.execute(
                "SELECT to_regprocedure(%s) IS NOT NULL"
                " AND to_regclass(%s) IS NOT NULL",
                (
                    "api_stock." "consume_v1(smallint,integer,integer)",
                    "sales.sale_capture",
                ),
            ).fetchone()
        return bool(row and row[0])

    kit = Kit(
        app,
        service="sales",
        suffix="sales",
        contracts_dir=Path(os.environ.get("MOSAIQ_CONTRACTS_DIR", CONTRACTS)),
        ready_checks=[schema_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit

    @kit.operation("recordSale")
    def record_sale() -> Representation:
        key = _idempotency_key()
        principal = kit.principal
        assert principal is not None
        sub = str(principal["sub"])
        body = kit.body
        occurred_at, lines = _validate_local(body)
        digest = _canonical(body, occurred_at, lines, sub)
        source_id = "svc-sales:" + key
        replay = _replay(kit, source_id, digest)
        if replay is not None:
            return replay
        total = sum(
            (line["unit_price"] * line["quantity"] for line in lines), Decimal(0)
        )
        for attempt in range(3):
            try:
                with kit.db.transaction() as connection:
                    _validate_references(connection, body, occurred_at, lines)
                    sale_id = store.insert_sale(
                        connection,
                        source_id=source_id,
                        customer_id=body["customer_id"],
                        store_id=body["store_id"],
                        channel_id=body["channel_id"],
                        occurred_at=occurred_at,
                        total=total,
                        lines=lines,
                        digest=digest,
                        recorded_by=sub,
                    )
                with kit.db.connection() as connection:
                    sale = store.get_sale(connection, sale_id)
                assert sale is not None
                return Representation(
                    "Sale",
                    sale,
                    status=201,
                    headers={"Location": f"/v1/sales/{sale_id}"},
                )
            except (
                psycopg.errors.DeadlockDetected,
                psycopg.errors.SerializationFailure,
            ):
                if attempt == 2:
                    raise
            except psycopg.errors.UniqueViolation as error:
                if _constraint(error) != "transaction_source_transaction_id_key":
                    raise
                replay = _replay(kit, source_id, digest)
                if replay is not None:
                    return replay
                raise
            except store.StockRefusal as refusal:
                raise _database_refusal(refusal.error, lines, refusal.index) from None
            except psycopg.errors.IntegrityError as error:
                raise _database_refusal(error, lines) from None
        raise RuntimeError("transaction retry exhausted")

    @kit.operation("listSales")
    def list_sales() -> Page:
        customer_id = _query_uuid()
        store_id = _query_integer("store_id")
        occurred_from = (
            _parse_timestamp(request.args["from"], "from")
            if "from" in request.args
            else None
        )
        occurred_to = (
            _parse_timestamp(request.args["to"], "to") if "to" in request.args else None
        )
        if occurred_from and occurred_to and occurred_from > occurred_to:
            raise _error(
                422,
                "invalid_query",
                "A filter is malformed or the range is reversed.",
                "from",
                "after_to",
            )
        with kit.db.connection() as connection:
            sales, total = store.list_sales(
                connection,
                customer_id=customer_id,
                store_id=store_id,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
                limit=kit.size,
                offset=(kit.page - 1) * kit.size,
            )
        return Page(
            "Sales",
            "Sale",
            "sales",
            sales,
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("getSale")
    def get_sale(sale_id: int) -> Representation:
        with kit.db.connection() as connection:
            sale = store.get_sale(connection, sale_id)
        if sale is None:
            raise ApiError(404, "sale_not_found", f"Sale {sale_id} was not found.")
        return Representation("Sale", sale)

    @kit.operation("listChannels")
    def list_channels() -> Collection:
        with kit.db.connection() as connection:
            items = store.channels(connection)
        return Collection("Channels", "Channel", "channels", items)

    enabled = (
        os.environ.get("OUTBOX_RELAY_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"}
        if start_relay is None
        else start_relay
    )
    if enabled and kit.db.dsn:
        relay = OutboxRelay(
            kit.db.dsn,
            "sales",
            [STREAM],
            _relay_client(
                os.environ.get("REDIS_STATE_URL", "redis://redis-state:6379/0")
            ),
        )
        relay.start()
        app.extensions["sales_relay"] = relay
    return app
