"""The engagement taxonomy over MongoDB (#799, contract /v2 of #798).

One section per acceptance criterion of #799, against real PostgreSQL, MongoDB
and Redis:

1. each kind posted twice with one client_event_id leaves one event (RN-55);
2. insert, query with index use, update, aggregation and indexing on the new
   kinds, with mongo/init.js the only setup;
3. a campaign exposure is announced on stream:engagement.v1.interaction_recorded
   (ADR-0075), and no service is called over HTTP;
4. a store visit or a cart event writes no row to `transaction`.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from lxml import etree

import redis

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/engagement"
NAMESPACE = "https://mosaiq.maxthecoder.online/xml/engagement/v2"
CUSTOMER = "00000000-0000-0000-0000-000000000007"
STREAM = "stream:engagement.v1.interaction_recorded"
ACL = ROOT / "redis/engagement.acl"

pytestmark = [pytest.mark.postgres, pytest.mark.mongo, pytest.mark.redis]


def kinds(space) -> dict[str, dict]:
    """One valid /v2 body per kind, on the workspace's own day."""

    def body(kind: str, **fields) -> dict:
        return {
            "client_event_id": str(uuid.uuid4()),
            "type": kind,
            "store_id": 3,
            "occurred_at": space.at(),
        } | fields

    return {
        "QR_SCAN": body("QR_SCAN", product_id=12),
        "RECO_CLICK": body("RECO_CLICK", product_id=12, label_code="LOYAL", rank=2),
        "STORE_VISIT": body("STORE_VISIT", customer_id=CUSTOMER),
        "RECO_IMPRESSION": body(
            "RECO_IMPRESSION", product_id=12, label_code="LOYAL", rank=1
        ),
        "CAMPAIGN_EXPOSURE": body(
            "CAMPAIGN_EXPOSURE", campaign_id=4, customer_id=CUSTOMER
        ),
        "CART_ADD": body("CART_ADD", product_id=12, quantity=2),
        "CART_REMOVE": body("CART_REMOVE", product_id=12, quantity=1),
        "NAVIGATION": body("NAVIGATION", screen="PRODUCT_DETAIL"),
    }


def post(space, body: dict, headers: dict | None = None):
    return space.client.post(
        "/v2/engagement-events", json=body, headers=headers or space.headers
    )


def to_xml(body: dict) -> bytes:
    xsi = "http://www.w3.org/2001/XMLSchema-instance"
    names = {
        "QR_SCAN": "QrScan",
        "RECO_CLICK": "RecoClick",
        "STORE_VISIT": "StoreVisit",
        "RECO_IMPRESSION": "RecoImpression",
        "CAMPAIGN_EXPOSURE": "CampaignExposure",
        "CART_ADD": "CartAdd",
        "CART_REMOVE": "CartRemove",
        "NAVIGATION": "Navigation",
    }
    root = etree.Element(
        f"{{{NAMESPACE}}}EngagementEventRequest", nsmap={None: NAMESPACE, "xsi": xsi}
    )
    root.set(f"{{{xsi}}}type", names[body["type"]] + "Request")
    # The XSD's sequence order for every shape.
    order = (
        "client_event_id",
        "type",
        "store_id",
        "product_id",
        "campaign_id",
        "customer_id",
        "label_code",
        "rank",
        "quantity",
        "screen",
        "occurred_at",
    )
    for field in order:
        if field in body:
            etree.SubElement(root, f"{{{NAMESPACE}}}{field}").text = str(body[field])
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def valid_v2(xml: bytes) -> bool:
    schema = etree.XMLSchema(etree.parse(str(CONTRACT / "engagement.v2.xsd")))
    return schema.validate(etree.fromstring(xml))


@pytest.fixture
def db(mongo):
    return mongo.get_default_database(default="engagement")


def rollup(db, space, kind, store=3):
    found = db.engagement_daily.find_one(
        {"store_id": store, "day": space.day.isoformat(), "type": kind}
    )
    return None if found is None else found["count"]


# ---- 1. one event per client_event_id, for every kind (RN-55) ----------------


