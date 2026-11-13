"""The worker of a sales import: a second process of sales-service's image.

    python -m app.import_worker

It connects as `svc_sales` like the API (ADR-0040) and applies the rows the API
staged, one row and one PostgreSQL transaction at a time (ADR-0070 section 3),
through the same rule as `POST /v1/sales`: `salerules.validate_sale_rules` and
`validate_sale_references`, the same stock contract (`store.consume_stock`) and
the same mapping of the database's refusals. What a file alone can get wrong is
`rows.py`. The staging table is its queue, since a queue that can be flushed
cannot be the record of what was imported, and the events of the sales it
writes reach the stream through the same outbox trigger as any other sale
(ADR-0044).

A row's outcome is written in the same transaction as the sale line it
describes, so a crash, a restart or a retry neither loses nor repeats a line:
after one, the worker skips every line that has an outcome and continues at the
first that has not.
"""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import time
from typing import Any

import psycopg
from mosaiq_kit import ApiError, configure_logging

from . import imports, rows, store
from .salerules import (
    MAX_TOTAL,
    database_refusal,
    validate_sale_references,
    validate_sale_rules,
)

LOGGER = logging.getLogger(__name__)
# contracts/db/advisory-locks.md: two keys, this and hashtext(import_id::text).
LOCK_KEY = 294001
ACCEPTED, REJECTED, SKIPPED, STOPPED = "accepted", "rejected", "skipped", "stopped"
OPEN = ("RECEIVING", "PROCESSING")


class Stop(Exception):  # noqa: N818 - control flow, not an error
    """The import was cancelled or expired while a chunk was being applied."""


def poll_seconds() -> float:
    try:
        return max(0.05, float(os.environ.get("SALES_IMPORT_POLL_SECONDS", "2")))
    except ValueError:
        return 2.0


def max_rows_per_second() -> float:
    try:
        return max(
            1.0, float(os.environ.get("SALES_IMPORT_MAX_ROWS_PER_SECOND", "100"))
        )
    except ValueError:
        return 100.0


def _existing_header(
    connection: psycopg.Connection,
    import_id: int,
    row: rows.Row,
    header: tuple[Any, ...],
) -> None:
    """A header with this id exists: a repeated line is a duplicate, a disagreeing
    header is refused, and anything else appends a line to it, as the web's CSV
    path does (ADR-0020)."""
    sale_id, customer_id, store_id, channel_id, occurred_at = header
    made_by = connection.execute(
        "SELECT import_id FROM sales.sale_capture WHERE transaction_id = %s",
        (sale_id,),
    ).fetchone()
    same_import = made_by is not None and made_by[0] == import_id
    already = connection.execute(
        "SELECT 1 FROM public.transaction_line"
        " WHERE transaction_id = %s AND product_id = %s",
        (sale_id, row.product_id),
    ).fetchone()
    if already is not None:
        if same_import:
            first = connection.execute(
                "SELECT line FROM sales.import_row_outcome WHERE import_id = %s"
                " AND transaction_id = %s AND product_id = %s"
                " AND outcome = 'ACCEPTED' ORDER BY line LIMIT 1",
                (import_id, sale_id, row.product_id),
            ).fetchone()
            first_line = None if first is None else first[0]
            raise rows.Rejection(
                "duplicate_row",
                None,
                f"Line {row.line}: repeats line {first_line} of this file"
                f" (transaction {row.source_id}, product {row.product_id}); the"
                " line was recorded once and nothing was added.",
                duplicate_of="FILE",
                first_line=first_line,
            )
        raise rows.Rejection(
            "duplicate_row",
            None,
            f"Line {row.line}: transaction {row.source_id} is already recorded as"
            f" sale {sale_id}; nothing was added.",
            duplicate_of="RECORDED_SALE",
            sale_id=sale_id,
        )
    for field, held, sent in (
        ("customer_id", str(customer_id), row.customer_id),
        ("store_id", store_id, row.store_id),
        ("channel_id", channel_id, row.channel_id),
        ("occurred_at", occurred_at, row.occurred_at),
    ):
        if held != sent:
            where = "in this import" if same_import else f"as sale {sale_id}"
            raise rows.rejection(
                row.line,
                "header_mismatch",
                field,
                f"transaction {row.source_id} was already accepted {where} with"
                f" another {field}",
                row.cell(field),
            )


