"""Cache, relay, consumer, switches and the ACL, against a throwaway Redis.

TEST_REDIS_URL is an administrator URL such as redis://:secret@127.0.0.1:6379/0.
The tests build the svc_products user from redis/products.acl, so the committed
line is what runs. They skip without it.
"""

from __future__ import annotations

import psycopg
import pytest

import redis

from .conftest import FIRST_TEST_ID, headers, product_body, wait_for

pytestmark = pytest.mark.usefixtures("owner_url")
PRODUCT = FIRST_TEST_ID + 1
URL = f"/v1/products/{PRODUCT}"


def seed(client) -> None:
    created = client.post("/v1/products", json=product_body(), headers=headers())
    assert created.status_code == 201


def cache_keys(admin: redis.Redis) -> list[str]:
    return list(admin.scan_iter(match="products:cache:*"))


def web_rename(owner_url: str, name: str) -> None:
    """A name change made as retail_app, the way the frozen web writes."""

    with psycopg.connect(owner_url, autocommit=True) as connection:
        connection.execute("SET ROLE retail_app")
        connection.execute(
            "UPDATE public.product SET name = %s WHERE product_id = %s",
            (name, PRODUCT),
        )


def test_the_acl_line_allows_its_own_and_denies_the_rest(redis_env) -> None:
    def dryrun(*command: str) -> str:
        return str(redis_env.execute_command("ACL", "DRYRUN", "svc_products", *command))

    assert dryrun("XADD", "stream:products.v1.changed", "*", "a", "b") == "OK"
    assert dryrun("XADD", "stream:categories.v1.changed", "*", "a", "b") != "OK"
    assert dryrun("GET", "auth:session:x") != "OK"
    assert dryrun("EXISTS", "pub:auth:v1:live:x") == "OK"
    assert dryrun("DEL", "pub:auth:v1:live:x") != "OK"
    assert dryrun("GET", "products:cache:item") == "OK"
    assert dryrun("SET", "products:cache:item", "1") == "OK"
    assert dryrun("SCAN", "0") != "OK"
    assert dryrun("KEYS", "*") != "OK"
    assert dryrun("WAITAOF", "1", "0", "2000") == "OK"
    assert (
        dryrun(
            "XREADGROUP",
            "GROUP",
            "products",
            "c",
            "STREAMS",
            "stream:products.v1.changed",
            ">",
        )
        == "OK"
    )
    assert (
        dryrun(
            "XREADGROUP",
            "GROUP",
            "products",
            "c",
            "STREAMS",
            "stream:categories.v1.changed",
            ">",
        )
        != "OK"
    )


def test_reads_are_cached_for_ten_minutes(build_app, redis_env, owner_url) -> None:
    client = build_app(CACHE_ENABLED="true").test_client()
    seed(client)
    assert client.get(URL, headers=headers()).get_json()["name"] == "Test product 1"
    keys = cache_keys(redis_env)
    assert any(":item:" in key for key in keys)
    item_key = next(key for key in keys if ":item:" in key)
    assert 0 < redis_env.ttl(item_key) <= 600
    # Changed behind the cache with the consumer off: the cached read is served.
    web_rename(owner_url, "Behind the cache")
    assert client.get(URL, headers=headers()).get_json()["name"] == "Test product 1"
    client.get("/v1/products?sku=TST-00001", headers=headers())
    assert any(":list:" in key for key in cache_keys(redis_env))


def test_a_web_edit_is_served_within_ten_seconds(
    build_app, redis_env, owner_url
) -> None:
    app = build_app(
        CACHE_ENABLED="true",
        STREAM_CONSUMER_ENABLED="true",
        OUTBOX_RELAY_ENABLED="true",
    )
    client = app.test_client()
    seed(client)
    assert client.get(URL, headers=headers()).get_json()["name"] == "Test product 1"
    groups = redis_env.xinfo_groups("stream:products.v1.changed")
    assert [group["name"] for group in groups] == ["products"]
    web_rename(owner_url, "Renamed by the web")
    assert wait_for(
        lambda: client.get(URL, headers=headers()).get_json()["name"]
        == "Renamed by the web",
        10,
    )


def test_a_committed_write_reaches_the_stream_and_is_marked_relayed(
    build_app, redis_env, owner_url
) -> None:
    app = build_app(OUTBOX_RELAY_ENABLED="true")
    client = app.test_client()
    seed(client)

    def entries() -> list[dict]:
        return [
            fields
            for _id, fields in redis_env.xrange("stream:products.v1.changed")
            if fields["entity_id"] == str(PRODUCT)
        ]

    assert wait_for(lambda: len(entries()) == 1, 10)
    fields = entries()[0]
    assert fields["event_type"] == "INSERT"
    assert fields["v"] == "1"
    assert set(fields) == {"event_id", "v", "event_type", "entity_id", "occurred_at"}

    def relayed() -> bool:
        with psycopg.connect(owner_url) as connection:
            row = connection.execute(
                "SELECT relayed_at IS NOT NULL FROM products_events.outbox "
                "WHERE event_id = %s",
                (fields["event_id"],),
            ).fetchone()
        return bool(row and row[0])

    assert wait_for(relayed, 5)