@pytest.mark.parametrize(
    "kind",
    [
        "QR_SCAN",
        "RECO_CLICK",
        "STORE_VISIT",
        "RECO_IMPRESSION",
        "CAMPAIGN_EXPOSURE",
        "CART_ADD",
        "CART_REMOVE",
        "NAVIGATION",
    ],
)
def test_each_kind_posted_twice_with_one_client_event_id_is_one_event(
    workspace, db, kind
):
    body = kinds(workspace)[kind]
    first = post(workspace, body)
    assert first.status_code == 201, first.get_json()
    again = post(workspace, body)
    assert again.status_code == 200, again.get_json()
    assert again.get_json() == first.get_json()
    assert (
        db.engagement_event.count_documents(
            {"client_event_id": body["client_event_id"]}
        )
        == 1
    )
    assert rollup(db, workspace, kind) == 1
    event = first.get_json()
    assert event["type"] == kind and event["recorded_by"] == workspace.sub
    for field in ("product_id", "campaign_id", "quantity", "screen", "label_code"):
        assert event[field] == body.get(field), field


def test_each_kind_posted_twice_in_xml_is_one_event_and_answers_valid_xml(
    workspace, db
):
    headers = workspace.headers | {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    for kind, body in kinds(workspace).items():
        statuses = []
        for _ in range(2):
            response = workspace.client.post(
                "/v2/engagement-events", data=to_xml(body), headers=headers
            )
            statuses.append(response.status_code)
            assert valid_v2(response.data), (kind, response.data)
            root = etree.fromstring(response.data)
            assert root.tag == f"{{{NAMESPACE}}}EngagementEvent"
        assert statuses == [201, 200], (kind, statuses)
        assert (
            db.engagement_event.count_documents(
                {"client_event_id": body["client_event_id"]}
            )
            == 1
        )


def test_a_reused_client_event_id_with_another_kind_is_409(workspace):
    sent = kinds(workspace)
    visit = sent["STORE_VISIT"]
    assert post(workspace, visit).status_code == 201
    cart = sent["CART_ADD"] | {"client_event_id": visit["client_event_id"]}
    reused = post(workspace, cart)
    assert reused.status_code == 409
    assert reused.get_json()["code"] == "client_event_conflict"
    # /v1 cannot see a /v2 kind, so its retry of that id conflicts too.
    v1 = workspace.post(workspace.scan(client_event_id=visit["client_event_id"]))
    assert v1.status_code == 409


def test_each_kind_refuses_the_fields_of_another(workspace):
    sent = kinds(workspace)
    for body in (
        sent["STORE_VISIT"] | {"product_id": 12},
        sent["CART_ADD"] | {"label_code": "LOYAL"},
        sent["NAVIGATION"] | {"quantity": 1},
        {k: v for k, v in sent["CAMPAIGN_EXPOSURE"].items() if k != "customer_id"},
        sent["CART_REMOVE"] | {"quantity": 0},
    ):
        response = post(workspace, body)
        assert response.status_code == 422, body
        assert response.get_json()["code"] == "invalid_body"


def test_a_reference_is_checked_only_where_the_kind_has_it(workspace):
    sent = kinds(workspace)
    unknown = post(workspace, sent["CART_ADD"] | {"product_id": 999999})
    assert unknown.get_json()["code"] == "unknown_product"
    nobody = post(
        workspace, sent["CAMPAIGN_EXPOSURE"] | {"customer_id": str(uuid.uuid4())}
    )
    assert nobody.get_json()["code"] == "unknown_customer"
    closed = post(workspace, sent["NAVIGATION"] | {"store_id": 999999})
    assert closed.get_json()["code"] == "unknown_store"


# ---- 2. MongoDB: insert, query with index use, update, aggregation, indexes --


def test_a_new_kind_is_stored_with_its_own_fields_and_nothing_else(workspace, db):
    for kind, body in kinds(workspace).items():
        event = post(workspace, body).get_json()
        stored = db.engagement_event.find_one({"_id": event["event_id"]})
        extra = {
            key
            for key in ("product_id", "campaign_id", "quantity", "screen", "rank")
            if key in stored
        }
        assert extra == {
            key
            for key in ("product_id", "campaign_id", "quantity", "screen", "rank")
            if key in body
        }, kind
        relayed = kind in ("RECO_IMPRESSION", "RECO_CLICK", "CAMPAIGN_EXPOSURE")
        assert stored.get("relay_pending") == (["RECORDED"] if relayed else None)


def plan_stages(plan):
    stages = []
    while plan:
        stages.append(plan["stage"])
        if "inputStage" in plan:
            plan = plan["inputStage"]
        else:
            break
    return stages, plan


def explain(db, query, sort, limit):
    explained = db.command(
        {
            "explain": {
                "find": "engagement_event",
                "filter": query,
                "sort": sort,
                "limit": limit,
            },
            "verbosity": "executionStats",
        }
    )
    stages, leaf = plan_stages(explained["queryPlanner"]["winningPlan"])
    return stages, leaf, explained["executionStats"]


@pytest.mark.parametrize(
    "kind", ["STORE_VISIT", "RECO_IMPRESSION", "CAMPAIGN_EXPOSURE", "CART_ADD"]
)
def test_listing_a_new_kind_is_an_index_scan(workspace, db, kind):
    for _ in range(3):
        post(workspace, kinds(workspace)[kind])
    store = workspace.client.application.extensions["engagement_store"]
    asked: dict = {}
    real = store.find

    def spy(q, skip, limit):
        asked.update(q=q, limit=limit)
        return real(q, skip, limit)

    store.find = spy
    try:
        response = workspace.client.get(
            "/v2/engagement-events",
            query_string={
                "type": kind,
                "from": workspace.at(0),
                "to": workspace.at(23, 59),
            },
            headers=workspace.headers,
        )
    finally:
        store.find = real
    assert response.status_code == 200
    page = response.get_json()
    assert {event["type"] for event in page["engagement_events"]} == {kind}
    stages, leaf, stats = explain(db, asked["q"], {"occurred_at": -1}, asked["limit"])
    assert "IXSCAN" in stages and "COLLSCAN" not in stages and "SORT" not in stages
    assert leaf["indexName"] == "type_1_occurred_at_-1"
    assert stats["totalDocsExamined"] == stats["nReturned"] >= 3


def test_the_relay_scan_reads_only_its_partial_index(workspace, db):
    post(workspace, kinds(workspace)["CAMPAIGN_EXPOSURE"])
    from app.events import RELAY_PENDING

    stages, leaf, stats = explain(db, RELAY_PENDING, {"occurred_at": 1}, 100)
    assert "COLLSCAN" not in stages and "SORT" not in stages
    assert leaf["indexName"] == "relay_pending_occurred_at_1"
    assert leaf["isPartial"] is True
    # Only events with a notice pending are in the index at all.
    assert stats["totalKeysExamined"] == db.engagement_event.count_documents(
        RELAY_PENDING
    )


def test_a_void_of_a_new_kind_updates_the_document_and_its_count(workspace, db):
    sent = kinds(workspace)
    for kind in ("CART_ADD", "CAMPAIGN_EXPOSURE"):
        event = post(workspace, sent[kind]).get_json()
        assert rollup(db, workspace, kind) == 1
        voided = workspace.client.post(
            f"/v2/engagement-events/{event['event_id']}/void",
            json={"reason": "Recorded by mistake"},
            headers=workspace.admin,
        )
        assert voided.status_code == 200, voided.get_json()
        body = voided.get_json()
        assert body["voided"] is True and body["history"][0]["action"] == "VOIDED"
        assert rollup(db, workspace, kind) == 0
        stored = db.engagement_event.find_one({"_id": event["event_id"]})
        if kind == "CAMPAIGN_EXPOSURE":
            assert stored["relay_pending"] == ["RECORDED", "VOIDED"]
        else:
            assert "relay_pending" not in stored
        again = workspace.client.post(
            f"/v2/engagement-events/{event['event_id']}/void",
            json={"reason": "Twice"},
            headers=workspace.admin,
        )
        assert again.get_json()["code"] == "already_voided"


def test_a_v2_summary_gives_every_kind_its_own_key(workspace):
    sent = kinds(workspace)
    for body in sent.values():
        assert post(workspace, body).status_code == 201
    post(workspace, sent["CART_ADD"] | {"client_event_id": str(uuid.uuid4())})
    query = {
        "from": workspace.day.isoformat(),
        "to": workspace.day.isoformat(),
        "store_id": 3,
        "group_by": "type",
    }
    v2 = workspace.client.get(
        "/v2/engagement-summaries", query_string=query, headers=workspace.headers
    ).get_json()
    counts = {group["key"]: group["count"] for group in v2["groups"]}
    assert counts == {kind: 1 for kind in sent} | {"CART_ADD": 2}
    # The frozen /v1 still counts its two kinds only.
    v1 = workspace.client.get(
        "/v1/engagement-summaries", query_string=query, headers=workspace.headers
    ).get_json()
    assert {g["key"]: g["count"] for g in v1["groups"]} == {
        "QR_SCAN": 1,
        "RECO_CLICK": 1,
    }
    by_store = workspace.client.get(
        "/v2/engagement-summaries",
        query_string=query | {"group_by": "store"},
        headers=workspace.headers | {"Accept": "application/xml"},
    )
    assert valid_v2(by_store.data)
    assert b"<count>9</count>" in by_store.data


def test_the_rollup_rebuild_agrees_with_the_new_kinds(workspace, db):
    from app.live import LiveCounters
    from app.rollup import rebuild

    for body in kinds(workspace).values():
        post(workspace, body)
    before = {
        doc["type"]: doc["count"]
        for doc in db.engagement_daily.find({"day": workspace.day.isoformat()})
    }
    store = workspace.client.application.extensions["engagement_store"]
    rebuild(store, LiveCounters(None), workspace.day, workspace.day)
    after = {
        doc["type"]: doc["count"]
        for doc in db.engagement_daily.find({"day": workspace.day.isoformat()})
    }
    assert after == before and len(after) == 8


def test_v1_never_sees_a_v2_kind(workspace):
    event = post(workspace, kinds(workspace)["CART_ADD"]).get_json()
    path = f"/v1/engagement-events/{event['event_id']}"
    assert workspace.client.get(path, headers=workspace.headers).status_code == 404
    voided = workspace.client.post(
        path + "/void", json={"reason": "x"}, headers=workspace.admin
    )
    assert voided.status_code == 404
    listed = workspace.client.get(
        "/v1/engagement-events",
        query_string={"from": workspace.at(0), "to": workspace.at(23, 59)},
        headers=workspace.headers,
    ).get_json()
    assert all(
        e["type"] in ("QR_SCAN", "RECO_CLICK") for e in listed["engagement_events"]
    )
    v2 = workspace.client.get(path.replace("/v1/", "/v2/"), headers=workspace.headers)
    assert v2.status_code == 200 and v2.get_json()["voided"] is False


def test_an_xml_page_of_every_kind_validates(workspace):
    for body in kinds(workspace).values():
        post(workspace, body)
    response = workspace.client.get(
        "/v2/engagement-events",
        query_string={"from": workspace.at(0), "to": workspace.at(23, 59)},
        headers=workspace.headers | {"Accept": "application/xml"},
    )
    assert response.status_code == 200
    assert valid_v2(response.data), response.data
    root = etree.fromstring(response.data)
    xsi = "{http://www.w3.org/2001/XMLSchema-instance}type"
    typed = {child.get(xsi) for child in root if child.get(xsi)}
    assert len(typed) == 8


# ---- 3. the exposure reaches the stream; nothing is called over HTTP ---------


@pytest.fixture
def relay(redis_url, cache, workspace):
    """The relay, publishing as an ACL user defined from redis/engagement.acl."""

    from mosaiq_kit import StreamPublisher

    from app.relay import InteractionRelay

    user = "svc_engagement_relaytest"
    line = ACL.read_text().strip().replace("{{password}}", ">pw")
    rules = re.findall(r"\([^)]*\)|\S+", line.split(" ", 2)[2])
    cache.execute_command("ACL", "DELUSER", user)
    cache.execute_command("ACL", "SETUSER", user, *rules)
    signed_in = redis.Redis.from_url(
        redis_url, username=user, password="pw", socket_timeout=3
    )
    store = workspace.client.application.extensions["engagement_store"]
    yield InteractionRelay(store, StreamPublisher(signed_in, STREAM))
    signed_in.close()
    cache.execute_command("ACL", "DELUSER", user)


def entries_for(cache, event_id: str) -> list[dict]:
    return [
        fields
        for _, fields in cache.xrange(STREAM)
        if fields.get("event_id", "").startswith(event_id + ":")
    ]


def test_a_campaign_exposure_is_announced_on_the_stream(workspace, db, relay, cache):
    body = kinds(workspace)["CAMPAIGN_EXPOSURE"]
    event = post(workspace, body).get_json()
    assert db.engagement_event.find_one({"_id": event["event_id"]})["relay_pending"]
    assert entries_for(cache, event["event_id"]) == []  # nothing on the request path
    while relay.run_once():
        pass
    stored = db.engagement_event.find_one({"_id": event["event_id"]})
    assert stored["relay_pending"] == []
    assert entries_for(cache, event["event_id"]) == [
        {
            "event_id": event["event_id"] + ":recorded",
            "v": "1",
            "notice": "RECORDED",
            "type": "CAMPAIGN_EXPOSURE",
            "store_id": "3",
            "customer_id": CUSTOMER,
            "campaign_id": "4",
            "occurred_at": workspace.at(),
        }
    ]
    workspace.client.post(
        f"/v2/engagement-events/{event['event_id']}/void",
        json={"reason": "Shown to the wrong customer"},
        headers=workspace.admin,
    )
    while relay.run_once():
        pass
    notices = [e["notice"] for e in entries_for(cache, event["event_id"])]
    assert notices == ["RECORDED", "VOIDED"]
    assert (
        db.engagement_event.find_one({"_id": event["event_id"]})["relay_pending"] == []
    )


def test_only_the_relayed_kinds_reach_the_stream(workspace, relay, cache):
    ids = {
        kind: post(workspace, body).get_json()["event_id"]
        for kind, body in kinds(workspace).items()
    }
    while relay.run_once():
        pass
    published = {kind for kind, event_id in ids.items() if entries_for(cache, event_id)}
    assert published == {"RECO_IMPRESSION", "RECO_CLICK", "CAMPAIGN_EXPOSURE"}


def test_a_failed_publish_keeps_the_notice_pending(workspace, db, relay):
    event = post(workspace, kinds(workspace)["RECO_IMPRESSION"]).get_json()

    class Down:
        def xadd(self, *args, **kwargs):
            raise redis.ConnectionError("redis-state is down")

    real = relay.publisher.redis
    relay.publisher.redis = Down()
    try:
        with pytest.raises(redis.ConnectionError):
            relay.run_once()
    finally:
        relay.publisher.redis = real
    stored = db.engagement_event.find_one({"_id": event["event_id"]})
    assert stored["relay_pending"] == ["RECORDED"]
    while relay.run_once():
        pass
    assert (
        db.engagement_event.find_one({"_id": event["event_id"]})["relay_pending"] == []
    )


def test_the_service_calls_no_other_service_over_http():
    http = re.compile(r"\b(import (requests|httpx|aiohttp|urllib3)|urllib\.request)")
    for path in (ROOT / "app").rglob("*.py"):
        assert not http.search(path.read_text()), path.name
    # XADD only through the kit (ADR-0044 guard), never in the service.
    for path in (ROOT / "app").rglob("*.py"):
        assert not re.search(r"\bxadd\b", path.read_text(), re.I), path.name


# ---- 4. no visit or cart is a sale ------------------------------------------------


def test_a_store_visit_or_a_cart_event_writes_no_transaction(
    workspace, owner, service_url
):
    import psycopg

    def transactions() -> int:
        return owner.execute("SELECT count(*) FROM transaction").fetchone()[0]

    before = transactions()
    sent = kinds(workspace)
    for kind in ("STORE_VISIT", "CART_ADD", "CART_REMOVE", "QR_SCAN"):
        assert post(workspace, sent[kind]).status_code == 201
    assert transactions() == before
    # And it could not: svc_engagement holds no privilege on the table.
    with psycopg.connect(service_url) as connection:
        allowed = connection.execute(
            "SELECT has_table_privilege(current_user, 'public.transaction', %s)",
            ("INSERT",),
        ).fetchone()[0]
    assert allowed is False


def test_a_requeue_redelivers_a_day_with_its_voids(workspace, db, relay, cache):
    from app.relay import requeue

    store = workspace.client.application.extensions["engagement_store"]
    sent = kinds(workspace)
    exposure = post(workspace, sent["CAMPAIGN_EXPOSURE"]).get_json()["event_id"]
    cart = post(workspace, sent["CART_ADD"]).get_json()["event_id"]
    workspace.client.post(
        f"/v2/engagement-events/{exposure}/void",
        json={"reason": "x"},
        headers=workspace.admin,
    )
    while relay.run_once():
        pass
    assert requeue(store, workspace.day, workspace.day) == 1
    assert db.engagement_event.find_one({"_id": exposure})["relay_pending"] == [
        "RECORDED",
        "VOIDED",
    ]
    assert "relay_pending" not in db.engagement_event.find_one({"_id": cart})
    while relay.run_once():
        pass
    notices = [e["notice"] for e in entries_for(cache, exposure)]
    assert notices == ["RECORDED", "VOIDED", "RECORDED", "VOIDED"]
