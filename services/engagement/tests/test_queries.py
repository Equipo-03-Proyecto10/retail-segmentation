"""Listing events and summarizing them (ADR-0038)."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/engagement"
NAMESPACE = "https://mosaiq.maxthecoder.online/xml/engagement/v1"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
CUSTOMERS = ["00000000-0000-0000-0000-00000000000" + str(n) for n in (1, 2)]

pytestmark = [pytest.mark.postgres, pytest.mark.mongo, pytest.mark.redis]


@pytest.fixture
def db(mongo):
    return mongo.get_default_database(default="engagement")


@pytest.fixture
def seeded(workspace):
    """Seven events on the workspace's day, newest last, one of them voided."""

    posted = []
    plan = [
        ("QR_SCAN", 3, 9, CUSTOMERS[0]),
        ("QR_SCAN", 3, 10, CUSTOMERS[0]),
        ("RECO_CLICK", 3, 11, CUSTOMERS[1]),
        ("QR_SCAN", 4, 12, None),
        ("RECO_CLICK", 4, 13, CUSTOMERS[0]),
        ("QR_SCAN", 3, 14, CUSTOMERS[1]),
        ("QR_SCAN", 4, 15, None),
    ]
    for kind, store, hour, customer in plan:
        body = (workspace.scan if kind == "QR_SCAN" else workspace.click)(
            store_id=store,
            occurred_at=workspace.at(hour),
            **({"customer_id": customer} if customer else {}),
        )
        response = workspace.post(body)
        assert response.status_code == 201
        posted.append(response.get_json())
    voided = workspace.client.post(
        f"/v1/engagement-events/{posted[1]['event_id']}/void",
        json={"reason": "dup"},
        headers=workspace.admin,
    )
    assert voided.status_code == 200
    return posted


def listing(space, **query):
    query = {"from": space.at(0), "to": space.at(23, 59)} | query
    return space.client.get(
        "/v1/engagement-events", query_string=query, headers=space.headers
    )


def ids(response):
    return [e["event_id"] for e in response.get_json()["engagement_events"]]


def test_events_come_newest_first_inside_the_range(workspace, seeded):
    response = listing(workspace)
    assert response.status_code == 200
    assert ids(response) == [e["event_id"] for e in reversed(seeded)]
    assert response.get_json()["page_info"] == {"page": 1, "size": 20, "total": 7}
    narrow = listing(workspace, **{"from": workspace.at(10), "to": workspace.at(12)})
    assert ids(narrow) == [
        seeded[3]["event_id"],
        seeded[2]["event_id"],
        seeded[1]["event_id"],
    ]


def test_the_filters_narrow_the_list(workspace, seeded):
    by_store = listing(workspace, store_id=4)
    assert ids(by_store) == [
        seeded[6]["event_id"],
        seeded[4]["event_id"],
        seeded[3]["event_id"],
    ]
    by_type = listing(workspace, type="RECO_CLICK")
    assert ids(by_type) == [seeded[4]["event_id"], seeded[2]["event_id"]]
    by_customer = listing(workspace, customer_id=CUSTOMERS[1].upper())
    assert ids(by_customer) == [seeded[5]["event_id"], seeded[2]["event_id"]]
    voided = listing(workspace, voided="true")
    assert ids(voided) == [seeded[1]["event_id"]]
    assert len(ids(listing(workspace, voided="false"))) == 6
    both = listing(workspace, store_id=3, type="QR_SCAN")
    assert ids(both) == [
        seeded[5]["event_id"],
        seeded[1]["event_id"],
        seeded[0]["event_id"],
    ]


def test_pages_split_the_list_without_overlap(workspace, seeded):
    first = listing(workspace, size=3)
    second = listing(workspace, size=3, page=2)
    third = listing(workspace, size=3, page=3)
    assert [len(ids(r)) for r in (first, second, third)] == [3, 3, 1]
    assert ids(first) + ids(second) + ids(third) == [
        e["event_id"] for e in reversed(seeded)
    ]
    assert third.get_json()["page_info"] == {"page": 3, "size": 3, "total": 7}


