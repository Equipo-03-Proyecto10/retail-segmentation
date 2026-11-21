"""The verification recalculation (F22-08, ADR-0063, RN-05).

It answers one question: does the profile held incrementally equal what a full
recomputation from the net purchases would give? Everyone is recomputed under one rule
set at one instant, `as_of`, from one snapshot of the database. No profile is written
and no segment moves; the verification records how many customers it compared, which
differ and how, and is audited.

What "equal" means at `as_of`. A profile is not rewritten while nothing about the
customer moves (ADR-0063), so it is compared as the design promises it, not as if it
were measured now:

- R ages by itself every day and the profile is not rewritten for it. `r_days` is
  measured to the instant the profile was computed (`computed_at`), so the recomputed
  value is measured to that instant too, from the customer's latest net purchase.
  Everything else (F, M, the scores, the label) is compared as it stands at `as_of`:
  a profile not yet due (`next_change_at` empty or after `as_of`) holds them for every
  instant until then, so any difference is a missed event.
- A profile that fell due after the last pass completed is not compared. The pass owns
  it and moves it at its next run; it is not a defect of the incremental update. One
  that fell due at or before that pass was the pass's to move and is compared.
- A customer with net purchases and no profile row is `pending` and differs.

`customers_checked` counts the customers compared. A verification is read-only: it
reads one repeatable-read snapshot, so a sale committed while it runs cannot split it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from mosaiq_kit import ApiError
from psycopg import Connection, IsolationLevel
from psycopg.errors import UniqueViolation

from . import profile, rulesets, store

LOGGER = logging.getLogger(__name__)
# contracts/db/advisory-locks.md: one key, held on a session of its own while a
# verification runs. A RUNNING row with no holder was left by an instance that died.
LOCK_KEY = 299001
BATCH = 1000
FIELDS = (
    "state",
    "r_days",
    "f_count",
    "m_total",
    "r_score",
    "f_score",
    "m_score",
    "label_code",
)
CALCULATION_RUN = (
    "SELECT recorded_at FROM api_audit.record_event_v1("
    "%s, 'CALCULATION_RUN', 'SUCCESS', now(), %s, %s, %s, NULL::smallint,"
    " 'calculation', %s, NULL::bigint, %s, NULL, %s::jsonb)"
)


@dataclass(frozen=True, slots=True)
class Values:
    """The eight values the contract compares (`RfmValues`)."""

    state: str
    r_days: int | None
    f_count: int | None
    m_total: Decimal | None
    r_score: int | None
    f_score: int | None
    m_score: int | None
    label_code: str | None

    def as_tuple(self) -> tuple[object, ...]:
        return tuple(getattr(self, name) for name in FIELDS)


PENDING = Values("pending", None, None, None, None, None, None, None)


def differing(incremental: Values, recomputed: Values) -> list[str]:
    return [
        name
        for name in FIELDS
        if getattr(incremental, name) != getattr(recomputed, name)
    ]


def wire(values: Values) -> dict[str, Any]:
    """`RfmValues` as the contract serves it: money is a two-decimal string."""
    wired = {name: getattr(values, name) for name in FIELDS}
    if values.m_total is not None:
        wired["m_total"] = format(values.m_total, ".2f")
    return wired


def _values(row: tuple[Any, ...]) -> Values:
    return Values(*row)


def outcome(row: dict[str, Any]) -> str | None:
    if row["state"] != "COMPLETED":
        return None
    return "EQUIVALENT" if row["customers_different"] == 0 else "DIFFERENT"


def _recalculation(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        rid,
        trigger,
        state,
        rule_set_id,
        as_of,
        started,
        finished,
        by,
        checked,
        different,
        failure,
    ) = row
    item = {
        "recalculation_id": rid,
        "trigger": trigger,
        "state": state,
        "rule_set_id": rule_set_id,
        "as_of": as_of,
        "started_at": started,
        "finished_at": finished,
        "started_by": None if by is None else str(by),
        "customers_checked": checked,
        "customers_different": different,
        "failure_code": failure,
    }
    item["outcome"] = outcome(item)
    return item


def not_found() -> ApiError:
    return ApiError(
        404,
        "recalculation_not_found",
        "No recalculation has the requested recalculation_id.",
    )


def in_progress() -> ApiError:
    return ApiError(
        409,
        "recalculation_in_progress",
        "A verification recalculation is running; wait for it to finish.",
    )


def no_published_rule_set(detail: str) -> ApiError:
    return ApiError(409, "no_published_rule_set", detail)


def not_finished() -> ApiError:
    return ApiError(
        409,
        "recalculation_not_finished",
        "The recalculation has not completed, so it has no final differences.",
    )


def _default_rule_set(connection: Connection, in_force: int) -> int:
    """The published rule set: the one in force when it is published, else the most
    recently published."""
    rules = store.rule_set(connection, in_force)
    if rules is not None and rules.published:
        return in_force
    row = connection.execute(
        "SELECT rule_set_id FROM rfm.rule_set WHERE state = 'PUBLISHED'"
        " ORDER BY published_at DESC, rule_set_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise no_published_rule_set("No rule set is published.")
    return int(row[0])


def start(
    connection: Connection,
    *,
    trigger: str,
    started_by: str | None,
    rule_set_id: int | None,
    in_force: int,
) -> dict[str, Any]:
    """Record a verification RUNNING, in the caller's transaction, at one instant."""
    if rule_set_id is None:
        chosen = _default_rule_set(connection, in_force)
    else:
        rules = store.rule_set(connection, rule_set_id)
        if rules is None:
            raise rulesets.not_found()
        if not rules.published:
            raise no_published_rule_set(
                f"Rule set {rule_set_id} is a draft; a draft is never a reference."
            )
        chosen = rule_set_id
    try:
        with connection.transaction():
            row = connection.execute(
                "INSERT INTO rfm.recalculation (trigger_kind, rule_set_id, as_of,"
                " started_by) VALUES (%s, %s, clock_timestamp(), %s)"
                " RETURNING recalculation_id, trigger_kind, state, rule_set_id,"
                " as_of, started_at, finished_at, started_by, customers_checked,"
                " customers_different, failure_code",
                (trigger, chosen, started_by),
            ).fetchone()
    except UniqueViolation:
        raise in_progress() from None
    assert row is not None
    return _recalculation(row)