def apply_row(
    connection: psycopg.Connection,
    *,
    import_id: int,
    created_by: str,
    row: rows.Row,
) -> tuple[int, int]:
    """Apply one row in the caller's transaction and return the sale and product
    of the line it made, or raise the `Rejection` that is its outcome."""
    body = row.sale_body()
    try:
        occurred_at, lines = validate_sale_rules(body)
        validate_sale_references(connection, body, occurred_at, lines)
    except ApiError as refusal:
        raise rows.from_api_error(row, refusal) from None
    source = rows.IMPORT_PREFIX + row.source_id
    amount = lines[0]["unit_price"] * lines[0]["quantity"]
    header = connection.execute(
        "SELECT transaction_id, customer_id, store_id, channel_id, occurred_at"
        " FROM public.transaction WHERE source_transaction_id = %s FOR UPDATE",
        (source,),
    ).fetchone()
    try:
        if header is None:
            sale_id = connection.execute(
                "INSERT INTO public.transaction (source_transaction_id,"
                " customer_id, store_id, channel_id, occurred_at, total,"
                " data_origin) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " RETURNING transaction_id",
                (
                    source,
                    row.customer_id,
                    row.store_id,
                    row.channel_id,
                    occurred_at,
                    amount,
                    "OBSERVED",
                ),
            ).fetchone()[0]
        else:
            _existing_header(connection, import_id, row, header)
            sale_id = header[0]
            held = connection.execute(
                "SELECT coalesce(sum(quantity * unit_price), 0)"
                " FROM public.transaction_line WHERE transaction_id = %s",
                (sale_id,),
            ).fetchone()[0]
            if held + amount > MAX_TOTAL:
                raise rows.rejection(
                    row.line,
                    "invalid_value",
                    "unit_price",
                    "the sale would be larger than a sale can hold",
                    row.cell("unit_price"),
                )
        connection.execute(
            "INSERT INTO public.transaction_line"
            " (transaction_id, product_id, quantity, unit_price)"
            " VALUES (%s, %s, %s, %s)",
            (sale_id, row.product_id, row.quantity, lines[0]["unit_price"]),
        )
        store.consume_stock(connection, row.store_id, lines)
        if header is None:
            connection.execute(
                "INSERT INTO sales.sale_capture (transaction_id, request_sha256,"
                " recorded_by, import_id) VALUES (%s, %s, %s, %s)",
                (
                    sale_id,
                    hashlib.sha256("\x1f".join(row.cells).encode()).digest(),
                    created_by,
                    import_id,
                ),
            )
        else:
            connection.execute(
                "UPDATE public.transaction SET total = ("
                " SELECT sum(quantity * unit_price) FROM public.transaction_line"
                " WHERE transaction_id = %s) WHERE transaction_id = %s",
                (sale_id, sale_id),
            )
    except store.StockRefusal as refusal:
        raise rows.from_api_error(
            row, database_refusal(refusal.error, lines, refusal.index)
        ) from None
    except psycopg.errors.IntegrityError as exc:
        # database_refusal gives the stable code, or re-raises what it does not know.
        raise rows.from_api_error(row, database_refusal(exc, lines)) from None
    return sale_id, row.product_id


def _recorded_elsewhere(
    connection: psycopg.Connection, line: int, cells: list[str]
) -> rows.Rejection:
    """The database refused the id because the web's page loaded it: the row is a
    duplicate of a recorded sale, and the report names that sale."""
    row = rows.parse(line, cells)
    found = connection.execute(
        "SELECT transaction_id FROM public.transaction"
        " WHERE source_transaction_id = %s",
        (row.source_id,),
    ).fetchone()
    sale_id = None if found is None else found[0]
    where = "" if sale_id is None else f" as sale {sale_id}"
    return rows.Rejection(
        "duplicate_row",
        None,
        f"Line {line}: transaction {row.source_id} is already recorded{where}"
        " through the web page's upload; nothing was added.",
        duplicate_of="RECORDED_SALE",
        sale_id=sale_id,
    )


