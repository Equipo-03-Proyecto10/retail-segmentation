"""The transport the contract promises: roles, negotiation, XML, meta routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/categories"
NAMESPACE = "https://mosaiq.maxthecoder.online/xml/categories/v1"

pytestmark = pytest.mark.postgres


def valid(xml: bytes) -> bool:
    schema = etree.XMLSchema(etree.parse(str(CONTRACT / "categories.xsd")))
    return schema.validate(etree.fromstring(xml))


def test_meta_routes_serve_the_contract_byte_for_byte(client):
    assert client.get("/openapi.yaml").data == (CONTRACT / "openapi.yaml").read_bytes()
    assert client.get("/schema.xsd").data == (CONTRACT / "categories.xsd").read_bytes()
    assert client.get("/health/live").status_code == 204
    assert client.get("/health/ready").status_code == 204


def test_a_database_outage_answers_503(make_app, admin_headers):
    app = make_app("postgresql://svc_categories:x@127.0.0.1:1/retail")
    client = app.test_client()
    response = client.get("/v1/categories", headers=admin_headers)
    assert (response.status_code, response.get_json()["code"]) == (
        503,
        "database_unavailable",
    )
    assert client.get("/health/ready").status_code == 503


def test_roles_follow_the_contract(client, token, new_id):
    assert (
        client.delete("/v1/categories/1", headers=token("MARKETING")).status_code == 403
    )
    assert (
        client.post("/v1/categories", json={}, headers=token("ANALYST")).status_code
        == 403
    )
    for role in (
        "ADMIN",
        "ANALYST",
        "STORE_MANAGER",
        "MARKETING",
        "INVENTORY_PLANNER",
        "AUDITOR",
    ):
        assert client.get("/v1/categories", headers=token(role)).status_code == 200
    assert client.get("/v1/categories", headers=token("CUSTOMER")).status_code == 403


def test_a_revoked_session_and_a_missing_token_are_401(client, admin_headers, state):
    assert client.get("/v1/categories").status_code == 401
    state.live.clear()
    response = client.get("/v1/categories", headers=admin_headers)
    assert (response.status_code, response.get_json()["code"]) == (
        401,
        "session_revoked",
    )


def test_other_accept_values_get_406(client, admin_headers):
    assert (
        client.get(
            "/v1/categories", headers={**admin_headers, "Accept": "text/html"}
        ).status_code
        == 406
    )


def test_xml_round_trip_is_valid_against_the_xsd(client, admin_headers, new_id, unique):
    category_id = new_id()
    headers = {
        **admin_headers,
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    request = (
        f'<CreateCategoryRequest xmlns="{NAMESPACE}"><category_id>{category_id}</category_id>'
        f"<name>{unique}</name></CreateCategoryRequest>"
    )
    created = client.post("/v1/categories", data=request, headers=headers)
    assert created.status_code == 201, created.data
    assert valid(created.data)
    fetched = client.get(
        f"/v1/categories/{category_id}",
        headers={**admin_headers, "Accept": "application/xml"},
    )
    assert valid(fetched.data)
    page = client.get(
        "/v1/categories", headers={**admin_headers, "Accept": "application/xml"}
    )
    assert valid(page.data)
    duplicate = client.post("/v1/categories", data=request, headers=headers)
    assert duplicate.status_code == 409
    assert valid(duplicate.data)


def test_xml_nil_parent_makes_a_root(client, admin_headers, new_id, unique):
    parent, child = new_id(), new_id()
    client.post(
        "/v1/categories",
        json={"category_id": parent, "name": unique},
        headers=admin_headers,
    )
    client.post(
        "/v1/categories",
        json={
            "category_id": child,
            "name": unique + "-c",
            "parent_category_id": parent,
        },
        headers=admin_headers,
    )
    request = (
        f'<UpdateCategoryRequest xmlns="{NAMESPACE}" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<parent_category_id xsi:nil="true"/></UpdateCategoryRequest>'
    )
    response = client.patch(
        f"/v1/categories/{child}",
        data=request,
        headers={
            **admin_headers,
            "Content-Type": "application/xml",
            "Accept": "application/xml",
        },
    )
    assert response.status_code == 200, response.data
    assert valid(response.data)
    assert (
        client.get(f"/v1/categories/{child}", headers=admin_headers).get_json()[
            "parent_category_id"
        ]
        is None
    )


def test_a_doctype_body_is_refused_as_malformed(client, admin_headers):
    request = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "v">]>'
        f'<CreateCategoryRequest xmlns="{NAMESPACE}"><category_id>1</category_id>'
        "<name>&e;</name></CreateCategoryRequest>"
    )
    response = client.post(
        "/v1/categories",
        data=request,
        headers={**admin_headers, "Content-Type": "application/xml"},
    )
    assert (response.status_code, response.get_json()["code"]) == (
        400,
        "malformed_body",
    )


# Inputs that used to reach PostgreSQL out of range and answer 500 (#581).


@pytest.mark.parametrize("parent_id", ["99999", "32768", "2147483647", "-1"])
def test_a_parent_filter_no_category_can_have_gives_an_empty_page(
    client, admin_headers, parent_id
):
    response = client.get(
        "/v1/categories?parent_id=" + parent_id, headers=admin_headers
    )
    assert response.status_code == 200, response.data
    assert response.get_json()["categories"] == []
    assert response.get_json()["page_info"]["total"] == 0


@pytest.mark.parametrize(
    "parent_id", ["2147483648", "-2147483649", "99999999999999999999"]
)
def test_a_parent_filter_outside_int32_is_refused(client, admin_headers, parent_id):
    response = client.get(
        "/v1/categories?parent_id=" + parent_id, headers=admin_headers
    )
    assert response.status_code == 422
    body = response.get_json()
    assert body["code"] == "invalid_query"
    assert body["details"] == [{"field": "parent_id", "code": "out_of_range"}]


@pytest.mark.parametrize("parent", [99999, 32768, 2**31, 2**40, 0, -1, -(2**40)])
def test_a_parent_beyond_category_id_is_an_unknown_parent(
    client, admin_headers, new_id, unique, parent
):
    category_id = new_id()
    created = client.post(
        "/v1/categories",
        json={"category_id": category_id, "name": unique, "parent_category_id": parent},
        headers=admin_headers,
    )
    assert created.status_code == 422, created.data
    assert created.get_json()["code"] == "unknown_parent_category"
    client.post(
        "/v1/categories",
        json={"category_id": category_id, "name": unique},
        headers=admin_headers,
    )
    moved = client.patch(
        f"/v1/categories/{category_id}",
        json={"parent_category_id": parent},
        headers=admin_headers,
    )
    assert moved.status_code == 422, moved.data
    assert moved.get_json()["code"] == "unknown_parent_category"


def test_an_xml_parent_beyond_category_id_is_an_unknown_parent(
    client, admin_headers, new_id, unique
):
    request = (
        f'<CreateCategoryRequest xmlns="{NAMESPACE}">'
        f"<category_id>{new_id()}</category_id><name>{unique}</name>"
        f"<parent_category_id>{2**40}</parent_category_id></CreateCategoryRequest>"
    )
    response = client.post(
        "/v1/categories",
        data=request,
        headers={**admin_headers, "Content-Type": "application/xml"},
    )
    assert (response.status_code, response.get_json()["code"]) == (
        422,
        "unknown_parent_category",
    )


# Refused by the kit since 0.1.3 for every Python service (#580, #581, #582).


def test_a_page_beyond_the_kit_bound_is_invalid_pagination(client, admin_headers):
    response = client.get(
        "/v1/categories?page=99999999999999999999", headers=admin_headers
    )
    assert (response.status_code, response.get_json()["code"]) == (
        422,
        "invalid_pagination",
    )


def test_a_nul_in_the_name_is_invalid_body(client, admin_headers, new_id):
    response = client.post(
        "/v1/categories",
        json={"category_id": new_id(), "name": "a\x00b"},
        headers=admin_headers,
    )
    assert (response.status_code, response.get_json()["code"]) == (
        422,
        "invalid_body",
    )


def test_an_xml_comment_in_the_body_is_skipped(client, admin_headers, new_id, unique):
    request = (
        f'<CreateCategoryRequest xmlns="{NAMESPACE}"><!--c--><?pi x?>'
        f"<category_id>{new_id()}</category_id><name>{unique}<!--c--></name>"
        "</CreateCategoryRequest>"
    )
    response = client.post(
        "/v1/categories",
        data=request,
        headers={**admin_headers, "Content-Type": "application/xml"},
    )
    assert response.status_code == 201, response.data
    assert response.get_json()["name"] == unique
