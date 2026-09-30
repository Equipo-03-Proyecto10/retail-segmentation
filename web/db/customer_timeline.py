"""Reads behind a customer's segment timeline (#337).

Read only, and every statement is parameterized. Two reads:

* **Every assignment the customer has held**, as `HistoryRow` -- the same shape
  the consumption profile reads its current and previous rows in
  (`web.db.consumption`), so a page can show any of them the same way. Like
  that read, it joins the run only for `run_at` and `window_days` and never
  reads `segmentation_run.method` (ADR-0018).
* **The accepted sales in one run's window and not in another's.** A run
  measures R/F/M over `occurred_at >= now() - make_interval(days =>
  window_days)` (`web.db.segments._SCORE_AND_MATCH`), so the window each run
  saw is rebuilt here from the run row with the same interval arithmetic, in
  the same session time zone, ending at `run_at`. Swapping the two run ids
  gives the other direction: sales that entered the later run's calculation,
  or sales that aged out of it.

Two limits follow from reading only what the schema keeps, and neither is
worked around here. `run_at` is taken after the run's advisory lock is granted
(`lock_for_run`) while the scoring statement uses `now()`, the start of the
same transaction, so a rebuilt window can sit a few seconds later than the one
the run actually used. And `transaction` records when a sale happened, not when
it was imported (ADR-0020): a sale loaded after a run but dated before it is
reported as having been in that run's window, which it was not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection

from web.db.consumption import HistoryRow


@dataclass(frozen=True)
class SaleRow:
    """One accepted sale, with what a reader needs to recognise it. `units` is
    the sum of its lines' quantities; `total` is the header total the run
    scores as Monetary."""

    transaction_id: int
    source_transaction_id: str
    occurred_at: datetime
    total: Decimal
    store_name: str
    channel_name: str
    units: int


def list_history_rows(
    connection: Connection[Any], customer_id: Any
) -> list[HistoryRow]:
    """Every assignment the customer has held, newest first."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT h.run_id, h.label_code, sl.name,
                   h.recency_last_purchase_at, h.frequency_count,
                   h.monetary_total, h.r_score, h.f_score, h.m_score,
                   h.valid_from, h.valid_to, r.run_at, r.window_days
            FROM customer_segment_history AS h
            JOIN segmentation_run AS r ON r.run_id = h.run_id
            LEFT JOIN segment_label AS sl ON sl.label_code = h.label_code
            WHERE h.customer_id = %(customer_id)s
            ORDER BY h.valid_from DESC, h.history_id DESC
            """,
            {"customer_id": str(customer_id)},
        )
        return [HistoryRow(*row) for row in cursor.fetchall()]


def list_sales_in_window_only(
    connection: Connection[Any],
    customer_id: Any,
    *,
    run_id: int,
    excluding_run_id: int,
) -> list[SaleRow]:
    """The customer's accepted sales inside `run_id`'s window and outside
    `excluding_run_id`'s, oldest first.

    Each window is [run_at - window_days, run_at], both ends inclusive, read
    from the run row itself.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH inside AS (
                SELECT run_at - make_interval(days => window_days) AS since,
                       run_at AS until
                FROM segmentation_run
                WHERE run_id = %(run_id)s
            ),
            outside AS (
                SELECT run_at - make_interval(days => window_days) AS since,
                       run_at AS until
                FROM segmentation_run
                WHERE run_id = %(excluding_run_id)s
            )
            SELECT t.transaction_id, t.source_transaction_id, t.occurred_at,
                   t.total, s.name, ch.name,
                   COALESCE(sum(tl.quantity), 0)
            FROM transaction AS t
            JOIN store AS s ON s.store_id = t.store_id
            JOIN channel AS ch ON ch.channel_id = t.channel_id
            LEFT JOIN transaction_line AS tl
                   ON tl.transaction_id = t.transaction_id
            CROSS JOIN inside AS i
            CROSS JOIN outside AS o
            WHERE t.customer_id = %(customer_id)s
              AND t.occurred_at >= i.since
              AND t.occurred_at <= i.until
              AND NOT (t.occurred_at >= o.since AND t.occurred_at <= o.until)
            GROUP BY t.transaction_id, s.name, ch.name
            ORDER BY t.occurred_at, t.transaction_id
            """,
            {
                "customer_id": str(customer_id),
                "run_id": run_id,
                "excluding_run_id": excluding_run_id,
            },
        )
        return [SaleRow(*row) for row in cursor.fetchall()]
