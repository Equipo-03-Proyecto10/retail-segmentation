"""A sale event changes one customer's profile and nothing else (F22-05, ADR-0063)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
import redis
from flask import Flask
from mosaiq_kit.streams import StreamConsumer

from app import recompute
from tests.conftest import Shop, days_ago

RULE_SET = 1
STREAM = "stream:sales.v1.recorded"
pytestmark = pytest.mark.postgres


def _recompute_sale(
    service: psycopg.Connection, transaction_id: int, as_of: datetime | None = None
) -> uuid.UUID | None:
    with service.transaction():
        return recompute.from_sale(service, transaction_id, RULE_SET, as_of)


def _recompute(
    service: psycopg.Connection, customer_id: uuid.UUID, as_of: datetime
) -> None:
    with service.transaction():
        recompute.customer(service, customer_id, RULE_SET, as_of)


def test_the_seeded_rule_set_is_the_one_in_force(service: psycopg.Connection) -> None:
    row = service.execute(
        "SELECT window_days, cardinality(recency_boundaries), fallback_label_code,"
        " (SELECT count(*) FROM rfm.rule_set_band WHERE rule_set_id = 1)"
        " FROM rfm.rule_set WHERE rule_set_id = 1"
    ).fetchone()
    assert row == (180, 4, "LOST", 30)


# --- Acceptance 1: exactly that customer ------------------------------------


def test_a_sale_writes_that_customers_profile_and_no_other(
    shop: Shop, service: psycopg.Connection, owner: psycopg.Connection, now: datetime
) -> None:
    buyer, bystander = shop.customer(), shop.customer()
    shop.sale(bystander, days_ago(now, 5), quantity=3, unit_price="50.00")
    _recompute(service, bystander, now)
    others_before = owner.execute(
        "SELECT customer_id, xmin::text, computed_at FROM rfm.customer_profile"
        " WHERE customer_id <> %s ORDER BY customer_id",
        (buyer,),
    ).fetchall()

    sale = shop.sale(buyer, days_ago(now, 2), quantity=4, unit_price="25.00")
    assert _recompute_sale(service, sale, now) == buyer

    state, rule_set, r_days, f_count, m_total, *_ = shop.profile(buyer) or ()
    assert (state, rule_set, r_days, f_count, m_total) == (
        "scored",
        RULE_SET,
        2,
        1,
        Decimal("100.00"),
    )
    others_after = owner.execute(
        "SELECT customer_id, xmin::text, computed_at FROM rfm.customer_profile"
        " WHERE customer_id <> %s ORDER BY customer_id",
        (buyer,),
    ).fetchall()
    assert others_after == others_before, "another customer's row was written"


def test_no_other_profile_is_read_for_writing(
    shop: Shop, service: psycopg.Connection, owner_url: str, now: datetime
) -> None:
    """Every other profile row is locked by someone else for the whole recomputation.

    A recomputation that selected, updated or locked a row other than the buyer's
    would wait for that lock, and the 500 ms lock timeout would end it with an error.
    """
    buyer = shop.customer()
    others = [shop.customer() for _ in range(3)]
    for other in others:
        shop.sale(other, days_ago(now, 9))
        _recompute(service, other, now)
    sale = shop.sale(buyer, days_ago(now, 1))
    with psycopg.connect(owner_url) as locker:
        locker.execute(
            "SELECT 1 FROM rfm.customer_profile WHERE customer_id <> %s FOR UPDATE",
            (buyer,),
        ).fetchall()
        service.execute("SET lock_timeout = '500ms'")
        try:
            assert _recompute_sale(service, sale, now) == buyer
        finally:
            service.execute("RESET lock_timeout")
        locker.rollback()
    assert shop.profile(buyer) is not None


def test_a_sale_without_lines_names_no_customer(
    shop: Shop, service: psycopg.Connection
) -> None:
    assert _recompute_sale(service, 2_000_000_000) is None


# --- Acceptance 2: twice, or out of order -----------------------------------


def test_the_same_events_twice_or_out_of_order_give_the_in_order_profile(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    ordered, shuffled = shop.customer(), shop.customer()
    sales: dict[uuid.UUID, list[int]] = {ordered: [], shuffled: []}
    for customer in sales:
        for age, quantity, price in (
            (40, 2, "30.00"),
            (12, 5, "80.00"),
            (3, 1, "9.99"),
        ):
            sales[customer].append(
                shop.sale(
                    customer, days_ago(now, age), quantity=quantity, unit_price=price
                )
            )

    for transaction in sales[ordered]:
        _recompute_sale(service, transaction, now)
    first, second, third = sales[shuffled]
    for transaction in (third, first, third, second, first, second, third):
        _recompute_sale(service, transaction, now)

    assert shop.profile(shuffled) == shop.profile(ordered)
    assert shop.profile(ordered) is not None


def test_a_late_event_for_an_early_sale_still_counts_the_later_one(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    """The event is a trigger, not a delta: the last sale is counted although its
    own event has not arrived when the first sale's does."""
    customer = shop.customer()
    early = shop.sale(customer, days_ago(now, 30), quantity=1, unit_price="10.00")
    shop.sale(customer, days_ago(now, 1), quantity=1, unit_price="15.00")
    _recompute_sale(service, early, now)
    row = shop.profile(customer)
    assert row is not None and row[3:5] == (2, Decimal("25.00"))


