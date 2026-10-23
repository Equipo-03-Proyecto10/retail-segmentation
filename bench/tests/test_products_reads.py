"""The products read cases (ADR-0042), run against either build.

`bench/conformance.sh` runs them, with the shared conformance suite, against
products-service and against its bench-only Rust twin on the same database
and token path. Both builds must pass the same cases before anything is
measured. `BENCH_TWIN=1` adds the twin's own promise: it serves no write.
"""

from __future__ import annotations

import os

import pytest

from tests.conformance.harness import BASE_URL, Client, Sessions, Signer

pytestmark = pytest.mark.skipif(not BASE_URL, reason="CONFORMANCE_BASE_URL")
READER = "ANALYST"


@pytest.fixture
def token(signer: Signer, sessions: Sessions) -> str:
    return signer.sign(signer.claims(READER, sessions.open()))


def get(client: Client, token: str, path: str, accept: str = "application/json"):
    return client.call("GET", path, token=token, accept=accept)


def test_a_page_is_ordered_by_id_with_page_info(client: Client, token: str) -> None:
    reply = get(client, token, "/v1/products?page=2&size=5")
    body = reply.json()
    assert reply.status == 200
    assert [item["product_id"] for item in body["products"]] == [6, 7, 8, 9, 10]
    assert body["page_info"]["page"] == 2 and body["page_info"]["size"] == 5
    assert body["page_info"]["total"] >= 40
    assert body["products"][0]["list_price"] == "37.50"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("q=demo%20product%201&ids=1,10,12,40", [1, 10, 12]),
        ("sku=SKU-00031", [31]),
        ("category_id=1&ids=1,2,31,32", [1, 31]),
        ("q=%25", []),
        ("active=false&ids=1,2,3", []),
        ("ids=5,3,5,1", [1, 3, 5]),
    ],
)
def test_filters(client: Client, token: str, query: str, expected: list[int]) -> None:
    reply = get(client, token, "/v1/products?size=100&" + query)
    assert reply.status == 200, reply.body
    assert [item["product_id"] for item in reply.json()["products"]] == expected


def test_past_the_last_page_the_total_still_comes_back(
    client: Client, token: str
) -> None:
    body = get(client, token, "/v1/products?ids=1,2,3&page=9&size=2").json()
    assert body["products"] == [] and body["page_info"]["total"] == 3


@pytest.mark.parametrize(
    ("query", "code", "field", "detail"),
    [
        ("q=", "invalid_query", "q", "too_short"),
        ("sku=" + "x" * 41, "invalid_query", "sku", "too_long"),
        ("category_id=x", "invalid_query", "category_id", "invalid_type"),
        ("category_id=32768", "invalid_query", "category_id", "out_of_range"),
        ("active=yes", "invalid_query", "active", "invalid_type"),
        ("ids=0", "invalid_query", "ids", "invalid_format"),
        (
            "ids=" + ",".join(str(i) for i in range(1, 52)),
            "too_many_ids",
            "ids",
            "max_items",
        ),
        ("ids=2147483648", "invalid_query", "ids", "out_of_range"),
    ],
)
def test_refusals(
    client: Client, token: str, query: str, code: str, field: str, detail: str
) -> None:
    reply = get(client, token, "/v1/products?" + query)
    assert (reply.status, reply.code()) == (422, code)
    assert reply.json()["details"] == [{"field": field, "code": detail}]


def test_one_product_and_an_unknown_one(client: Client, token: str) -> None:
    assert get(client, token, "/v1/products/3").json()["name"] == "Demo Product 3"
    reply = get(client, token, "/v1/products/999999")
    assert (reply.status, reply.code()) == (404, "product_not_found")


@pytest.mark.parametrize(
    "path", ["/v1/products?size=3", "/v1/products/3", "/v1/products/999999"]
)
def test_xml_answers_validate(client: Client, token: str, path: str) -> None:
    # Client.call validates every XML body against products.xsd.
    reply = get(client, token, path, accept="application/xml")
    assert reply.media_type == "application/xml"


@pytest.mark.skipif(
    os.environ.get("BENCH_TWIN") != "1", reason="the twin's own promise"
)
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/v1/products",
            b'{"product_id": 900001, "sku": "BENCH-X", "name": "Never written",'
            b' "category_id": 1, "list_price": "1.00"}',
        ),
        ("PATCH", "/v1/products/3", b'{"name": "Never written"}'),
        ("PUT", "/v1/products/3", b"{}"),
        ("DELETE", "/v1/products/3", None),
    ],
)
def test_the_twin_serves_no_write(
    client: Client, signer: Signer, sessions: Sessions, method: str, path: str, body
) -> None:
    # An ADMIN with a valid body: everything products-service would accept.
    admin = signer.sign(signer.claims("ADMIN", sessions.open()))
    reply = client.call(
        method,
        path,
        token=admin,
        content_type="application/json" if body else None,
        body=body,
    )
    assert (reply.status, reply.code()) == (405, "method_not_allowed")
    assert get(client, admin, "/v1/products/3").json()["name"] == "Demo Product 3"
