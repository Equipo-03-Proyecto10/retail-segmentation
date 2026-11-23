"""Recommendations parity after a change, within the propagation bound (F20-10).

The web computes a customer's recommendations from PostgreSQL on every request;
recommendations-service computes the same rule (RN-40) through the api_* views
and keeps each answer for 120 s in its result cache, which the streams it
consumes empty. So after a change the web is right at once, and the service is
right once the change's event has reached it. These cases make one change of
each kind the service consumes, each chosen so that it changes the answer:

* a preference change: an interest category whose products are eligible but
  not recommended is added through customer-preferences-service
  (stream:preferences.v1.changed);
* a sale: the customer buys a recommended product through sales-service, so it
  is no longer eligible (stream:sales.v1.recorded);
* a stock change: a recommended product is counted to zero at the customer's
  usual store through stock-service (stream:stock.v1.changed);
* a new run: the web runs a segmentation (stream:segments.v1.run_recorded).

Before each change the service's answer is cached and agrees with the web's.
After it, the service must agree with the web again within the maximum the
stream's contract states for the `recommendations` group (`## Propagation
bound`, F20-06), and its first read after that bound must be computed after
the change, never the cached answer from before it.

They write on the throwaway stack named by PARITY_DATABASE_URL; never point
them at the instance. Each case prints how long the service took.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import pytest

from tests.parity import stack
from tests.parity.compare import Recommendation, diff_recommendations
from tests.parity.stack import occurred_at, stream_bound

GROUP = "recommendations"
# The service's consumption window and its default limit (services/
# recommendations/app/config.py), the web's defaults too.
WINDOW_DAYS = 180
LIMIT = 10
# Between the two reads that prove an answer is cached: two computations never
# share a computed_at, which carries microseconds, so only a hit repeats it.
CACHE_PROOF_GAP = 0.2


@dataclass
class Subject:
    customer_id: str
    usual_store_id: int
    channel_id: int
    # The category the preference case adds: one of an eligible product that
    # no signal chooses yet, so adding it changes the answer.
    added_category_id: int


def _report(found) -> str:
    return "\n".join(str(d) for d in found)


def _candidates(db) -> list[str]:
    """Customers the service may personalize for and the web treats alike:
    PERSONALIZATION granted and no category restricted (the web reads neither)."""

    rows = db(
        "SELECT c.customer_id::text FROM api_preferences.consent_state_v1 AS c "
        "WHERE c.purpose = 'PERSONALIZATION' AND c.decision = 'GRANTED' "
        "AND NOT EXISTS (SELECT 1 FROM api_preferences.restriction_v1 AS r "
        "WHERE r.customer_id = c.customer_id) ORDER BY c.customer_id"
    )
    return [r[0] for r in rows]


def _interests(db, customer: str) -> list[int]:
    return [
        r[0]
        for r in db(
            "SELECT category_id FROM api_preferences.interest_category_v1 "
            "WHERE customer_id = %s",
            (customer,),
        )
    ]


def _unchosen_category(db, customer: str, store_id: int, recommended) -> int | None:
    """The category of an active product in stock at the usual store that the
    customer has not bought in the window, is not recommended, and is outside
    every category the customer already declared."""

    rows = db(
        "SELECT p.category_id FROM product AS p "
        "JOIN inventory AS i ON i.product_id = p.product_id AND i.store_id = %s "
        "WHERE p.is_active AND i.quantity_on_hand > 0 "
        "AND NOT (p.product_id = ANY(%s)) AND NOT (p.category_id = ANY(%s)) "
        "AND NOT EXISTS (SELECT 1 FROM transaction AS t "
        "JOIN transaction_line AS l ON l.transaction_id = t.transaction_id "
        "WHERE t.customer_id = %s AND l.product_id = p.product_id "
        "AND t.occurred_at >= now() - make_interval(days => %s)) "
        "ORDER BY p.product_id LIMIT 1",
        (store_id, list(recommended), _interests(db, customer), customer, WINDOW_DAYS),
    )
    return rows[0][0] if rows else None


@pytest.fixture(scope="module")
def subject(db, web, recommendations) -> Subject:
    """The first candidate with 2 to 7 recommendations and an eligible product
    no signal chooses, so a sale and a stock change each have one to remove and
    an added interest has room to show."""

    [[channel]] = db("SELECT min(channel_id) FROM channel")
    for customer in _candidates(db):
        seen = web.recommendations(customer)
        if seen.status != "recommended" or not 2 <= len(seen.items) <= LIMIT - 3:
            continue
        _, body = recommendations.read(customer)
        store = body["usual_store_id"]
        added = _unchosen_category(db, customer, store, seen.products)
        if added is not None:
            return Subject(customer, store, channel, added)
    pytest.fail(
        "no seeded customer with consent has 2 to 7 recommendations and an "
        "eligible product no signal chooses"
    )


@pytest.fixture(scope="module")
def admin_sales(recommendations) -> stack.ServicePath:
    """sales-service as ADMIN, who may sell at any store; a STORE_MANAGER only
    sells at the stores they are assigned to."""

    return stack.ServicePath(
        stack.env("PARITY_GATEWAY_URL"),
        stack.env("PARITY_WEB_EMAIL"),
        stack.env("PARITY_WEB_PASSWORD"),
    )


def _warm(web, recommendations, customer: str, change: str, differences):
    """Both paths agree, and the service's answer comes from its cache."""

    on_web = web.recommendations(customer)
    on_service, first = recommendations.read(customer)
    time.sleep(CACHE_PROOF_GAP)
    _, second = recommendations.read(customer)
    differences.extend(diff_recommendations(f"{change}, before", on_web, on_service))
    assert first["computed_at"] == second["computed_at"], (
        "the service's answer was not cached before the change: "
        f"{first['computed_at']} then {second['computed_at']}"
    )
    return on_web, first


