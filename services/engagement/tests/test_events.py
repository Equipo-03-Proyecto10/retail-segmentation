"""Recording, replaying and voiding an event, against real PostgreSQL, MongoDB
and Redis (ADR-0038)."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/engagement"
NAMESPACE = "https://mosaiq.maxthecoder.online/xml/engagement/v1"
CUSTOMER = "00000000-0000-0000-0000-000000000007"

pytestmark = [pytest.mark.postgres, pytest.mark.mongo, pytest.mark.redis]


def valid(xml: bytes) -> bool:
    schema = etree.XMLSchema(etree.parse(str(CONTRACT / "engagement.xsd")))
    return schema.validate(etree.fromstring(xml))


@pytest.fixture
def db(mongo):
    return mongo.get_default_database(default="engagement")


def rollup(db, space, kind="QR_SCAN", store=3):
    found = db.engagement_daily.find_one(
        {"store_id": store, "day": space.day.isoformat(), "type": kind}
    )
    return None if found is None else found["count"]


# ---- recording -------------------------------------------------------------


def test_a_scan_is_recorded_attributed_and_counted(workspace, db):
    body = workspace.scan(customer_id=CUSTOMER)
    response = workspace.post(body)
    assert response.status_code == 201, response.get_json()
    event = response.get_json()
    assert event["client_event_id"] == body["client_event_id"]
    assert event["recorded_by"] == workspace.sub
    assert (event["voided"], event["history"]) == (False, [])
    assert (event["label_code"], event["rank"]) == (None, None)
    assert event["customer_id"] == CUSTOMER
    assert event["occurred_at"] == workspace.at()
    recorded = datetime.fromisoformat(event["recorded_at"])
    assert abs(datetime.now(UTC) - recorded) < timedelta(seconds=30)
    stored = db.engagement_event.find_one({"_id": event["event_id"]})
    assert stored["voided"] is False and stored["history"] == []
    assert isinstance(stored["store_id"], int) and isinstance(
        stored["occurred_at"], datetime
    )
    assert rollup(db, workspace) == 1


def test_a_click_carries_its_label_and_rank(workspace, db):
    response = workspace.post(workspace.click())
    assert response.status_code == 201
    event = response.get_json()
    assert (event["type"], event["label_code"], event["rank"]) == (
        "RECO_CLICK",
        "LOYAL",
        2,
    )
    assert rollup(db, workspace, "RECO_CLICK") == 1
    assert rollup(db, workspace, "QR_SCAN") is None


def test_a_scan_with_a_rank_and_a_click_without_one_are_refused(workspace):
    for body in (
        workspace.scan(rank=1),
        workspace.scan(label_code="LOYAL"),
        workspace.click() | {"rank": 0},
        {k: v for k, v in workspace.click().items() if k != "label_code"},
        workspace.scan() | {"surprise": True},
    ):
        response = workspace.post(body)
        assert (response.status_code, response.get_json()["code"]) == (
            422,
            "invalid_body",
        )


def test_an_unknown_store_product_or_customer_is_refused_in_that_order(workspace, db):
    cases = (
        (workspace.scan(store_id=9999), "unknown_store"),
        (workspace.scan(product_id=9999), "unknown_product"),
        (workspace.scan(customer_id=str(uuid.uuid4())), "unknown_customer"),
        (workspace.scan(store_id=9999, product_id=9999), "unknown_store"),
        (
            workspace.scan(product_id=9999, customer_id=str(uuid.uuid4())),
            "unknown_product",
        ),
        (workspace.scan(store_id=2**31 - 1), "unknown_store"),
    )
    for body, code in cases:
        response = workspace.post(body)
        assert (response.status_code, response.get_json()["code"]) == (422, code)
        assert (
            db.engagement_event.count_documents(
                {"client_event_id": body["client_event_id"]}
            )
            == 0
        )


def test_an_inactive_store_is_unknown(workspace, owner):
    owner.execute("UPDATE store SET is_active = FALSE WHERE store_id = 29")
    try:
        response = workspace.post(workspace.scan(store_id=29))
    finally:
        owner.execute("UPDATE store SET is_active = TRUE WHERE store_id = 29")
    assert (response.status_code, response.get_json()["code"]) == (422, "unknown_store")
    assert workspace.post(workspace.scan(store_id=29)).status_code == 201


def test_an_event_more_than_five_minutes_ahead_is_refused(workspace, db, cache):
    now = datetime.now(UTC)
    ahead = workspace.post(
        workspace.scan(occurred_at=(now + timedelta(minutes=6)).isoformat())
    )
    assert (ahead.status_code, ahead.get_json()["code"]) == (422, "occurred_in_future")
    assert ahead.get_json()["details"] == [
        {"field": "occurred_at", "code": "in_future"}
    ]
    near = workspace.post(
        workspace.scan(occurred_at=(now + timedelta(minutes=4)).isoformat())
    )
    assert near.status_code == 201
    # It counted on today's rollup and counter; take that back.
    day = (now + timedelta(minutes=4)).date()
    db.engagement_event.delete_many({"_id": near.get_json()["event_id"]})
    db.engagement_daily.update_one(
        {"store_id": 3, "day": day.isoformat(), "type": "QR_SCAN"},
        {"$inc": {"count": -1}},
    )
    db.engagement_daily.delete_one(
        {"store_id": 3, "day": day.isoformat(), "type": "QR_SCAN", "count": 0}
    )
    cache.decr(f"engagement:live:3:{day:%Y%m%d}:QR_SCAN")


def test_the_roles_that_may_record_are_admin_and_store_manager(workspace, token):
    for role, status in (
        ("ADMIN", 201),
        ("STORE_MANAGER", 201),
        ("ANALYST", 403),
        ("MARKETING", 403),
        ("AUDITOR", 403),
    ):
        response = workspace.post(workspace.scan(), headers=token(role, workspace.sub))
        assert response.status_code == status, role
        if status == 403:
            assert response.get_json()["code"] == "forbidden_role"


# ---- once per client event ---------------------------------------------------


def test_a_replay_answers_200_with_the_original_and_writes_nothing(workspace, db):
    body = workspace.scan()
    first = workspace.post(body)
    again = workspace.post(body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.get_json() == first.get_json()
    assert (
        db.engagement_event.count_documents(
            {"client_event_id": body["client_event_id"]}
        )
        == 1
    )
    assert rollup(db, workspace) == 1


def test_a_replay_that_differs_only_in_unkeyed_fields_is_still_a_replay(workspace, db):
    body = workspace.scan()
    first = workspace.post(body).get_json()
    again = workspace.post(
        body | {"occurred_at": workspace.at(13), "customer_id": CUSTOMER}
    )
    assert again.status_code == 200
    assert again.get_json() == first


@pytest.mark.parametrize(
    "change",
    [
        {"type": "RECO_CLICK", "label_code": "LOYAL", "rank": 1},
        {"store_id": 4},
        {"product_id": 13},
    ],
)
def test_a_client_event_id_reused_with_another_type_store_or_product_conflicts(
    workspace, db, change
):
    body = workspace.scan()
    assert workspace.post(body).status_code == 201
    response = workspace.post(body | change)
    assert (response.status_code, response.get_json()["code"]) == (
        409,
        "client_event_conflict",
    )
    assert (
        db.engagement_event.count_documents(
            {"client_event_id": body["client_event_id"]}
        )
        == 1
    )
    assert rollup(db, workspace) == 1


def test_the_same_client_event_posted_concurrently_is_one_document(workspace, db):
    body = workspace.scan()
    statuses: list[int] = []
    ids: list[str] = []
    gate = threading.Barrier(8)

    def send():
        client = workspace.client.application.test_client()
        gate.wait()
        response = client.post(
            "/v1/engagement-events", json=body, headers=workspace.headers
        )
        statuses.append(response.status_code)
        ids.append(response.get_json()["event_id"])

    threads = [threading.Thread(target=send) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(set(statuses)) == [200, 201] and statuses.count(201) == 1
    assert len(set(ids)) == 1
    assert (
        db.engagement_event.count_documents(
            {"client_event_id": body["client_event_id"]}
        )
        == 1
    )
    assert rollup(db, workspace) == 1


def test_many_first_events_of_a_day_make_one_rollup_document(workspace, db):
    results: list[int] = []
    gate = threading.Barrier(10)

    def send():
        client = workspace.client.application.test_client()
        gate.wait()
        results.append(
            client.post(
                "/v1/engagement-events",
                json=workspace.scan(),
                headers=workspace.headers,
            ).status_code
        )

    threads = [threading.Thread(target=send) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [201] * 10
    assert (
        db.engagement_daily.count_documents(
            {"store_id": 3, "day": workspace.day.isoformat(), "type": "QR_SCAN"}
        )
        == 1
    )
    assert rollup(db, workspace) == 10


# ---- voiding ----------------------------------------------------------------


def void(space, event_id, reason="Scanned the wrong shelf label.", headers=None):
    return space.client.post(
        f"/v1/engagement-events/{event_id}/void",
        json={"reason": reason},
        headers=headers or space.admin,
    )


def test_a_void_marks_the_event_records_who_and_why_and_uncounts_it(workspace, db):
    event = workspace.post(workspace.scan()).get_json()
    workspace.post(workspace.scan())
    assert rollup(db, workspace) == 2
    response = void(workspace, event["event_id"])
    assert response.status_code == 200
    voided = response.get_json()
    assert voided["voided"] is True
    assert len(voided["history"]) == 1
    entry = voided["history"][0]
    assert (entry["action"], entry["reason"]) == (
        "VOIDED",
        "Scanned the wrong shelf label.",
    )
    assert entry["by"] != workspace.sub
    assert abs(datetime.now(UTC) - datetime.fromisoformat(entry["at"])) < timedelta(
        seconds=30
    )
    assert rollup(db, workspace) == 1
    stored = db.engagement_event.find_one({"_id": event["event_id"]})
    assert stored["voided"] is True and len(stored["history"]) == 1


def test_a_second_void_conflicts_and_changes_nothing(workspace, db):
    event = workspace.post(workspace.scan()).get_json()
    assert void(workspace, event["event_id"]).status_code == 200
    second = void(workspace, event["event_id"], reason="again")
    assert (second.status_code, second.get_json()["code"]) == (409, "already_voided")
    stored = db.engagement_event.find_one({"_id": event["event_id"]})
    assert len(stored["history"]) == 1 and stored["history"][0]["reason"] != "again"
    assert rollup(db, workspace) == 0


def test_two_voids_at_once_count_once(workspace, db):
    event = workspace.post(workspace.scan()).get_json()
    workspace.post(workspace.scan())
    statuses: list[int] = []
    gate = threading.Barrier(6)

    def send():
        client = workspace.client.application.test_client()
        gate.wait()
        statuses.append(
            client.post(
                f"/v1/engagement-events/{event['event_id']}/void",
                json={"reason": "x"},
                headers=workspace.admin,
            ).status_code
        )

    threads = [threading.Thread(target=send) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [200, 409, 409, 409, 409, 409]
    assert rollup(db, workspace) == 1
    assert len(db.engagement_event.find_one({"_id": event["event_id"]})["history"]) == 1


def test_an_unknown_event_is_404_and_only_an_administrator_may_void(workspace, token):
    missing = void(workspace, str(uuid.uuid4()))
    assert (missing.status_code, missing.get_json()["code"]) == (404, "event_not_found")
    event = workspace.post(workspace.scan()).get_json()
    denied = void(workspace, event["event_id"], headers=workspace.headers)
    assert (denied.status_code, denied.get_json()["code"]) == (403, "forbidden_role")
    assert (
        void(workspace, event["event_id"], headers=token("AUDITOR")).status_code == 403
    )


def test_a_void_needs_a_reason(workspace):
    event = workspace.post(workspace.scan()).get_json()
    for body in (
        {},
        {"reason": ""},
        {"reason": "   "},
        {"reason": "x" * 501},
        {"reason": "ok", "extra": 1},
    ):
        response = workspace.client.post(
            f"/v1/engagement-events/{event['event_id']}/void",
            json=body,
            headers=workspace.admin,
        )
        assert (response.status_code, response.get_json()["code"]) == (
            422,
            "invalid_body",
        )
    assert (
        workspace.client.get(
            f"/v1/engagement-events/{event['event_id']}", headers=workspace.admin
        ).get_json()["voided"]
        is False
    )


# ---- reading one --------------------------------------------------------------


def test_one_event_is_read_by_its_id(workspace):
    event = workspace.post(workspace.click(customer_id=CUSTOMER)).get_json()
    response = workspace.client.get(
        f"/v1/engagement-events/{event['event_id']}", headers=workspace.headers
    )
    assert response.status_code == 200
    assert response.get_json() == event
    missing = workspace.client.get(
        f"/v1/engagement-events/{uuid.uuid4()}", headers=workspace.headers
    )
    assert (missing.status_code, missing.get_json()["code"]) == (404, "event_not_found")


# ---- XML ----------------------------------------------------------------------


def xml_request(kind, **fields):
    body = {
        "client_event_id": str(uuid.uuid4()),
        "store_id": "3",
        "product_id": "12",
        "occurred_at": "1999-04-03T12:00:00Z",
    } | fields
    if kind == "RecoClickRequest":
        body |= {"label_code": "LOYAL", "rank": "2"} | fields
    order = [
        "client_event_id",
        "type",
        "store_id",
        "product_id",
        "customer_id",
        "label_code",
        "rank",
        "occurred_at",
    ]
    body["type"] = "QR_SCAN" if kind == "QrScanRequest" else "RECO_CLICK"
    items = "".join(f"<{k}>{body[k]}</{k}>" for k in order if k in body)
    return (
        f'<EngagementEventRequest xmlns="{NAMESPACE}" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        f' xsi:type="{kind}">{items}</EngagementEventRequest>'
    ).encode()


@pytest.mark.parametrize("kind", ["QrScanRequest", "RecoClickRequest"])
def test_xml_in_and_xml_out_follow_the_schema(workspace, kind, db):
    day = workspace.day.isoformat()
    payload = xml_request(kind, occurred_at=f"{day}T12:00:00Z", customer_id=CUSTOMER)
    response = workspace.client.post(
        "/v1/engagement-events",
        data=payload,
        content_type="application/xml",
        headers=workspace.headers | {"Accept": "application/xml"},
    )
    assert response.status_code == 201, response.data
    assert valid(response.data), response.data
    root = etree.fromstring(response.data)
    assert root.get("{http://www.w3.org/2001/XMLSchema-instance}type") == (
        "QrScanEvent" if kind == "QrScanRequest" else "RecoClickEvent"
    )
    event_id = root.findtext(f"{{{NAMESPACE}}}event_id")
    fetched = workspace.client.get(
        f"/v1/engagement-events/{event_id}",
        headers=workspace.headers | {"Accept": "application/xml"},
    )
    assert fetched.status_code == 200 and valid(fetched.data)
    voided = workspace.client.post(
        f"/v1/engagement-events/{event_id}/void",
        data=(
            f'<VoidRequest xmlns="{NAMESPACE}"><reason>wrong shelf</reason>'
            "</VoidRequest>"
        ).encode(),
        content_type="application/xml",
        headers=workspace.admin | {"Accept": "application/xml"},
    )
    assert voided.status_code == 200 and valid(voided.data), voided.data


def test_an_xml_scan_carrying_a_rank_is_refused(workspace):
    day = workspace.day.isoformat()
    payload = xml_request("QrScanRequest", occurred_at=f"{day}T12:00:00Z").replace(
        b"</EngagementEventRequest>", b"<rank>1</rank></EngagementEventRequest>"
    )
    response = workspace.client.post(
        "/v1/engagement-events",
        data=payload,
        content_type="application/xml",
        headers=workspace.headers,
    )
    assert response.status_code == 422