@pytest.mark.parametrize(
    "query,field,code",
    [
        ({"type": "SCAN"}, "type", "unknown_value"),
        ({"store_id": "three"}, "store_id", "invalid_format"),
        ({"store_id": "99999999999"}, "store_id", "invalid_format"),
        ({"customer_id": "nope"}, "customer_id", "invalid_format"),
        ({"voided": "yes"}, "voided", "invalid_format"),
        ({"from": "yesterday"}, "from", "invalid_format"),
        ({"from": "2026-10-15T10:00:00"}, "from", "invalid_format"),
        ({"to": "2026-10-15"}, "to", "invalid_format"),
        (
            {"from": "2026-10-15T10:00:00Z", "to": "2026-10-15T09:00:00Z"},
            "to",
            "before_from",
        ),
    ],
)
def test_a_bad_filter_is_422_invalid_query(workspace, query, field, code):
    response = workspace.client.get(
        "/v1/engagement-events", query_string=query, headers=workspace.headers
    )
    assert (response.status_code, response.get_json()["code"]) == (422, "invalid_query")
    assert response.get_json()["details"] == [{"field": field, "code": code}]


def test_bad_pagination_is_422_invalid_pagination(workspace):
    response = workspace.client.get(
        "/v1/engagement-events?size=1000", headers=workspace.headers
    )
    assert (response.status_code, response.get_json()["code"]) == (
        422,
        "invalid_pagination",
    )


def test_the_events_are_read_by_the_roles_the_contract_names(workspace, token):
    for role in ("ADMIN", "ANALYST", "STORE_MANAGER", "MARKETING", "AUDITOR"):
        assert (
            workspace.client.get(
                "/v1/engagement-events", headers=token(role)
            ).status_code
            == 200
        ), role
    denied = workspace.client.get(
        "/v1/engagement-events", headers=token("INVENTORY_PLANNER")
    )
    assert (denied.status_code, denied.get_json()["code"]) == (403, "forbidden_role")
    assert workspace.client.get("/v1/engagement-events").status_code == 401


def test_an_xml_page_types_every_event_and_validates(workspace, seeded):
    response = workspace.client.get(
        "/v1/engagement-events",
        query_string={"from": workspace.at(0), "to": workspace.at(23, 59), "size": 10},
        headers=workspace.headers | {"Accept": "application/xml"},
    )
    assert response.status_code == 200
    schema = etree.XMLSchema(etree.parse(str(CONTRACT / "engagement.xsd")))
    root = etree.fromstring(response.data)
    assert schema.validate(root), schema.error_log
    kinds = [
        c.get(f"{{{XSI}}}type")
        for c in root
        if c.tag == f"{{{NAMESPACE}}}EngagementEvent"
    ]
    assert kinds.count("QrScanEvent") == 5 and kinds.count("RecoClickEvent") == 2


# ---- every filter is served by its index (IXSCAN, nothing wasted) ---------------


def plan_stages(plan):
    stages = []
    while plan:
        stages.append(plan["stage"])
        if "inputStage" in plan:
            plan = plan["inputStage"]
        else:
            break
    return stages, plan


@pytest.mark.parametrize(
    "query,index",
    [
        ({"store_id": 3}, "store_id_1_occurred_at_-1"),
        ({"type": "QR_SCAN"}, "type_1_occurred_at_-1"),
        ({"customer_id": CUSTOMERS[0]}, "customer_id_1_occurred_at_-1"),
    ],
)
def test_each_filter_is_an_index_scan_that_reads_only_what_it_returns(
    workspace, seeded, db, query, index
):
    store = workspace.client.application.extensions["engagement_store"]
    asked: dict = {}
    real = store.find

    def spy(q, skip, limit):
        asked.update(q=q, skip=skip, limit=limit)
        return real(q, skip, limit)

    store.find = spy
    try:
        assert listing(workspace, **query).status_code == 200
    finally:
        store.find = real
    assert asked["q"]["occurred_at"].keys() == {"$gte", "$lte"}
    explained = db.command(
        {
            "explain": {
                "find": "engagement_event",
                "filter": asked["q"],
                "sort": {"occurred_at": -1},
                "limit": asked["limit"],
            },
            "verbosity": "executionStats",
        }
    )
    stages, leaf = plan_stages(explained["queryPlanner"]["winningPlan"])
    assert "IXSCAN" in stages and "COLLSCAN" not in stages and "SORT" not in stages
    assert leaf["indexName"] == index
    stats = explained["executionStats"]
    assert stats["totalDocsExamined"] == stats["nReturned"] > 0


