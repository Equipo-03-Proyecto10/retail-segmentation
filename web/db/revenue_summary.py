"""Revenue read behind the reports index (#344).

Read-only and parameterized. The window is closed at the newest sale on
record, not at the wall clock, so the summary describes the data that exists
rather than going blank when the last load is older than the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection


@dataclass(frozen=True)
class RevenueRow:
    """One store or channel: its sales and revenue inside the window."""

    name: str
    transactions: int
    revenue: Decimal


@dataclass(frozen=True)
class RevenueSummary:
    """Totals, and the split by channel and by store, for one window."""

    window_days: int
    until: datetime | None
    transactions: int
    revenue: Decimal
    by_channel: tuple[RevenueRow, ...]
    by_store: tuple[RevenueRow, ...]


_WINDOW = """
    t.occurred_at > (SELECT max(occurred_at) FROM transaction)
                    - make_interval(days => %s)
"""
_TOTALS = (
    "SELECT max(t.occurred_at), count(*), coalesce(sum(t.total), 0) "
    "FROM transaction AS t WHERE " + _WINDOW
)
_BY_CHANNEL = (
    "SELECT c.name, count(*), sum(t.total) FROM transaction AS t "
    "JOIN channel AS c ON c.channel_id = t.channel_id WHERE "
    + _WINDOW
    + "GROUP BY c.channel_id, c.name ORDER BY sum(t.total) DESC, c.name"
)
_BY_STORE = (
    "SELECT s.name, count(*), sum(t.total) FROM transaction AS t "
    "JOIN store AS s ON s.store_id = t.store_id WHERE "
    + _WINDOW
    + "GROUP BY s.store_id, s.name ORDER BY sum(t.total) DESC, s.name"
)


def read_revenue_summary(
    connection: Connection[Any], window_days: int
) -> RevenueSummary:
    """Accepted sales in the `window_days` ending at the newest sale."""
    with connection.cursor() as cursor:
        cursor.execute(_TOTALS, (window_days,))
        until, transactions, revenue = cursor.fetchone()
        cursor.execute(_BY_CHANNEL, (window_days,))
        by_channel = tuple(RevenueRow(*row) for row in cursor.fetchall())
        cursor.execute(_BY_STORE, (window_days,))
        by_store = tuple(RevenueRow(*row) for row in cursor.fetchall())
    return RevenueSummary(
        window_days, until, transactions, revenue, by_channel, by_store
    )
