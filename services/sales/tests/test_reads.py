"""Sales and channel reads include both phone and CSV writers."""

from __future__ import annotations

import uuid
from decimal import Decimal

import psycopg

from tests.conftest import idempotency_headers


def _phone_sale(client, token, body):
    response = client.post(
        "/v1/sales",
        json=body,
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert response.status_code == 201
    return response.get_json()


def _csv_sale(retail_url, body):
    source = "test-csv-" + uuid.uuid4().hex
    line = body["lines"][0]
    with psycopg.connect(retail_url) as connection:
        sale_id = connection.execute(
            "INSERT INTO public.transaction"
            " (source_transaction_id, customer_id, store_id, channel_id,"
            " occurred_at, total, data_origin) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " RETURNING transaction_id",
            (
                source,
                body["customer_id"],
                body["store_id"],
                body["channel_id"],
                body["occurred_at"],
                Decimal("0.00"),
                "OBSERVED",
            ),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO public.transaction_line"
            " (transaction_id, product_id, quantity, unit_price)"
            " VALUES (%s, %s, %s, %s)",
            (sale_id, line["product_id"], line["quantity"], line["unit_price"]),
        )
        connection.execute(
            "UPDATE public.transaction SET total = %s WHERE transaction_id = %s",
            (Decimal(line["unit_price"]) * line["quantity"], sale_id),
        )
    return sale_id, source


def test_get_and_list_include_phone_and_csv_sales_with_lines(
    client, token, sale_body, retail_url, owner
):
    phone = _phone_sale(client, token, sale_body)
    csv_id, source = _csv_sale(retail_url, sale_body)
    headers = token("AUDITOR")
    one = client.get(f"/v1/sales/{csv_id}", headers=headers)
    assert one.status_code == 200
    assert one.get_json()["lines"] == sale_body["lines"]
    page = client.get(
        f"/v1/sales?customer_id={sale_body['customer_id']}&size=100",
        headers=headers,
    )
    assert page.status_code == 200
    ids = {sale["sale_id"] for sale in page.get_json()["sales"]}
    assert {phone["sale_id"], csv_id} <= ids
    owner.execute(
        "DELETE FROM public.transaction WHERE source_transaction_id = %s", (source,)
    )


def test_unknown_sale_is_404(client, token):
    response = client.get("/v1/sales/9223372036854775807", headers=token("ADMIN"))
    assert response.status_code == 404
    assert response.get_json()["code"] == "sale_not_found"


def test_channels_are_the_five_seeded_rows(client, token):
    response = client.get("/v1/channels", headers=token("INVENTORY_PLANNER"))
    assert response.status_code == 200
    channels = response.get_json()["channels"]
    assert len(channels) == 5
    assert [channel["channel_id"] for channel in channels] == [1, 2, 3, 4, 5]


def test_list_filters_and_invalid_query(client, token, sale_body):
    _phone_sale(client, token, sale_body)
    headers = token("ANALYST")
    response = client.get(
        f"/v1/sales?store_id={sale_body['store_id']}&page=1&size=1",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.get_json()["page_info"]["size"] == 1
    invalid = client.get(
        "/v1/sales?from=2026-02-01T00:00:00Z&to=2026-01-01T00:00:00Z",
        headers=headers,
    )
    assert invalid.status_code == 422
    assert invalid.get_json()["code"] == "invalid_query"


def test_readiness_requires_stock_function(build_app):
    app = build_app(database_url="postgresql://postgres@127.0.0.1:1/retail")
    response = app.test_client().get("/health/ready")
    assert response.status_code == 503
    assert response.get_json()["code"] == "schema_not_ready"
