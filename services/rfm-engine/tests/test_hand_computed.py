"""rfm-engine's R, F and M equal the values computed by hand (F29-01, #886).

The same fixture the web is checked against (`tests/fixtures/rfm-hand/fixture.json`
at the repository root; a root test fails when the two copies differ, because a
service never imports `web/`, ADR-0045). Its worksheet is
`docs/evidence/f29-01-rfm-hand-computed.md`.

The engine takes its instant as an argument, so the fixture is read at a fixed one
and the expectations are exact. The purchases are placed with Python's own
calendar arithmetic, not the database's, so a window that counted 24-hour days
instead of calendar days would disagree with the hand values. The rule set is
published with the boundaries the hand quintiles imply, which is what the
engine's scores are read off (they are frozen, not drawn from the population).
Reversals are written as svc_sales writes them, through `Shop`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest

from app import recompute
from tests.conftest import Shop

pytestmark = pytest.mark.postgres

FIXTURE = Path(__file__).parent / "fixtures" / "rfm-hand" / "fixture.json"

# Four boxes that tile the 125 triples of scores (RN-46). The labels are not what
# this test is about; the scores are.
TILES = [
    ("CHAMPION", 4, 5, 4, 5, 1, 5),
    ("LOYAL", 4, 5, 1, 3, 1, 5),
    ("AT_RISK", 1, 3, 4, 5, 1, 5),
    ("LOST", 1, 3, 1, 3, 1, 5),
]


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _moment(instant: datetime, purchase: dict[str, Any]) -> datetime:
    """`days_before` calendar days before the instant at the same local clock time,
    then `minutes` later. Aware arithmetic in one zone moves the wall clock."""
    return (
        instant
        - timedelta(days=purchase["days_before"])
        + timedelta(minutes=purchase["minutes"])
    )


@pytest.fixture
def rule_set(owner: psycopg.Connection) -> Iterator[int]:
    """A published rule set with the fixture's frozen boundaries. A published rule
    set is never deleted, so the owner clears it with triggers off at the end."""
    spec = _fixture()
    bounds = spec["frozen_boundaries"]
    row = owner.execute(
        "INSERT INTO rfm.rule_set (name, window_days, recency_boundaries,"
        " frequency_boundaries, monetary_boundaries, fallback_label_code)"
        " VALUES (%s, %s, %s, %s, %s, 'LOST') RETURNING rule_set_id",
        (
            "hand " + uuid.uuid4().hex[:12],
            spec["window_days"],
            bounds["recency"],
            bounds["frequency"],
            [Decimal(value) for value in bounds["monetary"]],
        ),
    ).fetchone()
    assert row is not None
    rule_set_id = int(row[0])
    for number, (label, *box) in enumerate(TILES, start=1):
        owner.execute(
            "INSERT INTO rfm.rule_set_band (rule_set_id, band_no, label_code,"
            " r_min, r_max, f_min, f_max, m_min, m_max)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (rule_set_id, number, label, *box),
        )
    owner.execute(
        "UPDATE rfm.rule_set SET state = 'PUBLISHED', published_at = now()"
        " WHERE rule_set_id = %s",
        (rule_set_id,),
    )
    try:
        yield rule_set_id
    finally:
        owner.execute("SET session_replication_role = replica")
        try:
            owner.execute(
                "DELETE FROM rfm.customer_profile WHERE rule_set_id = %s",
                (rule_set_id,),
            )
            owner.execute(
                "DELETE FROM rfm.rule_set_band WHERE rule_set_id = %s", (rule_set_id,)
            )
            owner.execute(
                "DELETE FROM rfm.rule_set WHERE rule_set_id = %s", (rule_set_id,)
            )
        finally:
            owner.execute("SET session_replication_role = origin")


def test_every_customer_gets_the_hand_computed_r_f_m_and_scores(
    shop: Shop, service: psycopg.Connection, rule_set: int
) -> None:
    spec = _fixture()
    zone = ZoneInfo(spec["time_zone"])
    instant = datetime.fromisoformat(spec["instant"]).astimezone(zone)
    # The window is calendar days in the session's zone, so the zone is pinned.
    service.execute(f"SET TIME ZONE '{spec['time_zone']}'")

    customers: dict[str, uuid.UUID] = {}
    for customer in spec["customers"]:
        who = customers[customer["key"]] = shop.customer()
        for purchase in customer["purchases"]:
            sale = shop.sale(
                who,
                _moment(instant, purchase),
                quantity=purchase["quantity"],
                unit_price=purchase["unit_price"],
            )
            if purchase.get("cancelled"):
                shop.reverse(sale)
            elif purchase.get("returned_units"):
                shop.return_units(sale, purchase["returned_units"])

    wrong: list[str] = []
    for customer in spec["customers"]:
        key, expected = customer["key"], customer["expected"]
        recompute.customer(service, customers[key], rule_set, instant)
        row = shop.profile(customers[key])
        assert row is not None, key
        state, rule_set_id, r_days, f_count, m_total, r, f, m, _label, _fallback = row
        assert rule_set_id == rule_set
        got = (state, r_days, f_count, m_total, None if r is None else [r, f, m])
        want = (
            expected["state"],
            expected["r_days"],
            expected["frequency"],
            Decimal(expected["monetary"]),
            expected["scores"],
        )
        if got != want:
            wrong.append(f"{key}: expected {want}, got {got}")
    assert wrong == []


def test_across_a_clock_change_r_counts_calendar_days_and_the_window_follows_them(
    shop: Shop, service: psycopg.Connection, rule_set: int
) -> None:
    """The customer whose answer hangs on the clock change: the last purchase is 49
    calendar days back and, because the clocks went forward in between, 1175 elapsed
    hours. The engine subtracts two instants that both carry the session's zone, which
    Python does on the wall clock, so R is 49: the largest R that still scores 2, as
    the web's ranking of that customer says. Read as 24-hour days it would be 48; the
    worksheet records this (it only differs in a zone that changes its clocks)."""
    spec = _fixture()
    instant = datetime.fromisoformat(spec["instant"]).astimezone(
        ZoneInfo(spec["time_zone"])
    )
    service.execute(f"SET TIME ZONE '{spec['time_zone']}'")
    customer = next(c for c in spec["customers"] if c["key"] == "dst_boundary")
    who = shop.customer()
    for purchase in customer["purchases"]:
        shop.sale(
            who,
            _moment(instant, purchase),
            quantity=purchase["quantity"],
            unit_price=purchase["unit_price"],
        )
    last = _moment(instant, customer["purchases"][-1])
    elapsed = instant.astimezone(ZoneInfo("UTC")) - last.astimezone(ZoneInfo("UTC"))
    assert elapsed == timedelta(hours=49 * 24 - 1)

    recompute.customer(service, who, rule_set, instant)
    row = shop.profile(who)
    assert row is not None
    assert (row[2], row[5]) == (49, 2)
    assert spec["frozen_boundaries"]["recency"][3] == 49
