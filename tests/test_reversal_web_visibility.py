"""The web counts net units (F21-03, ADR-0062, RN-70).

sales-service records a reversal as svc_sales: the header, its lines, the
running total in `transaction_line.returned_quantity`, and the stock restored
through api_stock.restock_v1. The web, connected as retail_app, must then read
what the customer kept: its reports, its customer profile, its RFM inputs, its
experiment conversions and its stock page. Every case runs in one transaction
that is rolled back, so the web's own reads are called unchanged.

A second guard fails when any web query goes back to the gross `transaction` or
`transaction_line`, which would count a reversed sale again without a single
test of the reports noticing.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from web.db import (
    consumption,
    consumption_shift,
    customer_timeline,
    experiment_dashboard,
    experiment_report,
    recommendations,
    segmentation_dashboard,
    segments,
)
from web.db import experiment_conversions as conversions
from web.db.inventory import list_stock
from web.db.revenue_summary import read_revenue_summary

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
WINDOW_DAYS = 3650
D = Decimal


class Scenario:
    """One connection, switched between the service's role and the web's."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.cur = conn.cursor()
        self.user = self._one("SELECT min(user_id::text) FROM public.app_user")
        self.store, self.first, self.second = self._products()
        self.customer = self._one(
            "SELECT c.customer_id::text FROM public.customer AS c"
            " WHERE c.registered_on <= current_date AND NOT EXISTS ("
            "  SELECT 1 FROM public.transaction AS t"
            "  WHERE t.customer_id = c.customer_id"
            "    AND t.occurred_at > now() - interval '2 hours')"
            " ORDER BY c.customer_id LIMIT 1"
        )
        self.now = self._one("SELECT now()")

    def _one(self, statement: str, params: tuple = ()):
        self.cur.execute(statement, params)
        return self.cur.fetchone()[0]

    def _products(self) -> tuple[int, int, int]:
        self.cur.execute(
            "SELECT i.store_id, array_agg(i.product_id ORDER BY i.product_id)"
            " FROM public.inventory AS i JOIN public.product AS p USING (product_id)"
            " WHERE p.is_active AND p.list_price > 0 AND i.quantity_on_hand >= 10"
            " GROUP BY i.store_id HAVING count(*) >= 2 ORDER BY i.store_id LIMIT 1"
        )
        store, products = self.cur.fetchone()
        return store, products[0], products[1]

    def as_service(self) -> None:
        self.cur.execute("RESET ROLE")
        self.cur.execute("SET LOCAL SESSION AUTHORIZATION svc_sales")

    def as_web(self) -> None:
        self.cur.execute("RESET ROLE")
        self.cur.execute("RESET SESSION AUTHORIZATION")
        self.cur.execute("SET LOCAL ROLE retail_app")

    def sale(
        self,
        lines: list[tuple[int, int, str]],
        *,
        customer: str | None = None,
        occurred_at: datetime | None = None,
    ) -> int:
        """A sale as sales-service writes it: header, lines, stock consumed."""
        self.as_service()
        total = sum(D(price) * quantity for _, quantity, price in lines)
        self.cur.execute(
            "INSERT INTO public.transaction (source_transaction_id, customer_id,"
            " store_id, channel_id, occurred_at, total, data_origin)"
            " VALUES (%s, %s, %s, 1, %s, %s, 'OBSERVED') RETURNING transaction_id",
            (
                "svc-sales:" + str(uuid.uuid4()),
                customer or self.customer,
                self.store,
                occurred_at or self.now - timedelta(minutes=10),
                total,
            ),
        )
        sale_id = self.cur.fetchone()[0]
        for product, quantity, price in lines:
            self.cur.execute(
                "INSERT INTO public.transaction_line"
                " (transaction_id, product_id, quantity, unit_price)"
                " VALUES (%s, %s, %s, %s)",
                (sale_id, product, quantity, price),
            )
            self.cur.execute(
                "SELECT api_stock.consume_v1(%s::smallint, %s, %s)",
                (self.store, product, quantity),
            )
        self.cur.execute(
            "INSERT INTO sales.sale_capture (transaction_id, request_sha256,"
            " recorded_by) VALUES (%s, %s, %s)",
            (sale_id, bytes(32), self.user),
        )
        return sale_id

    def reverse(
        self,
        sale_id: int,
        kind: str,
        lines: list[tuple[int, int, bool]],
    ) -> None:
        """A reversal as sales-service writes it, in the sale's own shape."""
        self.as_service()
        self.cur.execute(
            "INSERT INTO sales.sale_reversal (transaction_id, kind, reason,"
            " idempotency_key, request_sha256, recorded_by)"
            " VALUES (%s, %s, %s, gen_random_uuid(), %s, %s) RETURNING reversal_id",
            (
                sale_id,
                kind,
                "test" if kind == "CANCELLATION" else None,
                bytes(32),
                self.user,
            ),
        )
        reversal_id = self.cur.fetchone()[0]
        for product, quantity, resellable in sorted(lines):
            self.cur.execute(
                "INSERT INTO sales.sale_reversal_line (reversal_id, transaction_id,"
                " product_id, quantity, reason, resellable)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    reversal_id,
                    sale_id,
                    product,
                    quantity,
                    None if kind == "CANCELLATION" else "test",
                    resellable,
                ),
            )
            self.cur.execute(
                "UPDATE public.transaction_line"
                " SET returned_quantity = returned_quantity + %s"
                " WHERE transaction_id = %s AND product_id = %s",
                (quantity, sale_id, product),
            )
            if resellable:
                self.cur.execute(
                    "SELECT api_stock.restock_v1(%s::smallint, %s, %s, %s)",
                    (self.store, product, quantity, reversal_id),
                )

    def stock(self, product: int) -> int:
        self.as_web()
        rows, _ = list_stock(
            self.conn, store_id=self.store, search=None, page=1, per_page=10_000
        )
        (row,) = [row for row in rows if row.product_id == product]
        return row.quantity_on_hand

    @property
    def window(self) -> tuple[datetime, datetime]:
        return self.now - timedelta(hours=1), self.now + timedelta(hours=1)


