"""The raw R, F and M and the scoring held to the web's captured answers (F20-11,
ADR-0045, E-17).

`tests/golden/rfm.json` records what the web's RFM read (`score_rfm_rules` and
`read_rfm_inputs` in web/db/segments.py) gives for every seeded customer at the
seed's anchor, and for synthetic customers whose sales are cancelled or partly
returned as sales-service records them (F21-01, ADR-0062). The service imports
nothing from `web/`: the web's answers are the fixture's rows.

Each case is replayed through the port: the net purchases the customer's sales
leave, read from `api_sales.net_purchase_line_v1`, and `profile.build` under the
seeded rule set. The instant of the last purchase is compared as the number of
microseconds before the anchor, so a database loaded on another day agrees. A
seed change that moves a figure fails the replay; the capture to redo is
`python -m tests.golden.capture --service rfm` (tests/golden/README.md). The
header's seed hash is not pinned, as in the other ports: the seed is shared, and
an edit to a table this port never reads must not fail rfm-engine's suite.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app import profile, store
from tests.conftest import Shop

pytestmark = pytest.mark.postgres

HERE = Path(__file__).parent
FIXTURE = json.loads((HERE / "golden/rfm.json").read_text())
CASES = {case["id"]: case for case in FIXTURE["cases"]}
SYNTHETIC = [case_id for case_id in CASES if case_id != "seeded-customers"]
MICROSECOND = timedelta(microseconds=1)


def _anchor(owner: psycopg.Connection):
    row = owner.execute("SELECT max(run_at) FROM public.segmentation_run").fetchone()
    assert row is not None and row[0] is not None
    return row[0]


def _before_anchor(anchor, instant) -> int | None:
    return None if instant is None else (anchor - instant) // MICROSECOND


def test_the_fixture_is_the_rfm_port() -> None:
    assert FIXTURE["header"]["port"] == "rfm"
    assert "seeded-customers" in CASES and SYNTHETIC


def test_every_seeded_customer_has_the_webs_raw_values_scores_and_label(
    owner: psycopg.Connection,
) -> None:
    case = CASES["seeded-customers"]
    window_days = case["input"]["window_days"]
    anchor = _anchor(owner)
    rules = store.rule_set(owner, 1)
    assert rules is not None and rules.window_days == window_days
    web = case["outcome"]["observed"]["customers"]
    assert len(web) >= 30

    different = []
    for expected in web:
        measures = store.measures(owner, expected["customer_id"], window_days, anchor)
        ported = profile.build(rules, measures, anchor)
        got = {
            "last_purchase_before_anchor_us": _before_anchor(anchor, measures.last_at),
            "frequency": measures.count or None,
            "monetary": str(measures.total) if measures.count else None,
            "r_score": ported.r_score,
            "f_score": ported.f_score,
            "m_score": ported.m_score,
            "label_code": ported.label_code,
            "via_fallback": bool(ported.via_fallback),
        }
        want = {key: value for key, value in expected.items() if key != "customer_id"}
        if got != want:
            different.append((expected["customer_id"], got, want))
    assert different == [], "\n".join(
        f"{customer}: rfm-engine={got} web={want}" for customer, got, want in different
    )


def _replay(shop: Shop, anchor, sales: list[dict[str, Any]]):
    customer = shop.customer()
    for sale in sales:
        sold = shop.sale(
            customer,
            anchor - timedelta(days=sale["days"]),
            quantity=sale["units"],
            unit_price=sale["price"],
        )
        if sale.get("cancelled"):
            shop.reverse(sold)
        elif sale.get("returned"):
            shop.return_units(sold, sale["returned"])
    return customer


@pytest.mark.parametrize("case_id", SYNTHETIC)
def test_a_cancelled_or_returned_sale_counts_as_the_web_counts_it(
    case_id: str, shop: Shop, owner: psycopg.Connection
) -> None:
    case = CASES[case_id]
    anchor = _anchor(owner)
    customer = _replay(shop, anchor, case["input"]["sales"])
    measures = store.measures(owner, customer, case["input"]["window_days"], anchor)
    got = {
        "last_purchase_before_anchor_us": _before_anchor(anchor, measures.last_at),
        "frequency": measures.count or None,
        "monetary": str(measures.total) if measures.count else None,
    }
    assert got == case["outcome"]["observed"], case_id


def test_the_fixture_pins_each_case_the_decision_names() -> None:
    """F21-01: a cancelled sale is no purchase, a partly returned one is one of
    what stayed, and a returned latest sale hands recency to the one before."""

    seen = {case_id: CASES[case_id]["outcome"]["observed"] for case_id in SYNTHETIC}
    day = 86_400 * 1_000_000
    assert seen["no-purchase"]["frequency"] is None
    assert seen["only-sale-cancelled"]["frequency"] is None
    assert seen["older-than-the-window"]["frequency"] is None
    assert seen["cancelled-latest-sale"] == {
        "frequency": 1,
        "monetary": "60.00",
        "last_purchase_before_anchor_us": 5 * day,
    }
    assert seen["partly-returned-sale"]["monetary"] == str(Decimal("30.00"))
    assert seen["returned-latest-sale-leaves-an-earlier-one"] == {
        "frequency": 1,
        "monetary": "20.00",
        "last_purchase_before_anchor_us": 10 * day,
    }
