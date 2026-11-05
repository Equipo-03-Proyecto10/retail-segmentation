"""The RN-40 guarantee, tested and not asserted (F24-01, #824).

A recommendation is never an out-of-stock, inactive, elsewhere-only or already
bought product. Each test builds a throwaway scenario in one transaction on the
seeded database, as the owner (new stores, categories, products and customers
with ids no seed row uses), then reads it back as ``svc_recommendations``
through the seven published views, the way the service does. The transaction is
rolled back: nothing outlives a test.

The last group pins what the current rule cannot do (choose a store, consider a
channel, offer an alternative, keep the explanation). Those tests describe
today's behaviour so that F24 can change it on purpose; the prose is in
docs/evidence/824-recommendation-guarantee.md.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from app import rule
from app.rule import Result, recommend
from app.service import ViewReads
from app.store import current_label

AS_OF = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
LIMIT = 10
USUAL, ELSEWHERE = 9001, 9002  # stores
LIKED = 9001  # the category the customer registered an interest in
OK, ZERO, ELSE, INACTIVE, BOUGHT, OLD, UNSTOCKED = range(900001, 900008)
BOUGHT_ANCHOR, OLD_ANCHOR = 900008, 900009


class Scenario:
    """One customer who buys at USUAL and likes category LIKED."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection
        self.customer = str(uuid.uuid4())
        self.peer = str(uuid.uuid4())
        self._sale = 0

    # ----- owner-side setup, every statement parameterized -----

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.connection.execute(sql, params)

    def product(self, product_id: int, *, active: bool = True) -> None:
        self.execute(
            "INSERT INTO product (product_id, sku, name, category_id, list_price,"
            " is_active) VALUES (%s, %s, %s, %s, 10.00, %s)",
            (product_id, f"F24-01-{product_id}", f"F24-01 {product_id}", LIKED, active),
        )

    def stock(self, store: int, product_id: int, quantity: int) -> None:
        self.execute(
            "INSERT INTO inventory (store_id, product_id, quantity_on_hand)"
            " VALUES (%s, %s, %s)",
            (store, product_id, quantity),
        )

    def sale(
        self,
        customer: str,
        product_id: int,
        *,
        store: int = USUAL,
        channel: int = 1,
        days_ago: int = 10,
    ) -> None:
        self._sale += 1
        (transaction_id,) = self.connection.execute(
            "INSERT INTO transaction (source_transaction_id, customer_id, store_id,"
            " channel_id, occurred_at, total, data_origin)"
            " VALUES (%s, %s, %s, %s, %s, 10.00, 'SEEDED')"
            " RETURNING transaction_id",
            (
                f"f24-01-{self.customer}-{self._sale}",
                customer,
                store,
                channel,
                AS_OF - timedelta(days=days_ago),
            ),
        ).fetchone()
        self.execute(
            "INSERT INTO transaction_line (transaction_id, product_id, quantity,"
            " unit_price) VALUES (%s, %s, 1, 10.00)",
            (transaction_id, product_id),
        )

    # ----- the question, asked as the service -----

    def ask(self, customer: str | None = None) -> Result:
        customer = customer or self.customer
        self.execute("SET LOCAL ROLE svc_recommendations")
        try:
            label = current_label(self.connection, customer)
            return recommend(ViewReads(self.connection, label), customer, AS_OF, LIMIT)
        finally:
            self.execute("RESET ROLE")