@pytest.fixture
def scenario(owner_url: str):
    with psycopg.connect(owner_url) as conn:
        try:
            yield Scenario(conn)
        finally:
            conn.rollback()


def _line(scenario: Scenario, which: str) -> tuple[int, int]:
    return (scenario.first, 3) if which == "first" else (scenario.second, 2)


def _price(scenario: Scenario, product: int) -> Decimal:
    scenario.cur.execute(
        "SELECT list_price FROM public.product WHERE product_id = %s", (product,)
    )
    return scenario.cur.fetchone()[0]


def _two_line_sale(s: Scenario) -> int:
    return s.sale([(s.first, 3, "10.00"), (s.second, 2, "4.25")])


def test_the_reports_and_the_customer_profile_count_net_units(
    scenario: Scenario,
) -> None:
    s = scenario
    s.as_web()
    revenue_before = read_revenue_summary(s.conn, WINDOW_DAYS)
    sale_id = _two_line_sale(s)
    s.as_web()
    gross = read_revenue_summary(s.conn, WINDOW_DAYS)
    assert gross.transactions == revenue_before.transactions + 1
    assert gross.revenue == revenue_before.revenue + D("38.50")

    # Product one: one of three comes back. Product two: both come back.
    s.reverse(sale_id, "RETURN", [(s.first, 1, True), (s.second, 2, True)])
    s.as_web()
    since, until = s.window

    after = read_revenue_summary(s.conn, WINDOW_DAYS)
    assert after.transactions == revenue_before.transactions + 1
    assert after.revenue == revenue_before.revenue + D("20.00")

    totals = consumption.get_sales_totals(s.conn, s.customer, since, until)
    assert (totals.purchases, totals.spend) == (1, D("20.00"))
    (channel,) = consumption.list_channel_totals(s.conn, s.customer, since, until)
    (store,) = consumption.list_store_totals(s.conn, s.customer, since, until)
    assert (channel.purchases, channel.spend) == (1, D("20.00"))
    assert (store.purchases, store.spend) == (1, D("20.00"))
    # The product returned in full is no longer something they bought.
    products = consumption.list_product_totals(s.conn, s.customer, since, until)
    assert [(p.product_id, p.purchases, p.units) for p in products] == [(s.first, 1, 2)]
    (category,) = consumption.list_category_totals(s.conn, s.customer, since, until)
    assert (category.purchases, category.units, category.spend) == (1, 2, D("20.00"))
    discount = consumption.get_discount_totals(s.conn, s.customer, since, until)
    assert (discount.paid, discount.at_list) == (D("20.00"), 2 * _price(s, s.first))


