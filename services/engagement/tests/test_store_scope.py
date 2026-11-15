"""Engagement events and summaries are narrowed to the caller's stores (F19-07).

ADR-0060: the floor roles work only in their assigned stores, read per request
from `api_users.store_assignment_v1`; ADMIN is not confined. A refusal is 403
`forbidden_store`, in JSON and in XML, and it precedes every other check.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest
from lxml import etree

from tests.conftest import set_assignments

pytestmark = [pytest.mark.postgres, pytest.mark.mongo, pytest.mark.redis]

COMMON = "https://mosaiq.maxthecoder.online/xml/common/v1"
HOME, OTHER = 3, 4


@pytest.fixture
def scope(workspace, floor_subjects, owner_url, db_events):
    """The workspace's manager assigned to HOME only; its admin cleans up."""

    manager = floor_subjects["STORE_MANAGER"]
    with psycopg.connect(owner_url, autocommit=True) as connection:
        before = [
            row[0]
            for row in connection.execute(
                "SELECT store_id FROM users.store_assignment WHERE user_id = %s",
                (manager,),
            )
        ]
    set_assignments(owner_url, manager, [HOME])
    yield workspace, manager
    set_assignments(owner_url, manager, before)
    db_events.delete_many(
        {"store_id": OTHER, "occurred_at": {"$lt": datetime(2021, 1, 1, tzinfo=UTC)}}
    )


@pytest.fixture
def db_events(mongo):
    return mongo.get_default_database(default="engagement").engagement_event


def code_of(response):
    if response.mimetype == "application/xml":
        return etree.fromstring(response.data).findtext(f"{{{COMMON}}}code")
    return response.get_json()["code"]


def other_store_event(space):
    """An event the administrator recorded in OTHER, on the workspace's day."""

    response = space.post(space.scan(store_id=OTHER), headers=space.admin)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_recording_in_another_store_is_refused_with_no_event(scope, accept, db_events):
    space, _ = scope
    body = space.scan(store_id=OTHER)
    refused = space.client.post(
        "/v1/engagement-events",
        json=body,
        headers=space.headers | {"Accept": accept},
    )
    assert (refused.status_code, code_of(refused)) == (403, "forbidden_store")
    assert db_events.count_documents({"client_event_id": body["client_event_id"]}) == 0
    assert space.post(space.scan(store_id=HOME)).status_code == 201


def test_the_store_refusal_precedes_the_unknown_store(scope):
    space, _ = scope
    refused = space.post(space.scan(store_id=9999))
    assert (refused.status_code, code_of(refused)) == (403, "forbidden_store")


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_reading_an_event_of_another_store_is_refused_with_no_data(scope, accept):
    space, _ = scope
    event = other_store_event(space)
    refused = space.client.get(
        f"/v1/engagement-events/{event['event_id']}",
        headers=space.headers | {"Accept": accept},
    )
    assert (refused.status_code, code_of(refused)) == (403, "forbidden_store")
    assert event["event_id"].encode() not in refused.data


def test_a_list_names_only_the_callers_stores(scope):
    space, _ = scope
    mine = space.post(space.scan(store_id=HOME)).get_json()
    theirs = other_store_event(space)
    query = {"from": space.at(0), "to": space.at(23, 59), "size": 100}
    listed = space.client.get(
        "/v1/engagement-events", query_string=query, headers=space.headers
    )
    ids = {item["event_id"] for item in listed.get_json()["engagement_events"]}
    assert mine["event_id"] in ids and theirs["event_id"] not in ids
    named = space.client.get(
        "/v1/engagement-events",
        query_string=query | {"store_id": OTHER},
        headers=space.headers,
    )
    assert (named.status_code, code_of(named)) == (403, "forbidden_store")


def test_summaries_cover_only_the_callers_stores(scope):
    space, _ = scope
    space.post(space.scan(store_id=HOME))
    other_store_event(space)
    day = space.day.isoformat()
    query = {"group_by": "store", "from": day, "to": day}
    own = space.client.get(
        "/v1/engagement-summaries", query_string=query, headers=space.headers
    ).get_json()
    assert {g["key"] for g in own["groups"]} == {str(HOME)}
    everyone = space.client.get(
        "/v1/engagement-summaries", query_string=query, headers=space.admin
    ).get_json()
    assert {g["key"] for g in everyone["groups"]} == {str(HOME), str(OTHER)}
    refused = space.client.get(
        "/v1/engagement-summaries",
        query_string=query | {"store_id": OTHER},
        headers=space.headers,
    )
    assert (refused.status_code, code_of(refused)) == (403, "forbidden_store")


def test_the_administrator_is_not_confined(scope):
    space, _ = scope
    theirs = other_store_event(space)
    read = space.client.get(
        f"/v1/engagement-events/{theirs['event_id']}", headers=space.admin
    )
    assert read.status_code == 200


def test_a_floor_user_with_no_assignment_works_in_no_store(scope, owner_url):
    space, manager = scope
    set_assignments(owner_url, manager, [])
    assert space.post(space.scan(store_id=HOME)).status_code == 403
    listed = space.client.get("/v1/engagement-events", headers=space.headers)
    assert listed.status_code == 200 and listed.get_json()["engagement_events"] == []


def test_a_removed_assignment_is_refused_on_the_next_call(scope, owner_url):
    space, manager = scope
    assert space.post(space.scan(store_id=HOME)).status_code == 201
    set_assignments(owner_url, manager, [])
    refused = space.post(space.scan(store_id=HOME))
    assert (refused.status_code, code_of(refused)) == (403, "forbidden_store")
