"""A reversal, a late sale and a duplicate leave the profile true (F22-06, ADR-0062).

Every case drives the real handlers of `stream:sales.v1.reversed` and
`stream:sales.v1.recorded` against PostgreSQL. The reversals are written as
svc_sales writes them (the reversal, its line and the line's returned quantity in
one transaction), so what the engine reads is the net view and nothing is faked.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
import redis
from flask import Flask
from mosaiq_kit.streams import StreamConsumer

from app import store
from app.profile import recency_score
from tests.conftest import Shop, days_ago

REVERSED = "stream:sales.v1.reversed"
RECORDED = "stream:sales.v1.recorded"
pytestmark = pytest.mark.postgres


def _reversal_of(owner: psycopg.Connection, sale: int) -> int:
    row = owner.execute(
        "SELECT max(reversal_id) FROM sales.sale_reversal WHERE transaction_id = %s",
        (sale,),
    ).fetchone()
    assert row is not None and row[0] is not None
    return int(row[0])


def _entry(sale: int, reversal: int, event_id: str | None = None) -> dict[str, str]:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "INSERT",
        "entity_id": f"{sale}:{reversal}",
        "occurred_at": "2026-11-01T12:00:00Z",
        "v": "1",
    }


@pytest.fixture
def app(build_app: Callable[..., Flask]) -> Flask:
    return build_app()


def _reversed(app: Flask, owner: psycopg.Connection, sale: int) -> None:
    """The event of the sale's latest reversal, delivered once."""
    app.extensions["rfm_handle_reversal"](_entry(sale, _reversal_of(owner, sale)))


def _recorded(app: Flask, sale: int) -> None:
    app.extensions["rfm_handle_sale"](
        {"event_id": str(uuid.uuid4()), "entity_id": str(sale)}
    )


def _values(profile: tuple[object, ...] | None) -> tuple[object, ...]:
    assert profile is not None
    return profile


def test_a_whole_cancellation_leaves_the_profile_a_sale_that_never_was_would(
    shop: Shop, app: Flask, owner: psycopg.Connection, now: datetime
) -> None:
    cancelled_for, never_had = shop.customer(), shop.customer()
    plan = ((40, 2, "30.00"), (12, 5, "80.00"), (3, 1, "9.99"))
    sales = [
        shop.sale(cancelled_for, days_ago(now, age), quantity=q, unit_price=p)
        for age, q, p in plan
    ]
    for age, q, p in (plan[0], plan[2]):  # the same history without the 12-day sale
        shop.sale(never_had, days_ago(now, age), quantity=q, unit_price=p)
    _recorded(app, sales[1])
    assert _values(shop.profile(cancelled_for))[3:5] == (3, Decimal("469.99"))

    shop.reverse(sales[1])
    _reversed(app, owner, sales[1])

    _recorded(app, shop.sales[-1])  # the other customer, computed from scratch
    assert shop.profile(cancelled_for) == shop.profile(never_had)
    assert _values(shop.profile(cancelled_for))[3:5] == (2, Decimal("69.99"))


def test_a_partial_return_lowers_m_and_leaves_f_alone(
    shop: Shop, app: Flask, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 5), quantity=4, unit_price="25.00")
    _recorded(app, sale)
    before = _values(shop.profile(customer))
    assert before[2:5] == (5, 1, Decimal("100.00"))

    shop.return_units(sale, 1)
    _reversed(app, owner, sale)

    after = _values(shop.profile(customer))
    # F21-01 decided: M is the sum of net amounts, F counts sales with a net unit.
    assert after[2:5] == (5, 1, Decimal("75.00"))
    # What equals a customer who only ever bought three units.
    twin = shop.customer()
    other = shop.sale(twin, days_ago(now, 5), quantity=3, unit_price="25.00")
    _recorded(app, other)
    assert shop.profile(twin) == shop.profile(customer)


def test_a_return_of_every_unit_makes_the_sale_stop_counting_so_f_falls(
    shop: Shop, app: Flask, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 9), quantity=1, unit_price="40.00")
    emptied = shop.sale(customer, days_ago(now, 2), quantity=2, unit_price="10.00")
    _recorded(app, emptied)
    assert _values(shop.profile(customer))[3:5] == (2, Decimal("60.00"))

    shop.return_units(emptied, 1)
    _reversed(app, owner, emptied)
    assert _values(shop.profile(customer))[3:5] == (2, Decimal("50.00"))
    shop.return_units(emptied, 1)
    _reversed(app, owner, emptied)

    row = _values(shop.profile(customer))
    assert row[2:5] == (9, 1, Decimal("40.00"))  # a second return, the sale is out


