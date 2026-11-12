"""The products read cases (ADR-0042, ADR-0078), run against either build.

`bench/conformance.sh` runs them, with the shared conformance suite, against
products-service and against its bench-only Rust twin on the same database
and token path. Both builds must pass the same cases before anything is
measured: the list, the one product, and the details and relations reads of
F21-10. `BENCH_TWIN=1` adds the twin's own promise: it serves no write.
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


def test_the_details_of_a_seeded_product(client: Client, token: str) -> None:
    reply = get(client, token, "/v1/products/3/details")
    body = reply.json()
    assert reply.status == 200
    assert list(body) == [
        "product_id",
        "sku",
        "name",
        "category_id",
        "list_price",
        "is_active",
        "commercial_status",
        "attributes",
        "photo_url",
        "barcode",
    ]
    assert body["name"] == "Demo Product 3" and body["list_price"] == "26.25"
    assert (body["commercial_status"], body["is_active"]) == ("active", True)
    assert [attribute["name"] for attribute in body["attributes"]] == [
        "brand",
        "color",
        "pack_size",
    ]
    assert body["barcode"] == "7501000000036" and body["photo_url"] is None


def test_a_product_with_no_details_reads_its_status_from_is_active(
    client: Client, token: str
) -> None:
    # The 10,000 bench products carry no status, attribute or barcode row.
    body = get(client, token, "/v1/products/100017/details").json()
    assert (body["commercial_status"], body["is_active"]) == ("active", True)
    assert (body["attributes"], body["barcode"], body["photo_url"]) == ([], None, None)


def test_relations_are_ordered_by_kind_then_id(client: Client, token: str) -> None:
    body = get(client, token, "/v1/products/1/relations").json()
    assert body["product_id"] == 1
    assert body["product_relations"] == [
        {"related_product_id": 2, "kind": "complement"},
        {"related_product_id": 31, "kind": "substitute"},
    ]
    assert body["page_info"] == {"page": 1, "size": 20, "total": 2}
    only = get(client, token, "/v1/products/1/relations?kind=substitute").json()
    assert [item["related_product_id"] for item in only["product_relations"]] == [31]
    paged = get(client, token, "/v1/products/1/relations?page=2&size=1").json()
    assert paged["page_info"] == {"page": 2, "size": 1, "total": 2}
    beyond = get(client, token, "/v1/products/1/relations?page=9").json()
    assert beyond["product_relations"] == [] and beyond["page_info"]["total"] == 2
    none = get(client, token, "/v1/products/100017/relations").json()
    assert none["product_id"] == 100017 and none["product_relations"] == []


@pytest.mark.parametrize(
    "path", ["/v1/products/999999/details", "/v1/products/999999/relations"]
)
def test_details_and_relations_of_an_unknown_product(
    client: Client, token: str, path: str
) -> None:
    reply = get(client, token, path)
    assert (reply.status, reply.code()) == (404, "product_not_found")


@pytest.mark.parametrize("kind", ["upsell", "", "Substitute"])
def test_an_unknown_relation_kind_is_invalid_query(
    client: Client, token: str, kind: str
) -> None:
    reply = get(client, token, "/v1/products/1/relations?kind=" + kind)
    assert (reply.status, reply.code()) == (422, "invalid_query")
    assert reply.json()["details"] == [{"field": "kind", "code": "enum"}]


@pytest.mark.parametrize(
    "path",
    [
        "/v1/products?size=3",
        "/v1/products/3",
        "/v1/products/999999",
        "/v1/products/3/details",
        "/v1/products/100017/details",
        "/v1/products/999999/details",
        "/v1/products/1/relations",
        "/v1/products/1/relations?kind=complement&size=1",
        "/v1/products/100017/relations",
        "/v1/products/1/relations?kind=upsell",
    ],
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
        ("PATCH", "/v1/products/3/details", b'{"commercial_status": "paused"}'),
        (
            "PUT",
            "/v1/products/3/relations/substitute",
            b'{"related_product_ids": [4]}',
        ),
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
    assert get(client, admin, "/v1/products/3/details").json()["is_active"] is True
    assert (
        get(client, admin, "/v1/products/3/relations").json()["page_info"]["total"] == 2
    )