def _build(connection: psycopg.Connection) -> Scenario:
    s = Scenario(connection)
    for store in (USUAL, ELSEWHERE):
        s.execute(
            "INSERT INTO store (store_id, name, city, state)"
            " VALUES (%s, %s, 'Test', 'Test')",
            (store, f"F24-01 store {store}"),
        )
    s.execute(
        "INSERT INTO category (category_id, name, parent_category_id)"
        " VALUES (%s, 'F24-01 liked', NULL)",
        (LIKED,),
    )
    for customer in (s.customer, s.peer):
        s.execute(
            "INSERT INTO customer (customer_id, name, registration_channel_id,"
            " registered_on) VALUES (%s, %s, 1, %s)",
            (customer, f"F24-01 {customer}", AS_OF.date()),
        )
    (run_id,) = connection.execute(
        "INSERT INTO segmentation_run (method, window_days, parameters,"
        " customer_count, run_at, data_origin)"
        " VALUES ('RFM_RULES', 180, '{\"window_days\": 180}'::jsonb, 2, %s, 'SEEDED')"
        " RETURNING run_id",
        (AS_OF,),
    ).fetchone()
    for customer in (s.customer, s.peer):
        s.execute(
            "INSERT INTO customer_segment_history (customer_id, run_id, label_code,"
            " valid_from) VALUES (%s, %s, 'LOYAL', %s)",
            (customer, run_id, AS_OF),
        )
    s.execute(
        "INSERT INTO customer_interest_category (customer_id, category_id)"
        " VALUES (%s, %s)",
        (s.customer, LIKED),
    )
    for product_id in (OK, ZERO, ELSE, BOUGHT, OLD, UNSTOCKED):
        s.product(product_id)
    s.product(INACTIVE, active=False)
    # Two anchors the customer bought, so the usual store is USUAL and the
    # bought products stay in the catalog with stock.
    s.product(BOUGHT_ANCHOR)
    s.product(OLD_ANCHOR)

    s.stock(USUAL, OK, 5)
    s.stock(USUAL, ZERO, 0)  # matching, zero stock at the usual store
    s.stock(ELSEWHERE, ZERO, 10)  # ...and plenty elsewhere
    s.stock(ELSEWHERE, ELSE, 7)  # held only by another store
    s.stock(USUAL, INACTIVE, 5)  # matching, in stock, inactive
    s.stock(USUAL, BOUGHT, 5)  # matching, in stock, but already bought
    s.stock(USUAL, OLD, 5)  # bought, but before the window
    # UNSTOCKED has no inventory row anywhere.

    s.sale(s.customer, BOUGHT, store=USUAL)
    s.sale(s.customer, OLD, store=USUAL, days_ago=400)
    s.sale(s.peer, OK, store=USUAL)  # a segment peer bought OK in the window
    return s


@pytest.fixture
def scenario(owner_url: str) -> Iterator[Scenario]:
    with psycopg.connect(owner_url) as connection:
        yield _build(connection)
        connection.rollback()


def _ids(result: Result) -> list[int]:
    return [item["product_id"] for item in result.items]


# ---------- the four exclusions, one acceptance criterion each ----------


def test_zero_stock_at_the_usual_store_does_not_appear(scenario: Scenario) -> None:
    result = scenario.ask()
    assert result.usual_store_id == USUAL
    assert ZERO not in _ids(result)
    assert UNSTOCKED not in _ids(result)  # no stock row at all is no stock


def test_a_product_only_another_store_holds_does_not_appear(
    scenario: Scenario,
) -> None:
    result = scenario.ask()
    assert result.usual_store_id == USUAL
    assert ELSE not in _ids(result)
    # ZERO is held in quantity 10 elsewhere and still does not appear.
    assert ZERO not in _ids(result)


def test_an_inactive_product_does_not_appear(scenario: Scenario) -> None:
    result = scenario.ask()
    assert INACTIVE not in _ids(result)


def test_a_product_bought_in_the_window_does_not_appear(scenario: Scenario) -> None:
    result = scenario.ask()
    assert BOUGHT not in _ids(result)


def test_the_window_is_the_boundary_of_already_bought(scenario: Scenario) -> None:
    """A purchase older than the window no longer excludes the product."""
    assert OLD in _ids(scenario.ask())


def test_every_returned_item_is_active_stocked_here_and_unbought(
    scenario: Scenario,
) -> None:
    """The positive half, checked against the owner's tables: whatever comes
    back is exactly what the four exclusions leave."""
    result = scenario.ask()
    assert result.status == "recommended"
    assert _ids(result) == [OK, OLD]
    for item in result.items:
        active, quantity = scenario.connection.execute(
            "SELECT p.is_active, i.quantity_on_hand FROM product AS p"
            " JOIN inventory AS i ON i.product_id = p.product_id"
            " WHERE p.product_id = %s AND i.store_id = %s",
            (item["product_id"], USUAL),
        ).fetchone()
        assert active is True
        assert quantity > 0
        assert item["quantity_on_hand"] == quantity


# ---------- the reasons name a real signal with its figures ----------


