"""A database that cannot be reached is a 503 in the error envelope, never a 500.

MongoDB is a dead address in most of these, so they need no server; the ones
that name PostgreSQL or Redis say so.
"""

from __future__ import annotations

import uuid

import pytest
from pymongo import MongoClient

from app.events import EVENTS, SERVER_SELECTION_TIMEOUT_MS, EventStore

EVENT = str(uuid.uuid4())
REQUESTS = [
    ("get", "/v1/engagement-events", "STORE_MANAGER", None),
    ("get", f"/v1/engagement-events/{EVENT}", "STORE_MANAGER", None),
    ("post", f"/v1/engagement-events/{EVENT}/void", "ADMIN", {"reason": "x"}),
    ("get", "/v1/engagement-summaries?group_by=type", "STORE_MANAGER", None),
]


@pytest.mark.parametrize("method,path,role,body", REQUESTS)
def test_every_operation_that_reaches_a_stopped_mongodb_answers_503(
    make_app, token, method, path, role, body
):
    app = make_app()
    response = getattr(app.test_client(), method)(path, json=body, headers=token(role))
    assert response.status_code == 503
    assert response.get_json()["code"] == "database_unavailable"
    assert response.get_json()["details"] == []


def test_the_xml_error_is_the_common_one(make_app, token):
    app = make_app()
    response = app.test_client().get(
        "/v1/engagement-events", headers=token("ADMIN") | {"Accept": "application/xml"}
    )
    assert response.status_code == 503
    assert b"<code>database_unavailable</code>" in response.data


def test_readiness_fails_and_liveness_does_not(make_app):
    client = make_app().test_client()
    assert client.get("/health/live").status_code == 204
    not_ready = client.get("/health/ready")
    assert not_ready.status_code == 503
    assert not_ready.get_json()["code"] == "schema_not_ready"


def test_an_unconfigured_service_is_not_ready_either(make_app, monkeypatch):
    app = make_app()
    app.extensions["engagement_store"].client = None
    assert app.test_client().get("/health/ready").status_code == 503


def test_the_server_selection_timeout_is_at_most_two_seconds(monkeypatch):
    assert SERVER_SELECTION_TIMEOUT_MS <= 2000
    monkeypatch.setenv(
        "MONGO_URI", "mongodb://127.0.0.1:1/engagement?serverSelectionTimeoutMS=30000"
    )
    store = EventStore.from_environment()
    assert store.client.options.server_selection_timeout <= 2.0
    store.client.close()


@pytest.mark.postgres
def test_posting_with_postgresql_stopped_is_503_and_the_rest_still_works(
    make_app, token, mongo_uri
):
    app = make_app(mongo_uri=mongo_uri)  # PostgreSQL is a dead address
    client = app.test_client()
    headers = token("STORE_MANAGER")
    body = {
        "client_event_id": str(uuid.uuid4()),
        "type": "QR_SCAN",
        "store_id": 3,
        "product_id": 12,
        "occurred_at": "2001-01-01T00:00:00Z",
    }
    posted = client.post("/v1/engagement-events", json=body, headers=headers)
    assert (posted.status_code, posted.get_json()["code"]) == (
        503,
        "database_unavailable",
    )
    assert (
        client.get("/v1/engagement-events?size=1", headers=headers).status_code == 200
    )
    assert (
        client.get(
            "/v1/engagement-summaries?group_by=type", headers=headers
        ).status_code
        == 200
    )
    assert client.get("/health/ready").status_code == 204


@pytest.mark.postgres
def test_posting_with_mongodb_stopped_is_503_after_the_references_check(
    make_app, token, service_url
):
    app = make_app(database_url=service_url)
    client = app.test_client()
    body = {
        "client_event_id": str(uuid.uuid4()),
        "type": "QR_SCAN",
        "store_id": 3,
        "product_id": 12,
        "occurred_at": "2001-01-01T00:00:00Z",
    }
    posted = client.post("/v1/engagement-events", json=body, headers=token("ADMIN"))
    assert (posted.status_code, posted.get_json()["code"]) == (
        503,
        "database_unavailable",
    )
    unknown = client.post(
        "/v1/engagement-events", json=body | {"store_id": 9999}, headers=token("ADMIN")
    )
    assert unknown.get_json()["code"] == "unknown_store"


@pytest.mark.mongo
def test_readiness_needs_every_collection_and_index(make_app, mongo_uri, mongo):
    app = make_app(mongo_uri=mongo_uri)
    client = app.test_client()
    assert client.get("/health/ready").status_code == 204
    events = mongo.get_default_database(default="engagement")[EVENTS]
    events.drop_index("customer_id_1_occurred_at_-1")
    try:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 204
    finally:
        events.create_index(
            [("customer_id", 1), ("occurred_at", -1)],
            name="customer_id_1_occurred_at_-1",
        )
    assert client.get("/health/ready").status_code == 204


def test_readiness_needs_the_validator_and_both_collections():
    class Listing:
        def __init__(self, infos, indexes):
            self.infos, self.indexes = infos, indexes

        def list_collections(self):
            return iter(self.infos)

        def __getitem__(self, name):
            indexes = self.indexes[name]

            class Collection:
                def list_indexes(self_inner):
                    return iter(indexes)

            return Collection()

    from app.events import DAILY, DAILY_INDEXES, EVENT_INDEXES

    def indexes(wanted):
        return [{"name": "_id_", "key": {"_id": 1}}] + [
            {"name": n, "key": dict(k), **({"unique": True} if u else {})}
            for n, (k, u) in wanted.items()
        ]

    good = {EVENTS: indexes(EVENT_INDEXES), DAILY: indexes(DAILY_INDEXES)}
    with_validator = [
        {"name": EVENTS, "options": {"validator": {"$jsonSchema": {}}}},
        {"name": DAILY, "options": {}},
    ]

    def ready(infos, idx):
        store = EventStore(MongoClient("mongodb://127.0.0.1:1", connect=False))
        store.database = lambda: Listing(infos, idx)
        return store.ready()

    assert ready(with_validator, good)
    assert not ready([with_validator[1]], good)
    assert not ready([{"name": EVENTS, "options": {}}, with_validator[1]], good)
    assert not ready(with_validator, good | {DAILY: indexes(DAILY_INDEXES)[:1]})
    not_unique = [dict(i) for i in good[EVENTS]]
    for index in not_unique:
        index.pop("unique", None)
    assert not ready(with_validator, good | {EVENTS: not_unique})
