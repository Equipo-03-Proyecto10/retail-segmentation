"""The demonstration chain, through the gateway of the local stack (F13-09).

Sign in through auth in JSON, record a sale through sales with an
`Idempotency-Key`, find its `transaction` row and the `inventory` decrement in
PostgreSQL, and read the sale back in XML valid against sales.xsd. Then sign
out and expect another service to refuse the token, and stop redis-state and
expect authentication to fail closed.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.conformance.harness import Client, wait_for

# A STORE_MANAGER of the seed, which records sales (02_seed_30_per_table.sql).
EMAIL = "user3@mosaiq-demo.com"
PASSWORD = "Password123!"


def compose(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["docker", "compose", "-f", os.environ["E2E_COMPOSE"], *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def psql(sql: str, **variables: str) -> list[list[str]]:
    """Rows of a query run as the owner; values arrive as psql variables."""

    settings = [f"--set={key}={value}" for key, value in variables.items()]
    command = ["exec", "-T", "db", "psql", "-X", "-At", "-F", "|"]
    command += ["-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "retail"]
    out = compose(*command, *settings, stdin=sql)
    return [line.split("|") for line in out.splitlines() if line]


def sign_in(auth: Client) -> str:
    body = {"email": EMAIL, "password": PASSWORD, "device_label": "nightly e2e"}
    reply = auth.call(
        "POST",
        "/api/auth/v1/sessions",
        content_type="application/json",
        body=json.dumps(body).encode(),
    )
    assert reply.status == 201, reply.body
    return reply.json()["access_token"]


@pytest.mark.services("auth", "sales")
def test_a_sale_lands_in_postgresql_and_reads_back_as_valid_xml(
    auth: Client, sales: Client
) -> None:
    token = sign_in(auth)
    [[customer_id, store_id, product_id, price, on_hand, channel_id]] = psql("""
        SELECT c.customer_id, i.store_id, i.product_id, p.list_price,
               i.quantity_on_hand, (SELECT min(channel_id) FROM channel)
          FROM inventory AS i
          JOIN product AS p USING (product_id)
         CROSS JOIN LATERAL (SELECT customer_id FROM customer
                              WHERE registered_on < CURRENT_DATE
                              ORDER BY customer_id LIMIT 1) AS c
         WHERE p.is_active AND p.list_price > 0 AND i.quantity_on_hand > 0
         ORDER BY i.store_id, i.product_id
         LIMIT 1;
        """)
    occurred_at = datetime.now(UTC) - timedelta(minutes=1)
    sale = {
        "customer_id": customer_id,
        "store_id": int(store_id),
        "channel_id": int(channel_id),
        "occurred_at": occurred_at.isoformat(timespec="seconds"),
        "lines": [{"product_id": int(product_id), "quantity": 1, "unit_price": price}],
    }
    key = str(uuid.uuid4())
    reply = sales.call(
        "POST",
        "/api/sales/v1/sales",
        token=token,
        content_type="application/json",
        body=json.dumps(sale).encode(),
        headers={"Idempotency-Key": key},
    )
    assert reply.status == 201, reply.body
    sale_id = str(reply.json()["sale_id"])

    rows = psql(
        "SELECT customer_id, store_id, total FROM transaction "
        "WHERE transaction_id = :'sale_id' "
        "AND source_transaction_id = 'svc-sales:' || lower(:'key');",
        sale_id=sale_id,
        key=key,
    )
    assert rows == [[customer_id, store_id, price]]
    [[now_on_hand]] = psql(
        "SELECT quantity_on_hand FROM inventory "
        "WHERE store_id = :'store_id' AND product_id = :'product_id';",
        store_id=store_id,
        product_id=product_id,
    )
    assert int(now_on_hand) == int(on_hand) - 1

    # Client.call validates the body against contracts/sales/sales.xsd.
    xml = sales.call(
        "GET", f"/api/sales/v1/sales/{sale_id}", token=token, accept="application/xml"
    )
    assert (xml.status, xml.media_type) == (200, "application/xml")


@pytest.mark.services("auth", "sales")
def test_a_signed_out_token_is_refused_by_another_service(
    auth: Client, sales: Client
) -> None:
    token = sign_in(auth)
    assert sales.call("GET", "/api/sales/v1/channels", token=token).status == 200
    assert (
        auth.call("DELETE", "/api/auth/v1/sessions/current", token=token).status == 204
    )
    reply = sales.call("GET", "/api/sales/v1/channels", token=token)
    assert (reply.status, reply.code()) == (401, "session_revoked")


@pytest.mark.services("auth", "sales")
def test_authentication_fails_closed_without_redis_state(
    auth: Client, sales: Client
) -> None:
    token = sign_in(auth)
    compose("stop", "redis-state")
    try:
        reply = sales.call("GET", "/api/sales/v1/channels", token=token)
        assert (reply.status, reply.code()) == (503, "auth_unavailable")
    finally:
        compose("start", "redis-state")
        wait_for(
            lambda: sales.call("GET", "/api/sales/v1/channels", token=token).status
            == 200,
            "sales after redis-state restarted",
        )
