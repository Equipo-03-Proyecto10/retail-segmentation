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

from tests.parity.compare import diff_levels
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


def _csv_row(source_id, customer, channel, store, product, price, quantity=1) -> dict:
    return {
        "transaction_id": source_id,
        "customer_id": customer,
        "store_id": store,
        "channel_id": channel,
        "occurred_at": occurred_at().isoformat(timespec="seconds"),
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
    db(
        "UPDATE inventory SET quantity_on_hand = 1 "
        "WHERE store_id = %s AND product_id = %s",
        (store, product),
    )
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
