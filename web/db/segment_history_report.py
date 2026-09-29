"""Reads behind the filtered segment history report (F12-02).

Read only, parameterized, paginated: `customer_segment_history` already holds
one row per customer per run, so an unfiltered read across every run can be
large -- unlike F12-01's per-run reads, this one is never read in full.

Three filters, each optional, combine with AND: a run, a label, and a period.
`label_code` is a tri-state, one parameter: None means no label filter; the
empty string, UNASSIGNED, means only the rows a run left unassigned (RN-21);
anything else is a real label code. A period filters on the row's own run's
`run_at`, closed on its last day, the same "whole day" rule
`web.db.audit`'s date filters already use.

The WHERE clause is a literal in each statement rather than a shared
constant, matching `web.db.audit`'s own pair of count/search functions:
building SQL text with an f-string or concatenation is refused elsewhere in
this codebase (`tests/test_write_services.py`), so the two clauses are kept
identical by a test that reads both statements' source and compares the
text, rather than by sharing it at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from psycopg import Connection

from web.db.segments import RunAssignment

UNASSIGNED = ""


@dataclass(frozen=True)
class HistoryEntry:
    """One customer_segment_history row, with the run it belongs to and the
    customer's assignment on it -- exactly the shape explain_migration (F7-06)
    already reads, so this row can stand in as either side of an explanation
    with no further query."""

    history_id: int
    run_id: int
    run_at: datetime
    method: str
    window_days: int
    valid_from: datetime
    valid_to: datetime | None
    label_name: str | None
    assignment: RunAssignment


def list_history_entries(
    connection: Connection[Any],
    *,
    run_id: int | None = None,
    label_code: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    page: int,
    per_page: int,
) -> list[HistoryEntry]:
    """One page of history rows matching every applied filter, newest run
    first, then by customer name."""
    parameters = {
        "run_id": run_id,
        "label_code": label_code,
        "period_start": period_start,
        "period_end": period_end,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT h.history_id, h.run_id, r.run_at, r.method, r.window_days,
                   h.valid_from, h.valid_to,
                   h.customer_id, c.name, h.segment_id, h.label_code, sl.name,
                   h.r_score, h.f_score, h.m_score,
                   h.recency_last_purchase_at, h.frequency_count, h.monetary_total
            FROM customer_segment_history AS h
            JOIN customer AS c ON c.customer_id = h.customer_id
            JOIN segmentation_run AS r ON r.run_id = h.run_id
            LEFT JOIN segment_label AS sl ON sl.label_code = h.label_code
            WHERE (%(run_id)s::int IS NULL OR h.run_id = %(run_id)s)
              AND (
                  %(label_code)s::text IS NULL
                  OR (%(label_code)s::text = '' AND h.label_code IS NULL)
                  OR h.label_code = %(label_code)s::text
              )
              AND (%(period_start)s::date IS NULL OR r.run_at >= %(period_start)s)
              AND (
                  %(period_end)s::date IS NULL
                  OR r.run_at < %(period_end)s::date + INTERVAL '1 day'
              )
            ORDER BY r.run_at DESC, c.name, h.customer_id
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {**parameters, "limit": per_page, "offset": (page - 1) * per_page},
        )
        rows = cursor.fetchall()

    entries = []
    for row in rows:
        (
            history_id,
            run_id_,
            run_at,
            method,
            window_days,
            valid_from,
            valid_to,
            customer_id,
            customer_name,
            segment_id,
            label_code_,
            label_name,
            r_score,
            f_score,
            m_score,
            last_purchase_at,
            frequency,
            monetary,
        ) = row
        entries.append(
            HistoryEntry(
                history_id=history_id,
                run_id=run_id_,
                run_at=run_at,
                method=method,
                window_days=window_days,
                valid_from=valid_from,
                valid_to=valid_to,
                label_name=label_name,
                assignment=RunAssignment(
                    customer_id=str(customer_id),
                    customer_name=customer_name,
                    segment_id=segment_id,
                    label_code=label_code_,
                    r_score=r_score,
                    f_score=f_score,
                    m_score=m_score,
                    recency_last_purchase_at=last_purchase_at,
                    frequency_count=frequency,
                    monetary_total=monetary,
                ),
            )
        )
    return entries


def count_history_entries(
    connection: Connection[Any],
    *,
    run_id: int | None = None,
    label_code: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> int:
    """How many rows match every applied filter, for the pagination footer."""
    parameters = {
        "run_id": run_id,
        "label_code": label_code,
        "period_start": period_start,
        "period_end": period_end,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM customer_segment_history AS h
            JOIN segmentation_run AS r ON r.run_id = h.run_id
            WHERE (%(run_id)s::int IS NULL OR h.run_id = %(run_id)s)
              AND (
                  %(label_code)s::text IS NULL
                  OR (%(label_code)s::text = '' AND h.label_code IS NULL)
                  OR h.label_code = %(label_code)s::text
              )
              AND (%(period_start)s::date IS NULL OR r.run_at >= %(period_start)s)
              AND (
                  %(period_end)s::date IS NULL
                  OR r.run_at < %(period_end)s::date + INTERVAL '1 day'
              )
            """,
            parameters,
        )
        return int(cursor.fetchone()[0])
