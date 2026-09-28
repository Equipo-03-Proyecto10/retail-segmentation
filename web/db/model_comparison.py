"""Reads behind the model comparison (F9-04).

Read only, and every statement is parameterized. Two things are read.

**A run's labelled rows**: every customer the run scored, with the label it
assigned, or None where it left them unassigned (RN-21). This is what a
comparison interprets, and it does not select what produced the run.

**A list of runs**, newest first, filtered by the kind of run. The kind is
metadata about a run, used only to offer the page one run of each kind and to
describe them. It is never read to decide how an assignment means anything.
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from web.db.segments import SegmentationRun


def list_run_label_rows(
    connection: Connection[Any], run_id: int
) -> list[tuple[str, str, str | None]]:
    """(customer id, customer name, label code) for every customer the run
    scored, ordered by name and then id."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT h.customer_id, c.name, h.label_code
            FROM customer_segment_history AS h
            JOIN customer AS c ON c.customer_id = h.customer_id
            WHERE h.run_id = %s
            ORDER BY c.name, h.customer_id
            """,
            (run_id,),
        )
        rows = cursor.fetchall()

    return [(str(customer_id), name, label) for customer_id, name, label in rows]


def list_runs_of_method(
    connection: Connection[Any], method: str, *, limit: int
) -> list[SegmentationRun]:
    """The newest runs of one kind, most recent first."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.run_id, r.method, r.window_days, r.parameters,
                   r.customer_count, r.executed_by, u.name, r.run_at
            FROM segmentation_run AS r
            LEFT JOIN app_user AS u ON u.user_id = r.executed_by
            WHERE r.method = %s
            ORDER BY r.run_at DESC, r.run_id DESC
            LIMIT %s
            """,
            (method, limit),
        )
        rows = cursor.fetchall()

    return [SegmentationRun(*row) for row in rows]