def test_a_whole_cancellation_removes_the_sale_from_every_reader(
    scenario: Scenario,
) -> None:
    s = scenario
    s.as_web()
    revenue_before = read_revenue_summary(s.conn, WINDOW_DAYS)
    inputs_before = {
        row.customer_id: row for row in segments.read_rfm_inputs(s.conn, WINDOW_DAYS)
    }[s.customer]
    sale_id = _two_line_sale(s)
    s.reverse(sale_id, "CANCELLATION", [(s.first, 3, True), (s.second, 2, True)])
    s.as_web()
    since, until = s.window

    assert read_revenue_summary(s.conn, WINDOW_DAYS) == revenue_before
    totals = consumption.get_sales_totals(s.conn, s.customer, since, until)
    assert (totals.purchases, totals.spend, totals.last_purchase_at) == (0, None, None)
    assert consumption.list_channel_totals(s.conn, s.customer, since, until) == []
    assert consumption.list_store_totals(s.conn, s.customer, since, until) == []
    assert consumption.list_product_totals(s.conn, s.customer, since, until) == []
    assert consumption.list_category_totals(s.conn, s.customer, since, until) == []
    # R, F and M are as if the sale had never happened: a whole cancellation
    # can move a customer's most recent purchase (ADR-0062).
    inputs_after = {
        row.customer_id: row for row in segments.read_rfm_inputs(s.conn, WINDOW_DAYS)
    }[s.customer]
    assert inputs_after == inputs_before


def test_a_partial_return_changes_the_monetary_value_alone(scenario: Scenario) -> None:
    s = scenario
    s.as_web()
    before = {
        row.customer_id: row for row in segments.read_rfm_inputs(s.conn, WINDOW_DAYS)
    }[s.customer]
    sale_id = _two_line_sale(s)
    s.as_web()
    bought = {
        row.customer_id: row for row in segments.read_rfm_inputs(s.conn, WINDOW_DAYS)
    }[s.customer]
    assert bought.frequency == (before.frequency or 0) + 1
    assert bought.monetary == (before.monetary or 0) + D("38.50")

    s.reverse(sale_id, "RETURN", [(s.first, 1, True)])
    s.as_web()
    returned = {
        row.customer_id: row for row in segments.read_rfm_inputs(s.conn, WINDOW_DAYS)
    }[s.customer]
    assert returned.frequency == bought.frequency
    assert returned.last_purchase_at == bought.last_purchase_at
    assert returned.monetary == bought.monetary - D("10.00")


def test_consumption_shift_counts_net_units_in_both_periods(
    scenario: Scenario,
) -> None:
    s = scenario
    sale_id = _two_line_sale(s)
    s.reverse(sale_id, "RETURN", [(s.first, 1, True), (s.second, 2, True)])
    s.as_web()
    later_start, later_end = s.window
    earlier_start = later_start - timedelta(days=1)
    earlier, later, seeded = consumption_shift.list_totals_for_periods(
        s.conn,
        earlier_start,
        later_start,
        later_start,
        later_end,
        customer_id=s.customer,
    )
    assert not any(earlier[:3][0].values())
    channels, stores, categories, lined = later
    assert [(g.purchases, g.spend) for g in channels.get(s.customer, [])] == [
        (1, D("20.00"))
    ]
    assert [(g.purchases, g.spend) for g in stores.get(s.customer, [])] == [
        (1, D("20.00"))
    ]
    assert [(c.units, c.spend) for c in categories.get(s.customer, [])] == [
        (2, D("20.00"))
    ]
    assert lined == {s.customer: 1}
    assert seeded is False

    # Cancelled in full, the same sale is no purchase in any dimension.
    s.reverse(sale_id, "RETURN", [(s.first, 2, True)])
    s.as_web()
    _, (channels, stores, categories, lined), _ = (
        consumption_shift.list_totals_for_periods(
            s.conn,
            earlier_start,
            later_start,
            later_start,
            later_end,
            customer_id=s.customer,
        )
    )
    assert not channels.get(s.customer) and not stores.get(s.customer)
    assert not categories.get(s.customer) and lined == {}


