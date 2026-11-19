"""GET /v2/recommendations over the seeded database (F24-04 #827, F24-07 #830).

What is tested: the refusals and their order (ADR-0084); the roles, and a floor
role confined to its stores (ADR-0060); the reference store and channel, the
usual store as the fallback, the exclusion counts, alternatives and elsewhere
entries over the real views; JSON and XML against the contract; the decision
recorded with its model version, equal to the answer; a cache keyed by store
and channel; and parity with the web on the default path (E-17). Real
PostgreSQL and real Redis.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import psycopg
import pytest
import yaml
from flask import Flask
from flask.testing import FlaskClient
from lxml import etree

import redis
from app import decision_store, rule, rule_v2
from app.cache import result_key_v2
from app.service import ViewReads
from app.store import current_label
from tests.test_golden import CASES, _setup

ROOT = Path(__file__).resolve().parents[1]
NS = "{https://mosaiq.maxthecoder.online/xml/recommendations/v2}"
OPENAPI = yaml.safe_load((ROOT / "contracts/recommendations/openapi.yaml").read_text())
XSD = etree.XMLSchema(
    etree.parse(str(ROOT / "contracts/recommendations/recommendations.v2.xsd"))
)
FIXTURE = json.loads((ROOT / "tests/golden/recommendations.json").read_text())
CUSTOMER = next(
    case["input"]["customer_id"]
    for case in FIXTURE["cases"]
    if case["outcome"]["status"] == "recommended"
)
# Seeded with PERSONALIZATION withdrawn (every fifth customer).
WITHDRAWN = "00000000-0000-0000-0000-000000000010"
FLOOR = ("STORE_MANAGER", "SALES_ASSOCIATE", "LOYALTY_OFFICER")
NO_STORE = 32000


def _valid(document: object) -> None:
    jsonschema.Draft202012Validator(
        {
            "$ref": "#/components/schemas/RecommendationV2",
            "components": OPENAPI["components"],
        }
    ).validate(document)


def _get(client: FlaskClient, headers: dict[str, str], **query: str):
    return client.get("/v2/recommendations", query_string=query, headers=headers)


def _ok(client: FlaskClient, headers: dict[str, str], **query: str) -> dict[str, Any]:
    response = _get(client, headers, **query)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    _valid(body)
    return body


def _error(response, status: int, code: str, field: str) -> None:
    assert response.status_code == status
    body = response.get_json()
    assert body["code"] == code
    assert body["details"][0]["field"] == field


@pytest.fixture(autouse=True, scope="module")
def leaves_no_decision_behind(owner_url: str) -> Iterator[None]:
    """Every decision a test made is removed: a leftover pins a run, a store or
    a product against deletion in every other suite."""
    with psycopg.connect(owner_url, autocommit=True) as connection:
        (before,) = connection.execute(
            "SELECT coalesce(max(computed_at), '-infinity')"
            " FROM recommendations.recommendation_decision"
        ).fetchone()
    yield
    with psycopg.connect(owner_url, autocommit=True) as connection:
        connection.execute(
            "DELETE FROM recommendations.recommendation_decision"
            " WHERE computed_at > %s",
            (before,),
        )


@pytest.fixture
def assigned(owner: psycopg.Connection, role_subjects: dict[str, str]):
    def stores(role: str) -> list[int]:
        return [
            store_id
            for (store_id,) in owner.execute(
                "SELECT store_id FROM api_users.store_assignment_v1"
                " WHERE user_id = %s ORDER BY store_id",
                (role_subjects[role],),
            )
        ]

    return stores


# ---------- refusals, in the contract's order ----------


@pytest.mark.parametrize(
    "query, field, code",
    [
        ({"store_id": "0"}, "store_id", "out_of_range"),
        ({"store_id": "-1"}, "store_id", "invalid_type"),
        ({"store_id": "abc"}, "store_id", "invalid_type"),
        ({"store_id": "2147483648"}, "store_id", "out_of_range"),
        ({"channel_id": "0"}, "channel_id", "out_of_range"),
        ({"channel_id": "1.5"}, "channel_id", "invalid_type"),
        ({"limit": "51"}, "limit", "out_of_range"),
    ],
)
def test_a_malformed_parameter_is_invalid_query_naming_it(
    client: FlaskClient, token: Callable[..., dict], query: dict, field: str, code: str
) -> None:
    response = _get(client, token(), customer_id=CUSTOMER, **query)
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_query"
    assert response.get_json()["details"] == [{"field": field, "code": code}]


def test_a_malformed_parameter_comes_before_a_forbidden_store(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    response = _get(
        client, token("SALES_ASSOCIATE"), customer_id="x", store_id=str(NO_STORE)
    )
    assert response.status_code == 422


@pytest.mark.parametrize("role", FLOOR)
def test_a_floor_role_naming_a_store_it_is_not_assigned_to_is_forbidden(
    client: FlaskClient,
    token: Callable[..., dict],
    assigned: Callable[[str], list[int]],
    role: str,
) -> None:
    other = next(s for s in range(1, 31) if s not in assigned(role))
    response = _get(client, token(role), customer_id=CUSTOMER, store_id=str(other))
    _error(response, 403, "forbidden_store", "store_id")


@pytest.mark.parametrize("role", FLOOR)
def test_a_floor_role_gets_403_before_422_for_a_store_that_does_not_exist(
    client: FlaskClient, token: Callable[..., dict], role: str
) -> None:
    response = _get(
        client,
        token(role),
        customer_id=str(uuid.uuid4()),  # unknown too: the store comes first
        store_id=str(NO_STORE),
        channel_id="99",
    )
    _error(response, 403, "forbidden_store", "store_id")


@pytest.mark.parametrize("role", FLOOR)
def test_a_floor_role_reads_at_its_own_store_and_unnarrowed_without_one(
    client: FlaskClient,
    token: Callable[..., dict],
    assigned: Callable[[str], list[int]],
    role: str,
) -> None:
    own = assigned(role)[0]
    body = _ok(client, token(role), customer_id=CUSTOMER, store_id=str(own))
    assert (body["store_id"], body["store_source"]) == (own, "given")
    body = _ok(client, token(role), customer_id=CUSTOMER)
    assert body["store_source"] == "usual_store"


def test_a_role_that_is_not_a_floor_role_names_any_store_and_learns_it_is_unknown(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    for role in ("ADMIN", "ANALYST", "MARKETING", "AUDITOR"):
        body = _ok(client, token(role), customer_id=CUSTOMER, store_id="30")
        assert body["store_id"] == 30
    response = _get(
        client,
        token("ADMIN"),
        customer_id=str(uuid.uuid4()),
        store_id=str(NO_STORE),
        channel_id="99",
    )
    _error(response, 422, "unknown_store", "store_id")


def test_an_unknown_channel_then_an_unknown_customer(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    response = _get(
        client, token("ADMIN"), customer_id=str(uuid.uuid4()), channel_id="99"
    )
    _error(response, 422, "unknown_channel", "channel_id")
    response = _get(client, token("ADMIN"), customer_id=str(uuid.uuid4()))
    _error(response, 422, "unknown_customer", "customer_id")


@pytest.mark.parametrize("role", ["INVENTORY_PLANNER", "CUSTOMER"])
def test_roles_outside_the_policy_are_forbidden(
    client: FlaskClient, token: Callable[..., dict], role: str
) -> None:
    response = _get(client, token(role), customer_id=CUSTOMER)
    assert response.status_code == 403
    assert response.get_json()["code"] == "forbidden_role"


# ---------- the answer, its formats and its record ----------


def test_the_usual_store_is_the_reference_unless_a_store_is_named(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    usual = _ok(client, token("ADMIN"), customer_id=CUSTOMER)
    assert usual["store_source"] == "usual_store"
    assert usual["store_id"] == usual["usual_store_id"] is not None
    assert usual["channel_id"] is None
    other = 1 if usual["store_id"] != 1 else 2
    given = _ok(
        client,
        token("ADMIN"),
        customer_id=CUSTOMER,
        store_id=str(other),
        channel_id="3",
    )
    assert (given["store_id"], given["store_source"]) == (other, "given")
    assert given["usual_store_id"] == usual["usual_store_id"]
    assert given["channel_id"] == 3
    for item in given["items"]:
        if item["kind"] != "elsewhere":
            assert item["availability"]["store_id"] == other
        assert item["availability"]["channel_id"] == 3


def test_the_answer_validates_as_xml_in_the_v2_namespace(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    for query in ({}, {"store_id": "1", "channel_id": "1"}):
        response = client.get(
            "/v2/recommendations",
            query_string={"customer_id": CUSTOMER, **query},
            headers={**token("ADMIN"), "Accept": "application/xml"},
        )
        assert response.status_code == 200
        document = etree.fromstring(response.data)
        assert document.tag == NS + "Recommendation"
        XSD.assertValid(document)
        assert document.find(NS + "decision_id") is not None
    response = client.get(
        "/v2/recommendations",
        query_string={"customer_id": WITHDRAWN, "store_id": "1"},
        headers={**token("ADMIN"), "Accept": "application/xml"},
    )
    XSD.assertValid(etree.fromstring(response.data))


def test_the_recorded_decision_is_the_answer_with_the_runs_model_version(
    client: FlaskClient, token: Callable[..., dict], owner: psycopg.Connection
) -> None:
    body = _ok(
        client, token("ADMIN"), customer_id=CUSTOMER, store_id="1", channel_id="1"
    )
    (version,) = owner.execute(
        "SELECT version_id FROM api_models.run_version_v1 WHERE run_id = %s",
        (body["run_id"],),
    ).fetchone()
    assert body["model_version_id"] == version
    stored = decision_store.read(owner, body["decision_id"])
    assert stored is not None
    recorded = stored.as_dict()
    for field in (
        "decision_id",
        "customer_id",
        "status",
        "computed_at",
        "run_id",
        "model_version_id",
        "store_id",
        "store_source",
        "channel_id",
        "exclusions",
        "items",
    ):
        assert recorded[field] == body[field], field


def test_a_run_with_no_version_yet_answers_null_and_never_the_published_one(
    owner_url: str,
) -> None:
    with psycopg.connect(owner_url) as connection:
        label = current_label(connection, CUSTOMER)
        # A run model-registry has not linked yet (its event is pending).
        (pending,) = connection.execute(
            "INSERT INTO segmentation_run"
            " (method, window_days, parameters, customer_count, run_at, data_origin)"
            " VALUES ('RFM_RULES', 180, '{\"window_days\": 180}'::jsonb, 1, now(),"
            " 'SEEDED') RETURNING run_id"
        ).fetchone()
        connection.execute("SET LOCAL ROLE svc_recommendations")
        unlinked = rule.Label("assigned", label.label_code, pending)
        result = rule_v2.recommend(
            ViewReads(connection, unlinked), CUSTOMER, datetime.now(UTC), 10
        )
        linked = rule_v2.recommend(
            ViewReads(connection, label), CUSTOMER, datetime.now(UTC), 10
        )
        connection.rollback()
    assert result.run_id == pending
    assert result.model_version_id is None
    assert linked.model_version_id is not None


def test_a_withdrawn_customer_is_answered_with_the_reason_and_recorded(
    client: FlaskClient, token: Callable[..., dict], owner: psycopg.Connection
) -> None:
    body = _ok(client, token("ADMIN"), customer_id=WITHDRAWN, store_id="4")
    assert body["status"] == "PERSONALIZATION_WITHDRAWN"
    assert body["items"] == [] and body["exclusions"] == []
    assert (body["store_id"], body["store_source"]) == (4, "given")
    for field in ("label_code", "run_id", "model_version_id", "usual_store_id"):
        assert body[field] is None
    stored = decision_store.read(owner, body["decision_id"])
    assert (stored.status, stored.store_id, stored.items) == (
        "PERSONALIZATION_WITHDRAWN",
        4,
        (),
    )


def test_a_cached_answer_is_keyed_by_store_and_channel(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
) -> None:
    client = app.test_client()
    first = _ok(client, token("ADMIN"), customer_id=CUSTOMER, store_id="1")
    again = _ok(client, token("ADMIN"), customer_id=CUSTOMER, store_id="1")
    assert again == first  # served from the cache, the same decision
    label = current_label(owner, CUSTOMER)
    key = result_key_v2(CUSTOMER, label.run_id, first["model_version_id"], 10, 1, None)
    assert json.loads(redis_connection.get(key))["decision_id"] == first["decision_id"]
    elsewhere = _ok(client, token("ADMIN"), customer_id=CUSTOMER, store_id="2")
    assert elsewhere["decision_id"] != first["decision_id"]
    assert elsewhere["store_id"] == 2
    channel = _ok(
        client, token("ADMIN"), customer_id=CUSTOMER, store_id="1", channel_id="3"
    )
    assert channel["decision_id"] != first["decision_id"]
    assert channel["channel_id"] == 3


def test_v1_is_untouched_by_v2(client: FlaskClient, token: Callable[..., dict]) -> None:
    v2 = _ok(client, token("ADMIN"), customer_id=CUSTOMER)
    v1 = client.get(
        "/v1/recommendations", query_string={"customer_id": CUSTOMER}, headers=token()
    ).get_json()
    assert "decision_id" not in v1 and "kind" not in v1["items"][0]
    assert [i["product_id"] for i in v1["items"]] == [
        i["product_id"] for i in v2["items"] if i["kind"] == "primary"
    ]


# ---------- the rule over the real views ----------


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = (tuple(map(math.radians, p)) for p in (a, b))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _as_service(connection: psycopg.Connection, store_id: int | None = None):
    connection.execute("SET LOCAL ROLE svc_recommendations")
    reads = ViewReads(connection, current_label(connection, CUSTOMER))
    reference = None
    if store_id is not None:
        reference = reads.stores()[store_id]
    return reads, reference


def test_a_channel_the_store_does_not_enable_is_reported_at_the_nearest_store(
    owner_url: str,
) -> None:
    with psycopg.connect(owner_url) as connection:
        stores = {
            s: (float(lat), float(lon), channels, active and status == "OPEN")
            for s, lat, lon, channels, active, status in connection.execute(
                "SELECT store_id, latitude, longitude, channel_ids, is_active, status"
                " FROM api_stores.store_directory_v2"
            )
        }
        reference = next(s for s, v in sorted(stores.items()) if 1 not in v[2])
        reads, store = _as_service(connection, reference)
        result = rule_v2.recommend(
            reads, CUSTOMER, datetime.now(UTC), 50, store=store, channel_id=1
        )
        stocked = {
            (s, p)
            for s, p in connection.execute(
                "SELECT store_id, product_id FROM api_stock.stock_level_v1"
                " WHERE quantity_on_hand > 0"
            )
        }
        connection.rollback()
    assert result.status == "none_match"
    assert [r for r, _ in result.exclusions][-1] == "channel_not_enabled"
    entries = [i for i in result.items if i.kind == "elsewhere"]
    assert entries
    for entry in entries:
        assert entry.unavailable_reason == "channel_not_enabled"
        selling = [
            s
            for s, (_, _, channels, open_) in stores.items()
            if s != reference
            and open_
            and 1 in channels
            and (s, entry.product_id) in stocked
        ]
        here = stores[reference][:2]
        expected = min(selling, key=lambda s: (_haversine(here, stores[s][:2]), s))
        assert entry.held_at_store_id == expected


def _discontinue(connection: psycopg.Connection, product_id: int) -> None:
    connection.execute("RESET ROLE")
    connection.execute(
        "INSERT INTO products.product_status (product_id, commercial_status)"
        " VALUES (%s, 'discontinued') ON CONFLICT (product_id)"
        " DO UPDATE SET commercial_status = 'discontinued'",
        (product_id,),
    )


def _alternatives(
    connection: psycopg.Connection,
) -> list[tuple[str, rule_v2.ResultV2, Any]]:
    """Every alternative the seed gives a personalized golden customer at the
    usual store, with no store or channel named."""
    found = []
    for case in CASES:
        customer = case["input"]["customer_id"]
        reads = ViewReads(connection, current_label(connection, customer))
        if not rule.eligibility(reads, customer).personalization:
            continue
        result = rule_v2.recommend(reads, customer, datetime.now(UTC), 50)
        found += [
            (customer, result, i) for i in result.items if i.kind == "alternative"
        ]
    return found


def test_an_unavailable_product_is_replaced_by_its_seeded_substitute(
    owner_url: str,
) -> None:
    """Over the seed as it is: a declared substitute, held at the reference
    store, stands in for a matching product with no stock there."""
    with psycopg.connect(owner_url) as connection:
        connection.execute("SET LOCAL ROLE svc_recommendations")
        found = _alternatives(connection)
        relations = set(
            connection.execute(
                "SELECT product_id, related_product_id"
                " FROM api_products.product_relation_v1"
                " WHERE relation_kind = 'substitute'"
            ).fetchall()
        )
        connection.rollback()
    assert found
    for _, result, alternative in found:
        assert (alternative.alternative_for, alternative.product_id) in relations
        assert alternative.unavailable_reason == "out_of_stock"
        assert alternative.quantity_on_hand >= 1
        assert alternative.held_at_store_id is None
        shown = [i.product_id for i in result.items]
        assert alternative.alternative_for not in shown
        assert shown.count(alternative.product_id) == 1
        assert dict(result.exclusions)["out_of_stock"] >= 1


def test_a_discontinued_product_is_not_sellable_and_keeps_its_alternative(
    owner_url: str,
) -> None:
    with psycopg.connect(owner_url) as connection:
        connection.execute("SET LOCAL ROLE svc_recommendations")
        customer, before, replaced = _alternatives(connection)[0]
        target = replaced.alternative_for
        _discontinue(connection, target)
        (active,) = connection.execute(
            "SELECT is_active FROM api_products.product_ref_v1 WHERE product_id = %s",
            (target,),
        ).fetchone()
        connection.execute("SET LOCAL ROLE svc_recommendations")
        reads = ViewReads(connection, current_label(connection, customer))
        after = rule_v2.recommend(reads, customer, datetime.now(UTC), 50)
        connection.rollback()
    assert active is False
    (alternative,) = [i for i in after.items if i.alternative_for == target]
    assert alternative.product_id == replaced.product_id
    assert alternative.unavailable_reason == "not_sellable"
    counts, earlier = dict(after.exclusions), dict(before.exclusions)
    assert "inactive" not in counts
    assert counts["not_sellable"] == earlier.get("not_sellable", 0) + 1
    assert counts.get("out_of_stock", 0) == earlier["out_of_stock"] - 1


def test_a_discontinued_primary_is_excluded_and_never_reported_elsewhere(
    owner_url: str,
) -> None:
    with psycopg.connect(owner_url) as connection:
        reads, _ = _as_service(connection)
        before = rule_v2.recommend(reads, CUSTOMER, datetime.now(UTC), 50)
        target = next(i.product_id for i in before.items if i.kind == "primary")
        _discontinue(connection, target)
        reads, _ = _as_service(connection)
        after = rule_v2.recommend(reads, CUSTOMER, datetime.now(UTC), 50)
        connection.rollback()
    assert target not in [i.product_id for i in after.items if i.kind == "primary"]
    assert not any(
        i.kind == "elsewhere" and i.product_id == target for i in after.items
    )
    assert dict(after.exclusions)["not_sellable"] == 1
    assert "inactive" not in dict(after.exclusions)


def test_an_out_of_stock_product_is_counted_and_never_recommended_here(
    owner_url: str,
) -> None:
    with psycopg.connect(owner_url) as connection:
        reads, _ = _as_service(connection)
        before = rule_v2.recommend(reads, CUSTOMER, datetime.now(UTC), 50)
        target = next(i.product_id for i in before.items if i.kind == "primary")
        connection.execute("RESET ROLE")
        connection.execute(
            "UPDATE inventory SET quantity_on_hand = 0"
            " WHERE product_id = %s AND store_id = %s",
            (target, before.store_id),
        )
        reads, _ = _as_service(connection)
        after = rule_v2.recommend(reads, CUSTOMER, datetime.now(UTC), 50)
        connection.rollback()
    assert target not in [i.product_id for i in after.items if i.kind == "primary"]
    assert dict(after.exclusions)["out_of_stock"] == (
        dict(before.exclusions).get("out_of_stock", 0) + 1
    )
    entry = next(i for i in after.items if i.product_id == target)
    assert entry.kind == "elsewhere"
    assert entry.held_at_store_id != before.store_id


# ---------- parity with the web on the default path (E-17, ADR-0067) ----------


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_default_path_recommends_what_the_web_did(
    case: dict[str, Any], owner_url: str
) -> None:
    """No store and no channel named: the primary items are /v1's, which the
    golden fixtures pin to the web's. Every difference is a recorded one."""
    customer = case["input"]["customer_id"]
    with psycopg.connect(owner_url) as connection:
        (anchor,) = connection.execute(
            "SELECT max(run_at) FROM segmentation_run"
        ).fetchone()
        _setup(case["id"], connection, anchor)
        connection.execute("SET LOCAL ROLE svc_recommendations")
        label = current_label(connection, customer)
        reads = ViewReads(connection, label)
        allowed = rule.eligibility(reads, customer)
        v1 = rule.recommend(reads, customer, anchor, 10, allowed.restricted)
        v2 = rule_v2.recommend(reads, customer, anchor, 10, allowed.restricted)
        connection.rollback()
    assert v2.usual_store_id == v1.usual_store_id
    assert v2.store_id == v1.usual_store_id
    primary = [i for i in v2.items if i.kind == "primary"]
    assert [(i.product_id, i.quantity_on_hand) for i in primary] == [
        (i["product_id"], i["quantity_on_hand"]) for i in v1.items
    ]
    for mine, theirs in zip(primary, v1.items, strict=True):
        assert [r.as_dict() for r in mine.reasons] == theirs["reasons"]
    if v1.status != "recommended":
        assert v2.status == v1.status
