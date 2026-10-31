"""Cache, relay, consumer group and ACL, against a real Redis (TEST_REDIS_URL)."""

from __future__ import annotations

import os
import time

import pytest
from mosaiq_kit import OutboxRelay, StreamConsumer

from tests.conftest import ACL, STREAM, service_client

pytestmark = pytest.mark.postgres

LINE = (
    "user svc_categories on {{password}} resetchannels -@all +@connection +@read"
    " +@write -@dangerous -scan -randomkey ~categories:rl:* ~categories:cache:*"
    " +waitaof (%R~pub:auth:v1:live:* +exists) (~stream:categories.v1.changed +xadd)"
    " (~stream:categories.v1.changed +xreadgroup +xack +xgroup|create +xpending +xclaim)"
)


def test_the_acl_is_one_line_with_the_documented_selectors():
    assert ACL.read_text().splitlines() == [LINE]


def test_dryrun_denies_other_streams_and_session_keys(redis_admin, service_redis_url):
    def dry(*command: str) -> str:
        return redis_admin.execute_command("ACL", "DRYRUN", "svc_categories", *command)

    assert dry("XADD", STREAM, "*", "a", "b") == "OK"
    assert dry("EXISTS", "pub:auth:v1:live:abc") == "OK"
    assert dry("SET", "categories:cache:0:x", "1") == "OK"
    assert dry("XADD", "stream:products.v1.changed", "*", "a", "b") != "OK"
    assert dry("GET", "auth:session:x") != "OK"
    assert dry("DEL", "pub:auth:v1:live:abc") != "OK"
    assert dry("SCAN", "0") != "OK"
    assert dry("KEYS", "*") != "OK"


@pytest.fixture
def stack(redis_admin, service_redis_url, make_app, service_url, monkeypatch):
    """The app with a cache, plus a relay and a consumer driven by hand."""

    password = os.environ["REDIS_CATEGORIES_PASSWORD"]
    before = redis_admin.config_get("appendonly")["appendonly"]
    redis_admin.config_set("appendonly", "yes")
    app = make_app(redis_url=service_redis_url)
    relay = OutboxRelay(service_url, "categories", [STREAM], service_client(password))
    consumer = StreamConsumer(
        service_client(password),
        STREAM,
        lambda _f: app.extensions["categories_cache"].invalidate(),
    )
    consumer.run_once()  # creates the group before any change
    yield app, relay, consumer
    redis_admin.config_set("appendonly", before)
    relay.stop()
    (
        redis_admin.xgroup_destroy(STREAM, "categories")
        if redis_admin.exists(STREAM)
        else None
    )


def relay_until(relay, want=1):
    deadline = time.time() + 10
    relayed = 0
    while relayed < want and time.time() < deadline:
        relayed += relay.run_once()
        time.sleep(0.2)
    return relayed


def test_a_write_reaches_the_stream_once_with_the_adr_fields(
    stack, admin_headers, new_id, unique, redis_admin, owner
):
    app, relay, _ = stack
    owner.execute("DELETE FROM categories_events.outbox WHERE relayed_at IS NULL")
    category_id = new_id()
    app.test_client().post(
        "/v1/categories",
        json={"category_id": category_id, "name": unique},
        headers=admin_headers,
    )
    assert relay_until(relay) == 1
    entries = redis_admin.xrange(STREAM)
    assert len(entries) == 1
    fields = entries[0][1]
    assert fields["event_type"] == "INSERT"
    assert fields["entity_id"] == str(category_id)
    assert fields["v"] == "1"
    assert fields["occurred_at"].endswith("Z")
    assert relay.run_once() == 0


def test_old_relayed_rows_are_purged(stack, owner):
    _, relay, _ = stack
    owner.execute(
        "INSERT INTO categories_events.outbox (stream, event_type, entity_id, occurred_at, relayed_at)"
        " VALUES (%s, 'UPDATE', 'old', now() - interval '9 days', now() - interval '8 days')",
        (STREAM,),
    )
    relay.run_once()
    gone = owner.execute(
        "SELECT count(*) FROM categories_events.outbox WHERE entity_id = 'old'"
    ).fetchone()[0]
    assert gone == 0


def test_reads_are_cached_and_the_group_invalidates_them(
    stack, admin_headers, new_id, unique, owner, redis_admin
):
    app, relay, consumer = stack
    client = app.test_client()
    category_id = new_id()
    client.post(
        "/v1/categories",
        json={"category_id": category_id, "name": unique},
        headers=admin_headers,
    )
    url = f"/v1/categories/{category_id}"
    assert client.get(url, headers=admin_headers).get_json()["name"] == unique
    keys = list(redis_admin.scan_iter(match="categories:cache:*"))
    assert any(key.endswith(f"one:{category_id}") for key in keys)
    assert (
        redis_admin.ttl(next(k for k in keys if k.endswith(f"one:{category_id}"))) > 800
    )
    # A rename the web makes (any other writer) is stale until the event runs.
    with owner.transaction():
        owner.execute("SET LOCAL ROLE retail_app")
        owner.execute(
            "UPDATE category SET name = %s WHERE category_id = %s",
            (unique + "-web", category_id),
        )
    assert client.get(url, headers=admin_headers).get_json()["name"] == unique
    relay_until(relay, want=1)
    deadline = time.time() + 10
    while time.time() < deadline:
        consumer.run_once(block_ms=200)
        if client.get(url, headers=admin_headers).get_json()["name"] == unique + "-web":
            break
    assert client.get(url, headers=admin_headers).get_json()["name"] == unique + "-web"


def test_reads_answer_200_with_redis_cache_down(make_app, admin_headers):
    app = make_app(redis_url="redis://127.0.0.1:1/0")
    client = app.test_client()
    assert client.get("/v1/categories", headers=admin_headers).status_code == 200
    assert client.get("/v1/categories/1", headers=admin_headers).status_code in (
        200,
        404,
    )


def test_a_rename_by_the_web_is_served_within_ten_seconds(
    redis_admin, service_redis_url, make_app, admin_headers, new_id, unique, owner
):
    before = redis_admin.config_get("appendonly")["appendonly"]
    redis_admin.config_set("appendonly", "yes")
    try:
        app = make_app(redis_url=service_redis_url, background=True)
        client = app.test_client()
        category_id = new_id()
        client.post(
            "/v1/categories",
            json={"category_id": category_id, "name": unique},
            headers=admin_headers,
        )
        url = f"/v1/categories/{category_id}"
        assert client.get(url, headers=admin_headers).get_json()["name"] == unique
        with owner.transaction():
            owner.execute("SET LOCAL ROLE retail_app")
            owner.execute(
                "UPDATE category SET name = %s WHERE category_id = %s",
                (unique + "-web", category_id),
            )
        deadline = time.time() + 10
        name = unique
        while time.time() < deadline and name == unique:
            time.sleep(0.25)
            name = client.get(url, headers=admin_headers).get_json()["name"]
        assert name == unique + "-web"
    finally:
        redis_admin.config_set("appendonly", before)
