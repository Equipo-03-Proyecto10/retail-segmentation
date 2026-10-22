"""The rollup, its rebuild and the live counters (ADR-0038)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pymongo import MongoClient

import redis
from app.__main__ import main
from app.events import EventStore, utc_day
from app.live import LiveCounters, counter_key
from app.rollup import rebuild

pytestmark = [pytest.mark.postgres, pytest.mark.mongo, pytest.mark.redis]

TODAY_STORE = 28  # live tests count on today, on a store no other test uses


@pytest.fixture
def db(mongo):
    return mongo.get_default_database(default="engagement")


def daily(db, day):
    return {
        (d["store_id"], d["type"]): d["count"]
        for d in db.engagement_daily.find({"day": day})
    }


def agrees_with_events(db, day):
    """ADR-0038's compliance check: the rollup is what the events add up to."""

    rebuilt = list(
        db.engagement_event.aggregate(
            [
                {"$match": {"voided": False, "recorded_by": {"$exists": True}}},
                {
                    "$group": {
                        "_id": {
                            "store_id": "$store_id",
                            "type": "$type",
                            "day": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$occurred_at",
                                    "timezone": "UTC",
                                }
                            },
                        },
                        "count": {"$sum": 1},
                    }
                },
                {"$match": {"_id.day": day}},
            ]
        )
    )
    expected = {(r["_id"]["store_id"], r["_id"]["type"]): r["count"] for r in rebuilt}
    return {k: v for k, v in daily(db, day).items() if v > 0} == expected


# ---- the rebuild ---------------------------------------------------------------


def test_the_rebuild_agrees_with_the_incremental_counts(workspace, db):
    posted = [
        workspace.post(workspace.scan(store_id=3 + n % 2)).get_json() for n in range(6)
    ]
    posted += [workspace.post(workspace.click()).get_json() for _ in range(3)]
    workspace.client.post(
        f"/v1/engagement-events/{posted[0]['event_id']}/void",
        json={"reason": "x"},
        headers=workspace.admin,
    )
    day = workspace.day.isoformat()
    incremental = daily(db, day)
    assert incremental == {(3, "QR_SCAN"): 2, (4, "QR_SCAN"): 3, (3, "RECO_CLICK"): 3}
    assert agrees_with_events(db, day)

    store = EventStore.from_environment()
    rebuild(store, LiveCounters(None), workspace.day, workspace.day)
    assert daily(db, day) == incremental
    assert agrees_with_events(db, day)


def test_the_rebuild_repairs_a_drifted_rollup(workspace, db):
    for _ in range(4):
        workspace.post(workspace.scan())
    dead = workspace.post(workspace.scan(store_id=5)).get_json()
    workspace.client.post(
        f"/v1/engagement-events/{dead['event_id']}/void",
        json={"reason": "x"},
        headers=workspace.admin,
    )
    day = workspace.day.isoformat()
    # A crash between insert and increment, a double increment, a stale group.
    db.engagement_daily.update_one(
        {"store_id": 3, "day": day, "type": "QR_SCAN"}, {"$set": {"count": 99}}
    )
    db.engagement_daily.update_one(
        {"store_id": 5, "day": day, "type": "QR_SCAN"}, {"$set": {"count": 7}}
    )
    db.engagement_daily.insert_one(
        {"store_id": 8, "day": day, "type": "RECO_CLICK", "count": 3}
    )
    assert not agrees_with_events(db, day)

    result = rebuild(
        EventStore.from_environment(), LiveCounters(None), workspace.day, workspace.day
    )
    assert agrees_with_events(db, day)
    assert daily(db, day) == {
        (3, "QR_SCAN"): 4,
        (5, "QR_SCAN"): 0,
        (8, "RECO_CLICK"): 0,
    }
    assert result["groups"] == 1


def test_the_rebuild_touches_only_its_days(workspace, db):
    workspace.post(workspace.scan())
    other = (workspace.day + timedelta(days=1)).isoformat()
    db.engagement_daily.insert_one(
        {"store_id": 3, "day": other, "type": "QR_SCAN", "count": 5}
    )
    try:
        rebuild(
            EventStore.from_environment(),
            LiveCounters(None),
            workspace.day,
            workspace.day,
        )
        assert db.engagement_daily.find_one({"day": other})["count"] == 5
    finally:
        db.engagement_daily.delete_many({"day": other})


