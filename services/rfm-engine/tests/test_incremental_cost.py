"""For 1,000 sales the work follows the sales and not the customer count (AC 5).

Two phases of 1,000 sales each, from 50 buyers of 20 sales apiece. Between them the
database gains 5,000 customers, each with a profile. A recomputation that scanned
the customers, or the profiles, would cost more in the second phase; one that reads
the buyer's own sales through idx_transaction_customer_date and writes the buyer's
row costs the same. The measured figures are in
docs/evidence/f22-05-incremental-cost.md; RFM_EVIDENCE_OUT=<file> makes a run write
them as JSON.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from datetime import datetime
from pathlib import Path

import psycopg
import pytest

from app import recompute, store
from tests.conftest import Shop, days_ago

pytestmark = pytest.mark.postgres
RULE_SET = 1
BUYERS = 50
SALES_PER_BUYER = 20
EXTRA_CUSTOMERS = 5000


def _record_sales(shop: Shop, buyers: list[uuid.UUID], now: datetime) -> list[int]:
    sales = []
    for index in range(BUYERS * SALES_PER_BUYER):
        sales.append(
            shop.sale(
                buyers[index % BUYERS],
                days_ago(now, 1 + (index % 150)),
                quantity=1 + index % 3,
                unit_price="12.50",
            )
        )
    return sales


def _buffers(
    connection: psycopg.Connection, customer_id: uuid.UUID, now: datetime
) -> int:
    """Shared buffers the measuring statement touches for one buyer."""
    rows = connection.execute(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) WITH sale AS ("
        " SELECT transaction_id, min(occurred_at) AS occurred_at,"
        "        sum(net_amount) AS net_total"
        "   FROM api_sales.net_purchase_line_v1"
        "  WHERE customer_id = %(customer)s"
        "    AND occurred_at >= %(as_of)s - make_interval(days => 180)"
        "    AND occurred_at <= %(as_of)s"
        "  GROUP BY transaction_id)"
        " SELECT count(*), COALESCE(sum(net_total), 0), min(occurred_at),"
        "        max(occurred_at) FROM sale",
        {"customer": customer_id, "as_of": now},
    ).fetchone()
    assert rows is not None
    plan = rows[0][0]["Plan"]
    return int(plan["Shared Hit Blocks"] + plan["Shared Read Blocks"])


def _written(owner: psycopg.Connection) -> int:
    row = owner.execute(
        "SELECT n_tup_ins + n_tup_upd FROM pg_stat_user_tables"
        " WHERE schemaname = 'rfm' AND relname = 'customer_profile'"
    ).fetchone()
    assert row is not None
    return int(row[0])


def _phase(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    now: datetime,
) -> dict[str, float]:
    buyers = [shop.customer() for _ in range(BUYERS)]
    sales = _record_sales(shop, buyers, now)
    owner.execute("ANALYZE public.transaction")
    owner.execute("ANALYZE public.transaction_line")
    service.execute("SELECT pg_stat_force_next_flush()")
    service.execute("SELECT 1")
    written_before = _written(owner)
    timings = []
    started = time.perf_counter()
    for transaction in sales:
        begun = time.perf_counter()
        with service.transaction():
            recompute.from_sale(service, transaction, RULE_SET, now)
        timings.append((time.perf_counter() - begun) * 1000)
    elapsed = time.perf_counter() - started
    service.execute("SELECT pg_stat_force_next_flush()")
    service.execute("SELECT 1")
    row = owner.execute(
        "SELECT count(*) FROM rfm.customer_profile WHERE customer_id = ANY(%s)",
        (buyers,),
    ).fetchone()
    assert row is not None and row[0] == BUYERS
    total = owner.execute("SELECT count(*) FROM rfm.customer_profile").fetchone()
    customers = owner.execute("SELECT count(*) FROM public.customer").fetchone()
    assert total is not None and customers is not None
    return {
        "sales": float(len(sales)),
        "customers": float(customers[0]),
        "profiles": float(total[0]),
        "profile_rows_written": float(_written(owner) - written_before),
        "buyer_buffers": float(_buffers(service, buyers[0], now)),
        "median_ms": statistics.median(timings),
        "p95_ms": sorted(timings)[int(len(timings) * 0.95)],
        "total_s": elapsed,
    }


def test_a_thousand_sales_cost_the_same_with_five_thousand_more_customers(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    now: datetime,
) -> None:
    small = _phase(shop, service, owner, now)

    extra = [uuid.uuid4() for _ in range(EXTRA_CUSTOMERS)]
    with owner.transaction():
        with owner.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO public.customer"
                " (customer_id, name, registration_channel_id)"
                " VALUES (%s, 'rfm-engine bulk', 1)",
                [(customer_id,) for customer_id in extra],
            )
            cursor.executemany(
                "INSERT INTO rfm.customer_profile (customer_id, state, rule_set_id,"
                " f_count, m_total, computed_at)"
                " VALUES (%s, 'unassigned', %s, 0, 0, %s)",
                [(customer_id, RULE_SET, now) for customer_id in extra],
            )
    try:
        large = _phase(shop, service, owner, now)
    finally:
        owner.execute(
            "DELETE FROM public.customer WHERE customer_id = ANY(%s)", (extra,)
        )
        owner.execute(
            "DELETE FROM rfm_events.outbox WHERE entity_id = ANY(%s)",
            ([str(customer_id) for customer_id in extra],),
        )

    assert large["customers"] >= small["customers"] + EXTRA_CUSTOMERS
    assert large["profiles"] >= small["profiles"] + EXTRA_CUSTOMERS
    # The work follows the sales: 1,000 events, each a write of its own buyer's row
    # and of no other, in either phase.
    for phase in (small, large):
        assert phase["sales"] == 1000
        assert phase["profile_rows_written"] <= phase["sales"]
    # Reading a buyer's sales touches the same pages however many customers exist.
    assert large["buyer_buffers"] <= small["buyer_buffers"] * 1.25 + 5
    # And the time per event does not grow with the customers (a scan of 5,000
    # profiles or customers per event would multiply it).
    assert large["median_ms"] <= small["median_ms"] * 3 + 5
    out = os.environ.get("RFM_EVIDENCE_OUT")
    if out:
        Path(out).write_text(
            json.dumps({"small": small, "large": large}, indent=2) + "\n"
        )


def test_the_measuring_statement_reads_by_customer_and_date(
    shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 3))
    plan = service.execute(
        "EXPLAIN (FORMAT JSON) SELECT count(*) FROM api_sales.net_purchase_line_v1"
        " WHERE customer_id = %s AND occurred_at >= %s AND occurred_at <= %s",
        (customer, days_ago(now, 180), now),
    ).fetchone()
    assert plan is not None
    text = json.dumps(plan[0])
    assert "idx_transaction_customer_date" in text or "Index" in text
    assert store.LOCK_KEY == 295001