def test_a_reversed_sale_stops_counting_on_the_next_event(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    kept = shop.sale(customer, days_ago(now, 4), unit_price="20.00")
    gone = shop.sale(customer, days_ago(now, 2), unit_price="30.00")
    _recompute_sale(service, gone, now)
    row = shop.profile(customer)
    assert row is not None and row[3:5] == (2, Decimal("50.00"))
    shop.reverse(gone)
    _recompute_sale(service, kept, now)
    row = shop.profile(customer)
    assert row is not None and row[3:5] == (1, Decimal("20.00"))
    # The reversed sale's own event, delivered late, gives the same profile.
    _recompute_sale(service, gone, now)
    assert shop.profile(customer) == row


def test_two_recomputations_of_one_customer_serialize(
    shop: Shop, service_url: str, now: datetime
) -> None:
    """Two consumers handling the same customer's events at once end on the profile
    of all the sales, never on a profile read before the other's write."""
    customer = shop.customer()
    sales = [shop.sale(customer, days_ago(now, n)) for n in (1, 2, 3, 4)]
    errors: list[BaseException] = []

    def work(transaction: int) -> None:
        try:
            with psycopg.connect(service_url) as connection:
                with connection.transaction():
                    recompute.from_sale(connection, transaction, RULE_SET)
        except BaseException as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=work, args=(s,)) for s in sales * 3]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    row = shop.profile(customer)
    assert row is not None and row[3:5] == (4, Decimal("40.00"))
    assert len(shop.events(customer)) == 1


# --- Acceptance 3: one event, in the same transaction -----------------------


def test_a_changed_profile_holds_one_event_and_a_repeat_adds_none(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    first = shop.sale(customer, days_ago(now, 6))
    _recompute_sale(service, first, now)
    assert shop.events(customer) == ["INSERT"]

    _recompute_sale(service, first, now)
    assert shop.events(customer) == ["INSERT"], "a repeat announced nothing new"

    second = shop.sale(customer, days_ago(now, 1))
    _recompute_sale(service, second, now)
    assert shop.events(customer) == ["INSERT", "UPDATE"]


def test_a_recomputation_that_only_moves_the_clock_is_silent(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 6.5))
    _recompute_sale(service, sale, now)
    before = shop.profile(customer)
    _recompute_sale(service, sale, now + timedelta(days=1))
    after = shop.profile(customer)
    assert before is not None and after is not None
    assert after[2] == before[2] + 1, "r_days advanced"
    assert shop.events(customer) == ["INSERT"]


def test_a_rolled_back_recomputation_publishes_nothing(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 6))
    with pytest.raises(RuntimeError):
        with service.transaction():
            recompute.from_sale(service, sale, RULE_SET, now)
            raise RuntimeError("fail after the write")
    assert shop.profile(customer) is None
    assert shop.events(customer) == []