def test_a_utc_day_is_not_the_local_day(workspace, db):
    """An evening scan in Tijuana counts on the next UTC date."""

    evening = f"{workspace.day.isoformat()}T20:30:00-08:00"
    next_day = (workspace.day + timedelta(days=1)).isoformat()
    try:
        workspace.post(workspace.scan(occurred_at=evening))
        assert daily(db, workspace.day.isoformat()) == {}
        assert daily(db, next_day) == {(3, "QR_SCAN"): 1}
        rebuild(
            EventStore.from_environment(),
            LiveCounters(None),
            workspace.day,
            workspace.day + timedelta(days=1),
        )
        assert daily(db, next_day) == {(3, "QR_SCAN"): 1}
    finally:
        db.engagement_daily.delete_many({"day": next_day})


def test_the_maintenance_command_rebuilds_and_reports(
    workspace, db, monkeypatch, capsys, mongo_uri
):
    workspace.post(workspace.scan())
    db.engagement_daily.update_many(
        {"day": workspace.day.isoformat()}, {"$set": {"count": 40}}
    )
    monkeypatch.setenv("MONGO_URI", mongo_uri)
    monkeypatch.delenv("REDIS_CACHE_URL", raising=False)
    day = workspace.day.isoformat()
    assert main(["rebuild-rollup", "--from", day, "--to", day]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["groups"] == 1
    assert daily(db, day) == {(3, "QR_SCAN"): 1}


@pytest.mark.parametrize(
    "arguments",
    [
        ["rebuild-rollup"],
        ["rebuild-rollup", "--from", "2026-10-02", "--to", "2026-10-01"],
        ["rebuild-rollup", "--from", "2026-10-01", "--to", "tomorrow"],
        ["rebuild-rollup", "--from", "2026-13-01", "--to", "2026-13-02"],
    ],
)
def test_the_maintenance_command_refuses_a_bad_range(arguments):
    with pytest.raises(SystemExit) as stopped:
        main(arguments)
    assert stopped.value.code == 2


def test_the_maintenance_command_without_mongodb_says_so(monkeypatch, capsys):
    monkeypatch.delenv("MONGO_URI", raising=False)
    with pytest.raises(SystemExit):
        main(["rebuild-rollup", "--from", "2026-10-01", "--to", "2026-10-01"])
    assert "MONGO_URI" in capsys.readouterr().err


def test_a_rebuild_puts_the_rollups_count_on_a_live_counter(db, cache, mongo_uri):
    store = EventStore(MongoClient(mongo_uri, tz_aware=True))
    today = datetime.now(UTC).date()
    day = today.isoformat()
    key = counter_key(TODAY_STORE, day, "QR_SCAN")
    event_ids = []
    try:
        for n in range(3):
            document = {
                "_id": str(uuid.uuid4()),
                "client_event_id": str(uuid.uuid4()),
                "type": "QR_SCAN",
                "store_id": TODAY_STORE,
                "product_id": 1,
                "recorded_by": "rebuild-test",
                "occurred_at": datetime.now(UTC) - timedelta(minutes=n),
                "recorded_at": datetime.now(UTC),
                "voided": False,
                "history": [],
            }
            db.engagement_event.insert_one(document)
            event_ids.append(document["_id"])
        baseline = db.engagement_event.count_documents(
            {"store_id": TODAY_STORE, "voided": False, "type": "QR_SCAN"}
        )
        cache.set(key, 1000)
        rebuild(store, LiveCounters(cache), today, today)
        assert (
            db.engagement_daily.find_one(
                {"store_id": TODAY_STORE, "day": day, "type": "QR_SCAN"}
            )["count"]
            == baseline
        )
        assert int(cache.get(key)) == baseline
        assert 0 < cache.ttl(key) <= 48 * 3600
    finally:
        db.engagement_event.delete_many({"_id": {"$in": event_ids}})
        db.engagement_daily.delete_many({"store_id": TODAY_STORE, "day": day})
        cache.delete(key)


# ---- live counters ---------------------------------------------------------------


@pytest.fixture
def today(workspace, db, cache):
    """Events that happened a minute ago, on a store of their own."""

    day = utc_day(datetime.now(UTC))
    keys = [counter_key(TODAY_STORE, day, kind) for kind in ("QR_SCAN", "RECO_CLICK")]
    for key in keys:
        cache.delete(key)

    class Today:
        pass

    context = Today()
    context.day, context.keys, context.posted = day, keys, []

    def post(kind="QR_SCAN"):
        moment = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        make = workspace.scan if kind == "QR_SCAN" else workspace.click
        response = workspace.post(make(store_id=TODAY_STORE, occurred_at=moment))
        assert response.status_code == 201
        context.posted.append(response.get_json()["event_id"])
        return response.get_json()

    context.post = post
    context.count = lambda kind="QR_SCAN": (
        db.engagement_daily.find_one(
            {"store_id": TODAY_STORE, "day": day, "type": kind}
        )
        or {"count": None}
    )["count"]
    yield context
    db.engagement_event.delete_many({"_id": {"$in": context.posted}})
    db.engagement_daily.delete_many({"store_id": TODAY_STORE, "day": day})
    for key in keys:
        cache.delete(key)


def test_a_counter_follows_the_rollup_and_expires_in_48_hours(today, cache):
    for expected in (1, 2, 3):
        today.post()
        assert int(cache.get(today.keys[0])) == today.count() == expected
    assert 48 * 3600 - 60 < cache.ttl(today.keys[0]) <= 48 * 3600
    today.post("RECO_CLICK")
    assert int(cache.get(today.keys[1])) == today.count("RECO_CLICK") == 1


def test_a_deleted_counter_comes_back_equal_to_the_rollup(today, cache):
    today.post()
    today.post()
    cache.delete(today.keys[0])
    today.post()
    assert int(cache.get(today.keys[0])) == today.count() == 3
    assert cache.ttl(today.keys[0]) > 0


def test_a_void_decrements_the_counter_and_a_deleted_one_is_repaired(
    today, cache, workspace
):
    first, second = today.post(), today.post()
    voided = workspace.client.post(
        f"/v1/engagement-events/{first['event_id']}/void",
        json={"reason": "x"},
        headers=workspace.admin,
    )
    assert voided.status_code == 200
    assert int(cache.get(today.keys[0])) == today.count() == 1
    cache.delete(today.keys[0])
    workspace.client.post(
        f"/v1/engagement-events/{second['event_id']}/void",
        json={"reason": "x"},
        headers=workspace.admin,
    )
    assert int(cache.get(today.keys[0])) == today.count() == 0


def summary_today(space, group_by="type"):
    day = utc_day(datetime.now(UTC))
    return space.client.get(
        "/v1/engagement-summaries",
        query_string={
            "group_by": group_by,
            "store_id": TODAY_STORE,
            "from": day,
            "to": day,
        },
        headers=space.headers,
    )


def test_a_one_store_summary_of_today_reads_the_counter(today, cache, workspace):
    today.post()
    today.post("RECO_CLICK")
    cache.set(
        today.keys[0], 41
    )  # a value MongoDB does not hold proves where it came from
    groups = summary_today(workspace).get_json()["groups"]
    assert groups == [
        {"key": "QR_SCAN", "count": 41},
        {"key": "RECO_CLICK", "count": 1},
    ]
    assert summary_today(workspace, "store").get_json()["groups"] == [
        {"key": str(TODAY_STORE), "count": 42}
    ]
    assert summary_today(workspace, "day").get_json()["groups"] == [
        {"key": today.day, "count": 42}
    ]


def test_a_missing_counter_is_read_from_the_rollup(today, cache, workspace):
    today.post()
    today.post()
    cache.delete(*today.keys)
    assert summary_today(workspace).get_json()["groups"] == [
        {"key": "QR_SCAN", "count": 2}
    ]


def test_a_summary_of_another_day_or_store_never_reads_a_counter(
    today, cache, workspace
):
    today.post()
    cache.set(today.keys[0], 41)
    wide = workspace.client.get(
        "/v1/engagement-summaries",
        query_string={"group_by": "type", "store_id": TODAY_STORE},
        headers=workspace.headers,
    )
    assert wide.get_json()["groups"] == [{"key": "QR_SCAN", "count": 1}]


def test_with_redis_cache_stopped_everything_answers_from_mongodb(
    today, workspace, make_app, token, mongo_uri, service_url
):
    today.post()
    today.post()
    blind = make_app(
        mongo_uri=mongo_uri, database_url=service_url
    )  # redis-cache is a dead address
    client = blind.test_client()
    headers = token("STORE_MANAGER")
    day = today.day
    summary = client.get(
        "/v1/engagement-summaries",
        query_string={
            "group_by": "type",
            "store_id": TODAY_STORE,
            "from": day,
            "to": day,
        },
        headers=headers,
    )
    assert summary.status_code == 200
    assert summary.get_json()["groups"] == [{"key": "QR_SCAN", "count": 2}]
    moment = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    posted = client.post(
        "/v1/engagement-events",
        json=workspace.scan(store_id=TODAY_STORE, occurred_at=moment),
        headers=headers,
    )
    assert posted.status_code == 201
    today.posted.append(posted.get_json()["event_id"])
    assert today.count() == 3


def test_a_redis_that_fails_midway_does_not_fail_the_request(
    today, workspace, monkeypatch
):
    live = workspace.client.application.extensions["engagement_live"]
    monkeypatch.setattr(
        live.client,
        "pipeline",
        lambda **_: (_ for _ in ()).throw(redis.ConnectionError("down")),
    )
    today.post()
    assert today.count() == 1
