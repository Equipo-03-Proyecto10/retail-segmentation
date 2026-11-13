"""Loyalty identifiers and role-minimised reads through the real service."""

from __future__ import annotations

import re

import psycopg
import pytest
from lxml import etree
from test_http import NAMESPACE, valid
from test_rules import body, register

from app.service import generate_card_code, refusal

pytestmark = pytest.mark.postgres

ACTIVE_KEY = "ux_loyalty_card_one_active_customer"
MISSING = "00000000-0000-4000-8000-000000000000"


def issue(client, headers, customer_id: str, accept: str = "application/json"):
    return client.post(
        "/v1/loyalty-cards",
        json={"customer_id": customer_id},
        headers={**headers, "Accept": accept},
    )


def make_customer(client, manager, unique: str) -> dict:
    response = register(client, manager, body(unique))
    assert response.status_code == 201
    return response.get_json()


def test_generated_codes_are_128_bit_csprng_shaped_and_not_customer_derived():
    codes = {generate_card_code() for _ in range(100)}
    assert len(codes) == 100
    assert all(re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", code) for code in codes)
    assert all(code[0] in "01234567" for code in codes)


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_floor_detail_omits_fields_by_role_in_both_formats(
    client, token, owner, accept
):
    customer_id = owner.execute(
        "SELECT customer_id::text FROM customer ORDER BY customer_id LIMIT 1"
    ).fetchone()[0]
    url = f"/v1/customers/{customer_id}/floor-view"

    seller = client.get(url, headers={**token("SALES_ASSOCIATE"), "Accept": accept})
    officer = client.get(url, headers={**token("LOYALTY_OFFICER"), "Accept": accept})
    assert seller.status_code == officer.status_code == 200
    if accept == "application/json":
        assert set(seller.get_json()) == {"customer_id", "name"}
        assert set(officer.get_json()) == {"customer_id", "name", "email", "phone"}
    else:
        assert valid(seller.data) and valid(officer.data)
        seller_names = {
            etree.QName(child).localname for child in etree.fromstring(seller.data)
        }
        officer_names = {
            etree.QName(child).localname for child in etree.fromstring(officer.data)
        }
        assert seller_names == {"customer_id", "name"}
        assert officer_names == {"customer_id", "name", "email", "phone"}


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_floor_search_uses_only_permitted_fields_and_omits_them(
    client, token, owner, accept
):
    row = owner.execute(
        "SELECT name, email FROM customer WHERE email IS NOT NULL"
        " ORDER BY customer_id LIMIT 1"
    ).fetchone()
    seller_headers = {**token("SALES_ASSOCIATE"), "Accept": accept}
    officer_headers = {**token("LOYALTY_OFFICER"), "Accept": accept}
    seller = client.get(
        "/v1/customers/floor-search", query_string={"q": row[1]}, headers=seller_headers
    )
    officer = client.get(
        "/v1/customers/floor-search",
        query_string={"q": row[1]},
        headers=officer_headers,
    )
    assert seller.status_code == officer.status_code == 200
    if accept == "application/json":
        assert seller.get_json()["customers"] == []
        assert officer.get_json()["customers"]
        assert all(
            set(item) == {"customer_id", "name", "email", "phone"}
            for item in officer.get_json()["customers"]
        )
    else:
        assert valid(seller.data) and valid(officer.data)
        assert (
            etree.fromstring(seller.data).find(f"{{{NAMESPACE}}}FloorCustomer") is None
        )
        item = etree.fromstring(officer.data).find(f"{{{NAMESPACE}}}FloorCustomer")
        assert item is not None
        assert {etree.QName(child).localname for child in item} == {
            "customer_id",
            "name",
            "email",
            "phone",
        }


def test_floor_validation_and_forbidden_roles(client, token):
    for query, code in (({}, "required"), ({"q": " x "}, "too_short")):
        response = client.get(
            "/v1/customers/floor-search",
            query_string=query,
            headers=token("SALES_ASSOCIATE"),
        )
        assert (response.status_code, response.get_json()["code"]) == (
            422,
            "invalid_query",
        )
        assert response.get_json()["details"][0]["code"] == code
    too_large = client.get(
        "/v1/customers/floor-search",
        query_string={"q": "Demo", "size": 21},
        headers=token("SALES_ASSOCIATE"),
    )
    assert (too_large.status_code, too_large.get_json()["code"]) == (
        422,
        "invalid_pagination",
    )
    for role in ("ADMIN", "STORE_MANAGER", "COMMERCIAL_SUPERVISOR"):
        denied = client.get(f"/v1/customers/{MISSING}/floor-view", headers=token(role))
        assert (denied.status_code, denied.get_json()["code"]) == (
            403,
            "forbidden_role",
        )


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_issue_list_lookup_and_revoke_round_trip_in_both_formats(
    client, token, manager, unique, accept
):
    customer = make_customer(client, manager, unique)
    officer = token("LOYALTY_OFFICER")
    created = issue(client, officer, customer["customer_id"], accept)
    assert created.status_code == 201
    assert valid(created.data) if accept == "application/xml" else created.get_json()
    card = (
        created.get_json()
        if accept == "application/json"
        else {
            etree.QName(child).localname: child.text
            for child in etree.fromstring(created.data)
        }
    )

    listed = client.get(
        "/v1/loyalty-cards",
        query_string={"customer_id": customer["customer_id"]},
        headers={**officer, "Accept": accept},
    )
    assert listed.status_code == 200
    assert valid(listed.data) if accept == "application/xml" else listed.get_json()
    if accept == "application/json":
        assert listed.get_json()["loyalty_state"] == "ACTIVE"
        assert listed.get_json()["loyalty_cards"][0]["card_id"] == card["card_id"]
    else:
        root = etree.fromstring(listed.data)
        assert root.findtext(f"{{{NAMESPACE}}}loyalty_state") == "ACTIVE"

    lookup = client.post(
        "/v1/loyalty-cards/lookup",
        json={"code": card["code"]},
        headers={**token("SALES_ASSOCIATE"), "Accept": accept},
    )
    assert lookup.status_code == 200
    assert valid(lookup.data) if accept == "application/xml" else lookup.get_json()

    revoked = client.put(
        f"/v1/loyalty-cards/{card['card_id']}/revocation",
        json={"reason": "LOST"},
        headers={**officer, "Accept": accept},
    )
    assert revoked.status_code == 200
    assert valid(revoked.data) if accept == "application/xml" else revoked.get_json()


def test_lookup_then_customer_read_applies_the_callers_representation(
    client, token, manager, unique
):
    customer = make_customer(client, manager, unique)
    created = issue(
        client, token("LOYALTY_OFFICER"), customer["customer_id"]
    ).get_json()
    for role, expected in (
        ("SALES_ASSOCIATE", {"customer_id", "name"}),
        ("LOYALTY_OFFICER", {"customer_id", "name", "email", "phone"}),
    ):
        lookup = client.post(
            "/v1/loyalty-cards/lookup",
            json={"code": created["code"]},
            headers=token(role),
        ).get_json()
        viewed = client.get(
            f"/v1/customers/{lookup['customer_id']}/floor-view", headers=token(role)
        )
        assert set(viewed.get_json()) == expected


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_unknown_and_revoked_lookups_reveal_no_customer(
    client, token, manager, unique, accept
):
    headers = {**token("SALES_ASSOCIATE"), "Accept": accept}
    officer = token("LOYALTY_OFFICER")
    unknown = client.post(
        "/v1/loyalty-cards/lookup",
        json={"code": "7M999999999999999999999999"},
        headers=headers,
    )
    assert unknown.status_code == 404
    assert customer_id_absent(unknown, accept, "card_not_found")

    customer = make_customer(client, manager, unique)
    card = issue(client, officer, customer["customer_id"]).get_json()
    client.put(
        f"/v1/loyalty-cards/{card['card_id']}/revocation",
        json={"reason": "STOLEN"},
        headers=officer,
    )
    revoked = client.post(
        "/v1/loyalty-cards/lookup",
        json={"code": card["code"]},
        headers=headers,
    )
    assert revoked.status_code == 409
    assert customer_id_absent(revoked, accept, "card_revoked")
    assert customer["customer_id"].encode() not in revoked.data


def customer_id_absent(response, accept: str, expected_code: str) -> bool:
    if accept == "application/json":
        body = response.get_json()
        return body["code"] == expected_code and "customer_id" not in body
    root = etree.fromstring(response.data)
    names = {etree.QName(element).localname for element in root.iter()}
    return root[0].text == expected_code and "customer_id" not in names


def test_issue_and_first_revocation_are_audited_and_repeat_is_unchanged(
    client, token, manager, owner, actor, unique
):
    customer = make_customer(client, manager, unique)
    officer = token("LOYALTY_OFFICER")
    card = issue(client, officer, customer["customer_id"]).get_json()
    first = client.put(
        f"/v1/loyalty-cards/{card['card_id']}/revocation",
        json={"reason": "LOST"},
        headers=officer,
    ).get_json()
    second = client.put(
        f"/v1/loyalty-cards/{card['card_id']}/revocation",
        json={"reason": "OTHER"},
        headers=officer,
    ).get_json()
    assert second == first
    rows = owner.execute(
        "SELECT action, user_id::text FROM audit_log"
        " WHERE entity = 'loyalty_card' AND entity_pk = %s ORDER BY audit_id",
        (card["card_id"],),
    ).fetchall()
    assert rows == [("INSERT", actor), ("UPDATE", actor)]


def test_second_active_card_is_409_and_direct_insert_hits_the_partial_index(
    client, token, manager, service_url, actor, unique
):
    customer = make_customer(client, manager, unique)
    officer = token("LOYALTY_OFFICER")
    assert issue(client, officer, customer["customer_id"]).status_code == 201
    duplicate = issue(client, officer, customer["customer_id"])
    assert (duplicate.status_code, duplicate.get_json()["code"]) == (
        409,
        "active_card_exists",
    )
    with psycopg.connect(service_url) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation) as raised:
            connection.execute(
                "INSERT INTO customers.loyalty_card"
                " (customer_id, code, issued_by) VALUES (%s, %s, %s)",
                (
                    customer["customer_id"],
                    "7M888888888888888888888888",
                    actor,
                ),
            )
    assert raised.value.diag.constraint_name == ACTIVE_KEY
    mapped = refusal(raised.value)
    assert mapped is not None and (mapped.status, mapped.code) == (
        409,
        "active_card_exists",
    )