def _write_rejection(
    connection: psycopg.Connection, import_id: int, line: int, rejection: rows.Rejection
) -> None:
    with connection.transaction():
        connection.execute(
            "INSERT INTO sales.import_row_outcome (import_id, line, outcome, code,"
            " field, message, duplicate_of, first_line, sale_id)"
            " VALUES (%s, %s, 'REJECTED', %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (import_id, line) DO NOTHING",
            (
                import_id,
                line,
                rejection.code,
                rejection.field,
                rejection.message,
                rejection.duplicate_of,
                rejection.first_line,
                rejection.sale_id,
            ),
        )


def process_row(
    connection: psycopg.Connection,
    *,
    import_id: int,
    created_by: str,
    line: int,
    cells: list[str],
) -> str:
    """Judge and record one row: accepted with its sale line, or rejected with
    its reason. The accepted outcome commits with the sale; a rejected row leaves
    nothing behind but its outcome, written afterwards."""
    refusal: rows.Rejection | None = None
    for attempt in range(3):
        try:
            with connection.transaction():
                status = connection.execute(
                    "SELECT status FROM sales.import WHERE import_id = %s FOR SHARE",
                    (import_id,),
                ).fetchone()
                if status is None or status[0] not in OPEN:
                    raise Stop
                if connection.execute(
                    "SELECT 1 FROM sales.import_row_outcome"
                    " WHERE import_id = %s AND line = %s",
                    (import_id, line),
                ).fetchone():
                    return SKIPPED
                connection.execute(
                    "SELECT set_config('mosaiq.user_id', %s, true)", (created_by,)
                )
                sale_id, product_id = apply_row(
                    connection,
                    import_id=import_id,
                    created_by=created_by,
                    row=rows.parse(line, cells),
                )
                connection.execute(
                    "INSERT INTO sales.import_row_outcome (import_id, line, outcome,"
                    " transaction_id, product_id) VALUES (%s, %s, 'ACCEPTED', %s, %s)",
                    (import_id, line, sale_id, product_id),
                )
            return ACCEPTED
        except Stop:
            return STOPPED
        except rows.Rejection as rejected:
            refusal = rejected
            break
        except psycopg.errors.InvalidParameterValue as exc:
            if "other path" not in str(exc):
                raise
            refusal = _recorded_elsewhere(connection, line, cells)
            break
        except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
            if attempt == 2:
                raise
        except (psycopg.errors.DataError, psycopg.errors.IntegrityError):
            LOGGER.warning("Import %s line %s could not be stored", import_id, line)
            refusal = rows.rejection(
                line,
                "invalid_value",
                None,
                "the row holds a value that cannot be stored",
            )
            break
    assert refusal is not None
    _write_rejection(connection, import_id, line, refusal)
    return REJECTED


def _finish_chunk(
    connection: psycopg.Connection, import_id: int, sequence: int, lines: list[int]
) -> None:
    with connection.transaction():
        connection.execute(
            "SELECT 1 FROM sales.import WHERE import_id = %s FOR UPDATE", (import_id,)
        )
        done = connection.execute(
            "SELECT count(*) FROM sales.import_row_outcome"
            " WHERE import_id = %s AND line = ANY(%s)",
            (import_id, lines),
        ).fetchone()[0]
        if done != len(lines):
            return
        connection.execute(
            "UPDATE sales.import_chunk SET status = 'DONE', rows = NULL"
            " WHERE import_id = %s AND sequence = %s AND status = 'PENDING'"
            " AND rows IS NOT NULL",
            (import_id, sequence),
        )
        connection.execute(
            "UPDATE sales.import AS i SET status = 'COMPLETED', finished_at = now()"
            " WHERE i.import_id = %s AND i.status = 'PROCESSING'"
            " AND (SELECT count(*) FROM sales.import_chunk AS c"
            "       WHERE c.import_id = i.import_id AND c.status = 'DONE')"
            "     = i.chunk_count",
            (import_id,),
        )


