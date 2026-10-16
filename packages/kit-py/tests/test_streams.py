from __future__ import annotations

import inspect
import os
from pathlib import Path

import psycopg
import pytest
import redis
from conftest import TEST_CONSUMED_STREAM, TEST_OWN_STREAM, TEST_SUFFIX
from psycopg import sql
from psycopg.conninfo import make_conninfo

from mosaiq_kit import OutboxRelay, StreamConsumer

REPOSITORY = Path(__file__).resolve().parents[3]
SCHEMA = REPOSITORY / "sql/01_schema.sql"
FIXTURE = REPOSITORY / "tests/kit_vectors/postgres_fixture.sql"


def _database_url() -> str:
    value = os.environ.get("KIT_TEST_DATABASE_URL")
    if value:
        return value
    if os.environ.get("KIT_REQUIRE_SERVICES") == "1":
        pytest.fail("KIT_TEST_DATABASE_URL is not set")
    pytest.skip("KIT_TEST_DATABASE_URL is not set")


def _template() -> str:
    text = SCHEMA.read_text()
    block = text.split("-- BEGIN OUTBOX TEMPLATE\n", 1)[1]
    block = block.split("-- END OUTBOX TEMPLATE", 1)[0]
    lines = block.splitlines()
    template_start = "-- CREATE " + "SCHEMA"
    start = next(
        index for index, line in enumerate(lines) if line.startswith(template_start)
    )
    return "\n".join(
        line[3:] if line.startswith("-- ") else "" for line in lines[start:]
    )


def _rendered_template() -> str:
    return (
        _template()
        .replace("<suffix>", TEST_SUFFIX)
        .replace("<table>", TEST_SUFFIX + ".item")
        .replace("<entity_column>", "item_id")
        .replace("<event>", "changed")
    )


def _relay_dsn(database_url: str) -> str:
    return make_conninfo(
        database_url,
        options="-c role=svc_" + TEST_SUFFIX,
    )


@pytest.fixture
def outbox_database() -> str:
    database_url = _database_url()
    fixture = FIXTURE.read_text()
    setup = fixture.split("-- SETUP\n", 1)[1].split("-- TEARDOWN\n", 1)[0]
    teardown = fixture.split("-- TEARDOWN\n", 1)[1]
    setup = setup.replace("<suffix>", TEST_SUFFIX)
    teardown = teardown.replace("<suffix>", TEST_SUFFIX)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(teardown)
        connection.execute(setup)
        connection.execute(_rendered_template())
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(teardown)


def _insert_item(database_url: str, item_id: int) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            sql.SQL("INSERT INTO {}.item (item_id, label) VALUES (%s, %s)").format(
                sql.Identifier(TEST_SUFFIX)
            ),
            (item_id, "relay"),
        )


def test_relay_uses_the_repository_template_and_marks_after_durability(
    outbox_database: str,
    service_redis: redis.Redis,
    redis_admin: redis.Redis,
) -> None:
    stream = "stream:" + TEST_OWN_STREAM
    _insert_item(outbox_database, 7)
    relay = OutboxRelay(
        _relay_dsn(outbox_database), TEST_SUFFIX, [stream], service_redis
    )
    assert relay.run_once() == 1
    assert relay._connect().execute("SELECT current_user").fetchone()[0] == (
        "svc_" + TEST_SUFFIX
    )
    with psycopg.connect(outbox_database) as connection:
        row = connection.execute(
            sql.SQL(
                "SELECT stream, event_type, entity_id, relayed_at IS NOT NULL "
                "FROM {}.outbox WHERE entity_id = %s"
            ).format(sql.Identifier(TEST_SUFFIX + "_events")),
            ("7",),
        ).fetchone()
    assert row == (stream, "INSERT", "7", True)
    entries = redis_admin.xrange(stream)
    assert entries[-1][1]["v"] == "1"
    assert entries[-1][1]["entity_id"] == "7"
    assert set(entries[-1][1]) == {
        "event_id",
        "v",
        "event_type",
        "entity_id",
        "occurred_at",
    }
    relay.stop()


def test_relay_leaves_a_row_when_stream_delivery_fails(outbox_database: str) -> None:
    class BrokenRedis:
        def xadd(self, *args, **kwargs):
            raise redis.RedisError("unavailable")

    _insert_item(outbox_database, 8)
    stream = "stream:" + TEST_OWN_STREAM
    relay = OutboxRelay(
        _relay_dsn(outbox_database), TEST_SUFFIX, [stream], BrokenRedis()
    )
    assert relay.run_once() == 0
    with psycopg.connect(outbox_database) as connection:
        relayed_at = connection.execute(
            sql.SQL("SELECT relayed_at FROM {}.outbox WHERE entity_id = %s").format(
                sql.Identifier(TEST_SUFFIX + "_events")
            ),
            ("8",),
        ).fetchone()[0]
    assert relayed_at is None
    relay.stop()