def _after(
    web,
    recommendations,
    customer: str,
    change: str,
    contract: str,
    changed_at: datetime,
    started: float,
    cached: dict,
    differences,
    must_change: Recommendation | None,
) -> dict:
    """Wait for the service to agree with the web within the stream's bound,
    then read once after the bound: never the answer cached before."""

    bound = stream_bound(contract, GROUP)
    on_web = web.recommendations(customer)
    if must_change is not None:
        assert on_web != must_change, f"{change} did not change the web's answer"
    deadline = started + bound
    on_service, _ = recommendations.read(customer)
    while on_service != on_web and time.monotonic() < deadline:
        time.sleep(0.1)
        on_service, _ = recommendations.read(customer)
    took = time.monotonic() - started
    differences.extend(
        diff_recommendations(f"{change}, within the bound", on_web, on_service)
    )

    time.sleep(max(0.0, deadline - time.monotonic()))
    after_bound, body = recommendations.read(customer)
    differences.extend(
        diff_recommendations(f"{change}, after the bound", on_web, after_bound)
    )
    computed = datetime.fromisoformat(body["computed_at"].replace("Z", "+00:00"))
    stale = body["computed_at"] == cached["computed_at"] or computed < changed_at
    print(
        f"\n{change}: agreed after {took * 1000:.0f} ms (bound {bound * 1000:.0f} ms, "
        f"{contract}); after the bound computed_at={body['computed_at']}, "
        f"cached before={cached['computed_at']}"
    )
    print(_report(differences) if differences else "no difference")
    assert not differences, _report(differences)
    assert took <= bound, f"{change}: {took:.3f} s exceeds the bound {bound} s"
    assert not stale, (
        f"{change}: the first read after the bound served the answer cached at "
        f"{cached['computed_at']}, before the change at {changed_at.isoformat()}"
    )
    return body


def _now(db) -> tuple[datetime, float]:
    """When a write's answer came back, which follows its commit, on the
    stack's clock: computed_at is the service's clock, and a laptop's Docker VM
    can run more than a minute behind or ahead of the host (it did on the first
    local run), so the host's clock is never compared with it."""

    [[now]] = db("SELECT clock_timestamp()")
    return now, time.monotonic()


def test_a_preference_change_reaches_the_service_within_the_bound(
    db, web, recommendations, subject, differences
) -> None:
    customer = subject.customer_id
    before, cached = _warm(web, recommendations, customer, "preference", differences)
    recommendations.replace_interests(
        customer, _interests(db, customer) + [subject.added_category_id]
    )
    changed_at, started = _now(db)
    _after(
        web,
        recommendations,
        customer,
        "preference",
        "preferences.changed.v1",
        changed_at,
        started,
        cached,
        differences,
        before,
    )


def test_a_sale_reaches_the_service_within_the_bound(
    db, web, recommendations, admin_sales, subject, differences
) -> None:
    customer = subject.customer_id
    before, cached = _warm(web, recommendations, customer, "sale", differences)
    product = before.products[0]
    [[price]] = db(
        "SELECT list_price::text FROM product WHERE product_id = %s", (product,)
    )
    reply = admin_sales.record_sale(
        {
            "customer_id": customer,
            "store_id": subject.usual_store_id,
            "channel_id": subject.channel_id,
            "occurred_at": occurred_at().isoformat(timespec="seconds"),
            "lines": [{"product_id": product, "quantity": 1, "unit_price": price}],
        }
    )
    assert reply.status == 201, f"{reply.status} {reply.body!r}"
    changed_at, started = _now(db)
    _after(
        web,
        recommendations,
        customer,
        "sale",
        "sales.recorded.v1",
        changed_at,
        started,
        cached,
        differences,
        before,
    )


def test_a_stock_change_reaches_the_service_within_the_bound(
    db, web, recommendations, subject, differences
) -> None:
    customer = subject.customer_id
    before, cached = _warm(web, recommendations, customer, "stock", differences)
    recommendations.count_stock(subject.usual_store_id, before.products[0], 0)
    changed_at, started = _now(db)
    _after(
        web,
        recommendations,
        customer,
        "stock",
        "stock.changed.v1",
        changed_at,
        started,
        cached,
        differences,
        before,
    )


def test_a_new_run_reaches_the_service_within_the_bound(
    db, web, recommendations, subject, differences
) -> None:
    customer = subject.customer_id
    _, cached = _warm(web, recommendations, customer, "run", differences)
    [[previous]] = db("SELECT coalesce(max(run_id), 0) FROM segmentation_run")
    web.run_segmentation()
    changed_at, started = _now(db)
    [[run_id]] = db("SELECT max(run_id) FROM segmentation_run")
    assert run_id > previous, "the web recorded no run"
    # A run may leave the answer's items as they were; the run behind the
    # customer's label is what a stale answer would still name.
    body = _after(
        web,
        recommendations,
        customer,
        "run",
        "segments.run_recorded.v1",
        changed_at,
        started,
        cached,
        differences,
        None,
    )
    [[open_run]] = db(
        "SELECT run_id FROM customer_segment_history "
        "WHERE customer_id = %s AND valid_to IS NULL",
        (customer,),
    )
    assert open_run == run_id, f"the run left the customer's open row at {open_run}"
    assert (
        body["run_id"] == run_id
    ), f"the service answers from run {body['run_id']}, not the new run {run_id}"
