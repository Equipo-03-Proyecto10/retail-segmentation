"""The outcome test: what the segments predict, and whether that changed (F23-11).

ADR-0065 adopted one predictive relation: the probability that a customer repurchases
within `HORIZON` given the customer's segment. ADR-0089 says how it is read and stored.
The published version's tenure starts at its latest publication or rollback, and `t0`
is the first 03:00 UTC at or after it. The cohort at `t0` is the reference, and the
cohorts at `t0 + k * HORIZON` are tested once their outcome window is in the past.
Each segment is compared with itself, so a change in how many customers a segment
holds cannot pass the test, and only a change in what a segment predicts can.

The statistics are pure functions (`judge`), so the same counts give the same result.
A pass (`run_once`) reads every cohort it needs in one repeatable-read snapshot, then
writes each new cohort's report, its rows, any alert and its audit event in a
transaction of its own, oldest first, so each one's persistence sees the one before.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg

from . import audit, monitor, store

KIND = "OUTCOME_TEST"
VARIABLE = "REPURCHASE_RATE"
RELATION = "REPURCHASE_GIVEN_SEGMENT"
HORIZON = timedelta(days=30)
ALPHA = 0.05
MIN_DIFFERENCE_POINTS = 5
MIN_CUSTOMERS = 100
REVIEW_CODE = "REVIEW_PREDICTIVE_RELATION"
CLAIM = "CONCEPT_DRIFT"

_PUBLISHED = (
    "SELECT v.version_id, max(p.published_at) FROM models.model_version AS v"
    " JOIN models.model_publication AS p USING (version_id)"
    " WHERE v.state = 'PUBLISHED' GROUP BY v.version_id"
)
# A customer is in the cohort at `as_of` when the assignment open at that instant has
# a label and was written under this version; they repurchased when a net purchase
# line of theirs falls in (as_of, end]. Net of reversals (ADR-0062).
_COHORT = (
    "SELECT a.label_code, count(*)::int,"
    " count(*) FILTER (WHERE EXISTS ("
    "   SELECT 1 FROM api_sales.net_purchase_line_v1 AS n"
    "   WHERE n.customer_id = a.customer_id"
    "   AND n.occurred_at > %(as_of)s AND n.occurred_at <= %(end)s))::int"
    " FROM api_segments.assignment_interval_v1 AS a"
    " JOIN models.run_version AS rv ON rv.run_id = a.run_id"
    " WHERE rv.version_id = %(version)s AND a.label_code IS NOT NULL"
    " AND a.valid_from <= %(as_of)s"
    " AND (a.valid_to IS NULL OR a.valid_to > %(as_of)s)"
    " GROUP BY a.label_code"
)
_EXISTING = (
    "SELECT window_end FROM models.drift_report"
    " WHERE version_id = %s AND kind = %s AND window_end >= %s"
)
_CHANGES = (
    "SELECT count(*) FROM models.model_publication"
    " WHERE published_at > %s AND published_at <= %s"
)
_PREVIOUS = (
    "SELECT r.report_id, coalesce(array_agg(s.label_code)"
    " FILTER (WHERE s.result = 'PASSED'), '{}')"
    " FROM models.drift_report AS r"
    " LEFT JOIN models.drift_outcome_segment AS s USING (report_id)"
    " WHERE r.version_id = %s AND r.kind = %s AND r.window_end = %s"
    " GROUP BY r.report_id"
)


@dataclass(frozen=True, slots=True)
class Side:
    customers: int
    repurchasers: int

    @property
    def rate(self) -> float | None:
        return self.repurchasers / self.customers if self.customers else None


@dataclass(frozen=True, slots=True)
class Segment:
    label_code: str
    reference: Side
    current: Side
    z: float | None
    p_value: float | None
    adjusted_p_value: float | None
    result: str
    persisted: bool


def first_cohort(published_at: datetime) -> datetime:
    """`t0`: the first scheduled instant at or after the tenure's start."""
    moment = published_at.astimezone(UTC).replace(
        hour=monitor.RUN_HOUR_UTC, minute=0, second=0, microsecond=0
    )
    return moment if moment >= published_at else moment + timedelta(days=1)


def matured(t0: datetime, now: datetime) -> list[datetime]:
    """The as-of dates of every current cohort whose outcome is known at `now`."""
    found, k = [], 1
    while t0 + (k + 1) * HORIZON <= now:
        found.append(t0 + k * HORIZON)
        k += 1
    return found


def z_test(reference: Side, current: Side) -> tuple[float, float]:
    """The pooled two-proportion z-test, current against reference, and its two-sided
    p-value. Equal rates of 0 or 1 have no spread and differ by nothing: z = 0."""
    pooled = (reference.repurchasers + current.repurchasers) / (
        reference.customers + current.customers
    )
    spread = pooled * (1 - pooled) * (1 / reference.customers + 1 / current.customers)
    if spread <= 0:
        return 0.0, 1.0
    z = ((current.rate or 0.0) - (reference.rate or 0.0)) / math.sqrt(spread)
    return z, math.erfc(abs(z) / math.sqrt(2))


