"""The two contracted profile reads (F22-19): stored, scoped and read-only."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import psycopg
import pytest
import redis
from flask import Flask
from flask.testing import FlaskClient
from lxml import etree
from mosaiq_kit.database import Database

from tests.conftest import Shop, days_ago

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
JSON = {"Accept": "application/json"}
XML = {"Accept": "application/xml"}
ALLOWED = ("ADMIN", "ANALYST", "STORE_MANAGER", "MARKETING", "AUDITOR")
FORBIDDEN = (
    "INVENTORY_PLANNER",
    "CUSTOMER",
    "SALES_ASSOCIATE",
    "LOYALTY_OFFICER",
    "COMMERCIAL_SUPERVISOR",
)


def _client(
    build_app: Callable[..., Flask], token: Callable[..., dict[str, str]], role: str
) -> tuple[Flask, FlaskClient, dict[str, str]]:
    app = build_app()
    return app, app.test_client(), {**token(role), **JSON}


def _consume(app: Flask, sale_id: int) -> None:
    app.extensions["rfm_handle_sale"](
        {
            "event_id": str(uuid.uuid4()),
            "entity_id": str(sale_id),
            "event_type": "SALE_RECORDED",
            "occurred_at": "2026-11-23T00:00:00Z",
            "v": "1",
        }
    )


def _assert_xsd(response: Any) -> etree._Element:
    schema = etree.XMLSchema(
        etree.parse(str(ROOT / "contracts/rfm-engine/rfm-engine.xsd"))
    )
    document = etree.fromstring(response.data)
    assert schema.validate(document), schema.error_log
    return document


@pytest.mark.parametrize("role", ALLOWED)
@pytest.mark.parametrize("path", ["/v1/rfm-profiles", "/v1/rfm-profiles/{customer}"])
def test_every_role_in_the_policy_reads_both_operations(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    role: str,
    path: str,
) -> None:
    customer = shop.customer()
    _app, client, headers = _client(build_app, token, role)
    response = client.get(path.format(customer=customer), headers=headers)
    assert response.status_code == 200


@pytest.mark.parametrize("role", FORBIDDEN)
@pytest.mark.parametrize("path", ["/v1/rfm-profiles", "/v1/rfm-profiles/{customer}"])
def test_every_role_outside_the_policy_is_forbidden(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    role: str,
    path: str,
) -> None:
    customer = shop.customer()
    _app, client, headers = _client(build_app, token, role)
    response = client.get(path.format(customer=customer), headers=headers)
    assert (response.status_code, response.get_json()["code"]) == (
        403,
        "forbidden_role",
    )


def test_a_registered_customer_without_a_profile_is_pending_in_both_formats(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
) -> None:
    customer = shop.customer()
    app, client, headers = _client(build_app, token, "ANALYST")
    json_response = client.get(f"/v1/rfm-profiles/{customer}", headers=headers)
    assert json_response.status_code == 200
    assert json_response.get_json() == {
        "customer_id": str(customer),
        "state": "pending",
        "rule_set_id": None,
        "r_days": None,
        "f_count": None,
        "m_total": None,
        "r_score": None,
        "f_score": None,
        "m_score": None,
        "label_code": None,
        "via_fallback": None,
        "computed_at": None,
        "next_change_at": None,
    }
    xml_response = app.test_client().get(
        f"/v1/rfm-profiles/{customer}", headers={**token("ANALYST"), **XML}
    )
    root = _assert_xsd(xml_response)
    namespace = {"rfm": "https://mosaiq.maxthecoder.online/xml/rfm-engine/v1"}
    assert root.findtext("rfm:state", namespaces=namespace) == "pending"
    assert [etree.QName(child).localname for child in root] == [
        "customer_id",
        "state",
    ]


@pytest.mark.parametrize("accept", [JSON, XML])
def test_anonymized_customers_remain_readable_through_single_and_filtered_lists(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    owner: psycopg.Connection,
    now: Any,
    accept: dict[str, str],
) -> None:
    """Anonymization keeps RFM history and the stable customer reference
    (ADR-0066 section 3); it does not turn a registered customer into unknown."""
    pending, scored = shop.customer(), shop.customer()
    app, client, _headers = _client(build_app, token, "ANALYST")
    _consume(app, shop.sale(scored, days_ago(now, 2), unit_price="25.00"))
    stored = client.get(
        f"/v1/rfm-profiles/{scored}", headers={**token("ANALYST"), **JSON}
    ).get_json()
    for customer in (pending, scored):
        owner.execute(
            "UPDATE public.customer SET name = 'Anonymized customer', email = NULL,"
            " phone = NULL, user_id = NULL, anonymized_at = clock_timestamp()"
            " WHERE customer_id = %s",
            (customer,),
        )

    headers = {**token("ANALYST"), **accept}
    for customer, state in ((pending, "pending"), (scored, "scored")):
        response = client.get(f"/v1/rfm-profiles/{customer}", headers=headers)
        assert response.status_code == 200
        if accept == JSON:
            assert response.get_json()["state"] == state
        else:
            root = _assert_xsd(response)
            assert root.xpath("string(//*[local-name()='state'])") == state

    queries = (
        ({"size": 100}, scored),
        ({"state": "scored", "size": 100}, scored),
        ({"label_code": stored["label_code"], "size": 100}, scored),
        ({"rule_set_id": stored["rule_set_id"], "size": 100}, scored),
        ({"state": "pending", "size": 100}, pending),
    )
    for query, expected in queries:
        response = client.get("/v1/rfm-profiles", query_string=query, headers=headers)
        assert response.status_code == 200
        if accept == JSON:
            ids = {
                profile["customer_id"] for profile in response.get_json()["profiles"]
            }
        else:
            root = _assert_xsd(response)
            ids = set(root.xpath("//*[local-name()='customer_id']/text()"))
        assert str(expected) in ids


@pytest.mark.parametrize(
    ("state", "via_fallback"),
    [("scored", False), ("scored", True), ("unassigned", None)],
)
@pytest.mark.parametrize("accept", [JSON, XML])
def test_every_stored_profile_shape_is_served_in_both_formats(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    owner: psycopg.Connection,
    now: Any,
    state: str,
    via_fallback: bool | None,
    accept: dict[str, str],
) -> None:
    customer = shop.customer()
    scored = state == "scored"
    owner.execute(
        "INSERT INTO rfm.customer_profile"
        " (customer_id, state, rule_set_id, r_days, f_count, m_total, r_score,"
        "  f_score, m_score, label_code, via_fallback, computed_at, next_change_at)"
        " VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            customer,
            state,
            3 if scored else None,
            2 if scored else 0,
            "42.50" if scored else "0.00",
            4 if scored else None,
            2 if scored else None,
            1 if scored else None,
            "LOST" if scored else None,
            via_fallback,
            now,
            now + timedelta(days=1) if scored else None,
        ),
    )
    app, client, _headers = _client(build_app, token, "ANALYST")
    response = client.get(
        f"/v1/rfm-profiles/{customer}", headers={**token("ANALYST"), **accept}
    )
    assert response.status_code == 200
    if accept == JSON:
        body = response.get_json()
        assert (body["state"], body["via_fallback"], body["m_total"]) == (
            state,
            via_fallback,
            "42.50" if scored else "0.00",
        )
    else:
        root = _assert_xsd(response)
        namespace = "{https://mosaiq.maxthecoder.online/xml/rfm-engine/v1}"
        assert root.findtext(namespace + "state") == state
        assert root.findtext(namespace + "via_fallback") == (
            None if via_fallback is None else str(via_fallback).lower()
        )
    assert app is not None


@pytest.mark.parametrize("accept", [JSON, XML])
def test_an_unknown_customer_is_customer_not_found(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    accept: dict[str, str],
) -> None:
    app, client, _headers = _client(build_app, token, "ANALYST")
    response = client.get(
        f"/v1/rfm-profiles/{uuid.uuid4()}",
        headers={**token("ANALYST"), **accept, "X-Correlation-ID": "missing-customer"},
    )
    assert response.status_code == 404
    if accept == JSON:
        assert response.get_json()["code"] == "customer_not_found"
    else:
        root = _assert_xsd(response)
        common = "{https://mosaiq.maxthecoder.online/xml/common/v1}"
        assert root.findtext(common + "code") == "customer_not_found"
    assert response.headers["X-Correlation-ID"] == "missing-customer"
    assert app is not None


def test_list_filters_paginates_orders_and_includes_pending_only_when_asked(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    owner: psycopg.Connection,
    now: Any,
) -> None:
    app, client, headers = _client(build_app, token, "ANALYST")
    customers = [shop.customer() for _ in range(4)]
    for customer, age, price in zip(
        customers[:3], (1, 5, 200), ("10.00", "900.00", "20.00"), strict=True
    ):
        sale = shop.sale(customer, days_ago(now, age), unit_price=price)
        _consume(app, sale)

    first = client.get("/v1/rfm-profiles?page=1&size=2", headers=headers)
    second = client.get("/v1/rfm-profiles?page=2&size=2", headers=headers)
    total = owner.execute("SELECT count(*) FROM rfm.customer_profile").fetchone()
    expected = owner.execute(
        "SELECT customer_id::text FROM rfm.customer_profile"
        " ORDER BY customer_id LIMIT 4"
    ).fetchall()
    assert total is not None
    assert first.get_json()["page_info"] == {
        "page": 1,
        "size": 2,
        "total": total[0],
    }
    listed = first.get_json()["profiles"] + second.get_json()["profiles"]
    assert [item["customer_id"] for item in listed] == [row[0] for row in expected]
    assert str(customers[3]) not in {item["customer_id"] for item in listed}

    pending = client.get("/v1/rfm-profiles?state=pending&size=100", headers=headers)
    pending_ids = {item["customer_id"] for item in pending.get_json()["profiles"]}
    assert str(customers[3]) in pending_ids

    scored = client.get(
        "/v1/rfm-profiles?state=scored&rule_set_id=1&size=100", headers=headers
    ).get_json()["profiles"]
    assert scored and all(
        item["state"] == "scored" and item["rule_set_id"] == 1 for item in scored
    )
    label = scored[0]["label_code"]
    by_label = client.get(
        "/v1/rfm-profiles",
        query_string={"label_code": label, "size": 100},
        headers=headers,
    ).get_json()["profiles"]
    assert by_label and all(item["label_code"] == label for item in by_label)

    xml_page = app.test_client().get(
        "/v1/rfm-profiles?state=scored&rule_set_id=1&size=1",
        headers={**token("ANALYST"), **XML},
    )
    assert xml_page.status_code == 200
    _assert_xsd(xml_page)


@pytest.mark.parametrize(
    ("query", "code", "field"),
    [
        ("state=unknown", "invalid_query", "state"),
        ("rule_set_id=no", "invalid_query", "rule_set_id"),
        ("rule_set_id=0", "invalid_query", "rule_set_id"),
        ("rule_set_id=999999999999999999999", "invalid_query", "rule_set_id"),
        ("page=0", "invalid_pagination", "page"),
        ("size=101", "invalid_pagination", "size"),
    ],
)
def test_invalid_filters_and_pagination_are_refused(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    query: str,
    code: str,
    field: str,
) -> None:
    _app, client, headers = _client(build_app, token, "ANALYST")
    response = client.get("/v1/rfm-profiles?" + query, headers=headers)
    assert (response.status_code, response.get_json()["code"]) == (422, code)
    assert response.get_json()["details"][0]["field"] == field


def test_the_sales_consumer_result_and_its_computed_instant_are_read_back(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    now: Any,
) -> None:
    customer = shop.customer()
    app, client, headers = _client(build_app, token, "ANALYST")
    first_sale = shop.sale(customer, now - timedelta(days=10), unit_price="15.00")
    _consume(app, first_sale)
    before = client.get(f"/v1/rfm-profiles/{customer}", headers=headers).get_json()
    assert (before["r_days"], before["f_count"], before["m_total"]) == (
        10,
        1,
        "15.00",
    )

    second_sale = shop.sale(customer, now - timedelta(days=1), unit_price="25.00")
    _consume(app, second_sale)
    after = client.get(f"/v1/rfm-profiles/{customer}", headers=headers).get_json()
    assert (after["r_days"], after["f_count"], after["m_total"]) == (
        1,
        2,
        "40.00",
    )
    assert datetime.fromisoformat(after["computed_at"]) > datetime.fromisoformat(
        before["computed_at"]
    )


def test_reads_write_no_row(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    owner: psycopg.Connection,
    now: Any,
) -> None:
    pending, scored = shop.customer(), shop.customer()
    app, client, headers = _client(build_app, token, "AUDITOR")
    _consume(app, shop.sale(scored, days_ago(now, 2)))
    before = owner.execute(
        "SELECT"
        " (SELECT count(*) FROM rfm.customer_profile),"
        " (SELECT count(*) FROM rfm_events.outbox),"
        " (SELECT count(*) FROM audit_events.domain_event)"
    ).fetchone()
    assert client.get(f"/v1/rfm-profiles/{pending}", headers=headers).status_code == 200
    assert client.get(f"/v1/rfm-profiles/{scored}", headers=headers).status_code == 200
    assert (
        client.get("/v1/rfm-profiles?state=pending", headers=headers).status_code == 200
    )
    after = owner.execute(
        "SELECT"
        " (SELECT count(*) FROM rfm.customer_profile),"
        " (SELECT count(*) FROM rfm_events.outbox),"
        " (SELECT count(*) FROM audit_events.domain_event)"
    ).fetchone()
    assert after == before


def test_contract_error_responses_and_correlation_id(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    redis_connection: redis.Redis,
) -> None:
    customer = shop.customer()
    app = build_app()
    client = app.test_client()
    missing = client.get(f"/v1/rfm-profiles/{customer}", headers=JSON)
    assert (missing.status_code, missing.get_json()["code"]) == (401, "invalid_token")

    good = token("ANALYST")
    claims = jwt.decode(
        good["Authorization"].removeprefix("Bearer "),
        options={"verify_signature": False},
    )
    redis_connection.delete("pub:auth:v1:live:" + str(claims["sid"]))
    revoked = client.get(f"/v1/rfm-profiles/{customer}", headers={**good, **JSON})
    assert (revoked.status_code, revoked.get_json()["code"]) == (
        401,
        "session_revoked",
    )

    valid = {**token("ANALYST"), "Accept": "text/csv", "X-Correlation-ID": "rfm-read"}
    unacceptable = client.get(f"/v1/rfm-profiles/{customer}", headers=valid)
    assert (unacceptable.status_code, unacceptable.get_json()["code"]) == (
        406,
        "not_acceptable",
    )
    assert unacceptable.headers["X-Correlation-ID"] == "rfm-read"


def test_rate_auth_and_database_unavailability_are_contracted_503_or_429(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    shop: Shop,
    redis_connection: redis.Redis,
) -> None:
    customer = shop.customer()
    app = build_app()
    client = app.test_client()
    kit = app.extensions["mosaiq_kit"]

    kit.rate_limit_enabled = True
    kit.rate_limit_requests = 1
    kit.rate_limit_window = 600
    old_rate_keys = list(redis_connection.scan_iter("rfm:rl:*"))
    if old_rate_keys:
        redis_connection.delete(*old_rate_keys)
    headers = {**token("ANALYST"), **JSON}
    assert (
        client.get(f"/v1/rfm-profiles/{customer}", headers=headers).status_code == 200
    )
    limited = client.get(f"/v1/rfm-profiles/{customer}", headers=headers)
    assert (limited.status_code, limited.get_json()["code"]) == (429, "rate_limited")
    assert int(limited.headers["Retry-After"]) >= 1
    kit.rate_limit_enabled = False

    live_redis = kit.auth.redis
    kit.auth.redis = redis.Redis(
        host="127.0.0.1", port=1, socket_connect_timeout=0.01, socket_timeout=0.01
    )
    unavailable_auth = client.get(
        f"/v1/rfm-profiles/{customer}", headers={**token("ANALYST"), **JSON}
    )
    assert (unavailable_auth.status_code, unavailable_auth.get_json()["code"]) == (
        503,
        "auth_unavailable",
    )
    kit.auth.redis = live_redis

    healthy_db = kit.db
    kit.db = Database("postgresql://svc_rfm:svc_rfm@127.0.0.1:1/retail")
    unavailable_db = client.get(
        f"/v1/rfm-profiles/{customer}", headers={**token("ANALYST"), **JSON}
    )
    assert (unavailable_db.status_code, unavailable_db.get_json()["code"]) == (
        503,
        "database_unavailable",
    )
    kit.db.close()
    kit.db = healthy_db