def test_roles_follow_the_loyalty_contract(client, token, manager, unique):
    customer = make_customer(client, manager, unique)
    for role in ("SALES_ASSOCIATE", "STORE_MANAGER", "COMMERCIAL_SUPERVISOR"):
        denied = issue(client, token(role), customer["customer_id"])
        assert (denied.status_code, denied.get_json()["code"]) == (
            403,
            "forbidden_role",
        )
    for role in ("STORE_MANAGER", "COMMERCIAL_SUPERVISOR"):
        denied = client.post(
            "/v1/loyalty-cards/lookup",
            json={"code": "7M999999999999999999999999"},
            headers=token(role),
        )
        assert denied.status_code == 403
        denied_list = client.get(
            "/v1/loyalty-cards",
            query_string={"customer_id": customer["customer_id"]},
            headers=token(role),
        )
        assert denied_list.status_code == 403
    card = issue(client, token("LOYALTY_OFFICER"), customer["customer_id"]).get_json()
    for role in ("SALES_ASSOCIATE", "STORE_MANAGER", "COMMERCIAL_SUPERVISOR"):
        denied_revoke = client.put(
            f"/v1/loyalty-cards/{card['card_id']}/revocation",
            json={"reason": "LOST"},
            headers=token(role),
        )
        assert (denied_revoke.status_code, denied_revoke.get_json()["code"]) == (
            403,
            "forbidden_role",
        )


