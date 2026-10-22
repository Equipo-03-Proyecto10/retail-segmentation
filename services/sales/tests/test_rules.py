"""The all-or-nothing sale rule and its stable refusals."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import idempotency_headers, sale_xml


def post(client, token, body, key=None):
    return client.post(
        "/v1/sales",
        json=body,
        headers=idempotency_headers(token("STORE_MANAGER"), key),
    )


@pytest.mark.parametrize(
    ("change", "status", "code", "field"),
    [
        (lambda body: body.update(lines=[]), 422, "no_lines", "lines"),
        (
            lambda body: body["lines"].append(copy.deepcopy(body["lines"][0])),
            409,
            "duplicate_product_line",
            "lines[1].product_id",
        ),
        (
            lambda body: body["lines"][0].update(quantity=0),
            422,
            "invalid_quantity",
            "lines[0].quantity",
        ),
        (
            lambda body: body["lines"][0].update(unit_price="0.00"),
            422,
            "invalid_price",
            "lines[0].unit_price",
        ),
        (
            lambda body: body["lines"][0].update(unit_price="-1"),
            422,
            "invalid_price",
            "lines[0].unit_price",
        ),
        (
            lambda body: body["lines"][0].update(quantity=2_147_483_648),
            422,
            "invalid_quantity",
            "lines[0].quantity",
        ),
        (
            lambda body: body["lines"][0].update(unit_price="100000000.00"),
            422,
            "invalid_price",
            "lines[0].unit_price",
        ),
        (
            lambda body: body["lines"][0].update(
                quantity=2_000_000_000, unit_price="99.00"
            ),
            422,
            "invalid_body",
            "lines",
        ),
        (
            lambda body: body["lines"][0].update(unit_price="1.005"),
            422,
            "invalid_price",
            "lines[0].unit_price",
        ),
        (
            lambda body: body.update(
                occurred_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()
            ),
            422,
            "future_dated",
            "occurred_at",
        ),
        (
            lambda body: body.update(
                occurred_at=(datetime.now(UTC) - timedelta(days=1))
                .replace(tzinfo=None)
                .isoformat()
            ),
            422,
            "invalid_body",
            "occurred_at",
        ),
    ],
)
def test_local_refusals(client, token, sale_body, change, status, code, field):
    body = copy.deepcopy(sale_body)
    change(body)
    response = post(client, token, body)
    assert response.status_code == status
    assert response.get_json()["code"] == code
    assert response.get_json()["details"][0]["field"] == field


@pytest.mark.parametrize("media", ["application/json", "application/xml"])
def test_a_nan_price_is_refused_by_the_contract_in_both_formats(
    client, token, sale_body, owner, media
):
    """NaN never reaches a Decimal or a float: the contract refuses it first.

    The OpenAPI pattern `^-?[0-9]+(\\.[0-9]+)?$` and the XSD's `xs:decimal`
    both reject the text, so JSON and XML agree on 422 invalid_body (ADR-0035);
    a finite value the pattern admits, like 0.00, -1 or 1.005, is invalid_price.
    """
    body = copy.deepcopy(sale_body)
    body["lines"][0]["unit_price"] = "NaN"
    before = owner.execute("SELECT count(*) FROM public.transaction").fetchone()[0]
    headers = idempotency_headers(token("STORE_MANAGER"))
    if media == "application/json":
        response = client.post("/v1/sales", json=body, headers=headers)
        assert response.get_json()["code"] == "invalid_body"
    else:
        response = client.post(
            "/v1/sales",
            data=sale_xml(body),
            content_type="application/xml",
            headers={**headers, "Accept": "application/json"},
        )
        assert response.get_json()["code"] == "invalid_body"
    assert response.status_code == 422
    after = owner.execute("SELECT count(*) FROM public.transaction").fetchone()[0]
    assert after == before


def test_before_registration(client, token, sale_body, owner):
    row = owner.execute(
        "SELECT customer_id, registered_on FROM public.customer"
        " ORDER BY registered_on DESC, customer_id LIMIT %s",
        (1,),
    ).fetchone()
    body = copy.deepcopy(sale_body)
    body["customer_id"] = str(row[0])
    body["occurred_at"] = (
        datetime.combine(row[1] - timedelta(days=1), datetime.min.time(), UTC)
    ).isoformat()
    response = post(client, token, body)
    assert response.status_code == 422
    assert response.get_json()["code"] == "before_registration"


def test_unknown_customer_store_and_channel(client, token, sale_body):
    cases = (
        ("customer_id", str(uuid.uuid4()), "unknown_customer"),
        ("store_id", 32767, "unknown_store"),
        ("channel_id", 32767, "unknown_channel"),
    )
    for field, value, code in cases:
        body = copy.deepcopy(sale_body)
        body[field] = value
        response = post(client, token, body)
        assert response.status_code == 422
        assert response.get_json()["code"] == code
        assert response.get_json()["details"][0]["field"] == field


def test_unknown_and_inactive_products(client, token, sale_body, owner):
    product_id = owner.execute(
        "SELECT product_id FROM public.product ORDER BY product_id LIMIT %s",
        (1,),
    ).fetchone()[0]
    owner.execute(
        "UPDATE public.product SET is_active = false WHERE product_id = %s",
        (product_id,),
    )
    try:
        for candidate, code in (
            (2_000_000_000, "unknown_product"),
            (product_id, "inactive_product"),
        ):
            body = copy.deepcopy(sale_body)
            body["lines"][0]["product_id"] = candidate
            response = post(client, token, body)
            assert response.status_code == 422
            assert response.get_json()["code"] == code
            assert response.get_json()["details"][0]["field"] == "lines[0].product_id"
    finally:
        owner.execute(
            "UPDATE public.product SET is_active = true WHERE product_id = %s",
            (product_id,),
        )


def test_missing_stock_record_names_the_line(client, token, sale_body, owner):
    row = owner.execute(
        "SELECT s.store_id, p.product_id FROM public.store AS s"
        " CROSS JOIN public.product AS p"
        " LEFT JOIN public.inventory AS i"
        " ON i.store_id = s.store_id AND i.product_id = p.product_id"
        " WHERE p.is_active AND i.store_id IS NULL"
        " ORDER BY s.store_id, p.product_id LIMIT %s",
        (1,),
    ).fetchone()
    assert row is not None
    body = copy.deepcopy(sale_body)
    body["store_id"], body["lines"][0]["product_id"] = row
    response = post(client, token, body)
    assert response.status_code == 422
    assert response.get_json()["code"] == "no_stock_record"
    assert response.get_json()["details"] == [
        {"field": "lines[0].product_id", "code": "no_stock_record"}
    ]


def test_short_second_line_rolls_back_header_and_first_decrement(
    client, token, sale_body, owner
):
    rows = owner.execute(
        "SELECT i.product_id, i.quantity_on_hand FROM public.inventory AS i"
        " JOIN public.product AS p USING (product_id)"
        " WHERE i.store_id = %s AND p.is_active AND i.quantity_on_hand > %s"
        " ORDER BY i.product_id LIMIT %s",
        (sale_body["store_id"], 2, 2),
    ).fetchall()
    assert len(rows) == 2
    body = copy.deepcopy(sale_body)
    body["lines"] = [
        {"product_id": rows[0][0], "quantity": 1, "unit_price": "2.00"},
        {
            "product_id": rows[1][0],
            "quantity": rows[1][1] + 1,
            "unit_price": "3.00",
        },
    ]
    key = str(uuid.uuid4())
    response = post(client, token, body, key)
    assert response.status_code == 409
    assert response.get_json()["code"] == "stock_unavailable"
    assert response.get_json()["details"] == [
        {"field": "lines[1].quantity", "code": "insufficient_stock"}
    ]
    assert (
        owner.execute(
            "SELECT count(*) FROM public.transaction"
            " WHERE source_transaction_id = %s",
            ("svc-sales:" + key,),
        ).fetchone()[0]
        == 0
    )
    quantity = owner.execute(
        "SELECT quantity_on_hand FROM public.inventory"
        " WHERE store_id = %s AND product_id = %s",
        (body["store_id"], rows[0][0]),
    ).fetchone()[0]
    assert quantity == rows[0][1]


def test_phone_sale_is_visible_to_web_report_and_inventory_queries(
    client, token, sale_body, owner
):
    product_id = sale_body["lines"][0]["product_id"]
    before = owner.execute(
        "SELECT quantity_on_hand FROM public.inventory"
        " WHERE store_id = %s AND product_id = %s",
        (sale_body["store_id"], product_id),
    ).fetchone()[0]
    response = post(client, token, sale_body)
    assert response.status_code == 201
    sale_id = response.get_json()["sale_id"]
    report = owner.execute(
        "SELECT count(*), sum(total) FROM public.transaction AS t"
        " WHERE t.occurred_at > (SELECT max(occurred_at) FROM public.transaction)"
        " - make_interval(days => %s) AND t.transaction_id = %s",
        (36500, sale_id),
    ).fetchone()
    assert report == (
        1,
        owner.execute(
            "SELECT total FROM public.transaction WHERE transaction_id = %s", (sale_id,)
        ).fetchone()[0],
    )
    stock = owner.execute(
        "SELECT i.quantity_on_hand FROM public.inventory AS i"
        " JOIN public.store AS s USING (store_id)"
        " JOIN public.product AS p USING (product_id)"
        " WHERE i.store_id = %s AND i.product_id = %s",
        (sale_body["store_id"], product_id),
    ).fetchone()[0]
    assert stock == before - sale_body["lines"][0]["quantity"]