# ---- summaries ---------------------------------------------------------------


def summary(space, **query):
    query = {"from": space.day.isoformat(), "to": space.day.isoformat()} | query
    return space.client.get(
        "/v1/engagement-summaries", query_string=query, headers=space.headers
    )


def test_summaries_count_only_what_is_not_voided(workspace, seeded):
    by_type = summary(workspace, group_by="type").get_json()
    assert by_type["groups"] == [
        {"key": "QR_SCAN", "count": 4},
        {"key": "RECO_CLICK", "count": 2},
    ]
    assert (by_type["from"], by_type["to"], by_type["group_by"]) == (
        workspace.day.isoformat(),
    ) * 2 + ("type",)
    by_store = summary(workspace, group_by="store").get_json()["groups"]
    assert by_store == [{"key": "3", "count": 3}, {"key": "4", "count": 3}]
    by_day = summary(workspace, group_by="day").get_json()["groups"]
    assert by_day == [{"key": workspace.day.isoformat(), "count": 6}]
    one_store = summary(workspace, group_by="type", store_id=4).get_json()["groups"]
    assert one_store == [
        {"key": "QR_SCAN", "count": 2},
        {"key": "RECO_CLICK", "count": 1},
    ]


def test_a_summary_over_several_days_groups_by_day(workspace, seeded):
    response = workspace.client.get(
        "/v1/engagement-summaries",
        query_string={
            "group_by": "day",
            "from": workspace.day.isoformat(),
            "to": "2020-12-31",
        },
        headers=workspace.headers,
    )
    keys = [g["key"] for g in response.get_json()["groups"]]
    assert keys == sorted(keys) and workspace.day.isoformat() in keys


@pytest.mark.parametrize(
    "query,field,code",
    [
        ({}, "group_by", "unknown_group_by"),
        ({"group_by": "week"}, "group_by", "unknown_group_by"),
        (
            {"group_by": "type", "from": "2026-10-15", "to": "2026-10-14"},
            "to",
            "before_from",
        ),
        ({"group_by": "type", "from": "15/10/2026"}, "from", "invalid_format"),
        ({"group_by": "type", "to": "2026-10-15T00:00:00Z"}, "to", "invalid_format"),
        ({"group_by": "type", "store_id": "x"}, "store_id", "invalid_format"),
    ],
)
def test_a_bad_summary_query_is_422_invalid_query(workspace, query, field, code):
    response = workspace.client.get(
        "/v1/engagement-summaries", query_string=query, headers=workspace.headers
    )
    assert (response.status_code, response.get_json()["code"]) == (422, "invalid_query")
    assert response.get_json()["details"] == [{"field": field, "code": code}]


def test_summaries_are_read_by_the_inventory_planner_too(workspace, token):
    for role in (
        "ADMIN",
        "ANALYST",
        "STORE_MANAGER",
        "MARKETING",
        "INVENTORY_PLANNER",
        "AUDITOR",
    ):
        response = workspace.client.get(
            "/v1/engagement-summaries?group_by=type", headers=token(role)
        )
        assert response.status_code == 200, role


def test_an_xml_summary_validates(workspace, seeded):
    response = workspace.client.get(
        "/v1/engagement-summaries",
        query_string={
            "group_by": "store",
            "from": workspace.day.isoformat(),
            "to": workspace.day.isoformat(),
        },
        headers=workspace.headers | {"Accept": "application/xml"},
    )
    schema = etree.XMLSchema(etree.parse(str(CONTRACT / "engagement.xsd")))
    assert response.status_code == 200 and schema.validate(
        etree.fromstring(response.data)
    )
