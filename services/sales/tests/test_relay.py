"""The sales outbox captures both writers and reaches Redis durably."""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

import psycopg

from tests.conftest import idempotency_headers

STREAM = "stream:sales.v1.recorded"


def _wait(redis_connection, count):
    # Rows written before the app's relay listens get no wake-up: its first
    # pass waits up to 5 s for one, and a failed pass retries 1 s later (#705,
    # #711). Allow for both, with room; a relayed entry returns at once.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        entries = redis_connection.xrange(STREAM)
        if len(entries) >= count:
            return entries
        time.sleep(0.05)
    return redis_connection.xrange(STREAM)


def _clear(owner, redis_connection):
    owner.execute("DELETE FROM sales_events.outbox")
    redis_connection.delete(STREAM)


def test_service_sale_adds_and_relays_exactly_one(
    build_app, token, sale_body, owner, redis_connection
):
    _clear(owner, redis_connection)
    app = build_app(relay=True)
    response = app.test_client().post(
        "/v1/sales",
        json=sale_body,
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert response.status_code == 201
    sale_id = response.get_json()["sale_id"]
    rows = owner.execute(
        "SELECT count(*) FROM sales_events.outbox WHERE entity_id = %s",
        (str(sale_id),),
    ).fetchone()[0]
    assert rows == 1
    entries = _wait(redis_connection, 1)
    assert len(entries) == 1
    assert entries[0][1]["entity_id"] == str(sale_id)


def test_csv_create_adds_two_and_append_adds_one_and_all_relay(
    build_app, retail_url, sale_body, owner, redis_connection
):
    _clear(owner, redis_connection)
    source = "test-csv-relay-" + uuid.uuid4().hex
    line = sale_body["lines"][0]
    with psycopg.connect(retail_url) as connection:
        sale_id = connection.execute(
            "INSERT INTO public.transaction"
            " (source_transaction_id, customer_id, store_id, channel_id, occurred_at,"
            " total, data_origin) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " RETURNING transaction_id",
            (
                source,
                sale_body["customer_id"],
                sale_body["store_id"],
                sale_body["channel_id"],
                sale_body["occurred_at"],
                Decimal(0),
                "OBSERVED",
            ),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO public.transaction_line"
            " (transaction_id, product_id, quantity, unit_price)"
            " VALUES (%s, %s, %s, %s)",
            (sale_id, line["product_id"], 1, line["unit_price"]),
        )
        connection.execute(
            "UPDATE public.transaction SET total = %s WHERE transaction_id = %s",
            (line["unit_price"], sale_id),
        )
    assert (
        owner.execute(
            "SELECT count(*) FROM sales_events.outbox WHERE entity_id = %s",
            (str(sale_id),),
        ).fetchone()[0]
        == 2
    )
    second = owner.execute(
        "SELECT i.product_id FROM public.inventory AS i"
        " JOIN public.product AS p USING (product_id)"
        " WHERE i.store_id = %s AND p.is_active AND i.product_id <> %s"
        " ORDER BY i.product_id LIMIT %s",
        (sale_body["store_id"], line["product_id"], 1),
    ).fetchone()[0]
    with psycopg.connect(retail_url) as connection:
        connection.execute(
            "INSERT INTO public.transaction_line"
            " (transaction_id, product_id, quantity, unit_price)"
            " VALUES (%s, %s, %s, %s)",
            (sale_id, second, 1, "1.00"),
        )
        connection.execute(
            "UPDATE public.transaction SET total = total + %s"
            " WHERE transaction_id = %s",
            (Decimal("1.00"), sale_id),
        )
    assert (
        owner.execute(
            "SELECT count(*) FROM sales_events.outbox WHERE entity_id = %s",
            (str(sale_id),),
        ).fetchone()[0]
        == 3
    )
    build_app(relay=True)
    entries = _wait(redis_connection, 3)
    assert len(entries) == 3
    assert all(entry[1]["entity_id"] == str(sale_id) for entry in entries)
    owner.execute(
        "DELETE FROM public.transaction WHERE transaction_id = %s", (sale_id,)
    )


def test_relay_purges_rows_relayed_more_than_seven_days_ago(
    build_app, owner, redis_connection
):
    _clear(owner, redis_connection)
    event_id = owner.execute(
        "INSERT INTO sales_events.outbox"
        " (stream, event_type, entity_id, occurred_at, relayed_at)"
        " VALUES (%s, %s, %s, now() - (%s * interval '1 day'),"
        " now() - (%s * interval '1 day')) RETURNING event_id",
        (STREAM, "UPDATE", "old", 9, 8),
    ).fetchone()[0]
    build_app(relay=True)
    # The relay's first pass waits up to 5 s for a wake-up before it polls and
    # purges, and this row was inserted before it listened, so none comes. A
    # failed pass retries 1 s later (#705). Allow for both, with room.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (
            owner.execute(
                "SELECT count(*) FROM sales_events.outbox WHERE event_id = %s",
                (event_id,),
            ).fetchone()[0]
            == 0
        ):
            break
        time.sleep(0.05)
    assert (
        owner.execute(
            "SELECT count(*) FROM sales_events.outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()[0]
        == 0
    )