def holm(p_values: list[float]) -> list[float]:
    """Holm's step-down adjustment: each p-value times the number not yet rejected,
    never below the one before it in ascending order, never above 1."""
    order = sorted(range(len(p_values)), key=lambda i: (p_values[i], i))
    adjusted = [0.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def judge(
    labels: list[str],
    reference: dict[str, Side],
    current: dict[str, Side],
    passed_before: set[str],
) -> list[Segment]:
    """Every segment of the version, tested where both sides have the minimum. A
    segment passes when its Holm-adjusted p-value is under alpha and its rate moved by
    at least the minimum difference; it persists when it also passed in the cohort
    before."""
    empty = Side(0, 0)
    sides = [
        (label, reference.get(label, empty), current.get(label, empty))
        for label in labels
    ]
    tested = [
        (label, ref, cur)
        for label, ref, cur in sides
        if ref.customers >= MIN_CUSTOMERS and cur.customers >= MIN_CUSTOMERS
    ]
    tests = {label: z_test(ref, cur) for label, ref, cur in tested}
    adjusted = dict(zip(tests, holm([p for _, p in tests.values()]), strict=True))
    found = []
    for label, ref, cur in sides:
        if label not in tests:
            found.append(
                Segment(label, ref, cur, None, None, None, "NOT_TESTED", False)
            )
            continue
        z, p_value = tests[label]
        points = 100 * ((cur.rate or 0.0) - (ref.rate or 0.0))
        passed = adjusted[label] < ALPHA and abs(points) >= MIN_DIFFERENCE_POINTS
        found.append(
            Segment(
                label,
                ref,
                cur,
                z,
                p_value,
                adjusted[label],
                "PASSED" if passed else "NOT_PASSED",
                passed and label in passed_before,
            )
        )
    return found


def state(segments: list[Segment]) -> str:
    """ALARM when a segment passed in this cohort and the one before; ALARM_PENDING
    when one passed for the first time; NORMAL otherwise. Only ALARM alerts."""
    if any(s.persisted for s in segments):
        return "ALARM"
    if any(s.result == "PASSED" for s in segments):
        return "ALARM_PENDING"
    return "NORMAL"


def _percent(rate: float | None) -> str:
    return "no customers" if rate is None else f"{rate * 100:.1f}%"


def review_text(segments: list[Segment]) -> str:
    """English text naming each segment that persisted and its two rates."""
    parts = [
        f"Customers of {s.label_code} repurchased within {HORIZON.days} days at"
        f" {_percent(s.current.rate)} against {_percent(s.reference.rate)} in the"
        " reference cohort"
        for s in segments
        if s.persisted
    ]
    tail = (
        ", in two consecutive matured cohorts. Review the campaigns and"
        " recommendations that rely on these segments, and consider a draft version"
        " fitted on current data."
    )
    body = "; ".join(parts)
    room = 500 - len(tail)
    if len(body) > room:
        body = body[: room - 1].rstrip("; ") + "…"
    return body + tail


# ---------- one pass ----------


def audit_failure(db: Any, now: datetime) -> None:
    """A pass that could not finish is still in the audit trail, as FAILED."""
    with db.transaction() as connection:
        published = connection.execute(_PUBLISHED).fetchone()
        if published is None:
            return
        end = monitor.boundary(now)
        audit.drift_run(
            connection,
            event_id=uuid5(
                NAMESPACE_URL, f"drift:{published[0]}:{KIND}:{end.isoformat()}:failed"
            ),
            version_id=published[0],
            window=(end - HORIZON, end),
            input_count=0,
            output_count=0,
            digest=None,
            status="FAILED",
            correlation_id=None,
        )


def _cohort(
    connection: psycopg.Connection[Any], version_id: int, as_of: datetime
) -> dict[str, Side]:
    rows = connection.execute(
        _COHORT, {"version": version_id, "as_of": as_of, "end": as_of + HORIZON}
    ).fetchall()
    return {label: Side(customers, bought) for label, customers, bought in rows}


def _read(connection: psycopg.Connection[Any], now: datetime) -> dict[str, Any] | None:
    """Everything a pass judges, from one read-only snapshot. None when no version is
    published or no cohort has matured."""
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        published = connection.execute(_PUBLISHED).fetchone()
        if published is None:
            return None
        version_id, since = published
        t0 = first_cohort(since)
        cohorts = matured(t0, now)
        if not cohorts:
            return None
        done = {
            end
            for (end,) in connection.execute(
                _EXISTING, (version_id, KIND, cohorts[0] + HORIZON)
            ).fetchall()
        }
        todo = [t for t in cohorts if t + HORIZON not in done]
        if not todo:
            return None
        read: dict[str, Any] = {
            "version_id": version_id,
            "t0": t0,
            "labels": store.label_vocabulary(connection),
            "reference": _cohort(connection, version_id, t0),
            "cohorts": [],
        }
        before = connection.execute(_PREVIOUS, (version_id, KIND, todo[0])).fetchone()
        read["before"] = (before[0], set(before[1])) if before else None
        for as_of in todo:
            (changes,) = connection.execute(_CHANGES, (t0, as_of + HORIZON)).fetchone()
            read["cohorts"].append(
                {
                    "as_of": as_of,
                    "changes": changes,
                    "current": (
                        {} if changes else _cohort(connection, version_id, as_of)
                    ),
                }
            )
    return read


def _write(
    connection: psycopg.Connection[Any],
    version_id: int,
    as_of: datetime,
    comparison: str,
    customers: int,
    previous_id: int | None,
    segments: list[Segment],
    correlation_id: str | None,
) -> int | None:
    end = as_of + HORIZON
    with connection.transaction():
        found = connection.execute(
            "SELECT report_id FROM models.drift_report"
            " WHERE version_id = %s AND kind = %s AND window_end = %s",
            (version_id, KIND, end),
        ).fetchone()
        if found:
            return int(found[0])  # another pass wrote this cohort meanwhile
        (report_id,) = connection.execute(
            "INSERT INTO models.drift_report (version_id, kind, comparison,"
            " not_compared_reason, window_start, window_end, customer_count,"
            " previous_report_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING report_id",
            (
                version_id,
                KIND,
                comparison,
                "VERSION_CHANGE" if comparison == "NOT_COMPARED" else None,
                as_of,
                end,
                customers,
                previous_id,
            ),
        ).fetchone()
        if comparison == "COMPARED":
            current_state = state(segments)
            connection.execute(
                "INSERT INTO models.drift_variable (report_id, variable, state)"
                " VALUES (%s, %s, %s)",
                (report_id, VARIABLE, current_state),
            )
            for s in segments:
                connection.execute(
                    "INSERT INTO models.drift_outcome_segment (report_id, label_code,"
                    " reference_customers, reference_repurchasers, current_customers,"
                    " current_repurchasers, z, p_value, adjusted_p_value, result,"
                    " persisted) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        report_id,
                        s.label_code,
                        s.reference.customers,
                        s.reference.repurchasers,
                        s.current.customers,
                        s.current.repurchasers,
                        s.z,
                        s.p_value,
                        s.adjusted_p_value,
                        s.result,
                        s.persisted,
                    ),
                )
            if current_state == "ALARM":
                connection.execute(
                    "INSERT INTO models.drift_alert (report_id, variable,"
                    " previous_report_id, claim, review_code)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (report_id, VARIABLE, previous_id, CLAIM, REVIEW_CODE),
                )
        audit.drift_run(
            connection,
            event_id=uuid5(
                NAMESPACE_URL, f"drift:{version_id}:{KIND}:{end.isoformat()}"
            ),
            version_id=version_id,
            window=(as_of, end),
            input_count=customers,
            output_count=len(segments),
            digest=None,
            status="COMPLETED",
            correlation_id=correlation_id,
        )
    return int(report_id)


