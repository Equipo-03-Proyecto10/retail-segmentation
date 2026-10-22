"""The transport the contract promises: roles, negotiation, XML, meta routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/customers"
NAMESPACE = "https://mosaiq.maxthecoder.online/xml/customers/v1"
COMMON_NAMESPACE = "https://mosaiq.maxthecoder.online/xml/common/v1"

pytestmark = pytest.mark.postgres


def valid(xml: bytes) -> bool:
    schema = etree.XMLSchema(etree.parse(str(CONTRACT / "customers.xsd")))
    return schema.validate(etree.fromstring(xml))


def test_meta_routes_serve_the_contract_byte_for_byte(client):
    openapi = client.get("/openapi.yaml")
    assert openapi.status_code == 200
    assert openapi.data == (CONTRACT / "openapi.yaml").read_bytes()
    schema = client.get("/schema.xsd")
    assert schema.data == (CONTRACT / "customers.xsd").read_bytes()
    assert client.get("/health/live").status_code == 204
    assert client.get("/health/ready").status_code == 204


def test_readiness_fails_when_the_database_is_unreachable(make_app):
    app = make_app("postgresql://svc_customers:x@127.0.0.1:1/retail")
    response = app.test_client().get("/health/ready")
    assert response.status_code == 503
    assert response.get_json()["code"] == "schema_not_ready"


def test_a_database_outage_answers_503_database_unavailable(make_app, manager):
    app = make_app("postgresql://svc_customers:x@127.0.0.1:1/retail")
    response = app.test_client().get("/v1/customers", headers=manager)
    assert response.status_code == 503
    assert response.get_json()["code"] == "database_unavailable"


def test_roles_follow_the_contract(client, token):
    assert (
        client.get("/v1/customers", headers=token("INVENTORY_PLANNER")).status_code
        == 403
    )
    denied = client.post("/v1/customers", json={}, headers=token("ANALYST"))
    assert (denied.status_code, denied.get_json()["code"]) == (403, "forbidden_role")
    for role in ("ADMIN", "ANALYST", "STORE_MANAGER", "MARKETING", "AUDITOR"):
        assert client.get("/v1/customers", headers=token(role)).status_code == 200


def test_a_revoked_session_and_a_missing_token_are_401(client, manager, state):
    assert client.get("/v1/customers").status_code == 401
    state.live.clear()
    response = client.get("/v1/customers", headers=manager)
    assert (response.status_code, response.get_json()["code"]) == (
        401,
        "session_revoked",
    )


def test_other_accept_values_get_406(client, manager):
    response = client.get("/v1/customers", headers={**manager, "Accept": "text/html"})
    assert response.status_code == 406


def test_xml_round_trip_is_valid_against_the_xsd(client, manager, unique):
    request = f"""<?xml version="1.0" encoding="UTF-8"?>
<RegisterCustomerRequest xmlns="{NAMESPACE}">
  <name>{unique}</name>
  <email>{unique}@example.com</email>
  <phone>555</phone>
  <registration_channel_id>3</registration_channel_id>
</RegisterCustomerRequest>"""
    headers = {
        **manager,
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    created = client.post("/v1/customers", data=request, headers=headers)
    assert created.status_code == 201, created.data
    assert valid(created.data)
    identifier = etree.fromstring(created.data).findtext(f"{{{NAMESPACE}}}customer_id")
    fetched = client.get(
        "/v1/customers/" + identifier, headers={**manager, "Accept": "application/xml"}
    )
    assert valid(fetched.data)
    page = client.get(
        "/v1/customers",
        query_string={"q": unique},
        headers={**manager, "Accept": "application/xml"},
    )
    assert valid(page.data)
    patched = client.patch(
        "/v1/customers/" + identifier,
        data=(
            f'<CorrectCustomerRequest xmlns="{NAMESPACE}">'
            "<phone>77</phone></CorrectCustomerRequest>"
        ),
        headers=headers,
    )
    assert patched.status_code == 200, patched.data
    assert valid(patched.data)
    duplicate = client.post("/v1/customers", data=request, headers=headers)
    assert duplicate.status_code == 409
    assert duplicate.mimetype == "application/xml"


def test_a_blank_name_is_refused_in_xml(client, manager, unique):
    request = f"""<?xml version="1.0" encoding="UTF-8"?>
<RegisterCustomerRequest xmlns="{NAMESPACE}">
  <name>   </name>
  <email>{unique}@example.com</email>
  <phone>555</phone>
  <registration_channel_id>3</registration_channel_id>
</RegisterCustomerRequest>"""
    headers = {
        **manager,
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    response = client.post("/v1/customers", data=request, headers=headers)
    assert response.status_code == 422, response.data
    assert response.mimetype == "application/xml"
    error = etree.fromstring(response.data)
    common = "{https://mosaiq.maxthecoder.online/xml/common/v1}"
    assert error.findtext(common + "code") == "invalid_body"
    assert error.findtext(f"{common}details/{common}field") == "name"


def test_a_doctype_body_is_refused_as_malformed(client, manager):
    request = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "v">]>'
        f'<RegisterCustomerRequest xmlns="{NAMESPACE}"><name>&e;</name>'
        "<registration_channel_id>3</registration_channel_id></RegisterCustomerRequest>"
    )
    response = client.post(
        "/v1/customers",
        data=request,
        headers={**manager, "Content-Type": "application/xml"},
    )
    assert (response.status_code, response.get_json()["code"]) == (
        400,
        "malformed_body",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("q", ""), ("email", ""), ("q", "a" * 161), ("email", "a" * 161)],
)
@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_text_filters_outside_their_bounds_are_422_invalid_query(
    client, manager, field, value, accept
):
    response = client.get(
        "/v1/customers",
        query_string={field: value},
        headers={**manager, "Accept": accept},
    )
    assert response.status_code == 422
    if accept == "application/json":
        body = response.get_json()
        assert body["code"] == "invalid_query"
        assert body["details"] == [{"field": field, "code": "out_of_range"}]
    else:
        root = etree.fromstring(response.data)
        assert root.findtext(f"{{{COMMON_NAMESPACE}}}code") == "invalid_query"
        detail = root.find(f"{{{COMMON_NAMESPACE}}}details")
        assert detail is not None
        assert detail.findtext(f"{{{COMMON_NAMESPACE}}}field") == field
        assert detail.findtext(f"{{{COMMON_NAMESPACE}}}code") == "out_of_range"


def test_text_filters_at_their_bounds_are_accepted(client, manager):
    for query in ({"q": "a"}, {"q": "a" * 160}, {"email": "a" * 160}):
        response = client.get("/v1/customers", query_string=query, headers=manager)
        assert response.status_code == 200