def process_chunk(
    connection: psycopg.Connection,
    *,
    import_id: int,
    sequence: int,
    created_by: str,
    rate: float | None = None,
    stop: Any = None,
) -> int:
    """Apply a staged chunk in file order. Returns how many rows it judged."""
    staged = connection.execute(
        "SELECT rows FROM sales.import_chunk WHERE import_id = %s AND sequence = %s"
        " AND status = 'PENDING' AND rows IS NOT NULL",
        (import_id, sequence),
    ).fetchone()
    if staged is None:
        return 0
    pace = 1.0 / (rate or max_rows_per_second())
    judged = 0
    lines = [item["line"] for item in staged[0]]
    for item in staged[0]:
        if stop is not None and stop():
            return judged
        started = time.monotonic()
        result = process_row(
            connection,
            import_id=import_id,
            created_by=created_by,
            line=item["line"],
            cells=item["cells"],
        )
        if result == STOPPED:
            return judged
        if result != SKIPPED:
            judged += 1
            spare = pace - (time.monotonic() - started)
            if spare > 0:
                time.sleep(spare)
    _finish_chunk(connection, import_id, sequence, lines)
    return judged


def run_once(
    connection: psycopg.Connection, *, rate: float | None = None, stop: Any = None
) -> int:
    """One pass: expire what is idle, then apply the oldest staged chunk of an
    open import. Returns the rows judged (0 when there is nothing to do)."""
    with connection.transaction():
        imports.expire_stale(connection)
    chunk = connection.execute(
        "SELECT c.import_id, c.sequence, i.created_by::text"
        " FROM sales.import_chunk AS c JOIN sales.import AS i USING (import_id)"
        " WHERE c.status = 'PENDING' AND c.rows IS NOT NULL"
        " AND i.status IN ('RECEIVING', 'PROCESSING')"
        " ORDER BY c.import_id, c.sequence LIMIT 1"
    ).fetchone()
    if chunk is None:
        return 0
    import_id, sequence, created_by = chunk
    locked = connection.execute(
        "SELECT pg_try_advisory_lock(%s, hashtext(%s))", (LOCK_KEY, str(import_id))
    ).fetchone()[0]
    if not locked:
        return 0
    try:
        return process_chunk(
            connection,
            import_id=import_id,
            sequence=sequence,
            created_by=created_by,
            rate=rate,
            stop=stop,
        )
    finally:
        connection.execute(
            "SELECT pg_advisory_unlock(%s, hashtext(%s))", (LOCK_KEY, str(import_id))
        )


def run_until_idle(connection: psycopg.Connection, **kwargs: Any) -> int:
    """Apply every staged chunk there is, then return the rows judged."""
    total = 0
    while True:
        judged = run_once(connection, **kwargs)
        if judged == 0:
            return total
        total += judged


def main() -> None:
    configure_logging()
    dsn = os.environ["DATABASE_URL"]
    stopping = False

    def request_stop(_signal: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    connection: psycopg.Connection | None = None
    while not stopping:
        try:
            if connection is None or connection.closed:
                connection = psycopg.connect(dsn, autocommit=True)
            judged = run_once(connection, stop=lambda: stopping)
        except psycopg.Error:
            LOGGER.exception("Import worker pass failed")
            if connection is not None:
                try:
                    connection.close()
                except psycopg.Error:
                    LOGGER.exception("Import worker connection cleanup failed")
            connection = None
            judged = 0
        if judged == 0 and not stopping:
            time.sleep(poll_seconds())
    if connection is not None and not connection.closed:
        connection.close()


if __name__ == "__main__":
    main()