def test_the_segment_pages_and_recommendations_count_net_units(
    scenario: Scenario,
) -> None:
    s = scenario
    # A customer who is in a segment now, and another customer in that segment
    # to look at the first one's purchases from.
    s.cur.execute(
        "SELECT h.label_code, h.run_id, h.customer_id::text"
        " FROM public.customer_segment_history AS h"
        " WHERE h.valid_to IS NULL AND h.label_code IS NOT NULL"
        "   AND h.customer_id::text <> %s"
        "   AND NOT EXISTS (SELECT 1 FROM public.transaction AS t"
        "     WHERE t.customer_id = h.customer_id"
        "       AND t.occurred_at > now() - interval '2 hours')"
        " ORDER BY h.customer_id LIMIT 2",
        (s.customer,),
    )
    (label, run_id, buyer), (_, _, observer) = s.cur.fetchall()
    sale_id = s.sale([(s.first, 3, "10.00"), (s.second, 2, "4.25")], customer=buyer)
    s.as_web()
    since, until = s.window

    def buyers() -> dict[int, int]:
        return recommendations.list_segment_buyers(
            s.conn, label, observer, since, until
        )

    def label_revenue() -> Decimal:
        return segmentation_dashboard.list_run_revenue_by_label(
            s.conn, run_id, since, until
        ).get(label, D(0))

    assert buyers() == {s.first: 1, s.second: 1}
    revenue = label_revenue()
    s.reverse(sale_id, "RETURN", [(s.second, 2, True), (s.first, 1, True)])
    s.as_web()
    # A product returned in full was not bought; the label's revenue is net.
    assert buyers() == {s.first: 1}
    assert label_revenue() == revenue - D("18.50")
    s.reverse(sale_id, "RETURN", [(s.first, 2, True)])
    s.as_web()
    assert buyers() == {}
    assert label_revenue() == revenue - D("38.50")


def test_the_customer_timeline_lists_net_sales_and_drops_a_cancelled_one(
    scenario: Scenario,
) -> None:
    s = scenario
    s.cur.execute(
        "SELECT run_id, run_at FROM public.segmentation_run"
        " ORDER BY run_at DESC, run_id DESC LIMIT 1"
    )
    run_id, run_at = s.cur.fetchone()
    s.cur.execute(
        "SELECT run_id FROM public.segmentation_run" " ORDER BY run_at, run_id LIMIT 1"
    )
    (oldest,) = s.cur.fetchone()
    if oldest == run_id:
        pytest.skip("the seed holds a single segmentation run")
    s.cur.execute(
        "SELECT run_at - make_interval(days => window_days) AS since, run_at"
        " FROM public.segmentation_run WHERE run_id = %s",
        (oldest,),
    )
    old_since, old_until = s.cur.fetchone()
    occurred = run_at - timedelta(hours=1)
    assert not old_since <= occurred <= old_until
    customer = s._one(
        "SELECT c.customer_id::text FROM public.customer AS c"
        " WHERE c.registered_on <= %s::date AND NOT EXISTS ("
        "  SELECT 1 FROM public.transaction AS t WHERE t.customer_id = c.customer_id"
        "   AND t.occurred_at BETWEEN %s AND %s)"
        " ORDER BY c.customer_id LIMIT 1",
        (occurred, occurred - timedelta(hours=2), occurred + timedelta(hours=2)),
    )
    sale_id = s.sale(
        [(s.first, 3, "10.00"), (s.second, 2, "4.25")],
        customer=customer,
        occurred_at=occurred,
    )
    s.reverse(sale_id, "RETURN", [(s.first, 1, True)])
    s.as_web()
    (sale,) = [
        row
        for row in customer_timeline.list_sales_in_window_only(
            s.conn, customer, run_id=run_id, excluding_run_id=oldest
        )
        if row.transaction_id == sale_id
    ]
    assert (sale.total, sale.units) == (D("28.50"), 4)
    s.reverse(sale_id, "RETURN", [(s.first, 2, True), (s.second, 2, True)])
    s.as_web()
    assert [
        row
        for row in customer_timeline.list_sales_in_window_only(
            s.conn, customer, run_id=run_id, excluding_run_id=oldest
        )
        if row.transaction_id == sale_id
    ] == []


def _assignment(s: Scenario):
    s.cur.execute(
        "SELECT a.assignment_id, a.experiment_id, a.customer_id::text, a.group_id,"
        "       a.assigned_at"
        " FROM public.experiment_assignment AS a"
        " JOIN public.experiment AS e USING (experiment_id)"
        " JOIN public.customer AS c USING (customer_id)"
        " WHERE c.registered_on <= a.assigned_at::date"
        "   AND a.assigned_at + interval '2 hours' < now()"
        "   AND NOT EXISTS (SELECT 1 FROM public.transaction AS t"
        "     WHERE t.customer_id = a.customer_id AND t.occurred_at >= a.assigned_at"
        "       AND t.occurred_at < a.assigned_at"
        "           + make_interval(days => e.conversion_window_days))"
        " ORDER BY a.assignment_id LIMIT 1"
    )
    return s.cur.fetchone()