def one(connection: Connection, recalculation_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT recalculation_id, trigger_kind, state, rule_set_id, as_of,"
        " started_at, finished_at, started_by, customers_checked,"
        " customers_different, failure_code FROM rfm.recalculation"
        " WHERE recalculation_id = %s",
        (recalculation_id,),
    ).fetchone()
    if row is None:
        raise not_found()
    return _recalculation(row)


def listing(
    connection: Connection,
    *,
    state: str | None,
    trigger: str | None,
    rule_set_id: int | None,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    filters = {"state": state, "trigger": trigger, "rule_set": rule_set_id}
    rows = connection.execute(
        "SELECT recalculation_id, trigger_kind, state, rule_set_id, as_of,"
        " started_at, finished_at, started_by, customers_checked,"
        " customers_different, failure_code FROM rfm.recalculation"
        " WHERE (%(state)s::text IS NULL OR state = %(state)s::text)"
        " AND (%(trigger)s::text IS NULL OR trigger_kind = %(trigger)s::text)"
        " AND (%(rule_set)s::bigint IS NULL OR rule_set_id = %(rule_set)s::bigint)"
        " ORDER BY recalculation_id DESC LIMIT %(limit)s OFFSET %(offset)s",
        filters | {"limit": size, "offset": (page - 1) * size},
    ).fetchall()
    total = connection.execute(
        "SELECT count(*) FROM rfm.recalculation"
        " WHERE (%(state)s::text IS NULL OR state = %(state)s::text)"
        " AND (%(trigger)s::text IS NULL OR trigger_kind = %(trigger)s::text)"
        " AND (%(rule_set)s::bigint IS NULL OR rule_set_id = %(rule_set)s::bigint)",
        filters,
    ).fetchone()
    assert total is not None
    return [_recalculation(row) for row in rows], int(total[0])


def differences(
    connection: Connection, recalculation_id: int, *, page: int, size: int
) -> tuple[list[dict[str, Any]], int]:
    """The customers a finished verification found different, by customer id."""
    state = connection.execute(
        "SELECT state FROM rfm.recalculation WHERE recalculation_id = %s",
        (recalculation_id,),
    ).fetchone()
    if state is None:
        raise not_found()
    if state[0] != "COMPLETED":
        raise not_finished()
    rows = connection.execute(
        "SELECT customer_id, inc_state, inc_r_days, inc_f_count, inc_m_total,"
        " inc_r_score, inc_f_score, inc_m_score, inc_label_code, rec_state,"
        " rec_r_days, rec_f_count, rec_m_total, rec_r_score, rec_f_score,"
        " rec_m_score, rec_label_code FROM rfm.recalculation_difference"
        " WHERE recalculation_id = %s ORDER BY customer_id LIMIT %s OFFSET %s",
        (recalculation_id, size, (page - 1) * size),
    ).fetchall()
    total = connection.execute(
        "SELECT count(*) FROM rfm.recalculation_difference WHERE recalculation_id = %s",
        (recalculation_id,),
    ).fetchone()
    assert total is not None
    items = []
    for row in rows:
        before, after = _values(row[1:9]), _values(row[9:17])
        items.append(
            {
                "customer_id": str(row[0]),
                "fields": differing(before, after),
                "incremental": wire(before),
                "recomputed": wire(after),
            }
        )
    return items, int(total[0])


def _stored(
    connection: Connection, customers: list[UUID]
) -> dict[UUID, tuple[Any, ...]]:
    rows = connection.execute(
        "SELECT customer_id, state, r_days, f_count, m_total, r_score, f_score,"
        " m_score, label_code, computed_at, next_change_at"
        " FROM rfm.customer_profile WHERE customer_id = ANY(%s)",
        (customers,),
    ).fetchall()
    return {row[0]: row[1:] for row in rows}


def _batch(
    connection: Connection,
    customers: list[UUID],
    rules: profile.RuleSet,
    as_of: datetime,
    last_pass: datetime | None,
) -> tuple[int, list[tuple[UUID, Values, Values]]]:
    """Compare these customers; return how many were compared and who differs."""
    measured = store.measures_many(connection, customers, rules.window_days, as_of)
    held = _stored(connection, customers)
    empty = profile.Measures(0, Decimal("0.00"), None, None)
    checked = 0
    found: list[tuple[UUID, Values, Values]] = []
    for customer_id in customers:
        measures = measured.get(customer_id, empty)
        reference = profile.build(rules, measures, as_of)
        recomputed = Values(
            reference.state,
            reference.r_days,
            reference.f_count,
            reference.m_total,
            reference.r_score,
            reference.f_score,
            reference.m_score,
            reference.label_code,
        )
        stored = held.get(customer_id)
        if stored is None:
            if reference.state == "unassigned":
                checked += 1  # no profile and nothing to count: nothing to differ
                continue
            incremental = PENDING
        else:
            computed_at, due = stored[8], stored[9]
            if (
                due is not None
                and due <= as_of
                and last_pass is not None
                and due > last_pass
            ):
                # Fell due after the last pass completed: the next pass moves it.
                continue
            incremental = Values(*stored[:8])
            if (
                measures.last_at is not None
                and reference.state == "scored"
                and measures.last_at <= computed_at
            ):
                # R is measured to the profile's own instant (see the module doc).
                recomputed = replace(
                    recomputed,
                    r_days=int((computed_at - measures.last_at) // profile.ONE_DAY),
                )
        checked += 1
        if differing(incremental, recomputed):
            found.append((customer_id, incremental, recomputed))
    return checked, found


def _universe(
    connection: Connection,
    after: UUID | None,
    window_days: int,
    as_of: datetime,
    only: list[UUID] | None,
) -> list[UUID]:
    """The next customers to compare, in key order: everyone with a profile or with a
    net purchase in the window, or the named customers."""
    if only is not None:
        rows = connection.execute(
            "SELECT customer_id FROM unnest(%(only)s::uuid[]) AS c(customer_id)"
            " WHERE (%(after)s::uuid IS NULL OR customer_id > %(after)s::uuid)"
            " ORDER BY customer_id LIMIT %(limit)s",
            {"only": only, "after": after, "limit": BATCH},
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT customer_id FROM ("
            " SELECT customer_id FROM rfm.customer_profile"
            " UNION"
            " SELECT customer_id FROM api_sales.net_purchase_line_v1"
            "  WHERE occurred_at >= %(as_of)s - make_interval(days => %(days)s)"
            "    AND occurred_at <= %(as_of)s) AS everyone"
            " WHERE (%(after)s::uuid IS NULL OR customer_id > %(after)s::uuid)"
            " ORDER BY customer_id LIMIT %(limit)s",
            {"as_of": as_of, "days": window_days, "after": after, "limit": BATCH},
        ).fetchall()
    return [row[0] for row in rows]


def reference(
    connection: Connection, customer_id: UUID, rule_set_id: int, as_of: datetime
) -> Values:
    """One customer recomputed from the net purchases under a rule set at `as_of`,
    and written nowhere: the reference a profile is compared with."""
    rules = store.rule_set(connection, rule_set_id)
    if rules is None:
        raise rulesets.not_found()
    built = profile.build(
        rules, store.measures(connection, customer_id, rules.window_days, as_of), as_of
    )
    return Values(
        built.state,
        built.r_days,
        built.f_count,
        built.m_total,
        built.r_score,
        built.f_score,
        built.m_score,
        built.label_code,
    )


def _audit(
    connection: Connection,
    row: dict[str, Any],
    actor: tuple[str, str] | None,
    status: str,
    checked: int,
    different: int,
) -> None:
    details = {
        "calculation": "RFM",
        "status": status,
        "input_count": checked,
        "output_count": different,
        "parameters_digest": hashlib.sha256(
            json.dumps(
                {"rule_set_id": row["rule_set_id"], "as_of": row["as_of"].isoformat()},
                sort_keys=True,
            ).encode()
        ).hexdigest(),
    }
    connection.execute(
        CALCULATION_RUN,
        (
            str(uuid4()),
            "SYSTEM" if actor is None else "USER",
            None if actor is None else actor[0],
            None if actor is None else actor[1],
            f"rfm-verification-{row['recalculation_id']}",
            None,
            json.dumps(details),
        ),
    )


def _finish(
    db: Any,
    row: dict[str, Any],
    actor: tuple[str, str] | None,
    *,
    checked: int | None,
    different: int | None,
    failure: str | None,
) -> None:
    status = "FAILED" if failure else "COMPLETED"
    with db.transaction() as connection:
        connection.execute(
            "UPDATE rfm.recalculation SET state = %s,"
            " finished_at = greatest(clock_timestamp(), started_at),"
            " customers_checked = %s, customers_different = %s, failure_code = %s"
            " WHERE recalculation_id = %s AND state = 'RUNNING'",
            (status, checked, different, failure, row["recalculation_id"]),
        )
        _audit(connection, row, actor, status, checked or 0, different or 0)


def execute(
    db: Any,
    recalculation_id: int,
    actor: tuple[str, str] | None,
    *,
    only: list[UUID] | None = None,
) -> dict[str, Any]:
    """Run a RUNNING verification to its end and return the finished row.

    `actor` is the administrator's `(user_id, role)`, or None for the schedule. `only`
    narrows the customers compared and is for tests and the acceptance evidence: the
    operation and the schedule always verify everyone. A failure is recorded as FAILED
    and audited, never lost.
    """
    lock = psycopg.connect(db.dsn, autocommit=True)
    try:
        lock.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        with db.connection() as connection:
            row = one(connection, recalculation_id)
            connection.commit()
        if row["state"] != "RUNNING":
            return row
        checked = different = 0
        failure: str | None = None
        try:
            with db.connection() as connection:
                rules = store.rule_set(connection, row["rule_set_id"])
                assert rules is not None
                connection.commit()
            snapshot = psycopg.connect(db.dsn)
            try:
                snapshot.isolation_level = IsolationLevel.REPEATABLE_READ
                snapshot.read_only = True
                pass_row = snapshot.execute(
                    "SELECT max(as_of) FROM rfm.pass_run WHERE status = 'COMPLETED'"
                ).fetchone()
                last_pass = None if pass_row is None else pass_row[0]
                after: UUID | None = None
                while True:
                    customers = _universe(
                        snapshot, after, rules.window_days, row["as_of"], only
                    )
                    if not customers:
                        break
                    done, found = _batch(
                        snapshot, customers, rules, row["as_of"], last_pass
                    )
                    checked += done
                    different += len(found)
                    if found:
                        with db.transaction() as write:
                            _write(write, recalculation_id, found)
                    after = customers[-1]
            finally:
                snapshot.close()
        except Exception as error:  # noqa: BLE001 - recorded, then reported
            failure = (
                "source_unavailable"
                if isinstance(error, psycopg.OperationalError)
                else "internal_error"
            )
            LOGGER.exception("Verification %s failed", recalculation_id)
        if failure:
            _finish(db, row, actor, checked=None, different=None, failure=failure)
        else:
            _finish(db, row, actor, checked=checked, different=different, failure=None)
        with db.connection() as connection:
            finished = one(connection, recalculation_id)
            connection.commit()
        _alert(finished)
        return finished
    finally:
        lock.close()


def _write(
    connection: Connection,
    recalculation_id: int,
    found: list[tuple[UUID, Values, Values]],
) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO rfm.recalculation_difference (recalculation_id, customer_id,"
            " inc_state, inc_r_days, inc_f_count, inc_m_total, inc_r_score,"
            " inc_f_score, inc_m_score, inc_label_code, rec_state, rec_r_days,"
            " rec_f_count, rec_m_total, rec_r_score, rec_f_score, rec_m_score,"
            " rec_label_code) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, %s, %s)",
            [
                (recalculation_id, customer_id, *before.as_tuple(), *after.as_tuple())
                for customer_id, before, after in found
            ],
        )


def _alert(finished: dict[str, Any]) -> None:
    """The alert: a stable ERROR line an operator watches for. A scheduled run that
    finds a difference, or fails, is an incident; one an administrator asked for is
    answered in the response, so it is a warning."""
    scheduled = finished["trigger"] == "SCHEDULE"
    level = logging.ERROR if scheduled else logging.WARNING
    if finished["state"] == "FAILED":
        LOGGER.log(
            level,
            "rfm_verification_failed recalculation_id=%s failure_code=%s",
            finished["recalculation_id"],
            finished["failure_code"],
        )
    elif finished["customers_different"]:
        LOGGER.log(
            level,
            "rfm_verification_differences recalculation_id=%s rule_set_id=%s"
            " customers_different=%s customers_checked=%s",
            finished["recalculation_id"],
            finished["rule_set_id"],
            finished["customers_different"],
            finished["customers_checked"],
        )


def reap_orphans(db: Any) -> int:
    """Mark FAILED a verification left RUNNING by an instance that stopped: while one
    runs its instance holds the lock, so a lock that is free means nobody does."""
    lock = psycopg.connect(db.dsn, autocommit=True)
    try:
        row = lock.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()
        if row is None or not row[0]:
            return 0
        done = lock.execute(
            "UPDATE rfm.recalculation SET state = 'FAILED',"
            " finished_at = greatest(clock_timestamp(), started_at),"
            " failure_code = 'interrupted' WHERE state = 'RUNNING'"
            " RETURNING recalculation_id"
        ).fetchall()
        return len(done)
    finally:
        lock.close()


def next_start(now: datetime, weekday: int, at: time) -> datetime:
    """The next instant the UTC clock reads `at` on `weekday` (0 is Monday),
    strictly after `now`."""
    today = now.astimezone(UTC).date()
    moment = datetime.combine(today, at, tzinfo=UTC) + timedelta(
        days=(weekday - today.weekday()) % 7
    )
    return moment if moment > now else moment + timedelta(days=7)


class WeeklyVerification(threading.Thread):
    """Starts a verification each week on its weekday and time, as the system."""

    def __init__(self, db: Any, in_force: int, weekday: int, at: time) -> None:
        super().__init__(name="rfm-engine-verification", daemon=True)
        self.db = db
        self.in_force = in_force
        self.weekday = weekday
        self.at = at
        self.stopping = threading.Event()

    def stop(self) -> None:
        self.stopping.set()

    def once(self) -> dict[str, Any] | None:
        """One scheduled verification; None when one is already running."""
        reap_orphans(self.db)
        try:
            with self.db.transaction() as connection:
                row = start(
                    connection,
                    trigger="SCHEDULE",
                    started_by=None,
                    rule_set_id=None,
                    in_force=self.in_force,
                )
        except ApiError as error:
            LOGGER.error("rfm_verification_not_started code=%s", error.code)
            return None
        return execute(self.db, row["recalculation_id"], None)

    def run(self) -> None:
        while not self.stopping.is_set():
            now = datetime.now(UTC)
            wait = (next_start(now, self.weekday, self.at) - now).total_seconds()
            if self.stopping.wait(wait):
                break
            try:
                self.once()
            except Exception:  # noqa: BLE001
                LOGGER.exception("The scheduled verification could not run")
