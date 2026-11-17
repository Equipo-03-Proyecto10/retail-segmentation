"""Inventory parity after a change, through both paths (F20-07, RT-03).

`tests/golden/` compares the web with the ports on fixed inputs. These cases
apply one scenario, a one-unit sale, through the web (a CSV import) and
through the service (POST /v1/sales), wait within the propagation bound, and
compare what /catalog/stock and GET /v1/stock-levels/{store_id}/{product_id}
report for every store and product touched. Differences are printed in full.

They write test sales and set stock on the throwaway stack named by
PARITY_DATABASE_URL; never point them at the instance.
"""

from __future__ import annotations

import concurrent.futures as futures
import uuid
from datetime import timedelta
from decimal import Decimal

from tests.parity.compare import ReportDifference, diff_levels
from tests.parity.stack import occurred_at, poll


def _report(found) -> str:
    return "\n".join(str(d) for d in found)


def _pick(db, count: int, minimum: int):
    """`count` distinct pairs of one store with stock, an active product with a
    price, a customer registered before today and a channel."""

    rows = db(
        "SELECT i.store_id, i.product_id, p.list_price::text "
        "FROM inventory AS i JOIN product AS p USING (product_id) "
        "WHERE p.is_active AND p.list_price > 0 AND i.quantity_on_hand >= %s "
        "ORDER BY i.store_id, i.product_id LIMIT %s",
        (minimum, count),
    )
    assert len(rows) == count, "the seed has too few stocked products"
    [[customer]] = db(
        "SELECT customer_id::text FROM customer WHERE registered_on < CURRENT_DATE "
        "ORDER BY customer_id LIMIT 1"
    )
    [[channel]] = db("SELECT min(channel_id) FROM channel")
    return (
        [(r[0], r[1]) for r in rows],
        customer,
        channel,
        {(r[0], r[1]): r[2] for r in rows},
    )


def _sale(customer, channel, store, product, price, quantity=1) -> dict:
    return {
        "customer_id": customer,
        "store_id": store,
        "channel_id": channel,
        "occurred_at": occurred_at().isoformat(timespec="seconds"),
        "lines": [{"product_id": product, "quantity": quantity, "unit_price": price}],
    }


def _csv_row(
    source_id, customer, channel, store, product, price, quantity=1, at=None
) -> dict:
    return {
        "transaction_id": source_id,
        "customer_id": customer,
        "store_id": store,
        "channel_id": channel,
        "occurred_at": (at or occurred_at()).isoformat(timespec="seconds"),
        "product_id": product,
        "quantity": quantity,
        "unit_price": price,
    }


def test_a_sale_through_each_path_leaves_the_same_stock(
    db, web, service, differences
) -> None:
    (via_web, via_service), customer, channel, prices = _pick(db, 2, 5)
    pairs = [via_web, via_service]
    before = web.stock(pairs)
    assert before == service.stock(pairs), "the paths disagree before any sale"

    web.import_sales(
        [
            _csv_row(
                "parity-" + uuid.uuid4().hex,
                customer,
                channel,
                *via_web,
                prices[via_web],
            )
        ]
    )
    reply = service.record_sale(
        _sale(customer, channel, *via_service, prices[via_service])
    )
    assert reply.status == 201, reply.body

    expected = {pair: before[pair] - 1 for pair in pairs}
    # Each reader sees both pairs: a change made by either path must show in
    # the other path's read.
    seen_by_web = poll(lambda: web.stock(pairs), expected)
    seen_by_service = poll(lambda: service.stock(pairs), expected)
    differences.extend(diff_levels(seen_by_web, seen_by_service))
    differences.extend(diff_levels(seen_by_web, expected))
    print("\n" + _report(differences) if differences else "\nno difference")
    assert not differences, _report(differences)


def test_concurrent_sales_on_the_last_unit_succeed_once(
    db, web, service, differences
) -> None:
    [pair], customer, channel, prices = _pick(db, 1, 1)
    store, product = pair
    [[original]] = db(
        "SELECT quantity_on_hand FROM inventory "
        "WHERE store_id = %s AND product_id = %s",
        (store, product),
    )
    db(
        "UPDATE inventory SET quantity_on_hand = 1 "
        "WHERE store_id = %s AND product_id = %s",
        (store, product),
    )
    try:
        _sell_the_last_unit(
            db, web, service, differences, pair, customer, channel, prices
        )
    finally:
        # Give back the units this case took by hand, so the suites that run
        # after it on the same stack (F20-03: tests/negative) find the seed's
        # stock less only the one unit really sold.
        db(
            "UPDATE inventory SET quantity_on_hand = quantity_on_hand + %s "
            "WHERE store_id = %s AND product_id = %s",
            (original - 1, store, product),
        )


