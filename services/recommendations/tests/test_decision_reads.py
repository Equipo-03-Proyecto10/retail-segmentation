"""The stored-decision reads of F24-09, over real PostgreSQL and Redis.

The contract's two operations are exercised in JSON and XSD-validated XML,
with every token role, store scope, every query filter, pagination, retention,
errors, correlation ids, and a before/after proof that a read writes no row.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import jsonschema
import jwt
import psycopg
import psycopg.sql
import pytest
import yaml
from flask import Flask
from flask.testing import FlaskClient
from lxml import etree
from mosaiq_kit.database import Database

import redis
from app import decision_store

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "contracts/recommendations/openapi.yaml").read_text())
XSD = etree.XMLSchema(
    etree.parse(str(ROOT / "contracts/recommendations/recommendations.v2.xsd"))
)
NS = "{https://mosaiq.maxthecoder.online/xml/recommendations/v2}"
TABLES = (
    "recommendation_decision",
    "recommendation_exclusion",
    "recommendation_item",
    "recommendation_reason",
)
ROLES = (
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
    "SALES_ASSOCIATE",
    "LOYALTY_OFFICER",
    "COMMERCIAL_SUPERVISOR",
)
ALLOWED = frozenset({"ADMIN", "ANALYST", "AUDITOR", "STORE_MANAGER"})
FULL_DECISION = "c0de0000-0000-4000-8000-000000000004"


def _valid(schema: str, document: object) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": OPENAPI["components"]}
    ).validate(document)


def _list(client: FlaskClient, headers: dict[str, str], **query: str):
    return client.get(
        "/v2/recommendation-decisions", query_string=query, headers=headers
    )


def _get(client: FlaskClient, headers: dict[str, str], decision_id: str):
    return client.get(f"/v2/recommendation-decisions/{decision_id}", headers=headers)


@pytest.fixture
def manager_decisions(
    owner: psycopg.Connection, role_subjects: dict[str, str]
) -> tuple[str, str, int]:
    assigned = {
        store_id
        for (store_id,) in owner.execute(
            "SELECT store_id FROM api_users.store_assignment_v1 WHERE user_id = %s",
            (role_subjects["STORE_MANAGER"],),
        ).fetchall()
    }
    allowed = owner.execute(
        "SELECT decision_id::text, store_id"
        " FROM recommendations.recommendation_decision"
        " WHERE store_id = ANY(%s::smallint[]) ORDER BY computed_at DESC LIMIT 1",
        (sorted(assigned),),
    ).fetchone()
    forbidden = owner.execute(
        "SELECT decision_id::text, store_id"
        " FROM recommendations.recommendation_decision"
        " WHERE store_id IS NULL OR NOT (store_id = ANY(%s::smallint[]))"
        " ORDER BY computed_at DESC LIMIT 1",
        (sorted(assigned),),
    ).fetchone()
    assert allowed is not None and forbidden is not None
    return allowed[0], forbidden[0], forbidden[1] or 32000


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("operation", ["list", "get"])
def test_every_token_role_is_in_or_out_of_each_operations_policy(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    manager_decisions: tuple[str, str, int],
    role: str,
    operation: str,
) -> None:
    allowed_id, _, _ = manager_decisions
    response = (
        _list(client, token(role))
        if operation == "list"
        else _get(client, token(role), allowed_id)
    )
    if role in ALLOWED:
        assert response.status_code == 200, response.get_json()
    else:
        assert (response.status_code, response.get_json()["code"]) == (
            403,
            "forbidden_role",
        )


def test_json_list_and_get_are_the_stored_contract_views(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    owner: psycopg.Connection,
) -> None:
    listed = _list(client, token("ANALYST"), size="100")
    assert listed.status_code == 200
    page = listed.get_json()
    _valid("RecommendationDecisionPage", page)
    (total,) = owner.execute(
        "SELECT count(*) FROM recommendations.recommendation_decision"
        " WHERE computed_at >= now() - interval '90 days'"
    ).fetchone()
    assert page["page_info"] == {"page": 1, "size": 100, "total": total}
    assert page["recommendation_decisions"] == sorted(
        page["recommendation_decisions"],
        key=lambda row: (row["computed_at"], row["decision_id"]),
        reverse=True,
    )

    response = _get(client, token("ANALYST"), FULL_DECISION)
    assert response.status_code == 200
    body = response.get_json()
    _valid("RecommendationDecision", body)
    assert body == decision_store.read(owner, FULL_DECISION).as_dict()
    assert {item["kind"] for item in body["items"]} == {
        "primary",
        "alternative",
        "elsewhere",
    }
    assert all(item["reasons"] for item in body["items"])


@pytest.mark.parametrize("operation", ["list", "get"])
def test_each_success_representation_is_valid_v2_xml(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    operation: str,
) -> None:
    headers = {**token("AUDITOR"), "Accept": "application/xml"}
    response = (
        _list(client, headers, size="2")
        if operation == "list"
        else _get(client, headers, FULL_DECISION)
    )
    assert response.status_code == 200
    document = etree.fromstring(response.data)
    XSD.assertValid(document)
    expected = (
        "RecommendationDecisions" if operation == "list" else "RecommendationDecision"
    )
    assert document.tag == NS + expected


def test_all_filters_combine_and_time_bounds_are_from_inclusive_to_exclusive(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    owner: psycopg.Connection,
) -> None:
    decision_id, customer_id, status, computed_at, store_id = owner.execute(
        "SELECT decision_id::text, customer_id::text, status, computed_at, store_id"
        " FROM recommendations.recommendation_decision"
        " WHERE store_id IS NOT NULL ORDER BY computed_at DESC LIMIT 1"
    ).fetchone()
    response = _list(
        client,
        token("ADMIN"),
        customer_id=customer_id,
        store_id=str(store_id),
        status=status,
        **{
            "from": computed_at.isoformat(),
            "to": (computed_at + timedelta(microseconds=1)).isoformat(),
        },
    )
    assert response.status_code == 200
    assert [
        row["decision_id"] for row in response.get_json()["recommendation_decisions"]
    ] == [decision_id]
    excluded = _list(
        client,
        token("ADMIN"),
        **{"to": computed_at.isoformat(), "customer_id": customer_id},
    )
    assert excluded.status_code == 200
    excluded_rows = excluded.get_json()["recommendation_decisions"]
    assert decision_id not in {row["decision_id"] for row in excluded_rows}
    assert all(
        datetime.fromisoformat(row["computed_at"].replace("Z", "+00:00")) < computed_at
        for row in excluded_rows
    )


def test_pagination_has_stable_newest_first_pages_and_the_filtered_total(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    owner: psycopg.Connection,
) -> None:
    first = _list(client, token("ANALYST"), page="1", size="2").get_json()
    second = _list(client, token("ANALYST"), page="2", size="2").get_json()
    (total,) = owner.execute(
        "SELECT count(*) FROM recommendations.recommendation_decision"
        " WHERE computed_at >= now() - interval '90 days'"
    ).fetchone()
    assert first["page_info"] == {"page": 1, "size": 2, "total": total}
    assert second["page_info"] == {"page": 2, "size": 2, "total": total}
    assert {row["decision_id"] for row in first["recommendation_decisions"]}.isdisjoint(
        row["decision_id"] for row in second["recommendation_decisions"]
    )
    assert (
        first["recommendation_decisions"][-1]["computed_at"]
        > second["recommendation_decisions"][0]["computed_at"]
    )


@pytest.mark.parametrize(
    "query, code, field, detail",
    [
        ({"customer_id": "no"}, "invalid_query", "customer_id", "invalid_format"),
        ({"store_id": "0"}, "invalid_query", "store_id", "out_of_range"),
        ({"status": "unknown"}, "invalid_query", "status", "invalid_value"),
        ({"from": "2026-01-01"}, "invalid_query", "from", "invalid_format"),
        (
            {"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
            "invalid_query",
            "to",
            "not_after_from",
        ),
        ({"page": "0"}, "invalid_pagination", "page", "out_of_range"),
        ({"size": "101"}, "invalid_pagination", "size", "out_of_range"),
    ],
)
def test_validation_refusals_name_the_bad_field(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    query: dict[str, str],
    code: str,
    field: str,
    detail: str,
) -> None:
    response = _list(client, token("ADMIN"), **query)
    assert response.status_code == 422
    assert response.get_json()["code"] == code
    assert {"field": field, "code": detail} in response.get_json()["details"]


def test_store_manager_is_narrowed_and_cannot_name_or_open_another_store(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    manager_decisions: tuple[str, str, int],
    role_subjects: dict[str, str],
    owner: psycopg.Connection,
) -> None:
    allowed_id, forbidden_id, forbidden_store = manager_decisions
    assigned = {
        row[0]
        for row in owner.execute(
            "SELECT store_id FROM api_users.store_assignment_v1 WHERE user_id = %s",
            (role_subjects["STORE_MANAGER"],),
        ).fetchall()
    }
    page = _list(client, token("STORE_MANAGER"), size="100").get_json()
    assert page["recommendation_decisions"]
    assert {row["store_id"] for row in page["recommendation_decisions"]} <= assigned
    assert _get(client, token("STORE_MANAGER"), allowed_id).status_code == 200
    for response in (
        _list(client, token("STORE_MANAGER"), store_id=str(forbidden_store)),
        _get(client, token("STORE_MANAGER"), forbidden_id),
    ):
        assert (response.status_code, response.get_json()["code"]) == (
            403,
            "forbidden_store",
        )


@pytest.mark.parametrize("operation", ["list", "get"])
def test_authentication_negotiation_and_correlation_errors(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    redis_connection: redis.Redis,
    operation: str,
) -> None:
    call = (
        (lambda headers: _list(client, headers))
        if operation == "list"
        else (lambda headers: _get(client, headers, FULL_DECISION))
    )
    invalid = call({"X-Correlation-ID": "decision-invalid"})
    assert (invalid.status_code, invalid.get_json()["code"]) == (401, "invalid_token")
    assert invalid.headers["X-Correlation-ID"] == "decision-invalid"
    assert invalid.get_json()["correlation_id"] == "decision-invalid"

    live = token("ADMIN")
    claims = jwt.decode(
        live["Authorization"].removeprefix("Bearer "),
        options={"verify_signature": False},
    )
    redis_connection.delete("pub:auth:v1:live:" + claims["sid"])
    revoked = call(live)
    assert (revoked.status_code, revoked.get_json()["code"]) == (401, "session_revoked")

    unacceptable = call({**token("ADMIN"), "Accept": "text/csv"})
    assert (unacceptable.status_code, unacceptable.get_json()["code"]) == (
        406,
        "not_acceptable",
    )


@pytest.mark.parametrize("operation", ["list", "get"])
def test_each_operation_is_rate_limited_with_retry_after(
    app: Flask,
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    redis_connection: redis.Redis,
    role_subjects: dict[str, str],
    operation: str,
) -> None:
    kit = app.extensions["mosaiq_kit"]
    kit.rate_limit_enabled = True
    kit.rate_limit_requests = 1
    pattern = f"recommendations:rl:{role_subjects['ADMIN']}:*"
    for key in redis_connection.scan_iter(pattern):
        redis_connection.delete(key)
    call = (
        (lambda: _list(client, token("ADMIN")))
        if operation == "list"
        else (lambda: _get(client, token("ADMIN"), FULL_DECISION))
    )
    assert call().status_code == 200
    limited = call()
    assert (limited.status_code, limited.get_json()["code"]) == (429, "rate_limited")
    assert int(limited.headers["Retry-After"]) >= 1


@pytest.mark.parametrize("operation", ["list", "get"])
@pytest.mark.parametrize("outage", ["auth", "database"])
def test_each_operation_fails_closed_when_a_dependency_is_unavailable(
    app: Flask,
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    operation: str,
    outage: str,
) -> None:
    kit = app.extensions["mosaiq_kit"]
    unavailable_database = None
    if outage == "auth":
        kit.auth.redis = redis.Redis.from_url(
            "redis://127.0.0.1:1/0",
            socket_connect_timeout=0.05,
            socket_timeout=0.05,
        )
    else:
        unavailable_database = Database(
            "postgresql://svc_recommendations:unused@127.0.0.1:1/retail"
        )
        unavailable_database.timeout = 0.05
        kit.db, original_database = unavailable_database, kit.db
    try:
        response = (
            _list(client, token("ADMIN"))
            if operation == "list"
            else _get(client, token("ADMIN"), FULL_DECISION)
        )
        assert (response.status_code, response.get_json()["code"]) == (
            503,
            "auth_unavailable" if outage == "auth" else "database_unavailable",
        )
    finally:
        if unavailable_database is not None:
            unavailable_database.close()
            kit.db = original_database


def test_an_unknown_or_expired_decision_is_not_held(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    owner: psycopg.Connection,
) -> None:
    unknown = str(uuid.uuid4())
    response = _get(client, token("ADMIN"), unknown)
    assert (response.status_code, response.get_json()["code"]) == (
        404,
        "decision_not_found",
    )

    expired = str(uuid.uuid4())
    source = owner.execute(
        "SELECT customer_id, status, run_id, model_version_id, store_id,"
        " store_source, channel_id FROM recommendations.recommendation_decision"
        " ORDER BY computed_at DESC LIMIT 1"
    ).fetchone()
    try:
        owner.execute(
            "INSERT INTO recommendations.recommendation_decision"
            " (decision_id, customer_id, status, computed_at, run_id, model_version_id,"
            "  store_id, store_source, channel_id)"
            " VALUES (%s, %s, %s, now() - interval '91 days', %s, %s, %s, %s, %s)",
            (expired, *source),
        )
        assert _get(client, token("ADMIN"), expired).status_code == 404
        listed = _list(client, token("ADMIN"), customer_id=str(source[0]), size="100")
        assert expired not in {
            row["decision_id"] for row in listed.get_json()["recommendation_decisions"]
        }
    finally:
        owner.execute(
            "DELETE FROM recommendations.recommendation_decision"
            " WHERE decision_id = %s",
            (expired,),
        )


def test_list_and_get_write_no_decision_row(
    client: FlaskClient,
    token: Callable[..., dict[str, str]],
    owner: psycopg.Connection,
) -> None:
    def counts() -> tuple[int, ...]:
        return tuple(
            owner.execute(
                psycopg.sql.SQL("SELECT count(*) FROM recommendations.{}").format(
                    psycopg.sql.Identifier(table)
                )
            ).fetchone()[0]
            for table in TABLES
        )

    before = counts()
    assert _list(client, token("ADMIN"), size="3").status_code == 200
    assert _get(client, token("ADMIN"), FULL_DECISION).status_code == 200
    assert counts() == before