def test_relay_waits_for_aof_once_after_the_whole_batch(
    outbox_database: str, service_redis: redis.Redis
) -> None:
    calls = []

    class RecordingRedis:
        def xadd(self, *args, **kwargs):
            calls.append("xadd")
            return service_redis.xadd(*args, **kwargs)

        def execute_command(self, *args):
            calls.append("waitaof")
            return service_redis.execute_command(*args)

    _insert_item(outbox_database, 20)
    _insert_item(outbox_database, 21)
    stream = "stream:" + TEST_OWN_STREAM
    relay = OutboxRelay(
        _relay_dsn(outbox_database), TEST_SUFFIX, [stream], RecordingRedis()
    )
    assert relay.run_once() == 2
    assert calls == ["xadd", "xadd", "waitaof"]
    relay.stop()


def test_relay_rolls_back_when_local_aof_does_not_confirm(
    outbox_database: str, service_redis: redis.Redis
) -> None:
    class UnconfirmedRedis:
        def xadd(self, *args, **kwargs):
            return service_redis.xadd(*args, **kwargs)

        def execute_command(self, *args):
            assert args == ("WAITAOF", 1, 0, 2000)
            return [0, 0]

    _insert_item(outbox_database, 22)
    _insert_item(outbox_database, 23)
    stream = "stream:" + TEST_OWN_STREAM
    relay = OutboxRelay(
        _relay_dsn(outbox_database), TEST_SUFFIX, [stream], UnconfirmedRedis()
    )
    assert relay.run_once() == 0
    with psycopg.connect(outbox_database) as connection:
        rows = connection.execute(
            sql.SQL(
                "SELECT entity_id, relayed_at FROM {}.outbox "
                "WHERE entity_id = ANY(%s) ORDER BY entity_id"
            ).format(sql.Identifier(TEST_SUFFIX + "_events")),
            (["22", "23"],),
        ).fetchall()
    assert rows == [("22", None), ("23", None)]
    relay.stop()


def test_relay_refuses_an_unconfigured_stream(
    outbox_database: str, service_redis: redis.Redis, caplog
) -> None:
    unknown = "stream:" + "another.v1.changed"
    with psycopg.connect(outbox_database) as connection:
        connection.execute(
            sql.SQL(
                "INSERT INTO {}.outbox "
                "(stream, event_type, entity_id) VALUES (%s, %s, %s)"
            ).format(sql.Identifier(TEST_SUFFIX + "_events")),
            (unknown, "UPDATE", "9"),
        )
    allowed = "stream:" + TEST_OWN_STREAM
    relay = OutboxRelay(
        _relay_dsn(outbox_database), TEST_SUFFIX, [allowed], service_redis
    )
    assert relay.run_once() == 0
    assert "unconfigured stream" in caplog.text
    with psycopg.connect(outbox_database) as connection:
        assert (
            connection.execute(
                sql.SQL("SELECT relayed_at FROM {}.outbox WHERE entity_id = %s").format(
                    sql.Identifier(TEST_SUFFIX + "_events")
                ),
                ("9",),
            ).fetchone()[0]
            is None
        )
    relay.stop()


def test_relay_purges_old_relayed_rows(outbox_database: str, service_redis) -> None:
    stream = "stream:" + TEST_OWN_STREAM
    with psycopg.connect(outbox_database) as connection:
        connection.execute(
            sql.SQL(
                "INSERT INTO {}.outbox "
                "(stream, event_type, entity_id, relayed_at) "
                "VALUES (%s, %s, %s, now() - (%s * interval '1 day'))"
            ).format(sql.Identifier(TEST_SUFFIX + "_events")),
            (stream, "UPDATE", "10", 8),
        )
    relay = OutboxRelay(
        _relay_dsn(outbox_database), TEST_SUFFIX, [stream], service_redis
    )
    relay.run_once()
    with psycopg.connect(outbox_database) as connection:
        count = connection.execute(
            sql.SQL("SELECT count(*) FROM {}.outbox WHERE entity_id = %s").format(
                sql.Identifier(TEST_SUFFIX + "_events")
            ),
            ("10",),
        ).fetchone()[0]
    assert count == 0
    relay.stop()


def _fresh_stream(redis_admin: redis.Redis) -> str:
    stream = "stream:" + TEST_CONSUMED_STREAM
    redis_admin.delete(stream)
    return stream


