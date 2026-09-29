"""Reads behind the segmentation dashboard (F12-01).

Read only, and every statement is parameterized. Two reads are new; everything
else the dashboard needs — the run itself, the label vocabulary, the migration
between two runs — is read by web.db.segments and web.services.segment_migration,
already built and tested by F7 and F9.

**Every customer's raw R/F/M, for one run, unpaged.** The dashboard aggregates
over the whole run to build the size and heatmap charts, so there is no page to
get wrong by forgetting one; `web.db.segments.list_run_assignments` is paged for
a table and is not reused here. Quintile scores are not read: they exist only for
RFM_RULES runs (F9-02), and a chart that read them would differ by method, which
ADR-0018 forbids. The raw values are recorded for either method and are what the
chart bins itself (web.services.segmentation_dashboard.quintile_bins).

**Revenue by label, summed over a stated window.** The window is closed at both
ends, like the consumption profile's; the caller supplies it, so this read never
decides for itself what "the window" means. An unassigned customer has no sale in
the run's own window by construction (RN-21), so the read is never asked to
invent a zero for one; a caller wanting the full label list, including a label
with no revenue, adds the zero itself (web.services.segmentation_dashboard.
build_revenue_by_label).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection

from web.db.segments import SegmentationRun


@dataclass(frozen=True)
class RunRfmRow:
    """One customer's raw R/F/M within a run. All but `customer_id` are None
    for the unassigned result (RN-21): no sale in the run's window."""

    customer_id: str
    label_code: str | None
    last_purchase_at: datetime | None
    frequency: int | None
    monetary: Decimal | None


def list_run_rfm_rows(connection: Connection[Any], run_id: int) -> list[RunRfmRow]:
    """Every customer the run scored, unpaged, for the dashboard to aggregate."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT customer_id, label_code,
                   recency_last_purchase_at, frequency_count, monetary_total
            FROM customer_segment_history
            WHERE run_id = %s
            """,
            (run_id,),
        )
        rows = cursor.fetchall()

    return [RunRfmRow(str(row[0]), *row[1:]) for row in rows]


def list_run_revenue_by_label(
    connection: Connection[Any], run_id: int, since: datetime, until: datetime
) -> dict[str, Decimal]:
    """Accepted sales in [since, until], summed by the customer's label in this
    run. A label with no revenue in the window is simply absent."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT h.label_code, sum(t.total)
            FROM customer_segment_history AS h
            JOIN transaction AS t ON t.customer_id = h.customer_id
            WHERE h.run_id = %s
              AND t.occurred_at >= %s
              AND t.occurred_at <= %s
            GROUP BY h.label_code
            """,
            (run_id, since, until),
        )
        rows = cursor.fetchall()

    return {label: total for label, total in rows if label is not None}


def get_previous_run(
    connection: Connection[Any], run_id: int
) -> SegmentationRun | None:
    """The run immediately before this one by run_at (tie-broken by run_id),
    whichever method produced either of them (ADR-0018). None for the
    earliest run there is."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.run_id, r.method, r.window_days, r.parameters,
                   r.customer_count, r.executed_by, u.name, r.run_at
            FROM segmentation_run AS r
            LEFT JOIN app_user AS u ON u.user_id = r.executed_by
            WHERE (r.run_at, r.run_id) < (
                SELECT run_at, run_id FROM segmentation_run WHERE run_id = %s
            )
            ORDER BY r.run_at DESC, r.run_id DESC
            LIMIT 1
            """,
            (run_id,),
        )
        row = cursor.fetchone()

    return SegmentationRun(*row) if row else None