def run_once(
    connection: psycopg.Connection[Any],
    now: datetime,
    correlation_id: str | None = None,
) -> list[int]:
    """One pass on a connection with no transaction open: a report for every matured
    cohort that has none, oldest first. Returns their ids."""
    read = _read(connection, now)
    if read is None:
        return []
    version_id, labels = read["version_id"], list(read["labels"])
    reference: dict[str, Side] = read["reference"]
    before = read["before"]
    written = []
    for cohort in read["cohorts"]:
        current: dict[str, Side] = cohort["current"]
        names = labels + sorted(
            (set(reference) | set(current)) - set(labels)
        )  # a label the vocabulary lost is still judged
        previous_id, passed_before = before if before else (None, set())
        customers = sum(side.customers for side in current.values())
        segments: list[Segment] = []
        if cohort["changes"]:
            comparison = "NOT_COMPARED"
        else:
            segments = judge(
                [n for n in names if n in reference or n in current],
                reference,
                current,
                passed_before,
            )
            comparison = (
                "COMPARED"
                if any(s.result != "NOT_TESTED" for s in segments)
                else "INSUFFICIENT_DATA"
            )
            if comparison != "COMPARED":
                segments = []
        report_id = _write(
            connection,
            version_id,
            cohort["as_of"],
            comparison,
            customers,
            previous_id,
            segments,
            correlation_id,
        )
        if report_id is None:
            continue
        written.append(report_id)
        before = (report_id, {s.label_code for s in segments if s.result == "PASSED"})
    return written
