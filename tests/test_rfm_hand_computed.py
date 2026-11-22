"""The web's RFM equals the values computed by hand (F29-01, #886, RN-21, RN-51).

`tests/fixtures/rfm-hand/fixture.json` is a small population whose recency,
frequency and monetary values and quintile scores were worked out by hand; the
worksheet is `docs/evidence/f29-01-rfm-hand-computed.md`. This test writes that
population into the schema and runs `web.db.segments.score_rfm_rules` and
`read_rfm_inputs`, unchanged, on it.

The web reads `now()`, which is the transaction's start and cannot be set, so the
fixture is written relative to it: every purchase is a number of calendar days
before `now()` plus minutes, built by the database's own calendar-day arithmetic
(the arithmetic the window uses), and the session time zone is pinned so that
"calendar day" has one meaning. Everything runs in one transaction that is rolled
back, after the sales the seed holds are taken out of it: the quintiles are
computed over the population, so a seeded sale inside the window would move them.
The same fixture, byte for byte, drives rfm-engine at a fixed instant in
`services/rfm-engine/tests/test_hand_computed.py`.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from web.db import segments

pytestmark = pytest.mark.postgres

FIXTURE = Path(__file__).parent / "fixtures" / "rfm-hand" / "fixture.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class Population:
    """The fixture, written inside the caller's transaction."""

    def __init__(self, connection: psycopg.Connection, spec: dict[str, Any]) -> None:
        self.connection = connection
        self.spec = spec
        self.customers: dict[str, str] = {}
        self.instant = self._one("SELECT now()")

    def _one(self, statement: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.connection.execute(statement, params).fetchone()
        assert row is not None
        return row[0]

    def moment(self, days_before: int, minutes: int) -> Any:
        """`days_before` calendar days before the transaction's `now()`, at the same
        local clock time, then `minutes` later (earlier when negative)."""
        return self._one(
            "SELECT now() - make_interval(days => %s) + make_interval(mins => %s)",
            (days_before, minutes),
        )

    def load(self) -> None:
        connection = self.connection
        connection.execute(
            "SELECT set_config('TimeZone', %s, true)", (self.spec["time_zone"],)
        )
        self.instant = self._one("SELECT now()")
        # The population is the fixture's alone: the seed's sales are removed for
        # this transaction, which is rolled back. The foreign keys are switched off
        # only for that delete (it needs the superuser the owner URL names).
        connection.execute("SET LOCAL session_replication_role = replica")
        connection.execute("DELETE FROM public.transaction")
        connection.execute("SET LOCAL session_replication_role = origin")
        user = self._one("SELECT min(user_id::text) FROM public.app_user")
        for customer in self.spec["customers"]:
            row = connection.execute(
                "INSERT INTO public.customer (name, registration_channel_id)"
                " VALUES (%s, 1) RETURNING customer_id::text",
                ("hand " + customer["key"],),
            ).fetchone()
            assert row is not None
            self.customers[customer["key"]] = row[0]
            for purchase in customer["purchases"]:
                self._sale(customer["key"], purchase, user)

    def _sale(self, key: str, purchase: dict[str, Any], user: str) -> None:
        quantity = purchase["quantity"]
        total = Decimal(purchase["unit_price"]) * quantity
        sale = self._one(
            "INSERT INTO public.transaction (source_transaction_id, customer_id,"
            " store_id, channel_id, occurred_at, total, data_origin)"
            " VALUES (%s, %s, 1, 1, %s, %s, 'OBSERVED') RETURNING transaction_id",
            (
                "hand:" + uuid.uuid4().hex,
                self.customers[key],
                self.moment(purchase["days_before"], purchase["minutes"]),
                total,
            ),
        )
        self.connection.execute(
            "INSERT INTO public.transaction_line (transaction_id, product_id,"
            " quantity, unit_price) VALUES (%s, 1, %s, %s)",
            (sale, quantity, purchase["unit_price"]),
        )
        if purchase.get("cancelled"):
            self._reverse(sale, "CANCELLATION", quantity, user)
        elif purchase.get("returned_units"):
            self._reverse(sale, "RETURN", purchase["returned_units"], user)

    def _reverse(self, sale: int, kind: str, units: int, user: str) -> None:
        """A reversal as svc_sales writes it: the header, its line and the line's
        running returned quantity, which only that role may write (ADR-0062)."""
        connection = self.connection
        connection.execute("SET LOCAL SESSION AUTHORIZATION svc_sales")
        try:
            reversal = self._one(
                "INSERT INTO sales.sale_reversal (transaction_id, kind, reason,"
                " idempotency_key, request_sha256, recorded_by)"
                " VALUES (%s, %s, %s, gen_random_uuid(), %s, %s)"
                " RETURNING reversal_id",
                (
                    sale,
                    kind,
                    "hand" if kind == "CANCELLATION" else None,
                    bytes(32),
                    user,
                ),
            )
            connection.execute(
                "INSERT INTO sales.sale_reversal_line (reversal_id, transaction_id,"
                " product_id, quantity, reason) VALUES (%s, %s, 1, %s, %s)",
                (reversal, sale, units, None if kind == "CANCELLATION" else "hand"),
            )
            connection.execute(
                "UPDATE public.transaction_line SET returned_quantity ="
                " returned_quantity + %s WHERE transaction_id = %s",
                (units, sale),
            )
        finally:
            connection.execute("RESET SESSION AUTHORIZATION")

    def as_web(self) -> None:
        self.connection.execute("SET LOCAL ROLE retail_app")


@pytest.fixture
def population(owner_url: str):
    with psycopg.connect(owner_url) as connection:
        try:
            loaded = Population(connection, _fixture())
            loaded.load()
            loaded.as_web()
            yield loaded
        finally:
            connection.rollback()


def _by_key(population: Population, rows: list[Any]) -> dict[str, Any]:
    wanted = {value: key for key, value in population.customers.items()}
    return {wanted[row.customer_id]: row for row in rows if row.customer_id in wanted}


def test_the_fixture_covers_what_the_story_names() -> None:
    spec = _fixture()
    customers = {c["key"]: c for c in spec["customers"]}
    purchases = [p for c in spec["customers"] for p in c["purchases"]]
    window = spec["window_days"]
    # Window edges: on the first instant (counted), a minute before it (not), and
    # on the last instant (counted).
    assert {(p["days_before"], p["minutes"]) for p in purchases} >= {
        (window, 0),
        (window, -1),
        (0, 0),
    }
    assert spec["time_zone"] == "America/New_York"
    # A purchase a calendar day outside the window by the clock that a 24-hour-day
    # window would count, across a clock change.
    assert any(p["days_before"] > window and p["minutes"] > 0 for p in purchases)
    # Cents, a cancellation, a partial return, no purchase at all.
    assert any(Decimal(p["unit_price"]) % 1 != 0 for p in purchases)
    assert any(p.get("cancelled") for p in purchases)
    assert any(p.get("returned_units") for p in purchases)
    assert customers["none"]["purchases"] == []
    # Tied values (RN-51): more than one customer on the same F, the same M and
    # the same last purchase.
    expected = [c["expected"] for c in spec["customers"] if c["expected"]["scores"]]
    for column in ("frequency", "monetary"):
        seen = [e[column] for e in expected]
        assert len(seen) > len(set(seen)), column
    lasts = [
        (e["last_purchase"]["days_before"], e["last_purchase"]["minutes"])
        for e in expected
    ]
    assert len(lasts) > len(set(lasts))


def test_the_web_scores_every_customer_as_the_hand_computation_does(
    population: Population,
) -> None:
    spec = population.spec
    rows = _by_key(
        population, segments.score_rfm_rules(population.connection, spec["window_days"])
    )
    assert set(rows) == {c["key"] for c in spec["customers"]}
    wrong: list[str] = []
    for customer in spec["customers"]:
        key, expected = customer["key"], customer["expected"]
        row = rows[key]
        if expected["state"] == "unassigned":
            # RN-21: recorded with nothing, not left out and not scored zero.
            got: tuple[Any, ...] = (
                row.last_purchase_at,
                row.frequency,
                row.monetary,
                row.r_score,
                row.f_score,
                row.m_score,
                row.segment_id,
                row.label_code,
            )
            if got != (None,) * 8:
                wrong.append(f"{key}: expected nothing, got {got}")
            continue
        last = expected["last_purchase"]
        got = (
            row.last_purchase_at,
            row.frequency,
            row.monetary,
            [row.r_score, row.f_score, row.m_score],
        )
        want = (
            population.moment(last["days_before"], last["minutes"]),
            expected["frequency"],
            Decimal(expected["monetary"]),
            expected["scores"],
        )
        if got != want:
            wrong.append(f"{key}: expected {want}, got {got}")
    assert wrong == []


def test_the_web_reads_the_same_raw_values_before_scoring(
    population: Population,
) -> None:
    spec = population.spec
    rows = _by_key(
        population, segments.read_rfm_inputs(population.connection, spec["window_days"])
    )
    for customer in spec["customers"]:
        expected, row = customer["expected"], rows[customer["key"]]
        assert row.frequency == (expected["frequency"] or None), customer["key"]
        assert row.monetary == (
            Decimal(expected["monetary"]) if expected["scores"] else None
        ), customer["key"]
