"""The scheduled pass: recompute the customers whose profile changed with no event.

A profile moves with the clock alone when the oldest purchase leaves the window or
the recency passes a boundary, and no sale names such a customer (ADR-0063). Each
profile records that instant in `next_change_at`, so the pass reads the customers
whose instant has come, through the index, and recomputes only them.

A pass measures every customer from one instant, `as_of`, fixed when it starts and
kept in `rfm.pass_run`. A customer it has recomputed is no longer due, so a pass
that stops and starts again finds only the customers left and, measuring from the
same instant, writes what one uninterrupted pass would. Each customer is its own
transaction, with the pass's counts, so a crash costs at most the customer in hand.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection

from . import recompute, store

LOGGER = logging.getLogger(__name__)
# contracts/db/advisory-locks.md: one key, held on a session of its own for as long
# as a pass runs, so two instances never run one at once.
LOCK_KEY = 297001
BATCH = 500
# A customer that fails is skipped and tried again by the next pass; this many in a
# row mean the trouble is not the customer, and the pass stops to be retried.
CONSECUTIVE_FAILURES = 5
RETRY_FIRST = 60.0
RETRY_MAX = 1800.0


@dataclass(frozen=True, slots=True)
class Run:
    """One row of rfm.pass_run."""

    pass_run_id: int
    rule_set_id: int
    as_of: datetime
    status: str
    started_at: datetime
    finished_at: datetime | None
    customers_examined: int
    customers_changed: int


def _run(row: tuple[Any, ...]) -> Run:
    return Run(*row)


def _unfinished(connection: Connection) -> Run | None:
    row = connection.execute(
        "SELECT pass_run_id, rule_set_id, as_of, status, started_at, finished_at,"
        " customers_examined, customers_changed"
        " FROM rfm.pass_run WHERE status = 'RUNNING'"
    ).fetchone()
    return None if row is None else _run(row)


def _begin(connection: Connection, rule_set_id: int) -> Run:
    """Resume the pass left RUNNING, or start one measuring from now."""
    with connection.transaction():
        left = _unfinished(connection)
        if left is not None:
            LOGGER.info("Resuming pass %s from %s", left.pass_run_id, left.as_of)
            return left
        row = connection.execute(
            "INSERT INTO rfm.pass_run (rule_set_id, as_of)"
            " VALUES (%s, clock_timestamp())"
            " RETURNING pass_run_id, rule_set_id, as_of, status, started_at,"
            " finished_at, customers_examined, customers_changed",
            (rule_set_id,),
        ).fetchone()
    assert row is not None
    return _run(row)


def _due(connection: Connection, as_of: datetime, after: UUID | None) -> list[UUID]:
    """The next customers whose instant has come, in key order so a batch never
    returns to a customer it has seen."""
    if after is None:
        rows = connection.execute(
            "SELECT customer_id FROM rfm.customer_profile"
            " WHERE next_change_at IS NOT NULL AND next_change_at <= %s"
            " ORDER BY customer_id LIMIT %s",
            (as_of, BATCH),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT customer_id FROM rfm.customer_profile"
            " WHERE next_change_at IS NOT NULL AND next_change_at <= %s"
            " AND customer_id > %s ORDER BY customer_id LIMIT %s",
            (as_of, after, BATCH),
        ).fetchall()
    return [row[0] for row in rows]


def _refresh(
    db: Any,
    run: Run,
    customer_id: UUID,
    version: recompute.Version | None = None,
) -> bool | None:
    """Recompute one customer at the pass's instant and count it, in one
    transaction. Returns whether a captured value changed, or None when the customer
    was not due any more and was left alone."""
    with db.transaction() as connection:
        store.lock(connection, customer_id)
        # The batch was read before this lock was taken. An event that recomputed the
        # customer in between moved `next_change_at` to a later instant, and the pass,
        # measuring from its older `as_of`, would write the profile back: the
        # customer's `computed_at` would go backwards and a spurious change would reach
        # the outbox. So the profile is read again here, and only a customer still due
        # at the pass's instant is recomputed.
        row = connection.execute(
            "SELECT next_change_at FROM rfm.customer_profile WHERE customer_id = %s",
            (customer_id,),
        ).fetchone()
        if row is None or row[0] is None or row[0] > run.as_of:
            return None
        before = store.current(connection, customer_id)
        result = recompute.customer(
            connection,
            customer_id,
            run.rule_set_id,
            as_of=run.as_of,
            applying=(
                None
                if version is None
                else recompute.Applying(
                    version, "AGEING", recompute.pass_batch(run.pass_run_id)
                )
            ),
        )
        changed = before is None or tuple(before) != result.values()
        connection.execute(
            "UPDATE rfm.pass_run SET customers_examined = customers_examined + 1,"
            " customers_changed = customers_changed + %s WHERE pass_run_id = %s",
            (1 if changed else 0, run.pass_run_id),
        )
    return changed


def run_pass(
    db: Any, rule_set_id: int, version: recompute.Version | None = None
) -> Run | None:
    """Run one pass to its end, or resume the one an instance left RUNNING.

    Returns the finished run, or None when another instance holds the pass. The lock
    is on a connection of its own, as the outbox relay's is, so a pass takes one
    connection of the pool at a time and leaves the rest to the consumers and the
    requests.
    """
    lock = psycopg.connect(db.dsn, autocommit=True)
    try:
        row = lock.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()
        if row is None or not row[0]:
            LOGGER.info("Another instance runs the pass; this one stands down")
            return None
        with db.connection() as work:
            # Before a run is recorded: a refused pass must not leave one RUNNING.
            recompute.require_published(work, rule_set_id)
            work.commit()
            run = _begin(work, rule_set_id)
        if version is not None and run.rule_set_id != version.rule_set_id:
            # A pass left RUNNING keeps the rule set it began under. The version now
            # published holds another, so what this pass writes is not scored under
            # it, and it is no assignment under that version (ADR-0063).
            LOGGER.warning(
                "Pass %s measures under rule set %s, the published version holds %s:"
                " its changes are not applied to the segment history",
                run.pass_run_id,
                run.rule_set_id,
                version.rule_set_id,
            )
            version = None
        after: UUID | None = None
        failed = streak = 0
        while True:
            with db.connection() as work:
                batch = _due(work, run.as_of, after)
            if not batch:
                break
            for customer_id in batch:
                try:
                    if version is None:
                        _refresh(db, run, customer_id)
                    else:
                        _refresh(db, run, customer_id, version)
                    streak = 0
                except Exception:  # noqa: BLE001 - one customer must not stop the pass
                    failed += 1
                    streak += 1
                    LOGGER.exception("Customer %s could not be recomputed", customer_id)
                    if streak >= CONSECUTIVE_FAILURES:
                        raise
            after = batch[-1]
        with db.transaction() as work:
            done = work.execute(
                "UPDATE rfm.pass_run SET status = 'COMPLETED',"
                " finished_at = greatest(clock_timestamp(), started_at)"
                " WHERE pass_run_id = %s RETURNING pass_run_id, rule_set_id, as_of,"
                " status, started_at, finished_at, customers_examined,"
                " customers_changed",
                (run.pass_run_id,),
            ).fetchone()
        assert done is not None
        finished = _run(done)
        LOGGER.info(
            "Pass %s examined %s customers, changed %s and skipped %s that failed",
            finished.pass_run_id,
            finished.customers_examined,
            finished.customers_changed,
            failed,
        )
        return finished
    finally:
        lock.close()  # closing the session releases the advisory lock


def next_start(now: datetime, at: time) -> datetime:
    """The next instant the clock reads `at` in UTC, strictly after `now`."""
    moment = datetime.combine(now.astimezone(UTC).date(), at, tzinfo=UTC)
    return moment if moment > now else moment + timedelta(days=1)


class DailyPass(threading.Thread):
    """Runs the pass at `at` UTC every day, and once at start to finish a pass an
    earlier instance left RUNNING. A pass that fails is retried with a backoff, not
    left to the next day."""

    def __init__(
        self,
        db: Any,
        rule_set_id: int | None,
        at: time,
        retry_first: float = RETRY_FIRST,
        retry_max: float = RETRY_MAX,
        default_rule_set_id: int = 1,
    ):
        super().__init__(name="rfm-engine-daily-pass", daemon=True)
        self.db = db
        self.rule_set_id = rule_set_id
        self.default_rule_set_id = default_rule_set_id
        self.at = at
        self.retry_first = retry_first
        self.retry_max = retry_max
        self.stopping = threading.Event()

    def stop(self) -> None:
        self.stopping.set()

    def _in_force(self) -> tuple[int, recompute.Version | None]:
        """The rule set to measure under and, when it is the published version's, the
        version to record the changes against. A pass built with a rule set of its
        own measures under it and applies nothing."""
        if self.rule_set_id is not None:
            return self.rule_set_id, None
        with self.db.connection() as connection:
            version = recompute.in_force(connection, self.default_rule_set_id)
            connection.commit()
        return version.rule_set_id, version

    def _pass(self) -> None:
        rule_set_id, version = self._in_force()
        if version is None:
            run_pass(self.db, rule_set_id)
        else:
            run_pass(self.db, rule_set_id, version)

    def _attempt(self) -> bool:
        """One pass. True when it ended, or another instance has it; False when it
        failed."""
        try:
            with self.db.connection() as connection:
                resumed = _unfinished(connection) is not None
                connection.commit()
            self._pass()
            if resumed:
                # A resumed pass measures from the instant it began, perhaps hours
                # ago, so what fell due since waits for the next day. One that starts
                # now measures from now.
                self._pass()
            return True
        except Exception:  # noqa: BLE001
            LOGGER.exception("The scheduled pass failed; it is retried")
            return False

    def _until_done(self) -> None:
        delay = self.retry_first
        while not self.stopping.is_set():
            if self._attempt():
                return
            if self.stopping.wait(delay):
                return
            delay = min(delay * 2, self.retry_max)

    def run(self) -> None:
        try:
            with self.db.connection() as connection:
                left = _unfinished(connection)
                connection.commit()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not look for an unfinished pass")
            left = None
        if left is not None:
            self._until_done()
        while not self.stopping.is_set():
            now = datetime.now(UTC)
            if self.stopping.wait((next_start(now, self.at) - now).total_seconds()):
                break
            self._until_done()
