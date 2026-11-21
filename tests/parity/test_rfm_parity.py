"""RFM parity between the web's run and rfm-engine (F20-11).

The web scores in SQL (`web.db.segments.score_rfm_rules`, which reads
`public.net_sale`); rfm-engine ports the rule (ADR-0045, pinned by
`services/rfm-engine/tests/golden/rfm.json`) and reads the same net units
through `api_sales.net_purchase_line_v1` (ADR-0062). These cases compare what
each component holds for the same customers, in the same database, over the same
window, under the same rule version: the raw R, F and M. Every difference is
printed with the customer and both values.

R is compared as ADR-0086 measures it: the whole days from the web's last
purchase to the profile's own `computed_at`. rfm-engine's profiles are read from
`rfm.customer_profile` because `GET /v1/rfm-profiles` is still planned; its
recalculation is started through the gateway and must find the profiles it holds
equivalent to a recomputation (ADR-0086).

The second case records and reverses sales through sales-service on the throwaway
stack named by PARITY_DATABASE_URL; never point it at the instance.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.parity.compare import RfmDifference, diff_rfm
from tests.parity.stack import occurred_at, poll, web_rfm


def _report(found) -> str:
    return "\n".join(str(d) for d in found)


def _latest_run(db) -> int:
    [[run_id]] = db(
        "SELECT max(run_id) FROM segmentation_run WHERE method = 'RFM_RULES'"
    )
    assert run_id is not None, "the web recorded no RFM run"
    return run_id


def test_every_customer_has_the_same_raw_r_f_and_m(db, web, rfm, differences) -> None:
    chosen = rfm.published_rule_set()
    if chosen is None:
        pytest.skip("rfm-engine has no published rule set")
    rule_set_id, window_days = chosen
    if not rfm.pass_completed():
        pytest.skip(
            "rfm-engine's daily pass has not completed, so a seeded customer no "
            "event reached holds no profile yet (ADR-0086)"
        )
    [[before]] = db("SELECT coalesce(max(run_id), 0) FROM segmentation_run")

    web.run_segmentation(window_days)
    run_id = _latest_run(db)
    assert run_id > before, "the web recorded no run"
    [[ran_over]] = db(
        "SELECT window_days FROM segmentation_run WHERE run_id = %s", (run_id,)
    )
    assert ran_over == window_days, f"the run's window is {ran_over}, not {window_days}"

    recalculation = rfm.recalculate(rule_set_id)
    # The recalculation's own verdict comes first: if the engine's profiles are
    # not what a recomputation gives, comparing them with the web says nothing.
    assert recalculation["state"] == "COMPLETED", recalculation
    if recalculation["outcome"] != "EQUIVALENT":
        listed = rfm.differences(recalculation["recalculation_id"])
        print("\n".join(str(d) for d in listed))
        raise AssertionError(
            f"rfm-engine's recalculation found {recalculation['customers_different']}"
            " customers whose profile differs from a recomputation"
        )

    differences.extend(diff_rfm(web_rfm(db, run_id), rfm.profiles(rule_set_id)))
    print("\n" + _report(differences) if differences else "\nno difference")
    assert not differences, _report(differences)


def _products(db) -> tuple[int, list[tuple[int, str]]]:
    """A store with two active priced products that each hold stock."""

    rows = db(
        "SELECT i.store_id, p.product_id, p.list_price::text "
        "FROM inventory AS i JOIN product AS p USING (product_id) "
        "WHERE p.is_active AND p.list_price > 0 AND i.quantity_on_hand >= 20 "
        "ORDER BY i.store_id, i.product_id"
    )
    by_store: dict[int, list[tuple[int, str]]] = {}
    for store, product, price in rows:
        by_store.setdefault(store, []).append((product, price))
    for store, found in by_store.items():
        if len(found) >= 2:
            return store, found[:2]
    pytest.skip("no store has two products with 20 units")


def _sale(customer, channel, store, lines, minutes_ago) -> dict:
    return {
        "customer_id": customer,
        "store_id": store,
        "channel_id": channel,
        "occurred_at": (occurred_at() - timedelta(minutes=minutes_ago)).isoformat(
            timespec="seconds"
        ),
        "lines": [
            {"product_id": product, "quantity": quantity, "unit_price": price}
            for product, quantity, price in lines
        ],
    }


def test_a_cancelled_and_a_returned_sale_count_as_f21_01_decided(
    db, web, rfm, service, segments, differences
) -> None:
    """ADR-0062: a cancelled sale is no purchase, a partly returned one is a
    purchase of what stayed, and a sale whose only returned line is one of two is
    still a purchase. The expected figures are worked out here from the prices,
    so the two components are held to the decision and not only to each other."""

    chosen = rfm.published_rule_set()
    if chosen is None:
        pytest.skip("rfm-engine has no published rule set")
    rule_set_id, window_days = chosen
    store, [(first, first_price), (second, second_price)] = _products(db)
    [[channel]] = db("SELECT min(channel_id) FROM channel")
    customer = segments.register_customer("Parity RFM " + uuid.uuid4().hex[:8], channel)
    p1, p2 = Decimal(first_price), Decimal(second_price)

    kept = service.record_sale(
        _sale(customer, channel, store, [(first, 2, first_price)], 4)
    )
    cancelled = service.record_sale(
        _sale(customer, channel, store, [(second, 1, second_price)], 3)
    )
    partly = service.record_sale(
        _sale(customer, channel, store, [(second, 3, second_price)], 2)
    )
    one_line = service.record_sale(
        _sale(
            customer,
            channel,
            store,
            [(first, 1, first_price), (second, 1, second_price)],
            1,
        )
    )
    for reply in (kept, cancelled, partly, one_line):
        assert reply.status == 201, reply.body

    for reversal in (
        {
            "sale_id": cancelled.json()["sale_id"],
            "kind": "CANCELLATION",
            "reason": "Parity RFM: whole sale cancelled",
        },
        {
            "sale_id": partly.json()["sale_id"],
            "kind": "RETURN",
            "lines": [
                {"product_id": second, "quantity": 1, "reason": "Parity RFM: one unit"}
            ],
        },
        {
            "sale_id": one_line.json()["sale_id"],
            "kind": "RETURN",
            "lines": [
                {"product_id": second, "quantity": 1, "reason": "Parity RFM: one line"}
            ],
        },
    ):
        reply = service.record_reversal(reversal)
        assert reply.status == 201, reply.body

    # Three purchases stay: the kept sale, two of three units of the partly
    # returned one, and the first line of the last. The cancelled one is none.
    expected_count = 3
    expected_total = 2 * p1 + 2 * p2 + p1

    def held():
        found = rfm.profiles(rule_set_id, [customer]).get(customer)
        return None if found is None else (found.f_count, found.m_total)

    seen = poll(held, (expected_count, expected_total))
    web.run_segmentation(window_days)
    run_id = _latest_run(db)

    mine = web_rfm(db, run_id, [customer])
    differences.extend(diff_rfm(mine, rfm.profiles(rule_set_id, [customer])))
    recorded = mine[customer]
    for what, got in (
        ("frequency", recorded.frequency),
        ("monetary", recorded.monetary),
    ):
        wanted = expected_count if what == "frequency" else expected_total
        if got != wanted:
            differences.append(RfmDifference(customer, f"{what} (F21-01)", got, wanted))
    print("\n" + _report(differences) if differences else "\nno difference")
    assert seen == (expected_count, expected_total), f"rfm-engine holds {seen}"
    assert not differences, _report(differences)
