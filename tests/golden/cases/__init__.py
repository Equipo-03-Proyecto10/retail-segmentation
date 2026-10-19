"""The case sets, one module per port, and what they share.

A case is an id, the input it records, and a `run` that calls the web on a
connection and returns a normalized outcome. `run` never commits: the capture
wraps every call in an owner that rolls the case back (capture.py). After a
refusal the transaction may be aborted, so `run` reads nothing more once the
web has refused.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import psycopg

# Campaign dates in the seed are absolute (2026), so the business date the
# transition is judged on is fixed too; otherwise a seeded draft would change
# outcome on the day its end date passed.
CAMPAIGN_TODAY = date(2026, 10, 16)


@dataclass(frozen=True)
class Context:
    """What every case set may rely on, read once before any case runs.

    `anchor` is the seed's own "now": the run_at of the newest seeded
    segmentation run, which 02_seed_30_per_table.sql stamps with now() when it
    loads, like every relative date it writes. A time taken from it keeps a
    case's meaning on a database loaded on any day.
    """

    anchor: datetime
    today: date = CAMPAIGN_TODAY

    def before_anchor(self, days: int) -> datetime:
        return self.anchor - timedelta(days=days)


@dataclass(frozen=True)
class Case:
    id: str
    input: Mapping[str, Any]
    run: Callable[[psycopg.Connection], dict[str, Any]]
    service_differs: str | None = None


def accepted(**observed: Any) -> dict[str, Any]:
    """The web accepted; `observed` is what it left behind, read in the case."""
    outcome: dict[str, Any] = {"outcome": "accepted"}
    if observed:
        outcome["observed"] = observed
    return outcome


def refused(error: BaseException) -> dict[str, Any]:
    """The web refused with an exception.

    A database error is reduced to its SQLSTATE, constraint and primary
    message: its full text carries the failing row, which holds generated ids
    and could hold a password hash.
    """
    if isinstance(error, psycopg.Error):
        diag = error.diag
        return {
            "outcome": "refused",
            "error": type(error).__name__,
            "sqlstate": diag.sqlstate,
            "constraint": diag.constraint_name,
            "reason": diag.message_primary,
        }
    outcome: dict[str, Any] = {
        "outcome": "refused",
        "error": type(error).__name__,
        "reason": str(error),
    }
    field = getattr(error, "field", None)
    if field is not None:
        outcome["field"] = field
    return outcome


def invalid(errors: Mapping[str, str]) -> dict[str, Any]:
    """A form validation refused; one message per field."""
    if not errors:
        return accepted()
    return {"outcome": "refused", "errors": dict(sorted(errors.items()))}


def one(connection: psycopg.Connection, query: str, params: tuple = ()) -> Any:
    """The single value of a single-row read."""
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    return None if row is None else row[0]
