"""Every sale and every segmentation run records its data origin (#559, ADR-0052).

These run against the database the three scripts load. On that seed every
window ending now holds a SEEDED sale, so a test that needs OBSERVED sales
relabels them as the owner, inside a transaction that always rolls back:
nothing here leaves a trace in the shared test database.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from web.db.segments import get_run
from web.db.transactions import atomic
from web.services import provenance, segmentation
from web.services.ingestion import RowRejected, SalesRow, ingest_row
from web.services.kmeans import KMeansParams

pytestmark = pytest.mark.postgres

WINDOW_DAYS = 180

# The seed's own K-means run is k = 4, seed 463 over 180 days.
_METHODS: dict[str, Callable[[psycopg.Connection], Any]] = {
    "RFM_RULES": lambda connection: segmentation.run(connection, WINDOW_DAYS),
    "KMEANS": lambda connection: segmentation.run_kmeans(
        connection, WINDOW_DAYS, KMeansParams(k=4, seed=463)
    ),
}

# The sales a run over WINDOW_DAYS scores: the same bounds as
# web/db/segments.py and fn_segmentation_window_origin, in the same transaction.
_IN_WINDOW = "occurred_at >= now() - make_interval(days => %s) AND occurred_at <= now()"
_KEEP_ONE_SEEDED = (
    "UPDATE transaction SET data_origin = 'OBSERVED'"
    " WHERE transaction_id <> ("
    "     SELECT transaction_id FROM transaction"
    "      WHERE " + _IN_WINDOW + " ORDER BY occurred_at, transaction_id LIMIT 1"
    " )"
)
_ALL_OBSERVED = "UPDATE transaction SET data_origin = 'OBSERVED'"
_SEEDED_IN_WINDOW = (
    "SELECT count(*) FROM transaction WHERE data_origin = 'SEEDED' AND " + _IN_WINDOW
)


class _Undo(Exception):
    """Raised last so the owning transaction rolls back whatever happened."""


def _scalar(connection: psycopg.Connection, sql: str, params: tuple = ()) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def _run_after(owner_url: str, method: str, relabel: str, params: tuple) -> dict:
    """Relabel sales as the owner, run `method`, and report what the run
    recorded, all in one transaction that is then rolled back."""
    seen: dict[str, Any] = {}
    with psycopg.connect(owner_url) as connection:

        @atomic
        def attempt(conn: psycopg.Connection) -> None:
            with conn.cursor() as cursor:
                cursor.execute(relabel, params)
            seen["seeded_in_window"] = _scalar(conn, _SEEDED_IN_WINDOW, (WINDOW_DAYS,))
            result = _METHODS[method](conn)
            run = get_run(conn, result.run_id)
            seen["data_origin"] = run.data_origin
            raise _Undo

        try:
            with pytest.raises(_Undo):
                attempt(connection)
        finally:
            connection.rollback()
    return seen


@pytest.mark.parametrize("method", sorted(_METHODS))
def test_a_run_over_one_seeded_sale_is_seeded(owner_url: str, method: str) -> None:
    seen = _run_after(owner_url, method, _KEEP_ONE_SEEDED, (WINDOW_DAYS,))

    assert seen == {"seeded_in_window": 1, "data_origin": provenance.SEEDED}


@pytest.mark.parametrize("method", sorted(_METHODS))
def test_a_run_over_observed_sales_only_is_observed(
    owner_url: str, method: str
) -> None:
    seen = _run_after(owner_url, method, _ALL_OBSERVED, ())

    assert seen == {"seeded_in_window": 0, "data_origin": provenance.OBSERVED}


def test_an_observed_run_over_seeded_sales_is_refused_by_the_schema(
    app_url: str,
) -> None:
    """The guard holds whoever writes the run, not only create_run."""
    with psycopg.connect(app_url) as connection:
        try:
            with pytest.raises(psycopg.errors.CheckViolation) as refused:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO segmentation_run"
                        " (method, window_days, data_origin) VALUES (%s, %s, %s)",
                        ("RFM_RULES", WINDOW_DAYS, provenance.OBSERVED),
                    )
        finally:
            connection.rollback()

    assert refused.value.diag.constraint_name == "segmentation_run_origin_observed_only"


def _sale_facts(connection: psycopg.Connection) -> tuple[str, int, int, int]:
    """A customer, an active product the store stocks, that store, a channel."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT customer_id::text FROM customer"
            " WHERE registered_on <= current_date ORDER BY customer_id LIMIT 1"
        )
        (customer_id,) = cursor.fetchone()
        cursor.execute(
            "SELECT i.store_id, i.product_id FROM inventory AS i"
            " JOIN product AS p ON p.product_id = i.product_id"
            " WHERE p.is_active AND i.quantity_on_hand >= %s"
            " ORDER BY i.store_id, i.product_id LIMIT 1",
            (1,),
        )
        store_id, product_id = cursor.fetchone()
        cursor.execute("SELECT min(channel_id) FROM channel")
        (channel_id,) = cursor.fetchone()
    return customer_id, store_id, product_id, channel_id