def _sell_the_last_unit(db, web, service, differences, pair, customer, channel, prices):
    store, product = pair
    price = prices[pair]
    source_id = "parity-" + uuid.uuid4().hex

    def by_web() -> bool:
        web.import_sales(
            [_csv_row(source_id, customer, channel, store, product, price)]
        )
        [[found]] = db(
            "SELECT count(*) FROM transaction WHERE source_transaction_id = %s",
            (source_id,),
        )
        return found == 1

    def by_service() -> bool:
        return (
            service.record_sale(_sale(customer, channel, store, product, price)).status
            == 201
        )

    with futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(by_web), pool.submit(by_service), pool.submit(by_service)]
        outcomes = [job.result() for job in jobs]

    assert sum(outcomes) == 1, f"RN-14: {sum(outcomes)} of 3 sales succeeded"
    [[stored]] = db(
        "SELECT quantity_on_hand FROM inventory "
        "WHERE store_id = %s AND product_id = %s",
        (store, product),
    )
    assert stored == 0, f"stock is {stored}, not 0"
    expected = {pair: 0}
    seen_by_web = poll(lambda: web.stock([pair]), expected)
    seen_by_service = poll(lambda: service.stock([pair]), expected)
    differences.extend(diff_levels(seen_by_web, seen_by_service))
    differences.extend(diff_levels(seen_by_web, expected))
    assert not differences, _report(differences)
    assert all(q >= 0 for q in (seen_by_web[pair], seen_by_service[pair]))


def test_a_reversal_through_the_service_is_what_the_web_reports_and_stock_shows(
    db, web, service, differences
) -> None:
    """F21-03, ADR-0062: sales-service reverses a sale the web loaded by CSV and
    one it recorded itself. The web's reports index counts net units and the
    stock page and GET /v1/stock-levels show the restored units, the same."""

    (via_web, via_service), customer, channel, prices = _pick(db, 2, 8)
    pairs = [via_web, via_service]
    # The reports window ends at the newest sale. This one stays the newest
    # (the sales below are a minute old), so only the reversals move the figures.
    web.import_sales(
        [
            _csv_row(
                "parity-" + uuid.uuid4().hex,
                customer,
                channel,
                *via_web,
                prices[via_web],
                at=occurred_at() + timedelta(seconds=55),
            )
        ]
    )
    before_stock = poll(lambda: web.stock(pairs), service.stock(pairs))
    before_revenue = web.revenue()
    assert before_revenue is not None, "the reports index shows no sales"

    kept = "parity-" + uuid.uuid4().hex
    web.import_sales(
        [_csv_row(kept, customer, channel, *via_web, prices[via_web], quantity=3)]
    )
    cancelled = service.record_sale(
        _sale(customer, channel, *via_service, prices[via_service], quantity=2)
    )
    assert cancelled.status == 201, cancelled.body
    [[kept_id]] = db(
        "SELECT transaction_id FROM transaction WHERE source_transaction_id = %s",
        (kept,),
    )

    # One of three units comes back from the CSV sale; the other sale is
    # cancelled whole.
    returned = service.record_reversal(
        {
            "sale_id": kept_id,
            "kind": "RETURN",
            "lines": [
                {
                    "product_id": via_web[1],
                    "quantity": 1,
                    "reason": "Parity: one unit returned",
                }
            ],
        }
    )
    assert returned.status == 201, returned.body
    voided = service.record_reversal(
        {
            "sale_id": cancelled.json()["sale_id"],
            "kind": "CANCELLATION",
            "reason": "Parity: whole sale cancelled",
        }
    )
    assert voided.status == 201, voided.body

    price = Decimal(prices[via_web])
    expected_stock = {
        via_web: before_stock[via_web] - 2,
        via_service: before_stock[via_service],
    }
    expected_revenue = (
        before_revenue[0] + 2 * price,
        before_revenue[1] + 1,
    )
    seen_by_web = poll(lambda: web.stock(pairs), expected_stock)
    seen_by_service = poll(lambda: service.stock(pairs), expected_stock)
    differences.extend(diff_levels(seen_by_web, seen_by_service))
    differences.extend(diff_levels(seen_by_web, expected_stock))
    revenue = web.revenue()
    for what, seen, wanted in (
        ("revenue", revenue and revenue[0], expected_revenue[0]),
        ("sales", revenue and revenue[1], expected_revenue[1]),
    ):
        if seen != wanted:
            differences.append(ReportDifference("reports index", what, seen, wanted))
    # The cancelled sale is no purchase, and the returned line counts two units.
    assert Decimal(returned.json()["net_total"]) == 2 * price
    assert voided.json()["net_total"] == "0.00"
    print("\n" + _report(differences) if differences else "\nno difference")
    assert not differences, _report(differences)