def test_a_cancelled_sale_is_no_conversion_and_a_return_lowers_its_total(
    scenario: Scenario,
) -> None:
    s = scenario
    _assignment_id, experiment_id, customer, group_id, assigned_at = _assignment(s)
    sale_id = s.sale(
        [(s.first, 3, "10.00"), (s.second, 2, "4.25")],
        customer=customer,
        occurred_at=assigned_at + timedelta(hours=1),
    )
    s.as_web()
    assert conversions.evaluate_conversions(s.conn, experiment_id) >= 1

    def attributed() -> list[conversions.Attribution]:
        rows, total = conversions.list_attributions(
            s.conn, experiment_id, page=1, per_page=1000
        )
        assert total == len(rows)
        return [row for row in rows if row.transaction_id == sale_id]

    def converted() -> int:
        (group,) = [
            g
            for g in conversions.list_group_conversion(
                s.conn, experiment_id, datetime.now(UTC)
            )
            if g.group_id == group_id
        ]
        return group.converted

    def report() -> experiment_report.ReportGroup:
        (group,) = [
            g
            for g in experiment_report.list_report_groups(
                s.conn, [experiment_id], datetime.now(UTC)
            )
            if g.group_id == group_id
        ]
        return group

    def first_conversions() -> int:
        return sum(
            day.customers
            for day in experiment_dashboard.list_conversion_days(s.conn, experiment_id)
            if day.group_id == group_id
        )

    (row,) = attributed()
    assert row.total == D("38.50")
    before = converted()
    report_before = report().converted
    days_before = first_conversions()
    assert report_before == before

    s.reverse(sale_id, "RETURN", [(s.first, 1, True)])
    s.as_web()
    (row,) = attributed()
    assert row.total == D("28.50")
    assert converted() == before
    assert report().converted == report_before
    assert first_conversions() == days_before

    # The conversion row stays (ADR-0050); it just no longer counts.
    s.cur.execute(
        "SELECT count(*) FROM public.experiment_conversion WHERE transaction_id = %s",
        (sale_id,),
    )
    rows_before = s.cur.fetchone()[0]
    s.reverse(sale_id, "RETURN", [(s.first, 2, True), (s.second, 2, True)])
    s.as_web()
    assert attributed() == []
    assert converted() == before - 1
    # The report and the dashboard agree with the conversion page.
    assert report().converted == report_before - 1
    assert first_conversions() == days_before - 1
    s.cur.execute(
        "SELECT count(*) FROM public.experiment_conversion WHERE transaction_id = %s",
        (sale_id,),
    )
    assert s.cur.fetchone()[0] == rows_before == 1


def test_the_stock_page_shows_what_a_reversal_restored(scenario: Scenario) -> None:
    s = scenario
    start = (s.stock(s.first), s.stock(s.second))
    sale_id = _two_line_sale(s)
    assert (s.stock(s.first), s.stock(s.second)) == (start[0] - 3, start[1] - 2)
    # One unit of the first product is returned damaged, so it is not restocked;
    # the second comes back resellable.
    s.reverse(sale_id, "RETURN", [(s.first, 1, False), (s.second, 2, True)])
    assert (s.stock(s.first), s.stock(s.second)) == (start[0] - 3, start[1])
    s.reverse(sale_id, "RETURN", [(s.first, 2, True)])
    assert (s.stock(s.first), s.stock(s.second)) == (start[0] - 1, start[1])


def test_no_web_query_reads_the_gross_sales_tables_except_ingestion() -> None:
    """ADR-0062: a reader that goes back to `transaction` or `transaction_line`
    counts a reversed sale as if nothing had happened. Only the two statements
    of web/db/sales.py, which validate ingestion, stay gross."""
    gross = re.compile(
        r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:public\.)?transaction(?:_line)?\b",
        re.IGNORECASE,
    )
    offenders = []
    for path in sorted((ROOT / "web").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("web/tests/") or relative == "web/db/sales.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if gross.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert offenders == []
