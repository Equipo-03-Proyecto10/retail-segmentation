"""HTTP operations for the atomic sales rule (ADR-0039)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
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

from . import audit, imports, reversal, store
from .salerules import (
    constraint as _constraint,
)
from .salerules import (
    database_refusal as _database_refusal,
)
from .salerules import (
    error as _error,
)
from .salerules import (
    parse_timestamp as _parse_timestamp,
)
from .salerules import (
    validate_sale_references,
    validate_sale_rules,
)

LOGGER = logging.getLogger(__name__)
STREAM = "stream:sales.v1.recorded"
REVERSED_STREAM = "stream:sales.v1.reversed"
CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
_INTEGER = re.compile(r"-?[0-9]+")


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


def _query_integer(name: str, maximum: int = 32767) -> int | None:
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


def _chunk_error_names(kit: Kit) -> None:
    """Name a refused chunk's fields as the contract does.

    The kit reports a schema error at `rows.0.cells.4` and gives `maxItems` the
    code `invalid`; the sales contract names them `rows[0].cells[4]` and
    `too_many` (ADR-0070 section 1). Only the chunk's body is renamed, so every
    other operation keeps the kit's own wording.
    """
    original = kit.contract.validate

    def validate(value: Any, schema: dict[str, Any]) -> None:
        try:
            original(value, schema)
        except ApiError as refusal:
            if request.endpoint != "mosaiq_operation_sendSaleImportChunk":
                raise
            rows = value.get("rows") if isinstance(value, dict) else None
            for detail in refusal.details:
                field = re.sub(r"\.(\d+)", r"[\1]", detail["field"])
                if detail["code"] == "invalid" and isinstance(rows, list):
                    if field == "rows" and len(rows) > 150:
                        detail["code"] = "too_many"
                    elif field == "rows" and not rows:
                        detail["code"] = "empty"
                    elif field.endswith(".cells") or field.endswith("].cells"):
                        detail["code"] = "too_many"
                detail["field"] = field
            raise

    kit.contract.validate = validate


def create_app(*, start_relay: bool | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    configure_logging()
    holder: dict[str, Kit] = {}

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            row = connection.execute(
                "SELECT to_regprocedure(%s) IS NOT NULL"
                " AND to_regprocedure(%s) IS NOT NULL"
                " AND to_regclass(%s) IS NOT NULL"
                " AND to_regclass(%s) IS NOT NULL"
                " AND to_regclass(%s) IS NOT NULL",
                (
                    "api_stock." "consume_v1(smallint,integer,integer)",
                    "api_stock." "restock_v1(smallint,integer,integer,bigint)",
                    "sales.sale_capture",
                    "sales.sale_reversal_line",
                    "sales.import_row_outcome",
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
    _chunk_error_names(kit)

    @kit.operation("recordSale")
    def record_sale() -> Representation:
        key = _idempotency_key()
        principal = kit.principal
        assert principal is not None
        sub = str(principal["sub"])
        body = kit.body
        occurred_at, lines = validate_sale_rules(body)
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
                    validate_sale_references(connection, body, occurred_at, lines)
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
        # A read naming one customer is a logged read: its HISTORY_READ is
        # recorded in the read's own transaction, before any row is returned.
        with kit.db.transaction() as connection:
            if customer_id is not None:
                audit.record_history_read(connection, kit.principal, customer_id)
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

    def _reversal_replay(
        connection: psycopg.Connection, reversal_id: int, earlier: bytes, digest: bytes
    ) -> Representation:
        if earlier != digest:
            raise _error(
                409,
                "idempotency_key_reused",
                "The Idempotency-Key was already used for a different reversal.",
                "Idempotency-Key",
                "reused",
            )
        found = store.get_reversal(connection, reversal_id)
        assert found is not None
        return Representation(
            "SaleReversal", found, headers={"Idempotent-Replayed": "true"}
        )

    @kit.operation("recordSaleReversal")
    def record_sale_reversal() -> Representation:
        principal = kit.principal
        assert principal is not None
        sub = str(principal["sub"])
        body = kit.body
        # A seller may cancel but never return (ADR-0062); that is decided by
        # the kind, which x-mosaiq-roles cannot say, so it is refused here with
        # the kit's own 403 before anything else is looked at.
        if body["kind"] == "RETURN" and principal.get("role") == reversal.FLOOR_ROLE:
            raise reversal.forbidden_role()
        key = _idempotency_key()
        request_body = reversal.validate(body)
        digest = reversal.canonical(request_body, sub)
        for attempt in range(3):
            try:
                with kit.db.transaction() as connection:
                    earlier = store.find_reversal_by_key(connection, key)
                    if earlier is not None:
                        return _reversal_replay(connection, *earlier, digest)
                    reversal_id = store.record_reversal(
                        connection,
                        request=request_body,
                        key=key,
                        digest=digest,
                        recorded_by=sub,
                        window_open=reversal.window_open,
                    )
                    created = store.get_reversal(connection, reversal_id)
                assert created is not None
                return Representation(
                    "SaleReversal",
                    created,
                    status=201,
                    headers={"Location": f"/v1/sale-reversals/{reversal_id}"},
                )
            except store.Replayed as replayed:
                with kit.db.connection() as connection:
                    return _reversal_replay(
                        connection, replayed.reversal_id, replayed.digest, digest
                    )
            except store.ReversalRefusal as refusal:
                raise _error(
                    refusal.status,
                    refusal.code,
                    refusal.message,
                    refusal.field,
                    refusal.detail,
                ) from None
            except (
                psycopg.errors.DeadlockDetected,
                psycopg.errors.SerializationFailure,
            ):
                if attempt == 2:
                    raise
            except psycopg.errors.UniqueViolation as error:
                if _constraint(error) != "sale_reversal_idempotency_key_key":
                    raise
                with kit.db.connection() as connection:
                    earlier = store.find_reversal_by_key(connection, key)
                    if earlier is not None:
                        return _reversal_replay(connection, *earlier, digest)
                raise
            except psycopg.errors.CheckViolation as error:
                # The database's own guard, for a race the locks should have
                # made impossible: the same answer the rule gives.
                if _constraint(error) == "sale_reversal_line_within_sale":
                    raise _error(
                        409,
                        "quantity_exceeds_remaining",
                        "A line returns more units than the sale still holds.",
                        "lines",
                        "exceeds_remaining",
                    ) from None
                raise
        raise RuntimeError("transaction retry exhausted")

    @kit.operation("listSaleReversals")
    def list_sale_reversals() -> Page:
        sale_id = _query_integer("sale_id", reversal.MAX_ID)
        customer_id = _query_uuid()
        store_id = _query_integer("store_id")
        kind = request.args.get("kind")
        if kind is not None and kind not in {"CANCELLATION", "RETURN"}:
            raise _error(
                422,
                "invalid_query",
                "A filter is malformed or the range is reversed.",
                "kind",
                "not_allowed",
            )
        recorded_from = (
            _parse_timestamp(request.args["from"], "from")
            if "from" in request.args
            else None
        )
        recorded_to = (
            _parse_timestamp(request.args["to"], "to") if "to" in request.args else None
        )
        if recorded_from and recorded_to and recorded_from > recorded_to:
            raise _error(
                422,
                "invalid_query",
                "A filter is malformed or the range is reversed.",
                "from",
                "after_to",
            )
        with kit.db.connection() as connection:
            found, total = store.list_reversals(
                connection,
                sale_id=sale_id,
                customer_id=customer_id,
                store_id=store_id,
                kind=kind,
                recorded_from=recorded_from,
                recorded_to=recorded_to,
                limit=kit.size,
                offset=(kit.page - 1) * kit.size,
            )
        return Page(
            "SaleReversals",
            "SaleReversal",
            "sale_reversals",
            found,
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("getSaleReversal")
    def get_sale_reversal(reversal_id: int) -> Representation:
        with kit.db.connection() as connection:
            found = store.get_reversal(connection, reversal_id)
        if found is None:
            raise ApiError(
                404,
                "sale_reversal_not_found",
                f"Sale reversal {reversal_id} was not found.",
            )
        return Representation("SaleReversal", found)

    def _import_or_404(connection: psycopg.Connection, import_id: int) -> dict:
        found = imports.get_import(connection, import_id)
        if found is None:
            raise imports.not_found(import_id)
        return found

    @kit.operation("createSaleImport")
    def create_sale_import() -> Representation:
        key = _idempotency_key()
        principal = kit.principal
        assert principal is not None
        sub = str(principal["sub"])
        body = kit.body
        imports.check_create(body)
        digest = imports.canonical_create(body, sub)
        for attempt in range(3):
            try:
                with kit.db.transaction() as connection:
                    imports.expire_stale(connection)
                    earlier = imports.find_by_key(connection, key)
                    if earlier is not None:
                        if earlier[1] != digest:
                            raise _error(
                                409,
                                "idempotency_key_reused",
                                "The Idempotency-Key was already used for a"
                                " different import.",
                                "Idempotency-Key",
                                "reused",
                            )
                        return Representation(
                            "SaleImport",
                            imports.get_import(connection, earlier[0]),
                            headers={"Idempotent-Replayed": "true"},
                        )
                    open_import = imports.newest_open(connection)
                    if open_import is not None:
                        raise imports.in_progress(*open_import)
                    import_id = imports.create_import(
                        connection, key=key, digest=digest, body=body, created_by=sub
                    )
                    created = imports.get_import(connection, import_id)
                return Representation(
                    "SaleImport",
                    created,
                    status=201,
                    headers={"Location": f"/v1/sale-imports/{import_id}"},
                )
            except psycopg.errors.UniqueViolation as exc:
                name = _constraint(exc)
                if name == "import_one_open":
                    with kit.db.connection() as connection:
                        open_import = imports.newest_open(connection)
                    if open_import is None:
                        continue
                    raise imports.in_progress(*open_import) from None
                if name != "import_idempotency_key_key" or attempt == 2:
                    raise
            except (
                psycopg.errors.DeadlockDetected,
                psycopg.errors.SerializationFailure,
            ):
                if attempt == 2:
                    raise
        raise RuntimeError("transaction retry exhausted")

    @kit.operation("listSaleImports")
    def list_sale_imports() -> Page:
        with kit.db.transaction() as connection:
            imports.expire_stale(connection)
            found, total = imports.list_imports(
                connection, kit.size, (kit.page - 1) * kit.size
            )
        return Page(
            "SaleImports",
            "SaleImport",
            "sale_imports",
            found,
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("getSaleImport")
    def get_sale_import(import_id: int) -> Representation:
        with kit.db.transaction() as connection:
            imports.expire_stale(connection)
            found = _import_or_404(connection, import_id)
        return Representation("SaleImport", found)

    @kit.operation("sendSaleImportChunk")
    def send_sale_import_chunk(import_id: int, sequence: int) -> Representation:
        rows = kit.body["rows"]
        imports.check_lines(rows, 0)
        digest = imports.canonical_chunk(rows)
        with kit.db.transaction() as connection:
            imports.expire_stale(connection)
            imp = imports.get_import(connection, import_id, lock=True)
            if imp is None:
                raise imports.not_found(import_id)
            earlier = imports.find_chunk(connection, import_id, sequence)
            if earlier is not None:
                if earlier != digest:
                    raise ApiError(
                        409,
                        "chunk_conflict",
                        f"Chunk {sequence} of import {import_id} was already"
                        " staged with other rows.",
                        [{"field": "sequence", "code": "already_staged"}],
                    )
                return Representation(
                    "SaleImport", imp, headers={"Idempotent-Replayed": "true"}
                )
            if imp["status"] != "RECEIVING":
                raise ApiError(
                    409,
                    "import_not_receiving",
                    f"Import {import_id} is {imp['status']} and takes no more chunks.",
                    [{"field": "status", "code": "not_receiving"}],
                )
            if sequence >= imp["chunk_count"]:
                raise _error(
                    422,
                    "invalid_body",
                    f"Import {import_id} announced {imp['chunk_count']} chunks,"
                    f" numbered 0 to {imp['chunk_count'] - 1}.",
                    "sequence",
                    "out_of_range",
                )
            if sequence > imp["next_sequence"]:
                raise ApiError(
                    409,
                    "chunk_out_of_order",
                    f"Import {import_id} expects chunk {imp['next_sequence']} next,"
                    f" and chunk {sequence} was sent. Read next_sequence from"
                    f" GET /v1/sale-imports/{import_id} and continue there.",
                    [{"field": "next_sequence", "code": "out_of_order"}],
                )
            imports.check_lines(
                rows, imports.previous_last_line(connection, import_id, sequence)
            )
            staged = imp["received"] + len(rows)
            if staged > imp["total_rows"] or (
                sequence == imp["chunk_count"] - 1 and staged != imp["total_rows"]
            ):
                raise ApiError(
                    422,
                    "row_count_mismatch",
                    f"The {sequence + 1} chunks of import {import_id} hold"
                    f" {staged} rows, and the import announced"
                    f" {imp['total_rows']}. Cancel it and open a new import with"
                    " the numbers of the file.",
                    [{"field": "total_rows", "code": "mismatch"}],
                )
            imports.stage_chunk(
                connection, imp=imp, sequence=sequence, digest=digest, rows=rows
            )
            staged_import = imports.get_import(connection, import_id)
        return Representation("SaleImport", staged_import, status=202)

    @kit.operation("listSaleImportRejections")
    def list_sale_import_rejections(import_id: int) -> Page:
        with kit.db.connection() as connection:
            _import_or_404(connection, import_id)
            found, total = imports.list_rejections(
                connection, import_id, kit.size, (kit.page - 1) * kit.size
            )
        return Page(
            "SaleImportRejections",
            "SaleImportRejection",
            "sale_import_rejections",
            found,
            page=kit.page,
            size=kit.size,
            total=total,
        )

    @kit.operation("cancelSaleImport")
    def cancel_sale_import(import_id: int) -> Representation:
        with kit.db.transaction() as connection:
            imports.expire_stale(connection)
            imp = imports.get_import(connection, import_id, lock=True)
            if imp is None:
                raise imports.not_found(import_id)
            if imp["status"] in {"COMPLETED", "EXPIRED"}:
                raise ApiError(
                    409,
                    "import_finished",
                    f"Import {import_id} is {imp['status']} and cannot be"
                    " cancelled.",
                    [{"field": "status", "code": "finished"}],
                )
            if imp["status"] != "CANCELLED":
                imports.cancel_import(connection, import_id)
            cancelled = imports.get_import(connection, import_id)
        return Representation("SaleImport", cancelled)

    @kit.operation("listChannels")
    def list_channels() -> Collection:
        with kit.db.connection() as connection:
            items = store.channels(connection)
        return Collection("Channels", "Channel", "channels", items)

    audit.log_refusals(app, kit, "listSales")

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
            [STREAM, REVERSED_STREAM],
            _relay_client(
                os.environ.get("REDIS_STATE_URL", "redis://redis-state:6379/0")
            ),
        )
        relay.start()
        app.extensions["sales_relay"] = relay
    return app
