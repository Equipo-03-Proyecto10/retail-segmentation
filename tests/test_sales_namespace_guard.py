"""A CSV row cannot write into sales-service's `svc-sales:` namespace (#434).

ADR-0039 reserves the prefix. BOUNDARY sales in sql/01_schema.sql refuses it
from any session user but svc_sales with SQLSTATE 22023, which ingest_row
already reports as a malformed row, so the web needs no change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest

from web.db.transactions import atomic
from web.services.ingestion import RowRejected, SalesRow, ingest_row

pytestmark = pytest.mark.postgres

SOURCE_ID = "svc-sales:00000000-0000-0000-0000-000000000001"


class _Undo(Exception):
    """Raised last so the owning transaction rolls back whatever happened."""


def _count(cur: psycopg.Cursor, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    return cur.fetchone()[0]


def test_a_csv_row_in_the_service_namespace_is_malformed(app_url: str) -> None:
    with psycopg.connect(app_url) as conn:
        try:
            with conn.cursor() as cur:
                # A row valid in every other respect, so the header insert is
                # what refuses it.
                cur.execute(
                    "SELECT customer_id::text FROM customer"
                    " WHERE registered_on <= current_date ORDER BY customer_id LIMIT 1"
                )
                (customer_id,) = cur.fetchone()
                cur.execute(
                    "SELECT product_id FROM product WHERE is_active"
                    " ORDER BY product_id LIMIT 1"
                )
                (product_id,) = cur.fetchone()
                cur.execute("SELECT min(store_id) FROM store")
                (store_id,) = cur.fetchone()
                cur.execute("SELECT min(channel_id) FROM channel")
                (channel_id,) = cur.fetchone()
                lines_before = _count(cur, "SELECT count(*) FROM transaction_line")

                row = SalesRow(
                    source_transaction_id=SOURCE_ID,
                    customer_id=customer_id,
                    store_id=store_id,
                    channel_id=channel_id,
                    occurred_at=datetime.now(UTC).replace(microsecond=0),
                    product_id=product_id,
                    quantity=1,
                    unit_price=Decimal("10.00"),
                )
                seen: dict[str, int] = {}

                # ingest_row commits its own work. Run it inside an owner that
                # always rolls back, so a guard that failed to refuse could not
                # leave a row in the shared test database.
                @atomic
                def attempt(connection: psycopg.Connection) -> None:
                    with pytest.raises(RowRejected, match=r"^malformed row"):
                        # A savepoint: the refusal undoes only this attempt.
                        with connection.transaction():
                            ingest_row(connection, row)
                    with connection.cursor() as check:
                        seen["headers"] = _count(
                            check,
                            "SELECT count(*) FROM transaction"
                            " WHERE source_transaction_id = %s",
                            (SOURCE_ID,),
                        )
                        seen["lines"] = _count(
                            check, "SELECT count(*) FROM transaction_line"
                        )
                    raise _Undo

                with pytest.raises(_Undo):
                    attempt(conn)
                assert seen == {"headers": 0, "lines": lines_before}
        finally:
            conn.rollback()