def test_an_accepted_csv_row_is_an_observed_sale(app_url: str) -> None:
    source_id = "VERIFY-559-OBSERVED"
    with psycopg.connect(app_url) as connection:
        customer_id, store_id, product_id, channel_id = _sale_facts(connection)
        row = SalesRow(
            source_transaction_id=source_id,
            customer_id=customer_id,
            store_id=store_id,
            channel_id=channel_id,
            occurred_at=datetime.now(UTC).replace(microsecond=0),
            product_id=product_id,
            quantity=1,
            unit_price=Decimal("10.00"),
        )
        seen: dict[str, Any] = {}

        # ingest_row commits its own work; inside an owner that always rolls
        # back, it joins that owner and leaves nothing behind.
        @atomic
        def attempt(conn: psycopg.Connection) -> None:
            ingest_row(conn, row)
            seen["data_origin"] = _scalar(
                conn,
                "SELECT data_origin FROM transaction WHERE source_transaction_id = %s",
                (source_id,),
            )
            raise _Undo

        try:
            with pytest.raises(_Undo):
                attempt(connection)
        finally:
            connection.rollback()

    assert seen == {"data_origin": provenance.OBSERVED}


def test_a_csv_line_onto_a_seeded_sale_is_refused(app_url: str) -> None:
    """TXN-00000001 is the seed's; a row repeating its header exactly still
    cannot add a line to it."""
    source_id = "TXN-00000001"
    with psycopg.connect(app_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT t.transaction_id, t.customer_id::text, t.store_id,"
                "       t.channel_id, t.occurred_at, t.data_origin"
                "  FROM transaction AS t WHERE t.source_transaction_id = %s",
                (source_id,),
            )
            transaction_id, customer_id, store_id, channel_id, occurred_at, origin = (
                cursor.fetchone()
            )
            cursor.execute(
                "SELECT min(p.product_id) FROM product AS p"
                " WHERE p.is_active AND p.product_id NOT IN ("
                "     SELECT product_id FROM transaction_line WHERE transaction_id = %s"
                " )",
                (transaction_id,),
            )
            (product_id,) = cursor.fetchone()
        lines_before = _scalar(
            connection,
            "SELECT count(*) FROM transaction_line WHERE transaction_id = %s",
            (transaction_id,),
        )
        connection.rollback()
        assert origin == provenance.SEEDED

        row = SalesRow(
            source_transaction_id=source_id,
            customer_id=customer_id,
            store_id=store_id,
            channel_id=channel_id,
            occurred_at=occurred_at,
            product_id=product_id,
            quantity=1,
            unit_price=Decimal("10.00"),
        )
        try:
            with pytest.raises(
                RowRejected, match=r"^duplicate: TXN-00000001 is a seeded sale$"
            ):
                ingest_row(connection, row)
            lines_after = _scalar(
                connection,
                "SELECT count(*) FROM transaction_line WHERE transaction_id = %s",
                (transaction_id,),
            )
        finally:
            connection.rollback()

    assert lines_after == lines_before