def test_seed_and_resolver_contract(owner, owner_url):
    assert (
        owner.execute("SELECT count(*) FROM customers.loyalty_card").fetchone()[0] == 30
    )
    assert owner.execute("SELECT count(*) FROM public.customer").fetchone()[0] == 30
    active = owner.execute(
        "SELECT code, card_id, customer_id FROM customers.loyalty_card"
        " WHERE status = 'ACTIVE' ORDER BY customer_id LIMIT 1"
    ).fetchone()
    revoked = owner.execute(
        "SELECT code FROM customers.loyalty_card WHERE status = 'REVOKED' LIMIT 1"
    ).fetchone()[0]
    with psycopg.connect(owner_url) as connection:
        connection.execute("SET LOCAL ROLE svc_checkins")
        assert (
            connection.execute(
                "SELECT card_id, customer_id"
                " FROM api_customers.resolve_loyalty_code_v1(%s)",
                (active[0],),
            ).fetchone()
            == active[1:]
        )
        assert (
            connection.execute(
                "SELECT * FROM api_customers.resolve_loyalty_code_v1(%s)", (revoked,)
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT * FROM api_customers.resolve_loyalty_code_v1(%s)",
                ("not-a-code",),
            ).fetchone()
            is None
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT code FROM customers.loyalty_card")
