"""PostgreSQL-backed idempotency, including concurrent phone retries."""

from __future__ import annotations

import copy
import threading
import uuid

from tests.conftest import idempotency_headers, sale_xml


def _quantity(owner, body):
    line = body["lines"][0]
    return owner.execute(
        "SELECT quantity_on_hand FROM public.inventory"
        " WHERE store_id = %s AND product_id = %s",
        (body["store_id"], line["product_id"]),
    ).fetchone()[0]


def test_missing_and_non_uuid_key(client, token, sale_body):
    auth = token("STORE_MANAGER")
    for headers in (auth, {**auth, "Idempotency-Key": "not-a-uuid"}):
        response = client.post("/v1/sales", json=sale_body, headers=headers)
        assert response.status_code == 400
        assert response.get_json()["code"] == "idempotency_key_required"


def test_same_key_different_body_conflicts(client, token, sale_body):
    key = str(uuid.uuid4())
    headers = idempotency_headers(token("STORE_MANAGER"), key)
    assert client.post("/v1/sales", json=sale_body, headers=headers).status_code == 201
    changed = copy.deepcopy(sale_body)
    changed["lines"][0]["quantity"] += 1
    response = client.post("/v1/sales", json=changed, headers=headers)
    assert response.status_code == 409
    assert response.get_json()["code"] == "idempotency_key_reused"


def test_two_concurrent_posts_create_one_sale_and_one_decrement(
    app, token, sale_body, owner
):
    key = str(uuid.uuid4())
    auth = token("STORE_MANAGER")
    headers = idempotency_headers(auth, key)
    before = _quantity(owner, sale_body)
    barrier = threading.Barrier(2)
    results = []

    def send() -> None:
        with app.test_client() as thread_client:
            barrier.wait()
            response = thread_client.post("/v1/sales", json=sale_body, headers=headers)
            results.append(
                (response.status_code, response.headers, response.get_json())
            )

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sorted(status for status, _, _ in results) == [200, 201]
    replay = next(headers for status, headers, _ in results if status == 200)
    assert replay["Idempotent-Replayed"] == "true"
    assert (
        owner.execute(
            "SELECT count(*) FROM public.transaction"
            " WHERE source_transaction_id = %s",
            ("svc-sales:" + key,),
        ).fetchone()[0]
        == 1
    )
    assert _quantity(owner, sale_body) == before - sale_body["lines"][0]["quantity"]


def test_replay_survives_flushall_and_new_session_same_subject(
    client, token, role_subjects, sale_body, redis_connection
):
    key = str(uuid.uuid4())
    sub = role_subjects["STORE_MANAGER"]
    first = idempotency_headers(token("STORE_MANAGER", sub=sub), key)
    created = client.post("/v1/sales", json=sale_body, headers=first)
    assert created.status_code == 201
    redis_connection.flushall()
    fresh = idempotency_headers(token("STORE_MANAGER", sub=sub), key)
    replay = client.post("/v1/sales", json=sale_body, headers=fresh)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.get_json() == created.get_json()


def test_json_then_xml_is_the_same_request(client, token, sale_body):
    key = str(uuid.uuid4())
    auth = idempotency_headers(token("STORE_MANAGER"), key)
    created = client.post("/v1/sales", json=sale_body, headers=auth)
    assert created.status_code == 201
    replay = client.post(
        "/v1/sales",
        data=sale_xml(sale_body),
        content_type="application/xml",
        headers={**auth, "Accept": "application/xml"},
    )
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"


def test_refusal_does_not_remember_key(client, token, sale_body, owner):
    key = str(uuid.uuid4())
    headers = idempotency_headers(token("STORE_MANAGER"), key)
    line = sale_body["lines"][0]
    available = _quantity(owner, sale_body)
    body = copy.deepcopy(sale_body)
    body["lines"][0]["quantity"] = available + 1
    refused = client.post("/v1/sales", json=body, headers=headers)
    assert refused.status_code == 409
    owner.execute(
        "UPDATE public.inventory SET quantity_on_hand = %s"
        " WHERE store_id = %s AND product_id = %s",
        (available + 2, body["store_id"], line["product_id"]),
    )
    accepted = client.post("/v1/sales", json=body, headers=headers)
    assert accepted.status_code == 201