def test_the_service_cannot_write_the_outbox_itself(
    service: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        service.execute(
            "INSERT INTO rfm_events.outbox (stream, event_type, entity_id)"
            " VALUES ('stream:rfm.v1.profile_changed', 'INSERT', 'x')"
        )


# --- Acceptance 4: the window ------------------------------------------------


def test_a_sale_outside_the_window_leaves_the_customer_unassigned(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    old = shop.sale(customer, days_ago(now, 200))
    _recompute_sale(service, old, now)
    assert shop.profile(customer) == (
        "unassigned", RULE_SET, None, 0, Decimal("0.00"), None, None, None, None, None,
    )  # fmt: skip
    row = service.execute(
        "SELECT next_change_at FROM rfm.customer_profile WHERE customer_id = %s",
        (customer,),
    ).fetchone()
    assert row == (None,)


def test_the_window_is_closed_at_both_ends_and_counts_net_purchases_only(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 180), unit_price="1.00")
    shop.sale(customer, days_ago(now, 180) - timedelta(seconds=1), unit_price="100.00")
    last = shop.sale(customer, days_ago(now, 10), quantity=2, unit_price="5.00")
    gone = shop.sale(customer, days_ago(now, 20), unit_price="400.00")
    shop.reverse(gone)
    _recompute_sale(service, last, now)
    row = shop.profile(customer)
    assert row is not None and row[:5] == (
        "scored", RULE_SET, 10, 2, Decimal("11.00"),
    )  # fmt: skip


def test_a_sale_leaving_the_window_moves_the_profile_when_it_is_recomputed(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    old = shop.sale(customer, days_ago(now, 170), unit_price="10.00")
    shop.sale(customer, days_ago(now, 1), unit_price="20.00")
    _recompute_sale(service, old, now)
    row = shop.profile(customer)
    assert row is not None and row[3:5] == (2, Decimal("30.00"))
    _recompute_sale(service, old, now + timedelta(days=11))
    row = shop.profile(customer)
    assert row is not None and row[3:5] == (1, Decimal("20.00"))
    assert shop.events(customer) == ["INSERT", "UPDATE"]


def test_next_change_at_is_stored_for_the_daily_pass(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 2))
    _recompute_sale(service, sale, now)
    row = service.execute(
        "SELECT next_change_at FROM rfm.customer_profile WHERE customer_id = %s",
        (customer,),
    ).fetchone()
    assert row is not None
    assert row[0] == days_ago(now, 2) + timedelta(days=21)


# --- The stream, end to end --------------------------------------------------


def test_an_entry_of_the_sales_stream_recomputes_its_customer_once_acknowledged(
    shop: Shop,
    build_app: Callable[..., Flask],
    redis_connection: redis.Redis,
    now: datetime,
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 3), quantity=2, unit_price="12.50")
    app = build_app()
    redis_connection.delete(STREAM)
    consumer = StreamConsumer(
        redis_connection, STREAM, app.extensions["rfm_handle_sale"]
    )
    consumer._ensure_group()
    for _ in range(2):  # one entry, redelivered by the producer
        redis_connection.xadd(
            STREAM,
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "INSERT",
                "entity_id": str(sale),
                "occurred_at": now.isoformat(),
                "v": "1",
            },
        )
    assert consumer.run_once() == 2

    row = shop.profile(customer)
    assert row is not None and row[:5] == (
        "scored", RULE_SET, 3, 1, Decimal("25.00"),
    )  # fmt: skip
    assert shop.events(customer) == ["INSERT"]
    pending = redis_connection.xpending(STREAM, consumer.group)
    assert pending["pending"] == 0
    redis_connection.delete(STREAM)


def test_an_entry_that_names_no_sale_is_acknowledged_and_changes_nothing(
    shop: Shop,
    build_app: Callable[..., Flask],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
) -> None:
    app = build_app()
    redis_connection.delete(STREAM)
    consumer = StreamConsumer(
        redis_connection, STREAM, app.extensions["rfm_handle_sale"]
    )
    consumer._ensure_group()
    before = owner.execute("SELECT count(*) FROM rfm.customer_profile").fetchone()
    redis_connection.xadd(STREAM, {"event_id": str(uuid.uuid4()), "entity_id": "abc"})
    redis_connection.xadd(
        STREAM, {"event_id": str(uuid.uuid4()), "entity_id": "2000000000"}
    )
    assert consumer.run_once() == 2
    after = owner.execute("SELECT count(*) FROM rfm.customer_profile").fetchone()
    assert before == after
    redis_connection.delete(STREAM)


def test_a_missing_rule_set_is_a_failure_the_stream_redelivers(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 3))
    with pytest.raises(recompute.NoRuleSet):
        with service.transaction():
            recompute.from_sale(service, sale, 999_999, now)
    assert shop.profile(customer) is None


def test_the_rule_sets_listing_shows_the_seeded_rule_set(
    build_app: Callable[..., Flask], token: Callable[..., dict[str, str]]
) -> None:
    response = build_app().test_client().get("/v1/rfm-rule-sets", headers=token())
    assert response.status_code == 200
    body = response.get_json()
    first = next(item for item in body["rule_sets"] if item["rule_set_id"] == 1)
    assert (first["window_days"], first["recency_boundaries"]) == (
        180,
        [20, 44, 68, 92],
    )
    assert first["monetary_boundaries"][0] == "4510.00"
    assert len(first["bands"]) == 30 and first["fallback_label_code"] == "LOST"
    assert first["published"] is True and "state" not in first
    assert body["page_info"]["total"] >= 1