def test_reads_answer_from_postgresql_with_redis_cache_down(
    build_app, redis_env, monkeypatch
) -> None:
    client = build_app(CACHE_ENABLED="true").test_client()
    seed(client)
    monkeypatch.setenv("REDIS_CACHE_URL", "redis://127.0.0.1:1/0")
    down = build_app(CACHE_ENABLED="true").test_client()
    assert down.get(URL, headers=headers()).status_code == 200
    assert down.get("/v1/products", headers=headers()).status_code == 200


def test_the_switches_off_touch_no_key_and_no_group(build_app, redis_env) -> None:
    app = build_app(
        CACHE_ENABLED="false",
        RATE_LIMIT_ENABLED="false",
        STREAM_CONSUMER_ENABLED="false",
    )
    client = app.test_client()
    seed(client)
    assert client.get(URL, headers=headers()).status_code == 200
    assert client.get("/v1/products", headers=headers()).status_code == 200
    assert list(redis_env.scan_iter(match="products:*")) == []
    assert not redis_env.exists("stream:products.v1.changed") or (
        redis_env.xinfo_groups("stream:products.v1.changed") == []
    )
    assert "products_consumer" not in app.extensions


def test_the_switches_default_to_on(build_app, redis_env, monkeypatch) -> None:
    for name in ("CACHE_ENABLED", "RATE_LIMIT_ENABLED", "STREAM_CONSUMER_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    from app.config import Settings

    settings = Settings.from_env()
    assert settings.cache_enabled
    assert settings.stream_consumer_enabled
    assert settings.relay_enabled
    app = build_app(
        CACHE_ENABLED="true", RATE_LIMIT_ENABLED="true", STREAM_CONSUMER_ENABLED="true"
    )
    client = app.test_client()
    assert client.get("/v1/products?size=1", headers=headers()).status_code == 200
    assert list(redis_env.scan_iter(match="products:rl:*"))
    assert wait_for(
        lambda: redis_env.exists("stream:products.v1.changed") == 1
        and [g["name"] for g in redis_env.xinfo_groups("stream:products.v1.changed")]
        == ["products"],
        5,
    )


class Crash(BaseException):
    """The process dies: the kit catches Exception only, so no XACK follows."""


def test_reapplying_an_event_leaves_the_same_state(
    build_app, redis_env, owner_url, monkeypatch
) -> None:
    """#764 criterion 3: re-applying an event is idempotent by re-read (ADR-0044).

    The production handler runs in a process that dies after the effect and
    before XACK; the restarted process applies the entry again from its
    pending list; then a duplicate entry, as a relay that crashed between XADD
    and its UPDATE appends, is applied once more. The next read is the same
    answer each time.
    """
    from mosaiq_kit import StreamConsumer

    import app.service

    # Build the real consumer, but drive it by hand instead of in its thread.
    monkeypatch.setattr(app.service.StreamConsumer, "start", lambda self: None)
    application = build_app(
        CACHE_ENABLED="true",
        STREAM_CONSUMER_ENABLED="true",
        OUTBOX_RELAY_ENABLED="true",
    )
    production = application.extensions["products_consumer"]
    stream = production.stream
    client = application.test_client()
    seed(client)

    def drained() -> bool:  # the creation's own event is relayed and handled
        production.run_once(block_ms=10)
        groups = redis_env.xinfo_groups(stream)
        return bool(redis_env.xrange(stream)) and groups[0]["lag"] == 0

    assert wait_for(drained, 10)

    def observe():
        return client.get(URL, headers=headers()).get_json()

    assert observe()["name"] == "Test product 1"  # now cached
    before = len(redis_env.xrange(stream))
    web_rename(owner_url, "Renamed twice")
    assert wait_for(lambda: len(redis_env.xrange(stream)) > before, 10)
    _entry_id, fields = redis_env.xrevrange(stream, count=1)[0]

    def crashing(entry):
        production.handler(entry)
        raise Crash

    with pytest.raises(Crash):
        StreamConsumer(production.redis, stream, crashing).run_once(block_ms=10)
    first = observe()
    assert first["name"] == "Renamed twice"
    assert redis_env.xpending(stream, "products")["pending"] == 1

    restarted = StreamConsumer(production.redis, stream, production.handler)
    assert restarted.run_once(block_ms=10) == 1
    assert observe() == first

    redis_env.xadd(stream, fields)
    duplicate = StreamConsumer(production.redis, stream, production.handler)
    assert duplicate.run_once(block_ms=10) == 1
    assert observe() == first
    assert redis_env.xpending(stream, "products")["pending"] == 0