def test_when_the_latest_sale_is_cancelled_r_comes_from_the_one_before(
    shop: Shop, app: Flask, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 40))
    shop.sale(customer, days_ago(now, 12))
    latest = shop.sale(customer, days_ago(now, 2))
    _recorded(app, latest)
    assert _values(shop.profile(customer))[2] == 2

    shop.reverse(latest)
    _reversed(app, owner, latest)

    row = _values(shop.profile(customer))
    assert row[2] == 12 and row[3] == 2
    assert row[5] is not None  # an R score, from the 12-day purchase
    rules = store.rule_set(owner, 1)
    assert rules is not None
    assert row[5] == recency_score(12, rules.recency)


def test_a_customer_left_with_no_purchase_is_unassigned_not_zero_scored(
    shop: Shop, app: Flask, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    only = shop.sale(customer, days_ago(now, 6), quantity=2, unit_price="20.00")
    _recorded(app, only)
    assert _values(shop.profile(customer))[0] == "scored"

    shop.reverse(only)
    _reversed(app, owner, only)

    row = _values(shop.profile(customer))
    # RN-21: recorded as unassigned, with no scores and no label (never 0, never LOST).
    assert row[0] == "unassigned"
    assert row[2:4] == (None, 0) and row[4] == Decimal("0.00")
    assert row[5:10] == (None, None, None, None, None)
    # The change is announced: the customer lost their label.
    assert shop.events(customer) == ["INSERT", "UPDATE"]


def test_a_reversal_is_a_late_event_that_still_gives_the_in_order_profile(
    shop: Shop, app: Flask, owner: psycopg.Connection, now: datetime
) -> None:
    """The reversal is consumed before the sale's own recorded event: same profile."""
    forward, backward = shop.customer(), shop.customer()
    for customer in (forward, backward):
        kept = shop.sale(customer, days_ago(now, 20), unit_price="15.00")
        gone = shop.sale(customer, days_ago(now, 1), unit_price="35.00")
        shop.reverse(gone)
        if customer == forward:
            _recorded(app, kept)
            _recorded(app, gone)
            _reversed(app, owner, gone)
        else:
            _reversed(app, owner, gone)
            _recorded(app, gone)
            _recorded(app, kept)
    assert shop.profile(forward) == shop.profile(backward)
    assert _values(shop.profile(forward))[3:5] == (1, Decimal("15.00"))


def test_a_late_sale_counts_by_when_it_happened_not_when_it_arrived(
    shop: Shop, app: Flask, now: datetime
) -> None:
    customer = shop.customer()
    recent = shop.sale(customer, days_ago(now, 2), unit_price="10.00")
    _recorded(app, recent)
    # Recorded only now, dated three weeks ago: it is a late sale.
    late = shop.sale(customer, days_ago(now, 21), quantity=3, unit_price="10.00")
    _recorded(app, late)

    row = _values(shop.profile(customer))
    assert row[2] == 2  # R is the newest sale's, not the late one's
    assert row[3:5] == (2, Decimal("40.00"))


def test_the_engine_cannot_rewrite_a_closed_run(owner: psycopg.Connection) -> None:
    """ADR-0017 holds structurally: a late sale changes the current profile, and the
    history of a closed run is `public.customer_segment_history`, which svc_rfm cannot
    write, so no handler can rewrite it."""
    for privilege in ("INSERT", "UPDATE", "DELETE"):
        granted = owner.execute(
            "SELECT has_table_privilege('svc_rfm', %s, %s)",
            ("public.customer_segment_history", privilege),
        ).fetchone()
        assert granted == (False,), privilege


def test_a_late_sale_older_than_the_window_changes_nothing(
    shop: Shop, app: Flask, now: datetime
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 3), unit_price="12.00")
    _recorded(app, shop.sales[-1])
    before = shop.profile(customer)
    events = shop.events(customer)
    stale = shop.sale(customer, days_ago(now, 200), quantity=9, unit_price="99.00")
    _recorded(app, stale)  # RN-37 allows it in; the window leaves it out
    assert shop.profile(customer) == before
    assert shop.events(customer) == events


