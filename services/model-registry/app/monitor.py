"""The drift monitor: one pass writes one report for the published version.

A pass reads the published version's reference and the customers' current
profiles in one repeatable-read snapshot, judges them with `drift.py`, appends
the report, its variables, statistics, bins and any alert, and records the run in
the audit trail, all in one transaction. A pass for a window that already has a
report changes nothing and returns that report, so repeating it on the same data
gives the same result (ADR-0065, F23-09).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg

from . import audit, drift

LOGGER = logging.getLogger(__name__)
LOCK_KEY = 301001
KIND = "DATA_DRIFT"
RUN_HOUR_UTC = 3

_PUBLISHED = (
    "SELECT version_id, name, reference_distribution"
    " FROM models.model_version WHERE state = 'PUBLISHED'"
)
_EXISTING = (
    "SELECT report_id FROM models.drift_report"
    " WHERE version_id = %s AND kind = %s AND window_end = %s"
)
_VERSION_CHANGES = (
    "SELECT count(*) FROM models.model_publication"
    " WHERE published_at > %s AND published_at <= %s"
)
_PROFILES = (
    "SELECT label_code, r_days, f_count, m_total FROM api_rfm.current_profile_v1"
    " WHERE state = 'scored' AND computed_at > %s AND computed_at <= %s"
)
_PREVIOUS = (
    "SELECT report_id FROM models.drift_report"
    " WHERE version_id = %s AND kind = %s AND window_end < %s"
    " ORDER BY window_end DESC LIMIT 1"
)
_PREVIOUS_STATES = (
    "SELECT variable, state FROM models.drift_variable WHERE report_id = %s"
)


def boundary(now: datetime) -> datetime:
    """The latest scheduled instant at or before `now`: the end of the window."""
    moment = now.astimezone(UTC).replace(
        hour=RUN_HOUR_UTC, minute=0, second=0, microsecond=0
    )
    return moment if moment <= now else moment - timedelta(days=1)


def _digest(version_id: int, end: datetime, profiles: list[tuple[Any, ...]]) -> str:
    """What the run read, as 64 hex characters, order-independent."""
    body = json.dumps(
        [version_id, end.isoformat(), sorted(map(repr, profiles))], sort_keys=True
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _insert_report(
    connection: psycopg.Connection[Any],
    version_id: int,
    end: datetime,
    comparison: str,
    reason: str | None,
    customers: int,
    previous_id: int | None,
    previous_states: dict[str, str],
    variables: list[drift.Variable],
) -> int:
    start = end - timedelta(days=drift.WINDOW_DAYS)
    (report_id,) = connection.execute(
        "INSERT INTO models.drift_report (version_id, kind, comparison,"
        " not_compared_reason, window_start, window_end, customer_count,"
        " previous_report_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING report_id",
        (version_id, KIND, comparison, reason, start, end, customers, previous_id),
    ).fetchone()
    for item in variables:
        connection.execute(
            "INSERT INTO models.drift_variable (report_id, variable, state)"
            " VALUES (%s, %s, %s)",
            (report_id, item.variable, item.state),
        )
        for stat in item.statistics:
            connection.execute(
                "INSERT INTO models.drift_statistic (report_id, variable, statistic,"
                " value, p_value, degrees_of_freedom, level)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    report_id,
                    item.variable,
                    stat.statistic,
                    stat.value,
                    stat.p_value,
                    stat.degrees_of_freedom,
                    stat.level,
                ),
            )
        for position, bin_ in enumerate(item.bins, start=1):
            connection.execute(
                "INSERT INTO models.drift_bin (report_id, variable, position, label,"
                " lower_bound, upper_bound, reference_share, current_share)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    report_id,
                    item.variable,
                    position,
                    bin_.label,
                    bin_.lower,
                    bin_.upper,
                    bin_.reference_share,
                    bin_.current_share,
                ),
            )
        if drift.raises_alert(item.state, previous_states.get(item.variable)):
            connection.execute(
                "INSERT INTO models.drift_alert (report_id, variable,"
                " previous_report_id, review_code) VALUES (%s, %s, %s, %s)",
                (
                    report_id,
                    item.variable,
                    previous_id,
                    drift.review_code(item.variable),
                ),
            )
    return int(report_id)


def run_once(
    connection: psycopg.Connection[Any],
    now: datetime,
    correlation_id: str | None = None,
) -> int | None:
    """One pass in the caller's transaction. Returns the report's id, or None
    when no version is published. The caller commits."""
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    published = connection.execute(_PUBLISHED).fetchone()
    if published is None:
        return None
    version_id, _name, reference = published
    end = boundary(now)
    start = end - timedelta(days=drift.WINDOW_DAYS)
    found = connection.execute(_EXISTING, (version_id, KIND, end)).fetchone()
    if found:
        return int(found[0])
    previous = connection.execute(_PREVIOUS, (version_id, KIND, end)).fetchone()
    previous_id = int(previous[0]) if previous else None
    previous_states = (
        dict(connection.execute(_PREVIOUS_STATES, (previous_id,)).fetchall())
        if previous_id
        else {}
    )
    profiles: list[tuple[Any, ...]] = []
    reason = None
    changes = connection.execute(_VERSION_CHANGES, (start, end)).fetchone()[0]
    labels = reference["labels"]
    known = {label["label_code"] for label in labels}
    if changes:
        comparison, reason = "NOT_COMPARED", "VERSION_CHANGE"
    else:
        profiles = [
            row
            for row in connection.execute(_PROFILES, (start, end)).fetchall()
            if row[0] in known
        ]
        comparison = (
            "COMPARED" if len(profiles) >= drift.MIN_CUSTOMERS else "INSUFFICIENT_DATA"
        )
    variables: list[drift.Variable] = []
    if comparison == "COMPARED":
        n = len(profiles)
        variables.append(
            drift.judge(
                drift.SEGMENT_SHARES,
                drift.segment_bins(labels, [row[0] for row in profiles]),
                n,
                previous_states.get(drift.SEGMENT_SHARES),
            )
        )
        columns = {"R": 1, "F": 2, "M": 3}
        for feature in reference["features"]:
            name = feature["feature"]
            variables.append(
                drift.judge(
                    name,
                    drift.feature_bins(
                        feature, [row[columns[name]] for row in profiles]
                    ),
                    n,
                    previous_states.get(name),
                )
            )
    report_id = _insert_report(
        connection,
        version_id,
        end,
        comparison,
        reason,
        len(profiles),
        previous_id,
        previous_states,
        variables,
    )
    audit.drift_run(
        connection,
        event_id=uuid5(NAMESPACE_URL, f"drift:{version_id}:{KIND}:{end.isoformat()}"),
        version_id=version_id,
        window=(start, end),
        input_count=len(profiles),
        output_count=len(variables),
        digest=_digest(version_id, end, profiles),
        status="COMPLETED",
        correlation_id=correlation_id,
    )
    return report_id


def run(db: Any, now: datetime | None = None) -> int | None:
    """One pass under the monitor's own transaction, audited as FAILED when it
    cannot finish."""
    moment = now or datetime.now(UTC)
    try:
        with db.transaction() as connection:
            return run_once(connection, moment)
    except Exception:
        LOGGER.exception("drift pass failed")
        try:
            with db.transaction() as connection:
                published = connection.execute(_PUBLISHED).fetchone()
                if published is not None:
                    end = boundary(moment)
                    audit.drift_run(
                        connection,
                        event_id=uuid5(
                            NAMESPACE_URL,
                            f"drift:{published[0]}:{KIND}:{end.isoformat()}:failed",
                        ),
                        version_id=published[0],
                        window=(end - timedelta(days=drift.WINDOW_DAYS), end),
                        input_count=0,
                        output_count=0,
                        digest=None,
                        status="FAILED",
                        correlation_id=None,
                    )
        except Exception:  # noqa: BLE001
            LOGGER.exception("the failed drift pass could not be audited")
        return None


def serve(dsn: str, db: Any, interval_seconds: int, stop: threading.Event) -> None:
    """Run a pass every `interval_seconds` while holding the monitor's lock on a
    session of its own, so only one instance runs it."""
    while not stop.is_set():
        try:
            with psycopg.connect(dsn, autocommit=True) as lock:
                got = lock.execute(
                    "SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)
                ).fetchone()[0]
                if got:
                    run(db)
        except psycopg.Error:
            LOGGER.exception("the drift monitor could not reach the database")
        stop.wait(interval_seconds)


def start(
    dsn: str, db: Any, interval_seconds: int, stop: threading.Event
) -> threading.Thread:
    thread = threading.Thread(
        target=serve,
        args=(dsn, db, interval_seconds, stop),
        name="drift-monitor",
        daemon=True,
    )
    thread.start()
    return thread