def test_consumer_creates_its_own_group_and_acks_after_success(
    monkeypatch: pytest.MonkeyPatch,
    redis_admin: redis.Redis,
    service_redis: redis.Redis,
) -> None:
    stream = _fresh_stream(redis_admin)
    monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "kit-consumer-success")
    handled = []
    consumer = StreamConsumer(service_redis, stream, handled.append)
    assert consumer.run_once(block_ms=1) == 0
    redis_admin.xadd(stream, {"event_id": "event-1", "v": "1"})
    assert consumer.run_once(block_ms=1) == 1
    assert handled == [{"event_id": "event-1", "v": "1"}]
    assert redis_admin.xpending(stream, consumer.group)["pending"] == 0


def test_consumer_claims_entries_idle_over_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch,
    redis_admin: redis.Redis,
    service_redis: redis.Redis,
) -> None:
    stream = _fresh_stream(redis_admin)
    monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "kit-consumer-claim")
    first = StreamConsumer(service_redis, stream, lambda _entry: None)
    first.run_once(block_ms=1)
    message_id = redis_admin.xadd(stream, {"event_id": "event-claim", "v": "1"})
    service_redis.xreadgroup(
        first.group, "old-consumer", {stream: ">"}, count=1, block=1
    )
    service_redis.xclaim(
        stream,
        first.group,
        "old-consumer",
        min_idle_time=0,
        message_ids=[message_id],
        idle=61_000,
    )
    handled = []
    recovering = StreamConsumer(service_redis, stream, handled.append)
    assert recovering.run_once(block_ms=1) == 1
    assert handled[0]["event_id"] == "event-claim"


def test_consumer_deduplicates_event_ids_in_memory(
    monkeypatch: pytest.MonkeyPatch,
    redis_admin: redis.Redis,
    service_redis: redis.Redis,
) -> None:
    stream = _fresh_stream(redis_admin)
    monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "kit-consumer-deduplicate")
    handled = []
    consumer = StreamConsumer(service_redis, stream, handled.append)
    consumer.run_once(block_ms=1)
    redis_admin.xadd(stream, {"event_id": "same-event", "v": "1"})
    redis_admin.xadd(stream, {"event_id": "same-event", "v": "1"})
    assert consumer.run_once(block_ms=1) == 2
    assert len(handled) == 1


def test_consumer_acks_after_the_fifth_failed_delivery(
    monkeypatch: pytest.MonkeyPatch,
    redis_admin: redis.Redis,
    service_redis: redis.Redis,
    caplog,
) -> None:
    stream = _fresh_stream(redis_admin)
    monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "kit-consumer-failures")

    def fail(_entry):
        raise ValueError("handler refusal")

    consumer = StreamConsumer(service_redis, stream, fail)
    consumer.run_once(block_ms=1)
    message_id = redis_admin.xadd(stream, {"event_id": "event-fail", "v": "1"})
    consumer.run_once(block_ms=1)
    for _delivery in range(4):
        if redis_admin.xpending(stream, consumer.group)["pending"] == 0:
            break
        service_redis.xclaim(
            stream,
            consumer.group,
            consumer.consumer,
            min_idle_time=0,
            message_ids=[message_id],
            idle=61_000,
            justid=True,
        )
        consumer.run_once(block_ms=1)
    assert redis_admin.xpending(stream, consumer.group)["pending"] == 0
    assert "exhausted five deliveries" in caplog.text


def test_consumer_group_cannot_be_supplied_by_a_caller() -> None:
    signature = inspect.signature(StreamConsumer)
    assert "group" not in signature.parameters


def test_consumer_creates_group_once_and_recreates_only_after_nogroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GroupRedis:
        def __init__(self):
            self.creates = 0
            self.no_group = False

        def xgroup_create(self, *_args, **_kwargs):
            self.creates += 1

        def xreadgroup(self, *_args, **_kwargs):
            if self.no_group:
                self.no_group = False
                raise redis.ResponseError("NOGROUP No such key or consumer group")
            return []

        def xpending_range(self, *_args, **_kwargs):
            return []

    monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "kit-consumer-group")
    redis_client = GroupRedis()
    consumer = StreamConsumer(
        redis_client, "stream:" + TEST_CONSUMED_STREAM, lambda _entry: None
    )
    consumer.run_once(block_ms=1)
    consumer.run_once(block_ms=1)
    assert redis_client.creates == 1
    redis_client.no_group = True
    consumer.run_once(block_ms=1)
    assert redis_client.creates == 2


def test_consumer_acks_a_trimmed_pending_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AckRecorder:
        def __init__(self):
            self.acked = []

        def xack(self, stream, group, message_id):
            self.acked.append((stream, group, message_id))

    monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "kit-consumer-trimmed")
    redis_client = AckRecorder()
    consumer = StreamConsumer(redis_client, "stream:" + TEST_CONSUMED_STREAM, dict)
    assert consumer._handle_entries([("1-0", None)]) == 1
    assert redis_client.acked == [
        ("stream:" + TEST_CONSUMED_STREAM, consumer.group, "1-0")
    ]