def test_a_redelivered_reversal_changes_nothing_and_announces_nothing_twice(
    shop: Shop,
    app: Flask,
    owner: psycopg.Connection,
    redis_connection: redis.Redis,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 30), unit_price="20.00")
    gone = shop.sale(customer, days_ago(now, 4), unit_price="45.00")
    _recorded(app, gone)
    shop.reverse(gone)
    reversal = _reversal_of(owner, gone)
    event = str(uuid.uuid4())
    redis_connection.delete(REVERSED)
    handled: list[dict[str, str]] = []

    def counting(fields: dict[str, str]) -> None:
        handled.append(fields)
        app.extensions["rfm_handle_reversal"](fields)

    consumer = StreamConsumer(redis_connection, REVERSED, counting)
    consumer._ensure_group()
    # The same entry twice (one event_id), and the producer's own duplicate (a new
    # event_id for the same reversal): three deliveries, one effect.
    redis_connection.xadd(REVERSED, _entry(gone, reversal, event))
    redis_connection.xadd(REVERSED, _entry(gone, reversal, event))
    redis_connection.xadd(REVERSED, _entry(gone, reversal))
    assert consumer.run_once() == 3
    # The kit drops the repeated event_id before the handler sees it; the producer's
    # duplicate carries a new one, so the handler runs for it and recomputes to the
    # same profile, and the trigger announces nothing for a recomputation that moves
    # no captured value.
    assert [f["event_id"] for f in handled].count(event) == 1
    assert len(handled) == 2

    row = _values(shop.profile(customer))
    assert row[3:5] == (1, Decimal("20.00"))
    assert shop.events(customer) == ["INSERT", "UPDATE"]
    assert redis_connection.xpending(REVERSED, consumer.group)["pending"] == 0
    redis_connection.delete(REVERSED)


@pytest.mark.parametrize("entity", ["abc", "12", "12:x", ":3", "x:3", ""])
def test_an_entry_that_names_no_reversal_is_acknowledged_and_changes_nothing(
    app: Flask,
    owner: psycopg.Connection,
    redis_connection: redis.Redis,
    entity: str,
) -> None:
    redis_connection.delete(REVERSED)
    consumer = StreamConsumer(
        redis_connection, REVERSED, app.extensions["rfm_handle_reversal"]
    )
    consumer._ensure_group()
    count = "SELECT count(*) FROM rfm.customer_profile"
    before = owner.execute(count).fetchone()
    redis_connection.xadd(
        REVERSED, {"event_id": str(uuid.uuid4()), "entity_id": entity}
    )
    assert consumer.run_once() == 1
    assert owner.execute(count).fetchone() == before
    redis_connection.delete(REVERSED)


def test_a_reversal_of_a_sale_outside_the_window_leaves_the_profile_alone(
    shop: Shop, app: Flask, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 3), unit_price="30.00")
    _recorded(app, shop.sales[-1])
    old = shop.sale(customer, days_ago(now, 200), unit_price="50.00")
    before, events = shop.profile(customer), shop.events(customer)
    shop.reverse(old)
    _reversed(app, owner, old)
    assert shop.profile(customer) == before
    assert shop.events(customer) == events


def test_a_reversal_names_only_its_own_customer(
    shop: Shop, app: Flask, owner: psycopg.Connection, now: datetime
) -> None:
    buyer, bystander = shop.customer(), shop.customer()
    gone = shop.sale(buyer, days_ago(now, 2), unit_price="22.00")
    kept = shop.sale(bystander, days_ago(now, 5), unit_price="18.00")
    _recorded(app, gone)
    _recorded(app, kept)
    bystander_before = owner.execute(
        "SELECT xmin::text, computed_at FROM rfm.customer_profile"
        " WHERE customer_id = %s",
        (bystander,),
    ).fetchone()
    shop.reverse(gone)
    _reversed(app, owner, gone)
    assert (
        owner.execute(
            "SELECT xmin::text, computed_at FROM rfm.customer_profile"
            " WHERE customer_id = %s",
            (bystander,),
        ).fetchone()
        == bystander_before
    )
    assert _values(shop.profile(buyer))[0] == "unassigned"


def test_the_reversal_consumer_is_started_beside_the_sales_one(
    build_app: Callable[..., Flask], monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[str, Any]] = []

    def fake_start(self: StreamConsumer) -> None:
        started.append((self.stream, self.handler))

    monkeypatch.setattr(StreamConsumer, "start", fake_start)
    build_app()
    assert started == []  # off in tests, as every consumer is
    monkeypatch.setenv("STREAM_CONSUMER_ENABLED", "true")
    from app import create_app

    enabled = create_app()
    try:
        assert [stream for stream, _ in started] == [
            RECORDED,
            REVERSED,
            "stream:preferences.v1.consent_changed",
        ]
        assert started[1][1] is enabled.extensions["rfm_handle_reversal"]
    finally:
        enabled.extensions["mosaiq_kit"].db.close()


def test_a_malformed_entry_is_logged_with_the_stream_it_came_from(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="app"):
        app.extensions["rfm_handle_reversal"](_entry(1, 0) | {"entity_id": "x:3"})
        app.extensions["rfm_handle_reversal"](_entry(1, 0) | {"entity_id": "7:x"})
        app.extensions["rfm_handle_sale"](_entry(1, 0) | {"entity_id": "x"})
    messages = [r.getMessage() for r in caplog.records]
    assert any(REVERSED in m and "transaction id" in m for m in messages)
    assert any(REVERSED in m and "reversal id" in m for m in messages)
    assert any(RECORDED in m and "transaction id" in m for m in messages)
