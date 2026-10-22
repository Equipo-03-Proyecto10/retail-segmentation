"""Pure logic: no PostgreSQL, MongoDB or Redis is reached."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from mosaiq_kit import ApiError
from pymongo.errors import (
    DuplicateKeyError,
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)

import redis
from app.events import (
    EventStore,
    event_json,
    mongo_guard,
    to_millis,
    utc_day,
)
from app.live import TTL_SECONDS, LiveCounters, counter_key
from app.refs import check_references
from app.service import parse_instant


def test_a_day_is_a_utc_day():
    evening_in_merida = datetime(
        2026, 10, 13, 20, 30, tzinfo=timezone(timedelta(hours=-6))
    )
    assert utc_day(evening_in_merida) == "2026-10-14"
    assert utc_day(datetime(2026, 10, 14, 0, 0, tzinfo=UTC)) == "2026-10-14"
    assert utc_day(datetime(2026, 10, 13, 23, 59, 59, tzinfo=UTC)) == "2026-10-13"
    assert utc_day(datetime(5, 1, 2, tzinfo=UTC)) == "0005-01-02"


def test_instants_need_an_offset_and_are_cut_to_the_millisecond():
    assert parse_instant("2026-10-15T15:20:00Z") == datetime(
        2026, 10, 15, 15, 20, tzinfo=UTC
    )
    assert parse_instant("2026-10-15T09:20:00-06:00") == datetime(
        2026, 10, 15, 15, 20, tzinfo=UTC
    )
    assert parse_instant("2026-10-15T15:20:00.123456Z").microsecond == 123000
    assert parse_instant("2026-10-15T15:20:00") is None
    assert parse_instant("not a time") is None
    assert (
        to_millis(datetime(2026, 1, 1, 0, 0, 0, 999999, tzinfo=UTC)).microsecond
        == 999000
    )


def test_a_stored_event_becomes_the_contracts_event():
    stored = {
        "_id": "a",
        "client_event_id": "b",
        "type": "QR_SCAN",
        "store_id": 3,
        "product_id": 4,
        "recorded_by": "u",
        "occurred_at": datetime(2026, 10, 15, 15, 20),
        "recorded_at": datetime(2026, 10, 15, 15, 20, 3, tzinfo=UTC),
        "voided": False,
        "history": [],
    }
    event = event_json(stored)
    assert event["event_id"] == "a"
    assert (event["customer_id"], event["label_code"], event["rank"]) == (
        None,
        None,
        None,
    )
    assert event["occurred_at"].tzinfo is not None
    assert list(event)[:6] == [
        "event_id",
        "client_event_id",
        "type",
        "store_id",
        "product_id",
        "customer_id",
    ]


@pytest.mark.parametrize(
    "error",
    [
        ServerSelectionTimeoutError("no server"),
        NetworkTimeout("slow"),
        ExecutionTimeout("slower"),
        OperationFailure("not authorized", code=13),
        OperationFailure("auth failed", code=18),
    ],
)
def test_every_way_mongodb_cannot_be_used_is_a_503(error):
    with pytest.raises(ApiError) as caught, mongo_guard():
        raise error
    assert (caught.value.status, caught.value.code) == (503, "database_unavailable")


def test_other_mongodb_errors_are_not_swallowed():
    with pytest.raises(OperationFailure), mongo_guard():
        raise OperationFailure("Document failed validation", code=121)
    with pytest.raises(DuplicateKeyError), mongo_guard():
        raise DuplicateKeyError("E11000", 11000)


def test_a_service_without_mongodb_answers_503():
    store = EventStore(None)
    for call in (lambda: store.get("x"), lambda: store.find({}, 0, 1), store.ready):
        with pytest.raises(ApiError) as caught:
            call()
        assert caught.value.code == "database_unavailable"


class FakeRedis:
    """INCR/DECR/EXPIRE/SET/MGET over a dict, in the order a pipeline runs."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttl: dict[str, int] = {}
        self.calls: list[tuple] = []
        self.broken = False

    def pipeline(self, transaction: bool = True):
        outer = self

        class Pipe:
            def __init__(self) -> None:
                self.queue: list = []

            def incr(self, key):
                self.queue.append(("incr", key))

            def decr(self, key):
                self.queue.append(("decr", key))

            def expire(self, key, seconds, nx=False):
                self.queue.append(("expire", key, seconds, nx))

            def execute(self):
                if outer.broken:
                    raise redis.ConnectionError("down")
                out = []
                for name, key, *rest in self.queue:
                    outer.calls.append((name, key, *rest))
                    if name in ("incr", "decr"):
                        outer.values[key] = outer.values.get(key, 0) + (
                            1 if name == "incr" else -1
                        )
                        out.append(outer.values[key])
                    else:
                        if rest[1] and key in outer.ttl:
                            out.append(0)
                        else:
                            outer.ttl[key] = rest[0]
                            out.append(1)
                return out

        return Pipe()

    def set(self, key, value, ex=None):
        if self.broken:
            raise redis.ConnectionError("down")
        self.calls.append(("set", key, value, ex))
        self.values[key] = int(value)
        self.ttl[key] = ex

    def mget(self, keys):
        if self.broken:
            raise redis.ConnectionError("down")
        return [None if k not in self.values else str(self.values[k]) for k in keys]


