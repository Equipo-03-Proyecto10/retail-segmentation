"""The result cache and its six invalidation consumers, on a real Redis."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from flask import Flask

import redis
from app.cache import INDEX, result_key
from app.config import STREAMS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/golden/recommendations.json").read_text())
RECOMMENDED = next(
    case for case in FIXTURE["cases"] if case["outcome"]["status"] == "recommended"
)
CUSTOMER = RECOMMENDED["input"]["customer_id"]


def _run_id(owner: psycopg.Connection) -> int:
    (run_id,) = owner.execute(
        "SELECT run_id FROM api_segments.current_label_v1 WHERE customer_id = %s",
        (CUSTOMER,),
    ).fetchone()
    return run_id


def _ask(app: Flask, token: Callable[..., dict], limit: int = 10) -> dict:
    response = app.test_client().get(
        "/v1/recommendations",
        query_string={"customer_id": CUSTOMER, "limit": str(limit)},
        headers=token(),
    )
    assert response.status_code == 200
    return response.get_json()


def _wait(predicate: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _groups_ready(client: redis.Redis) -> bool:
    for stream in STREAMS:
        try:
            groups = client.xinfo_groups(stream)
        except redis.ResponseError:
            return False
        if not any(group["name"] == "recommendations" for group in groups):
            return False
    return True


def test_a_result_is_cached_by_customer_run_and_limit_for_120_s(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
) -> None:
    first = _ask(app, token, limit=7)
    key = result_key(CUSTOMER, _run_id(owner), 7)
    assert key == f"recommendations:cache:{CUSTOMER}:{_run_id(owner)}:7"
    assert 0 < redis_connection.ttl(key) <= 120
    assert redis_connection.sismember(INDEX, key)
    # A second answer is the cached one, computed_at and all.
    assert _ask(app, token, limit=7) == first


def test_a_cache_write_needs_nothing_the_acl_denies(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Redis refuses MULTI and EXEC to this user (ADR-0037) but still runs the
    # writes queued between them, so a transactional pipeline still caches.
    # Its one symptom is this warning, on every write.
    with caplog.at_level(logging.WARNING, logger="app.cache"):
        _ask(app, token, limit=5)
    assert redis_connection.exists(result_key(CUSTOMER, _run_id(owner), 5))
    assert [r.getMessage() for r in caplog.records if r.name == "app.cache"] == []


@pytest.mark.parametrize("stream", STREAMS)
def test_an_event_on_each_stream_empties_the_cache(
    build_app: Callable[..., Flask],
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
    stream: str,
) -> None:
    app = build_app(consumers=True)
    assert _wait(lambda: _groups_ready(redis_connection), 10)
    _ask(app, token)
    key = result_key(CUSTOMER, _run_id(owner), 10)
    assert redis_connection.exists(key)
    redis_connection.xadd(
        stream,
        {"event_id": f"test-{stream}-{time.time_ns()}", "v": 1, "event_type": "TEST"},
    )
    assert _wait(lambda: not redis_connection.exists(key), 10)
    assert not redis_connection.exists(INDEX)


def test_stock_set_to_zero_by_the_web_leaves_the_next_answer_within_10_s(
    build_app: Callable[..., Flask],
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
    retail_url: str,
) -> None:
    app = build_app(consumers=True)
    assert _wait(lambda: _groups_ready(redis_connection), 10)
    before = _ask(app, token)
    product = before["items"][0]["product_id"]
    store = before["usual_store_id"]
    (quantity,) = owner.execute(
        "SELECT quantity_on_hand FROM inventory"
        " WHERE store_id = %s AND product_id = %s",
        (store, product),
    ).fetchone()
    try:
        # The web's CSV path: retail_app writes inventory, stock's trigger
        # captures it in stock_events.outbox.
        with psycopg.connect(retail_url, autocommit=True) as retail:
            retail.execute(
                "UPDATE inventory SET quantity_on_hand = 0"
                " WHERE store_id = %s AND product_id = %s",
                (store, product),
            )
        row = owner.execute(
            "SELECT event_id, stream, event_type, entity_id FROM stock_events.outbox"
            " WHERE relayed_at IS NULL ORDER BY occurred_at DESC LIMIT 1"
        ).fetchone()
        assert row is not None and row[1] == "stream:stock.v1.changed"
        # What stock-service's relay publishes for that row (kit OutboxRelay).
        redis_connection.xadd(
            row[1],
            {
                "event_id": str(row[0]),
                "v": 1,
                "event_type": row[2],
                "entity_id": row[3],
            },
        )
        assert _wait(
            lambda: product not in [i["product_id"] for i in _ask(app, token)["items"]],
            10,
        )
    finally:
        owner.execute(
            "UPDATE inventory SET quantity_on_hand = %s"
            " WHERE store_id = %s AND product_id = %s",
            (quantity, store, product),
        )
        owner.execute("DELETE FROM stock_events.outbox WHERE relayed_at IS NULL")


def test_with_redis_cache_down_the_same_items_are_recomputed(
    build_app: Callable[..., Flask], token: Callable[..., dict], app: Flask
) -> None:
    cached = _ask(app, token)
    down = build_app(cache_url="redis://127.0.0.1:1/0")
    answer = _ask(down, token)
    assert answer["items"] == cached["items"]
    assert answer["status"] == cached["status"]


def test_an_inflight_result_cannot_repopulate_the_cache_after_invalidation(
    app: Flask, token: Callable[..., dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import rule

    original = rule.recommend
    calls = []

    def invalidated_while_computing(*args):
        answer = original(*args)
        calls.append(answer)
        app.extensions["recommendations_cache"].invalidate()
        return answer

    monkeypatch.setattr(rule, "recommend", invalidated_while_computing)
    _ask(app, token)
    _ask(app, token)
    assert len(calls) == 2, "A pre-invalidation result was cached for 120 seconds"


def test_eviction_of_the_index_does_not_defeat_invalidation(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import rule

    _ask(app, token)
    redis_connection.delete(INDEX)
    app.extensions["recommendations_cache"].invalidate()
    original = rule.recommend
    calls = []

    def recompute(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(rule, "recommend", recompute)
    _ask(app, token)
    assert calls, "A result survived invalidation after the index was evicted"
