"""Cancellations and returns (F21-03, ADR-0062, RN-70): the rules, the refusals,
the transaction and the contract, in JSON and XML, against real PostgreSQL."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from lxml import etree

from tests.conftest import idempotency_headers

NS = "https://mosaiq.maxthecoder.online/xml/sales/v1"
XML = {"Accept": "application/xml"}


# ---- helpers -----------------------------------------------------------------------


def reversal_xml(body: dict) -> bytes:
    def text(name: str, value) -> str:
        if isinstance(value, bool):
            value = "true" if value else "false"
        return f"<{name}>{value}</{name}>"

    lines = "".join(
        "<lines>"
        + "".join(text(name, value) for name, value in line.items())
        + "</lines>"
        for line in body.get("lines", [])
    )
    head = "".join(
        text(name, body[name]) for name in ("sale_id", "kind", "reason") if name in body
    )
    return (
        f'<SaleReversalRequest xmlns="{NS}">{head}{lines}</SaleReversalRequest>'
    ).encode()


def post_xml(client, body: dict, headers: dict):
    return client.post(
        "/v1/sale-reversals",
        data=reversal_xml(body),
        content_type="application/xml",
        headers={**headers, **XML},
    )


def on_hand(owner, store_id: int, product_id: int) -> int:
    return owner.execute(
        "SELECT quantity_on_hand FROM public.inventory"
        " WHERE store_id = %s AND product_id = %s",
        (store_id, product_id),
    ).fetchone()[0]


def returned(owner, sale_id: int) -> dict[int, int]:
    return dict(
        owner.execute(
            "SELECT product_id, returned_quantity FROM public.transaction_line"
            " WHERE transaction_id = %s",
            (sale_id,),
        ).fetchall()
    )


def counts(owner, sale_id: int) -> tuple[int, int, int, int]:
    """Reversals, reversal lines, restock movements and reversed outbox rows."""
    return (
        owner.execute(
            "SELECT count(*) FROM sales.sale_reversal WHERE transaction_id = %s",
            (sale_id,),
        ).fetchone()[0],
        owner.execute(
            "SELECT count(*) FROM sales.sale_reversal_line WHERE transaction_id = %s",
            (sale_id,),
        ).fetchone()[0],
        owner.execute(
            "SELECT count(*) FROM stock.stock_movement WHERE movement_type = 'RESTOCK'"
            " AND reversal_id IN (SELECT reversal_id FROM sales.sale_reversal"
            " WHERE transaction_id = %s)",
            (sale_id,),
        ).fetchone()[0],
        owner.execute(
            "SELECT count(*) FROM sales_events.outbox WHERE stream = %s"
            " AND entity_id LIKE %s",
            ("stream:sales.v1.reversed", f"{sale_id}:%"),
        ).fetchone()[0],
    )


@pytest.fixture
def make_sale(client, token, owner):
    """A recorded two-line sale made just now, so both windows are open."""

    def make(
        quantities: tuple[int, int] = (3, 2),
        *,
        age: timedelta = timedelta(seconds=5),
        store_id: int | None = None,
    ) -> dict:
        rows = owner.execute(
            "SELECT i.store_id, i.product_id FROM public.inventory AS i"
            " JOIN public.product AS p USING (product_id)"
            " WHERE p.is_active AND i.quantity_on_hand >= 10"
            " AND (%s::smallint IS NULL OR i.store_id = %s)"
            " ORDER BY i.store_id, i.product_id",
            (store_id, store_id),
        ).fetchall()
        store, first = rows[0]
        second = next(
            product for found, product in rows if found == store and product != first
        )
        customer = owner.execute(
            "SELECT customer_id FROM public.customer ORDER BY registered_on,"
            " customer_id LIMIT 1"
        ).fetchone()[0]
        channel = owner.execute(
            "SELECT min(channel_id) FROM public.channel"
        ).fetchone()[0]
        body = {
            "customer_id": str(customer),
            "store_id": store,
            "channel_id": channel,
            "occurred_at": (datetime.now(UTC) - age).isoformat(),
            "lines": [
                {"product_id": first, "quantity": quantities[0], "unit_price": "10.00"},
                {"product_id": second, "quantity": quantities[1], "unit_price": "4.25"},
            ],
        }
        response = client.post(
            "/v1/sales", json=body, headers=idempotency_headers(token("ADMIN"))
        )
        assert response.status_code == 201, response.get_json()
        sale = response.get_json()
        return {
            "sale_id": sale["sale_id"],
            "store_id": store,
            "customer_id": str(customer),
            "lines": body["lines"],
        }

    return make


def cancellation(sale: dict, reason: str = "Duplicate scan at the till") -> dict:
    return {"sale_id": sale["sale_id"], "kind": "CANCELLATION", "reason": reason}


def return_of(sale: dict, *units: int | None, **line) -> dict:
    lines = []
    for index, quantity in enumerate(units):
        if quantity is None:
            continue
        lines.append(
            {
                "product_id": sale["lines"][index]["product_id"],
                "quantity": quantity,
                "reason": "Customer changed their mind",
                **line,
            }
        )
    return {"sale_id": sale["sale_id"], "kind": "RETURN", "lines": lines}


# ---- a whole cancellation ----------------------------------------------------------


def test_cancellation_records_restores_stock_and_queues_the_event(
    client, token, owner, make_sale
):
    sale = make_sale()
    before = [
        on_hand(owner, sale["store_id"], line["product_id"]) for line in sale["lines"]
    ]
    sale_before = client.get(
        f"/v1/sales/{sale['sale_id']}", headers=token("ADMIN")
    ).get_json()
    key = str(uuid.uuid4())
    response = client.post(
        "/v1/sale-reversals",
        json=cancellation(sale),
        headers=idempotency_headers(token("STORE_MANAGER"), key),
    )
    assert response.status_code == 201, response.get_json()
    created = response.get_json()
    assert (
        response.headers["Location"] == f"/v1/sale-reversals/{created['reversal_id']}"
    )
    assert "Idempotent-Replayed" not in response.headers
    assert created["sale_id"] == sale["sale_id"]
    assert created["kind"] == "CANCELLATION"
    assert created["reason"] == "Duplicate scan at the till"
    assert created["customer_id"] == sale["customer_id"]
    assert created["store_id"] == sale["store_id"]
    assert created["amount"] == "38.50"
    assert created["net_total"] == "0.00"
    assert [
        (
            line["product_id"],
            line["quantity"],
            line["remaining_quantity"],
            line["unit_price"],
            line["reason"],
            line["resellable"],
        )
        for line in created["lines"]
    ] == [
        (sale["lines"][0]["product_id"], 3, 0, "10.00", None, True),
        (sale["lines"][1]["product_id"], 2, 0, "4.25", None, True),
    ]
    # Stock came back through the stock contract: one RESTOCK per line.
    after = [
        on_hand(owner, sale["store_id"], line["product_id"]) for line in sale["lines"]
    ]
    assert after == [before[0] + 3, before[1] + 2]
    assert owner.execute(
        "SELECT product_id, quantity_delta, quantity_before, quantity_after"
        " FROM stock.stock_movement WHERE movement_type = 'RESTOCK'"
        " AND reversal_id = %s ORDER BY product_id",
        (created["reversal_id"],),
    ).fetchall() == [
        (sale["lines"][0]["product_id"], 3, before[0], before[0] + 3),
        (sale["lines"][1]["product_id"], 2, before[1], before[1] + 2),
    ]
    # The sale is recorded beside, never edited: quantity, price and total stay.
    sale_after = client.get(
        f"/v1/sales/{sale['sale_id']}", headers=token("ADMIN")
    ).get_json()
    assert sale_after == sale_before
    assert returned(owner, sale["sale_id"]) == {
        sale["lines"][0]["product_id"]: 3,
        sale["lines"][1]["product_id"]: 2,
    }
    assert counts(owner, sale["sale_id"]) == (1, 2, 2, 1)
    assert owner.execute(
        "SELECT event_type, entity_id FROM sales_events.outbox WHERE stream = %s"
        " AND entity_id LIKE %s",
        ("stream:sales.v1.reversed", f"{sale['sale_id']}:%"),
    ).fetchall() == [("INSERT", f"{sale['sale_id']}:{created['reversal_id']}")]
    assert owner.execute(
        "SELECT recorded_by::text, idempotency_key::text FROM sales.sale_reversal"
        " WHERE reversal_id = %s",
        (created["reversal_id"],),
    ).fetchone() == (
        owner.execute(
            "SELECT user_id::text FROM public.app_user AS u JOIN public.role AS r"
            " USING (role_id) WHERE r.code = 'STORE_MANAGER' AND u.is_active"
            " ORDER BY user_id LIMIT 1"
        ).fetchone()[0],
        key,
    )
    # The sale no longer counts as a purchase, for the web or for a service.
    assert owner.execute(
        "SELECT count(*) FROM public.net_sale WHERE transaction_id = %s",
        (sale["sale_id"],),
    ).fetchone() == (0,)
    assert owner.execute(
        "SELECT count(*) FROM api_sales.net_purchase_line_v1 WHERE transaction_id = %s",
        (sale["sale_id"],),
    ).fetchone() == (0,)
    assert owner.execute(
        "SELECT count(*) FROM api_sales.purchase_line_v1 WHERE transaction_id = %s",
        (sale["sale_id"],),
    ).fetchone() == (2,)


def test_cancellation_in_xml_validates_against_the_xsd(app, client, token, make_sale):
    sale = make_sale()
    response = post_xml(
        client, cancellation(sale), idempotency_headers(token("SALES_ASSOCIATE"))
    )
    assert response.status_code == 201, response.data
    assert response.mimetype == "application/xml"
    document = etree.fromstring(response.data)
    app.extensions["mosaiq_kit"].contract.xml_schema.assertValid(document)
    assert document.tag == f"{{{NS}}}SaleReversal"
    assert document.findtext(f"{{{NS}}}kind") == "CANCELLATION"
    assert document.findtext(f"{{{NS}}}net_total") == "0.00"
    # A cancellation's lines carry no reason: the element is absent in XML.
    assert all(
        line.find(f"{{{NS}}}reason") is None
        for line in document.findall(f"{{{NS}}}lines")
    )


# ---- idempotency -------------------------------------------------------------------


def test_a_replay_returns_the_same_reversal_and_changes_nothing(
    client, token, owner, make_sale
):
    sale = make_sale()
    headers = idempotency_headers(token("STORE_MANAGER"))
    first = client.post("/v1/sale-reversals", json=cancellation(sale), headers=headers)
    assert first.status_code == 201
    state = (
        counts(owner, sale["sale_id"]),
        returned(owner, sale["sale_id"]),
        [
            on_hand(owner, sale["store_id"], line["product_id"])
            for line in sale["lines"]
        ],
    )
    replay = client.post("/v1/sale-reversals", json=cancellation(sale), headers=headers)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert "Location" not in replay.headers
    assert replay.get_json() == first.get_json()
    xml_replay = post_xml(client, cancellation(sale), headers)
    assert xml_replay.status_code == 200
    assert xml_replay.headers["Idempotent-Replayed"] == "true"
    assert (
        counts(owner, sale["sale_id"]),
        returned(owner, sale["sale_id"]),
        [
            on_hand(owner, sale["store_id"], line["product_id"])
            for line in sale["lines"]
        ],
    ) == state


def test_a_replay_of_a_return_keeps_the_figures_of_the_first_answer(
    client, token, make_sale
):
    sale = make_sale()
    headers = idempotency_headers(token("STORE_MANAGER"))
    first = client.post("/v1/sale-reversals", json=return_of(sale, 1), headers=headers)
    assert first.status_code == 201
    later = client.post(
        "/v1/sale-reversals",
        json=return_of(sale, 2, 1),
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert later.status_code == 201
    replay = client.post("/v1/sale-reversals", json=return_of(sale, 1), headers=headers)
    assert replay.status_code == 200
    # Remaining units and net total are as they were after the first reversal,
    # not as they are now.
    assert replay.get_json() == first.get_json()
    assert replay.get_json()["lines"][0]["remaining_quantity"] == 2
    assert replay.get_json()["net_total"] == "28.50"
    assert later.get_json()["net_total"] == "4.25"
    fetched = client.get(
        f"/v1/sale-reversals/{first.get_json()['reversal_id']}",
        headers=token("AUDITOR"),
    )
    assert fetched.get_json() == first.get_json()


def test_the_same_key_with_another_body_is_409_and_records_nothing(
    client, token, owner, make_sale
):
    sale = make_sale()
    headers = idempotency_headers(token("STORE_MANAGER"))
    assert (
        client.post(
            "/v1/sale-reversals", json=return_of(sale, 1), headers=headers
        ).status_code
        == 201
    )
    other = client.post("/v1/sale-reversals", json=return_of(sale, 2), headers=headers)
    assert other.status_code == 409
    assert other.get_json()["code"] == "idempotency_key_reused"
    assert counts(owner, sale["sale_id"])[0] == 1
    # Another caller using the same key is a different request too.
    stranger = client.post(
        "/v1/sale-reversals",
        json=return_of(sale, 1),
        headers={**token("ADMIN"), "Idempotency-Key": headers["Idempotency-Key"]},
    )
    assert stranger.status_code == 409


def test_a_key_used_for_a_sale_is_unrelated(client, token, make_sale):
    sale = make_sale()
    sale_key = str(uuid.uuid4())
    # The sale's own key was spent on POST /v1/sales; here it opens a reversal.
    response = client.post(
        "/v1/sale-reversals",
        json=cancellation(sale),
        headers=idempotency_headers(token("STORE_MANAGER"), sale_key),
    )
    assert response.status_code == 201


@pytest.mark.parametrize("header", [None, "not-a-uuid"])
def test_the_key_is_required(client, token, make_sale, header):
    sale = make_sale()
    headers = token("STORE_MANAGER")
    if header:
        headers = {**headers, "Idempotency-Key": header}
    response = client.post(
        "/v1/sale-reversals", json=cancellation(sale), headers=headers
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "idempotency_key_required"


def test_a_refusal_does_not_remember_the_key(client, token, owner, make_sale):
    sale = make_sale()
    headers = idempotency_headers(token("STORE_MANAGER"))
    refused = client.post(
        "/v1/sale-reversals", json=return_of(sale, 99), headers=headers
    )
    assert refused.status_code == 409
    accepted = client.post(
        "/v1/sale-reversals", json=return_of(sale, 1), headers=headers
    )
    assert accepted.status_code == 201


def test_concurrent_duplicates_record_one_reversal_and_one_restock(
    app, token, owner, make_sale
):
    sale = make_sale()
    headers = idempotency_headers(token("STORE_MANAGER"))
    before = [
        on_hand(owner, sale["store_id"], line["product_id"]) for line in sale["lines"]
    ]
    barrier = threading.Barrier(2)
    results = []

    def send() -> None:
        with app.test_client() as thread_client:
            barrier.wait()
            response = thread_client.post(
                "/v1/sale-reversals", json=cancellation(sale), headers=headers
            )
            results.append(
                (response.status_code, response.headers.get("Idempotent-Replayed"))
            )

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    assert sorted(results) == [(200, "true"), (201, None)]
    assert counts(owner, sale["sale_id"]) == (1, 2, 2, 1)
    assert [
        on_hand(owner, sale["store_id"], line["product_id"]) for line in sale["lines"]
    ] == [before[0] + 3, before[1] + 2]


# ---- partial returns ---------------------------------------------------------------


def test_a_partial_return_counts_net_units_and_restores_only_resellable_goods(
    client, token, owner, make_sale
):
    sale = make_sale()
    first, second = (line["product_id"] for line in sale["lines"])
    before = [on_hand(owner, sale["store_id"], product) for product in (first, second)]
    body = {
        "sale_id": sale["sale_id"],
        "kind": "RETURN",
        "lines": [
            {
                "product_id": first,
                "quantity": 1,
                "reason": "Packaging damaged",
                "resellable": False,
            },
            {"product_id": second, "quantity": 2, "reason": "Wrong size"},
        ],
    }
    response = client.post(
        "/v1/sale-reversals",
        json=body,
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert response.status_code == 201, response.get_json()
    created = response.get_json()
    assert created["kind"] == "RETURN" and created["reason"] is None
    assert created["amount"] == "18.50"
    assert created["net_total"] == "20.00"
    assert [
        (
            line["product_id"],
            line["quantity"],
            line["remaining_quantity"],
            line["reason"],
            line["resellable"],
        )
        for line in created["lines"]
    ] == [(first, 1, 2, "Packaging damaged", False), (second, 2, 0, "Wrong size", True)]
    # Nothing came back for the line that cannot be resold; the other did.
    assert [
        on_hand(owner, sale["store_id"], product) for product in (first, second)
    ] == [
        before[0],
        before[1] + 2,
    ]
    assert counts(owner, sale["sale_id"]) == (1, 2, 1, 1)
    # Both lines still reduce what the customer bought.
    assert returned(owner, sale["sale_id"]) == {first: 1, second: 2}
    net = owner.execute(
        "SELECT net_units, net_total, total FROM public.net_sale"
        " WHERE transaction_id = %s",
        (sale["sale_id"],),
    ).fetchone()
    assert (net[0], str(net[1]), str(net[2])) == (2, "20.00", "38.50")
    assert owner.execute(
        "SELECT product_id, net_quantity, net_amount::text"
        " FROM api_sales.net_purchase_line_v1 WHERE transaction_id = %s",
        (sale["sale_id"],),
    ).fetchall() == [(first, 2, "20.00")]


def test_a_return_in_xml_validates_against_the_xsd(app, client, token, make_sale):
    sale = make_sale()
    response = post_xml(
        client,
        return_of(sale, 1, 1, resellable=False),
        idempotency_headers(token("STORE_MANAGER")),
    )
    assert response.status_code == 201, response.data
    document = etree.fromstring(response.data)
    app.extensions["mosaiq_kit"].contract.xml_schema.assertValid(document)
    assert [
        line.findtext(f"{{{NS}}}resellable")
        for line in document.findall(f"{{{NS}}}lines")
    ] == ["false", "false"]
    assert document.find(f"{{{NS}}}reason") is None


def test_returns_repeat_until_every_unit_is_back_and_then_refuse(
    client, token, owner, make_sale
):
    sale = make_sale()
    for units in ((1, 0), (1, 1), (1, 1)):
        response = client.post(
            "/v1/sale-reversals",
            json=return_of(sale, *(u or None for u in units)),
            headers=idempotency_headers(token("STORE_MANAGER")),
        )
        assert response.status_code == 201, response.get_json()
    assert returned(owner, sale["sale_id"]) == {
        sale["lines"][0]["product_id"]: 3,
        sale["lines"][1]["product_id"]: 2,
    }
    state = (counts(owner, sale["sale_id"]), returned(owner, sale["sale_id"]))
    over = client.post(
        "/v1/sale-reversals",
        json=return_of(sale, 1),
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert over.status_code == 409
    assert over.get_json()["code"] == "quantity_exceeds_remaining"
    assert (counts(owner, sale["sale_id"]), returned(owner, sale["sale_id"])) == state


@pytest.mark.parametrize("fmt", ["json", "xml"])
def test_returning_more_than_remains_is_409_and_nothing_changes(
    client, token, owner, make_sale, fmt
):
    sale = make_sale()
    # A first return leaves 2 of the first product; the next asks for 3, after a
    # valid second line, so the refusal must undo nothing it did not do.
    assert (
        client.post(
            "/v1/sale-reversals",
            json=return_of(sale, 1),
            headers=idempotency_headers(token("STORE_MANAGER")),
        ).status_code
        == 201
    )
    state = (
        counts(owner, sale["sale_id"]),
        returned(owner, sale["sale_id"]),
        [
            on_hand(owner, sale["store_id"], line["product_id"])
            for line in sale["lines"]
        ],
        owner.execute("SELECT count(*) FROM stock.stock_movement").fetchone()[0],
    )
    body = return_of(sale, 3, 1)
    headers = idempotency_headers(token("STORE_MANAGER"))
    if fmt == "json":
        response = client.post("/v1/sale-reversals", json=body, headers=headers)
        assert response.status_code == 409
        error = response.get_json()
        assert error["code"] == "quantity_exceeds_remaining"
        assert error["details"] == [
            {"field": "lines[0].quantity", "code": "exceeds_remaining"}
        ]
    else:
        response = post_xml(client, body, headers)
        assert response.status_code == 409
        assert response.mimetype == "application/xml"
        assert b"quantity_exceeds_remaining" in response.data
    assert (
        counts(owner, sale["sale_id"]),
        returned(owner, sale["sale_id"]),
        [
            on_hand(owner, sale["store_id"], line["product_id"])
            for line in sale["lines"]
        ],
        owner.execute("SELECT count(*) FROM stock.stock_movement").fetchone()[0],
    ) == state


def test_a_cancellation_after_a_return_and_a_return_after_a_cancellation(
    client, token, owner, make_sale
):
    returned_sale = make_sale()
    assert (
        client.post(
            "/v1/sale-reversals",
            json=return_of(returned_sale, 1),
            headers=idempotency_headers(token("STORE_MANAGER")),
        ).status_code
        == 201
    )
    state = counts(owner, returned_sale["sale_id"])
    refused = client.post(
        "/v1/sale-reversals",
        json=cancellation(returned_sale),
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert refused.status_code == 409
    assert refused.get_json()["code"] == "sale_already_reversed"
    assert counts(owner, returned_sale["sale_id"]) == state

    cancelled = make_sale()
    assert (
        client.post(
            "/v1/sale-reversals",
            json=cancellation(cancelled),
            headers=idempotency_headers(token("STORE_MANAGER")),
        ).status_code
        == 201
    )
    for body in (return_of(cancelled, 1), cancellation(cancelled)):
        again = client.post(
            "/v1/sale-reversals",
            json=body,
            headers=idempotency_headers(token("ADMIN")),
        )
        assert again.status_code == 409
        assert again.get_json()["code"] in {
            "quantity_exceeds_remaining",
            "sale_already_reversed",
        }


def test_concurrent_returns_of_the_last_units_succeed_once(
    app, token, owner, make_sale
):
    sale = make_sale()
    barrier = threading.Barrier(2)
    results = []

    def send() -> None:
        with app.test_client() as thread_client:
            barrier.wait()
            response = thread_client.post(
                "/v1/sale-reversals",
                json=return_of(sale, 3),
                headers=idempotency_headers(token("STORE_MANAGER")),
            )
            results.append(response.status_code)

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    assert sorted(results) == [201, 409]
    assert returned(owner, sale["sale_id"])[sale["lines"][0]["product_id"]] == 3
    assert counts(owner, sale["sale_id"]) == (1, 1, 1, 1)


def test_a_cancellation_reverses_a_sale_the_csv_path_loaded(
    client, token, owner, retail_url, make_sale
):
    template = make_sale((1, 1))
    owner.execute(
        "DELETE FROM public.transaction WHERE transaction_id = %s",
        (template["sale_id"],),
    )
    line = template["lines"][0]
    source = "test-csv-" + uuid.uuid4().hex
    with psycopg.connect(retail_url) as connection:
        sale_id = connection.execute(
            "INSERT INTO public.transaction (source_transaction_id, customer_id,"
            " store_id, channel_id, occurred_at, total, data_origin)"
            " VALUES (%s, %s, %s, 1, now() - interval '1 minute', 0, 'OBSERVED')"
            " RETURNING transaction_id",
            (source, template["customer_id"], template["store_id"]),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO public.transaction_line (transaction_id, product_id, quantity,"
            " unit_price) VALUES (%s, %s, 2, 10.00)",
            (sale_id, line["product_id"]),
        )
        connection.execute(
            "UPDATE public.transaction SET total = 20.00 WHERE transaction_id = %s",
            (sale_id,),
        )
    before = on_hand(owner, template["store_id"], line["product_id"])
    response = client.post(
        "/v1/sale-reversals",
        json={"sale_id": sale_id, "kind": "CANCELLATION", "reason": "Entered twice"},
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["net_total"] == "0.00"
    assert on_hand(owner, template["store_id"], line["product_id"]) == before + 2


# ---- who may do what ---------------------------------------------------------------


def test_a_seller_may_cancel_but_not_return(client, token, owner, make_sale):
    sale = make_sale()
    seller = token("SALES_ASSOCIATE")
    refused = client.post(
        "/v1/sale-reversals",
        json=return_of(sale, 1),
        headers=idempotency_headers(seller),
    )
    assert refused.status_code == 403
    assert refused.get_json()["code"] == "forbidden_role"
    xml = post_xml(client, return_of(sale, 1), idempotency_headers(seller))
    assert xml.status_code == 403 and b"forbidden_role" in xml.data
    assert counts(owner, sale["sale_id"]) == (0, 0, 0, 0)
    allowed = client.post(
        "/v1/sale-reversals",
        json=cancellation(sale),
        headers=idempotency_headers(seller),
    )
    assert allowed.status_code == 201


@pytest.mark.parametrize(
    "role", ["ANALYST", "MARKETING", "INVENTORY_PLANNER", "AUDITOR", "CUSTOMER"]
)
def test_other_roles_cannot_record_a_reversal(client, token, owner, make_sale, role):
    sale = make_sale()
    response = client.post(
        "/v1/sale-reversals",
        json=cancellation(sale),
        headers=idempotency_headers(token(role)),
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "forbidden_role"
    assert counts(owner, sale["sale_id"]) == (0, 0, 0, 0)


@pytest.mark.parametrize("role", ["ADMIN", "STORE_MANAGER"])
def test_admin_and_manager_record_both_kinds(client, token, make_sale, role):
    for body in (cancellation, lambda s: return_of(s, 1)):
        sale = make_sale()
        response = client.post(
            "/v1/sale-reversals",
            json=body(sale),
            headers=idempotency_headers(token(role)),
        )
        assert response.status_code == 201


def test_a_reversal_needs_a_token(client, make_sale):
    sale = make_sale()
    response = client.post(
        "/v1/sale-reversals",
        json=cancellation(sale),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 401


# ---- refusals ----------------------------------------------------------------------


def _refusal(client, token, owner, sale, body, status, code, field=None, *, xml=False):
    state = counts(owner, sale["sale_id"])
    headers = idempotency_headers(token("STORE_MANAGER"))
    response = (
        post_xml(client, body, headers)
        if xml
        else client.post("/v1/sale-reversals", json=body, headers=headers)
    )
    assert response.status_code == status, response.data
    if xml:
        assert response.mimetype == "application/xml"
        assert f"<code>{code}</code>".encode() in response.data
    else:
        error = response.get_json()
        assert error["code"] == code
        if field:
            assert error["details"][0]["field"] == field
    assert counts(owner, sale["sale_id"]) == state


@pytest.mark.parametrize("xml", [False, True])
def test_refusals_of_the_request_alone(client, token, owner, make_sale, xml):
    sale = make_sale()
    sid = sale["sale_id"]
    first = sale["lines"][0]["product_id"]
    line = {"product_id": first, "quantity": 1, "reason": "Wrong size"}
    cases = [
        (
            {"sale_id": sid, "kind": "RETURN", "lines": []},
            422,
            "no_return_lines",
            "lines",
        ),
        (
            {"sale_id": sid, "kind": "CANCELLATION", "reason": "x", "lines": [line]},
            422,
            "unexpected_lines",
            "lines",
        ),
        ({"sale_id": sid, "kind": "CANCELLATION"}, 422, "reason_required", "reason"),
        (
            {"sale_id": sid, "kind": "CANCELLATION", "reason": "   "},
            422,
            "reason_required",
            "reason",
        ),
        (
            {
                "sale_id": sid,
                "kind": "RETURN",
                "lines": [{k: v for k, v in line.items() if k != "reason"}],
            },
            422,
            "reason_required",
            "lines[0].reason",
        ),
        (
            {"sale_id": sid, "kind": "RETURN", "lines": [{**line, "quantity": 0}]},
            422,
            "invalid_quantity",
            "lines[0].quantity",
        ),
        (
            {"sale_id": sid, "kind": "RETURN", "lines": [{**line, "quantity": -1}]},
            422,
            "invalid_quantity",
            "lines[0].quantity",
        ),
        (
            {"sale_id": sid, "kind": "RETURN", "lines": [{**line, "quantity": 2**31}]},
            422,
            "invalid_quantity",
            "lines[0].quantity",
        ),
        (
            {
                "sale_id": sid,
                "kind": "RETURN",
                "lines": [line, {**line, "quantity": 2}],
            },
            409,
            "duplicate_product_line",
            "lines[1].product_id",
        ),
        (
            {"sale_id": sid, "kind": "RETURN", "reason": "x", "lines": [line]},
            422,
            "invalid_body",
            "reason",
        ),
    ]
    for body, status, code, field in cases:
        _refusal(client, token, owner, sale, body, status, code, field, xml=xml)


def test_refusals_that_need_the_sale(client, token, owner, make_sale):
    sale = make_sale()
    _refusal(
        client,
        token,
        owner,
        sale,
        {"sale_id": 2**62, "kind": "CANCELLATION", "reason": "x"},
        422,
        "unknown_sale",
        "sale_id",
    )
    _refusal(
        client,
        token,
        owner,
        sale,
        {"sale_id": 2**63, "kind": "CANCELLATION", "reason": "x"},
        422,
        "unknown_sale",
        "sale_id",
    )
    _refusal(
        client,
        token,
        owner,
        sale,
        {
            "sale_id": sale["sale_id"],
            "kind": "RETURN",
            "lines": [{"product_id": 2**30, "quantity": 1, "reason": "x"}],
        },
        422,
        "unknown_sale_line",
        "lines[0].product_id",
    )


def test_a_body_that_breaks_the_schema_is_invalid_body(client, token, make_sale):
    sale = make_sale()
    headers = idempotency_headers(token("STORE_MANAGER"))
    for body in (
        {"sale_id": sale["sale_id"], "kind": "VOID", "reason": "x"},
        {"sale_id": sale["sale_id"]},
        {"kind": "CANCELLATION", "reason": "x"},
        {"sale_id": sale["sale_id"], "kind": "CANCELLATION", "reason": "x" * 201},
        {"sale_id": sale["sale_id"], "kind": "CANCELLATION", "reason": "x", "extra": 1},
    ):
        response = client.post("/v1/sale-reversals", json=body, headers=headers)
        assert response.status_code == 422, body
        assert response.get_json()["code"] == "invalid_body"


def test_an_unsupported_media_type_and_a_too_large_body(client, token):
    headers = idempotency_headers(token("STORE_MANAGER"))
    plain = client.post(
        "/v1/sale-reversals", data="x", content_type="text/plain", headers=headers
    )
    assert plain.status_code == 415
    large = client.post(
        "/v1/sale-reversals",
        data=b"{" + b" " * 70000 + b"}",
        content_type="application/json",
        headers=headers,
    )
    assert large.status_code == 413


# ---- time limits -------------------------------------------------------------------


def test_the_windows_are_configuration_defaults(
    client, token, owner, make_sale, monkeypatch
):
    # A sale from yesterday cannot be cancelled by default, but can be returned.
    yesterday = make_sale(age=timedelta(days=1, minutes=1))
    refused = client.post(
        "/v1/sale-reversals",
        json=cancellation(yesterday),
        headers=idempotency_headers(token("ADMIN")),
    )
    assert refused.status_code == 409
    assert refused.get_json()["code"] == "reversal_window_closed"
    assert refused.get_json()["details"] == [{"field": "kind", "code": "window_closed"}]
    assert counts(owner, yesterday["sale_id"]) == (0, 0, 0, 0)
    assert (
        client.post(
            "/v1/sale-reversals",
            json=return_of(yesterday, 1),
            headers=idempotency_headers(token("STORE_MANAGER")),
        ).status_code
        == 201
    )
    # A sale from 31 days ago cannot be returned either, in either format.
    old = make_sale(age=timedelta(days=31))
    for send in (
        lambda h: client.post("/v1/sale-reversals", json=return_of(old, 1), headers=h),
        lambda h: post_xml(client, return_of(old, 1), h),
    ):
        closed = send(idempotency_headers(token("ADMIN")))
        assert closed.status_code == 409
        assert b"reversal_window_closed" in closed.data
    assert counts(owner, old["sale_id"]) == (0, 0, 0, 0)
    # The windows are configuration, not requirements.
    monkeypatch.setenv("SALES_RETURN_WINDOW_DAYS", "45")
    monkeypatch.setenv("SALES_CANCELLATION_WINDOW_DAYS", "40")
    assert (
        client.post(
            "/v1/sale-reversals",
            json=return_of(old, 1),
            headers=idempotency_headers(token("ADMIN")),
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/v1/sale-reversals",
            json=cancellation(make_sale(age=timedelta(days=10))),
            headers=idempotency_headers(token("ADMIN")),
        ).status_code
        == 201
    )


def test_a_replay_is_answered_after_the_window_closes(
    client, token, make_sale, monkeypatch
):
    sale = make_sale()
    headers = idempotency_headers(token("ADMIN"))
    first = client.post("/v1/sale-reversals", json=return_of(sale, 1), headers=headers)
    assert first.status_code == 201
    monkeypatch.setenv("SALES_RETURN_WINDOW_DAYS", "0")
    # The same instant, the same request: the first answer, not a refusal.
    monkeypatch.setattr(
        "app.reversal.window_open", lambda kind, occurred_at, now=None: False
    )
    replay = client.post("/v1/sale-reversals", json=return_of(sale, 1), headers=headers)
    assert replay.status_code == 200


# ---- one transaction ---------------------------------------------------------------


def test_a_missing_stock_record_refuses_the_whole_reversal(
    client, token, owner, make_sale
):
    sale = make_sale()
    second = sale["lines"][1]["product_id"]
    owner.execute(
        "DELETE FROM public.inventory WHERE store_id = %s AND product_id = %s",
        (sale["store_id"], second),
    )
    first_before = on_hand(owner, sale["store_id"], sale["lines"][0]["product_id"])
    movements = owner.execute("SELECT count(*) FROM stock.stock_movement").fetchone()[0]
    response = client.post(
        "/v1/sale-reversals",
        json=cancellation(sale),
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "no_stock_record"
    assert error["details"] == [
        {"field": "lines[1].product_id", "code": "no_stock_record"}
    ]
    # The first line's restock, the reversal, its lines, the running totals and
    # the event were all rolled back.
    assert (
        on_hand(owner, sale["store_id"], sale["lines"][0]["product_id"]) == first_before
    )
    assert counts(owner, sale["sale_id"]) == (0, 0, 0, 0)
    assert set(returned(owner, sale["sale_id"]).values()) == {0}
    assert (
        owner.execute("SELECT count(*) FROM stock.stock_movement").fetchone()[0]
        == movements
    )


def test_a_failure_after_the_writes_rolls_everything_back_and_publishes_nothing(
    client, token, owner, make_sale, monkeypatch
):
    sale = make_sale()
    from app import store

    def explode(*args, **kwargs):
        raise psycopg.errors.InternalError_("the connection broke after the writes")

    original = store.get_reversal
    monkeypatch.setattr(store, "get_reversal", explode)
    response = client.post(
        "/v1/sale-reversals",
        json=cancellation(sale),
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    monkeypatch.setattr(store, "get_reversal", original)
    assert response.status_code == 500
    assert counts(owner, sale["sale_id"]) == (0, 0, 0, 0)
    assert set(returned(owner, sale["sale_id"]).values()) == {0}


def test_a_committed_reversal_reaches_the_stream_once_and_a_refused_one_never(
    build_app, token, owner, make_sale, redis_connection
):
    stream = "stream:sales.v1.reversed"
    redis_connection.delete(stream)
    app = build_app(relay=True)
    client = app.test_client()
    sale = make_sale()
    refused = client.post(
        "/v1/sale-reversals",
        json=return_of(sale, 99),
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert refused.status_code == 409
    created = client.post(
        "/v1/sale-reversals",
        json=return_of(sale, 1),
        headers=idempotency_headers(token("STORE_MANAGER")),
    )
    assert created.status_code == 201
    entity = f"{sale['sale_id']}:{created.get_json()['reversal_id']}"
    deadline = time.monotonic() + 15
    entries = []
    while time.monotonic() < deadline and not entries:
        entries = [
            e for e in redis_connection.xrange(stream) if e[1]["entity_id"] == entity
        ]
        time.sleep(0.05)
    assert len(entries) == 1
    fields = entries[0][1]
    assert set(fields) == {"event_id", "event_type", "entity_id", "occurred_at", "v"}
    assert fields["event_type"] == "INSERT" and fields["v"] == "1"
    assert [
        e
        for e in redis_connection.xrange(stream)
        if e[1]["entity_id"].startswith(f"{sale['sale_id']}:")
    ] == entries


# ---- reads -------------------------------------------------------------------------


def test_reads_filter_page_and_negotiate(app, client, token, owner, make_sale):
    sale = make_sale()
    other = make_sale()
    one = client.post(
        "/v1/sale-reversals",
        json=return_of(sale, 1),
        headers=idempotency_headers(token("STORE_MANAGER")),
    ).get_json()
    two = client.post(
        "/v1/sale-reversals",
        json=cancellation(other),
        headers=idempotency_headers(token("STORE_MANAGER")),
    ).get_json()
    auditor = token("AUDITOR")

    listing = client.get("/v1/sale-reversals?size=100", headers=auditor).get_json()
    ids = [item["reversal_id"] for item in listing["sale_reversals"]]
    assert ids.index(two["reversal_id"]) < ids.index(one["reversal_id"])
    assert listing["page_info"]["total"] == len(ids)

    def only(query: str) -> list[int]:
        response = client.get("/v1/sale-reversals?" + query, headers=auditor)
        assert response.status_code == 200, response.get_json()
        return [item["reversal_id"] for item in response.get_json()["sale_reversals"]]

    assert only(f"sale_id={sale['sale_id']}") == [one["reversal_id"]]
    assert only(f"sale_id={other['sale_id']}&kind=CANCELLATION") == [two["reversal_id"]]
    assert only(f"sale_id={other['sale_id']}&kind=RETURN") == []
    assert only(
        f"customer_id={sale['customer_id']}&store_id={sale['store_id']}&sale_id={sale['sale_id']}"
    ) == [one["reversal_id"]]
    assert only(f"store_id=32000&sale_id={sale['sale_id']}") == []
    now = datetime.now(UTC)
    start = (now - timedelta(minutes=5)).isoformat().replace("+", "%2B")
    end = (now + timedelta(minutes=5)).isoformat().replace("+", "%2B")
    window = f"from={start}&to={end}"
    assert one["reversal_id"] in only(window + "&size=100")
    assert only(f"sale_id={sale['sale_id']}&page=2&size=1") == []

    fetched = client.get(f"/v1/sale-reversals/{one['reversal_id']}", headers=auditor)
    assert fetched.status_code == 200 and fetched.get_json() == one
    missing = client.get("/v1/sale-reversals/999999999", headers=auditor)
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "sale_reversal_not_found"

    xml_list = client.get(
        f"/v1/sale-reversals?sale_id={sale['sale_id']}", headers={**auditor, **XML}
    )
    assert xml_list.status_code == 200
    document = etree.fromstring(xml_list.data)
    app.extensions["mosaiq_kit"].contract.xml_schema.assertValid(document)
    assert document.tag == f"{{{NS}}}SaleReversals"
    xml_one = client.get(
        f"/v1/sale-reversals/{one['reversal_id']}", headers={**auditor, **XML}
    )
    app.extensions["mosaiq_kit"].contract.xml_schema.assertValid(
        etree.fromstring(xml_one.data)
    )
    xml_missing = client.get("/v1/sale-reversals/999999999", headers={**auditor, **XML})
    assert (
        xml_missing.status_code == 404
        and b"sale_reversal_not_found" in xml_missing.data
    )


@pytest.mark.parametrize(
    "query",
    [
        "sale_id=abc",
        "sale_id=0",
        "customer_id=nope",
        "store_id=-1",
        "kind=VOID",
        "from=2026-01-01",
        "from=2026-02-01T00:00:00Z&to=2026-01-01T00:00:00Z",
    ],
)
def test_a_malformed_filter_is_422(client, token, query):
    response = client.get("/v1/sale-reversals?" + query, headers=token("AUDITOR"))
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_query"


@pytest.mark.parametrize(
    "role",
    ["ADMIN", "ANALYST", "STORE_MANAGER", "MARKETING", "AUDITOR", "SALES_ASSOCIATE"],
)
def test_reads_are_open_to_the_listed_roles_only(client, token, role):
    assert client.get("/v1/sale-reversals", headers=token(role)).status_code == 200
    assert client.get("/v1/sale-reversals/1", headers=token(role)).status_code in {
        200,
        404,
    }


@pytest.mark.parametrize("role", ["INVENTORY_PLANNER", "CUSTOMER"])
def test_reads_refuse_the_other_roles(client, token, role):
    for path in ("/v1/sale-reversals", "/v1/sale-reversals/1"):
        response = client.get(path, headers=token(role))
        assert response.status_code == 403
        assert response.get_json()["code"] == "forbidden_role"


# ---- the database guarantees, without the service ----------------------------------


@pytest.fixture
def a_user(owner) -> str:
    return str(
        owner.execute(
            "SELECT user_id FROM public.app_user ORDER BY user_id LIMIT 1"
        ).fetchone()[0]
    )


def _insert_reversal(connection, sale_id, user, kind="RETURN", reason=None):
    return connection.execute(
        "INSERT INTO sales.sale_reversal (transaction_id, kind, reason,"
        " idempotency_key, request_sha256, recorded_by)"
        " VALUES (%s, %s, %s, gen_random_uuid(), decode(repeat('00', 32), 'hex'), %s)"
        " RETURNING reversal_id",
        (sale_id, kind, reason, user),
    ).fetchone()[0]


def _insert_line(connection, reversal_id, sale_id, product_id, quantity, reason="x"):
    connection.execute(
        "INSERT INTO sales.sale_reversal_line (reversal_id, transaction_id,"
        " product_id, quantity, reason) VALUES (%s, %s, %s, %s, %s)",
        (reversal_id, sale_id, product_id, quantity, reason),
    )


def test_the_sum_of_a_sales_reversals_can_never_exceed_the_sale(
    owner_url, service_url, owner, make_sale, a_user
):
    sale = make_sale()
    product = sale["lines"][0]["product_id"]
    sold = sale["lines"][0]["quantity"]
    # A direct INSERT past what was sold, by the owner and by svc_sales.
    for url in (owner_url, service_url):
        with psycopg.connect(url) as connection:
            reversal = _insert_reversal(connection, sale["sale_id"], a_user)
            with pytest.raises(psycopg.errors.CheckViolation) as raised:
                _insert_line(connection, reversal, sale["sale_id"], product, sold + 1)
            assert raised.value.diag.constraint_name == "sale_reversal_line_within_sale"
    # Two reversals that fit one by one and not together: the first goes in
    # whole, so the second is refused at its own INSERT.
    with psycopg.connect(owner_url) as connection:
        first = _insert_reversal(connection, sale["sale_id"], a_user)
        _insert_line(connection, first, sale["sale_id"], product, sold - 1)
        second = _insert_reversal(connection, sale["sale_id"], a_user)
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            _insert_line(connection, second, sale["sale_id"], product, 2)
        assert raised.value.diag.constraint_name == "sale_reversal_line_within_sale"
    assert counts(owner, sale["sale_id"])[:2] == (0, 0)


def test_a_reversal_line_never_lands_without_its_running_total(
    owner_url, make_sale, a_user
):
    sale = make_sale()
    product = sale["lines"][0]["product_id"]
    with psycopg.connect(owner_url) as connection:
        reversal = _insert_reversal(connection, sale["sale_id"], a_user)
        _insert_line(connection, reversal, sale["sale_id"], product, 1)
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            connection.commit()
        assert (
            raised.value.diag.constraint_name == "returned_quantity_matches_reversals"
        )


def test_the_running_total_never_exceeds_the_units_sold_or_goes_negative(
    service_url, make_sale
):
    sale = make_sale()
    product = sale["lines"][0]["product_id"]
    # As svc_sales, the one writer the guard admits, the CHECK still holds.
    statements = (
        "UPDATE public.transaction_line SET returned_quantity = quantity + %s"
        " WHERE transaction_id = %s AND product_id = %s",
        "UPDATE public.transaction_line SET returned_quantity = %s"
        " WHERE transaction_id = %s AND product_id = %s",
    )
    with psycopg.connect(service_url, autocommit=True) as connection:
        for statement, value in zip(statements, (1, -1), strict=True):
            with pytest.raises(psycopg.errors.CheckViolation) as raised:
                connection.execute(statement, (value, sale["sale_id"], product))
            assert (
                raised.value.diag.constraint_name
                == "transaction_line_returned_quantity_check"
            )


def test_only_the_service_writes_the_running_total(retail_url, owner_url, make_sale):
    sale = make_sale()
    product = sale["lines"][0]["product_id"]
    # The web's role cannot write it, and cannot create a line with some returned.
    with psycopg.connect(retail_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.DataError):
            connection.execute(
                "UPDATE public.transaction_line SET returned_quantity = 1"
                " WHERE transaction_id = %s AND product_id = %s",
                (sale["sale_id"], product),
            )
        with pytest.raises(psycopg.errors.DataError):
            connection.execute(
                "INSERT INTO public.transaction_line (transaction_id, product_id,"
                " quantity, unit_price, returned_quantity) VALUES (%s, %s, 5, 1.00, 1)",
                (sale["sale_id"], 1),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT count(*) FROM sales.sale_reversal")
        # The views the web reads are read-only for it.
        assert connection.execute(
            "SELECT bool_or(has_table_privilege('retail_app', v, p))"
            " FROM unnest(ARRAY['public.net_sale_line', 'public.net_sale']) AS v,"
            " unnest(ARRAY['INSERT', 'UPDATE', 'DELETE']) AS p"
        ).fetchone() == (False,)
        assert connection.execute(
            "SELECT has_table_privilege('retail_app', 'public.net_sale', 'SELECT')"
        ).fetchone() == (True,)
    # Even the owner, who is not svc_sales, is refused by the guard.
    with psycopg.connect(owner_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.DataError):
            connection.execute(
                "UPDATE public.transaction_line SET returned_quantity = 1"
                " WHERE transaction_id = %s AND product_id = %s",
                (sale["sale_id"], product),
            )


def test_a_sale_is_cancelled_once_and_a_cancellation_takes_whole_lines(
    owner_url, make_sale, a_user
):
    sale = make_sale()
    product = sale["lines"][0]["product_id"]
    with psycopg.connect(owner_url, autocommit=True) as connection:
        header = _insert_reversal(
            connection, sale["sale_id"], a_user, "CANCELLATION", "oops"
        )
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            _insert_line(connection, header, sale["sale_id"], product, 1, None)
        assert (
            raised.value.diag.constraint_name == "sale_reversal_line_cancellation_whole"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_reversal(
                connection, sale["sale_id"], a_user, "CANCELLATION", "twice"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_reversal(connection, sale["sale_id"], a_user, "CANCELLATION", None)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_reversal(
                connection, sale["sale_id"], a_user, "RETURN", "header reason"
            )
        ret = _insert_reversal(connection, sale["sale_id"], a_user)
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            _insert_line(connection, ret, sale["sale_id"], product, 1, None)
        assert (
            raised.value.diag.constraint_name == "sale_reversal_line_reason_kind_check"
        )
        # A line cannot name a sale its header is not a reversal of.
        other = make_sale()
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_line(
                connection, ret, other["sale_id"], other["lines"][0]["product_id"], 1
            )


def test_the_outbox_row_is_written_by_the_reversal_itself(owner_url, make_sale, a_user):
    sale = make_sale()
    with psycopg.connect(owner_url) as connection:
        reversal = _insert_reversal(connection, sale["sale_id"], a_user)
        assert connection.execute(
            "SELECT entity_id FROM sales_events.outbox WHERE stream = %s"
            " AND entity_id = %s",
            ("stream:sales.v1.reversed", f"{sale['sale_id']}:{reversal}"),
        ).fetchall() == [(f"{sale['sale_id']}:{reversal}",)]
        connection.rollback()
    with psycopg.connect(owner_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sales_events.outbox WHERE entity_id = %s",
            (f"{sale['sale_id']}:{reversal}",),
        ).fetchone() == (0,)


def test_net_units_in_the_views_match_what_was_reversed(
    owner, client, token, make_sale
):
    sale = make_sale()
    first, second = (line["product_id"] for line in sale["lines"])
    for units in ((1, None), (1, 1)):
        assert (
            client.post(
                "/v1/sale-reversals",
                json=return_of(sale, *units),
                headers=idempotency_headers(token("STORE_MANAGER")),
            ).status_code
            == 201
        )
    # Every line's running total equals the sum of its reversal lines.
    assert owner.execute(
        "SELECT count(*) FROM public.transaction_line AS l"
        " WHERE l.returned_quantity <> ("
        " SELECT coalesce(sum(x.quantity), 0) FROM sales.sale_reversal_line AS x"
        " WHERE x.transaction_id = l.transaction_id AND x.product_id = l.product_id)"
    ).fetchone() == (0,)
    expected = [
        (first, 3, 2, 1, "10.00"),
        (second, 2, 1, 1, "4.25"),
    ]
    assert (
        owner.execute(
            "SELECT product_id, quantity, returned_quantity, net_quantity,"
            " net_amount::text FROM public.net_sale_line"
            " WHERE transaction_id = %s ORDER BY product_id",
            (sale["sale_id"],),
        ).fetchall()
        == expected
    )
    assert (
        owner.execute(
            "SELECT product_id, quantity, returned_quantity, net_quantity,"
            " net_amount::text FROM api_sales.net_purchase_line_v1"
            " WHERE transaction_id = %s ORDER BY product_id",
            (sale["sale_id"],),
        ).fetchall()
        == expected
    )
    net = owner.execute(
        "SELECT net_units, net_total::text, total::text FROM public.net_sale"
        " WHERE transaction_id = %s",
        (sale["sale_id"],),
    ).fetchone()
    assert net == (2, "14.25", "38.50")
