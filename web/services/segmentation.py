"""The segment recalculation, and what a run is allowed to be (F3-10).

RF-12, and the demonstration list's "ejecución de un proceso principal". The
narrow slice this delivers is written down in `docs/requirements.md`: quintile
R/F/M scoring over a window of recorded sales, matched against the bands
already in `segment_rule`. Not k-means, not segment history, not migration
reporting — [`docs/roadmap.md`](../../docs/roadmap.md) defers those, and
ADR-0004 records that `customer.current_segment_id` is a column the history
module will replace. Nothing here adds anything else that depends on it.

RN-21 lives here: a customer with no sales in the window is *unassigned*, not
left holding a stale segment. An empty segment is information; a wrong one is
not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from web.db.segments import recalculate_segments

# The seed covers 180 days of sales, so the default window sees all of it.
# Per-run rather than per-deployment: the operator chooses the window on the
# form, which is what "configurable" has to mean for a process somebody runs
# and then reads the result of.
DEFAULT_WINDOW_DAYS = 180
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 3650


class InvalidWindow(ValueError):
    """A window outside what a recalculation will accept."""


@dataclass(frozen=True)
class RunResult:
    """What one recalculation did, and how long it took."""

    window_days: int
    processed: int
    assigned: int
    unmatched: int
    reassigned: int
    cleared: int
    seconds: float

    @property
    def changed_nothing(self) -> bool:
        """True when the run wrote nothing — and so audited nothing."""
        return self.reassigned == 0 and self.cleared == 0


def parse_window(raw: str | None) -> int:
    """Read a window from form input, refusing what is not a usable one."""
    if raw is None:
        return DEFAULT_WINDOW_DAYS
    try:
        days = int(str(raw).strip())
    except ValueError:
        raise InvalidWindow(
            "The window is a number of days, for example 180."
        ) from None

    if not MIN_WINDOW_DAYS <= days <= MAX_WINDOW_DAYS:
        raise InvalidWindow(
            f"The window must be between {MIN_WINDOW_DAYS} and "
            f"{MAX_WINDOW_DAYS} days."
        )
    return days


def run(connection: Connection[Any], window_days: int) -> RunResult:
    """Recalculate every customer's segment over the window, and commit.

    Committed here rather than left to the route because the audit entries the
    triggers write are part of the run: a caller that forgot to commit would
    roll back the assignment and its own record of having made it.
    """
    started = time.perf_counter()
    counts = recalculate_segments(connection, window_days)
    connection.commit()
    elapsed = time.perf_counter() - started

    return RunResult(
        window_days=window_days,
        processed=counts.processed,
        assigned=counts.assigned,
        unmatched=counts.unmatched,
        reassigned=counts.reassigned,
        cleared=counts.cleared,
        seconds=elapsed,
    )