def test_reasons_name_each_signal_with_its_figures(scenario: Scenario) -> None:
    item = scenario.ask().items[0]
    assert item["product_id"] == OK
    assert item["reasons"] == [
        {"signal": "segment", "segment_buyers": 1, "category_id": None, "share": None},
        {
            "signal": "preferred_category",
            "segment_buyers": None,
            "category_id": LIKED,
            "share": None,
        },
        {
            "signal": "purchase_history",
            "segment_buyers": None,
            "category_id": LIKED,
            "share": Decimal("1"),  # the customer's one window purchase was here
        },
    ]


def test_a_segment_figure_is_backed_by_real_buyers(scenario: Scenario) -> None:
    (reason,) = [
        r for r in scenario.ask().items[0]["reasons"] if "segment" in r.values()
    ]
    (buyers,) = scenario.connection.execute(
        "SELECT count(DISTINCT t.customer_id) FROM transaction AS t"
        " JOIN transaction_line AS l USING (transaction_id)"
        " JOIN customer_segment_history AS h ON h.customer_id = t.customer_id"
        " WHERE l.product_id = %s AND h.label_code = 'LOYAL' AND t.customer_id <> %s",
        (OK, scenario.customer),
    ).fetchone()
    assert reason["segment_buyers"] == buyers == 1


def test_no_item_is_returned_without_a_reason(scenario: Scenario) -> None:
    for item in scenario.ask().items:
        assert item["reasons"]
        for reason in item["reasons"]:
            assert reason["signal"] in {
                "segment",
                "preferred_category",
                "purchase_history",
            }


def test_in_stock_alone_is_not_a_reason(scenario: Scenario) -> None:
    """A stocked, active, unbought product matching no signal is not offered."""
    scenario.execute(
        "INSERT INTO category (category_id, name, parent_category_id)"
        " VALUES (9003, 'F24-01 unrelated', NULL)",
    )
    scenario.execute(
        "INSERT INTO product (product_id, sku, name, category_id, list_price)"
        " VALUES (900010, 'F24-01-900010', 'F24-01 unrelated', 9003, 10.00)",
    )
    scenario.stock(USUAL, 900010, 50)
    assert 900010 not in _ids(scenario.ask())


# ---------- what the current rule cannot do (pinned, see the evidence) ----------


def test_it_cannot_choose_a_store(scenario: Scenario) -> None:
    """The store is derived from the purchases; the caller cannot name one, and
    the answer holds nothing of any other store."""
    assert "store_id" not in rule.recommend.__annotations__
    assert [f.name for f in dataclasses.fields(Result) if "store" in f.name] == [
        "usual_store_id"
    ]
    result = scenario.ask()
    assert ELSE not in _ids(result)  # held in ELSEWHERE; no way to ask for it
    assert all("store_id" not in item for item in result.items)


def test_it_cannot_consider_a_channel(scenario: Scenario) -> None:
    """Channel is carried by neither the question nor the rows the rule reads,
    and a purchase's channel changes nothing."""
    assert "channel_id" not in {f.name for f in dataclasses.fields(rule.Line)}
    before = scenario.ask()
    scenario.execute(
        "UPDATE transaction SET channel_id = 2 WHERE customer_id = %s",
        (scenario.customer,),
    )
    assert scenario.ask().items == before.items


def test_it_cannot_offer_an_alternative(scenario: Scenario) -> None:
    """When a customer's only matching product has no stock here, nothing takes
    its place and the answer does not say it was lost."""
    scenario.execute(
        "UPDATE inventory SET quantity_on_hand = 0 WHERE product_id = %s", (OK,)
    )
    scenario.execute(
        "DELETE FROM inventory WHERE product_id = %s AND store_id = %s", (OLD, USUAL)
    )
    result = scenario.ask()
    assert result.status == "none_match"
    assert result.items == []
    assert "alternatives" not in result.as_dict(AS_OF)


def test_it_cannot_keep_the_explanation(scenario: Scenario) -> None:
    """The reasons exist only in the response: no table, anywhere, holds a
    recommendation (recommendations owns none, E-07), and the same question
    asked again is a fresh computation."""
    (stored,) = scenario.connection.execute(
        "SELECT count(*) FROM information_schema.tables"
        " WHERE table_name LIKE %s AND table_schema NOT IN"
        " ('pg_catalog', 'information_schema')",
        ("%recommend%",),
    ).fetchone()
    assert stored == 0
    first = scenario.ask()
    assert set(first.as_dict(AS_OF)) == {
        "customer_id",
        "status",
        "computed_at",
        "window",
        "label_code",
        "run_id",
        "usual_store_id",
        "items",
    }