def test_the_counter_key_is_store_day_type():
    assert (
        counter_key(1, "2026-10-13", "QR_SCAN") == "engagement:live:1:20261013:QR_SCAN"
    )


def test_a_counter_that_was_missing_takes_the_rollups_count_and_a_48_hour_ttl():
    fake = FakeRedis()
    live = LiveCounters(fake)
    live.count(1, "2026-10-13", "QR_SCAN", 1, stored=7)
    key = counter_key(1, "2026-10-13", "QR_SCAN")
    assert fake.values[key] == 7
    assert fake.ttl[key] == TTL_SECONDS == 48 * 3600
    live.count(1, "2026-10-13", "QR_SCAN", 1, stored=8)
    assert fake.values[key] == 8
    live.count(1, "2026-10-13", "QR_SCAN", -1, stored=7)
    assert fake.values[key] == 7
    assert [c[0] for c in fake.calls].count("decr") == 1


def test_a_decrement_of_a_missing_counter_is_also_overwritten():
    fake = FakeRedis()
    LiveCounters(fake).count(1, "2026-10-13", "RECO_CLICK", -1, stored=4)
    assert fake.values[counter_key(1, "2026-10-13", "RECO_CLICK")] == 4


def test_a_broken_cache_is_a_miss_not_an_error():
    fake = FakeRedis()
    fake.broken = True
    live = LiveCounters(fake)
    live.count(1, "2026-10-13", "QR_SCAN", 1, stored=1)
    live.set(1, "2026-10-13", "QR_SCAN", 1)
    assert live.read(1, "2026-10-13", ("QR_SCAN",)) is None
    assert LiveCounters(None).read(1, "2026-10-13", ("QR_SCAN",)) is None


def test_reading_counters_reports_the_missing_ones():
    fake = FakeRedis()
    live = LiveCounters(fake)
    live.set(1, "2026-10-13", "QR_SCAN", 5)
    assert live.read(1, "2026-10-13", ("QR_SCAN", "RECO_CLICK")) == {
        "QR_SCAN": 5,
        "RECO_CLICK": None,
    }


class Rows:
    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.asked: list[tuple[str, tuple]] = []

    def execute(self, statement: str, params: tuple):
        self.asked.append((statement, params))
        table = statement.split("FROM ")[1].split()[0]
        found = table in self.present

        class Result:
            def fetchone(self_inner):
                return (1,) if found else None

        return Result()


def test_references_are_checked_in_order_through_the_three_views_with_bound_values():
    everything = {
        "api_stores.store_directory_v1",
        "api_products.product_ref_v1",
        "api_customers.customer_ref_v1",
    }
    rows = Rows(everything)
    check_references(rows, 3, 12, "00000000-0000-0000-0000-000000000001")
    assert [s.split("FROM ")[1].split()[0] for s, _ in rows.asked] == [
        "api_stores.store_directory_v1",
        "api_products.product_ref_v1",
        "api_customers.customer_ref_v1",
    ]
    assert [p for _, p in rows.asked] == [
        (3,),
        (12,),
        ("00000000-0000-0000-0000-000000000001",),
    ]
    assert all("%s" in s and "3" not in s.split("WHERE")[1] for s, _ in rows.asked)
    for missing, code in (
        ("api_stores.store_directory_v1", "unknown_store"),
        ("api_products.product_ref_v1", "unknown_product"),
        ("api_customers.customer_ref_v1", "unknown_customer"),
    ):
        with pytest.raises(ApiError) as caught:
            check_references(
                Rows(everything - {missing}),
                3,
                12,
                "00000000-0000-0000-0000-000000000001",
            )
        assert (caught.value.status, caught.value.code) == (422, code)
        assert caught.value.details == [
            {"field": code.removeprefix("unknown_") + "_id", "code": code}
        ]


def test_a_customer_is_checked_only_when_one_is_given():
    rows = Rows({"api_stores.store_directory_v1", "api_products.product_ref_v1"})
    check_references(rows, 3, 12, None)
    assert len(rows.asked) == 2


def test_out_of_range_ids_never_reach_postgresql():
    rows = Rows({"api_stores.store_directory_v1", "api_products.product_ref_v1"})
    with pytest.raises(ApiError) as caught:
        check_references(rows, 2**31, 12, None)
    assert caught.value.code == "unknown_store"
    assert rows.asked == []
