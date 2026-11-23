"""The scheduled pass recomputes the customers the clock moved (F22-07, ADR-0063)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from datetime import time as clock

import psycopg
import pytest
from mosaiq_kit.database import Database

from app import ageing, recompute
from tests.conftest import Shop, days_ago

RULE_SET = 1
pytestmark = pytest.mark.postgres


@pytest.fixture
def db(service_url: str) -> Iterator[Database]:
    database = Database(service_url)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture(autouse=True)
def no_runs(owner: psycopg.Connection, db: Database) -> Iterator[None]:
    """The record of passes is the test's own: nothing else may be RUNNING. The
    population is settled first, so the seeded customers with purchases and no profile
    yet (which the pass also gives one, ADR-0086) are not counted in the test's pass."""
    owner.execute("DELETE FROM rfm.pass_run")
    ageing.run_pass(db, RULE_SET)
    owner.execute("DELETE FROM rfm.pass_run")
    yield
    owner.execute("DELETE FROM rfm.pass_run")


class Died(BaseException):
    """A process that is killed: no `except Exception` in the pass sees it."""


def _recompute(
    service: psycopg.Connection, customer_id: uuid.UUID, as_of: datetime
) -> None:
    with service.transaction():
        recompute.customer(service, customer_id, RULE_SET, as_of)


def _aged(shop: Shop, service: psycopg.Connection, now: datetime) -> uuid.UUID:
    """A customer whose one purchase was in the window when the profile was written
    and has left it since: the profile is due and is no longer right."""
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 200))
    _recompute(service, customer, days_ago(now, 100))
    return customer


def _profile(owner: psycopg.Connection, customer: uuid.UUID) -> tuple[object, ...]:
    row = owner.execute(
        "SELECT state, f_count, xmin::text, computed_at, next_change_at"
        " FROM rfm.customer_profile WHERE customer_id = %s",
        (customer,),
    ).fetchone()
    assert row is not None
    return row


def test_the_pass_recomputes_the_customers_whose_instant_has_come(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
) -> None:
    due = _aged(shop, service, now)
    steady = shop.customer()
    shop.sale(steady, days_ago(now, 5))
    _recompute(service, steady, now)
    assert _profile(owner, due)[0] == "scored"
    untouched = _profile(owner, steady)

    run = ageing.run_pass(db, RULE_SET)

    assert run is not None and run.status == "COMPLETED"
    assert (run.customers_examined, run.customers_changed) == (1, 1)
    assert _profile(owner, due)[:2] == ("unassigned", 0)
    assert _profile(owner, steady) == untouched
    assert shop.events(due) == ["INSERT", "UPDATE"]


def test_a_customer_with_purchases_and_no_profile_gets_one_from_the_pass(
    shop: Shop,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
) -> None:
    """No event reached them (a seeded customer, or a first sale whose event was
    lost): the pass writes their first profile, so a verification after it does not
    report them `pending` (ADR-0086). A purchase out of the window gives none."""
    lost = shop.customer()
    shop.sale(lost, days_ago(now, 10))
    gone = shop.customer()
    shop.sale(gone, days_ago(now, 400))
    owner.execute(
        "DELETE FROM rfm.customer_profile WHERE customer_id = ANY(%s)", ([lost, gone],)
    )

    run = ageing.run_pass(db, RULE_SET)

    assert run is not None and run.status == "COMPLETED"
    assert (run.customers_examined, run.customers_changed) == (1, 1)
    assert _profile(owner, lost)[:2] == ("scored", 1)
    assert (
        owner.execute(
            "SELECT 1 FROM rfm.customer_profile WHERE customer_id = %s", (gone,)
        ).fetchone()
        is None
    )


def test_a_due_customer_whose_values_hold_is_examined_and_not_changed(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 170))
    _recompute(service, customer, now)
    owner.execute(
        "UPDATE rfm.customer_profile SET next_change_at = %s WHERE customer_id = %s",
        (now - timedelta(hours=1), customer),
    )
    events = shop.events(customer)

    run = ageing.run_pass(db, RULE_SET)

    assert run is not None
    assert (run.customers_examined, run.customers_changed) == (1, 0)
    assert shop.events(customer) == events
    assert _profile(owner, customer)[4] > run.as_of  # no longer due


def test_the_run_records_when_it_started_and_finished(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
) -> None:
    _aged(shop, service, now)
    run = ageing.run_pass(db, RULE_SET)
    assert run is not None
    row = owner.execute(
        "SELECT status, started_at <= finished_at, as_of >= %s, customers_examined,"
        " customers_changed FROM rfm.pass_run WHERE pass_run_id = %s",
        (now, run.pass_run_id),
    ).fetchone()
    assert row == ("COMPLETED", True, True, 1, 1)


def test_a_pass_that_stops_resumes_from_the_same_instant(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _aged(shop, service, now), _aged(shop, service, now)
    original = ageing._refresh
    calls: list[uuid.UUID] = []

    def stop_after_one(
        database: object, run: ageing.Run, customer: uuid.UUID
    ) -> bool | None:
        if calls:
            raise Died("the instance died")
        calls.append(customer)
        return original(database, run, customer)

    monkeypatch.setattr(ageing, "_refresh", stop_after_one)
    with pytest.raises(Died):
        ageing.run_pass(db, RULE_SET)
    left = owner.execute(
        "SELECT pass_run_id, status, as_of, started_at, customers_examined"
        " FROM rfm.pass_run"
    ).fetchall()
    assert len(left) == 1 and left[0][1] == "RUNNING" and left[0][4] == 1

    monkeypatch.setattr(ageing, "_refresh", original)
    done = ageing.run_pass(db, RULE_SET)

    assert done is not None
    assert (done.pass_run_id, done.as_of, done.started_at) == left[0][0:1] + (
        left[0][2],
        left[0][3],
    )
    assert (done.status, done.customers_examined, done.customers_changed) == (
        "COMPLETED",
        2,
        2,
    )
    # Both customers were measured from the one instant of the run.
    assert {_profile(owner, c)[3] for c in (first, second)} == {done.as_of}


def test_an_instance_stands_down_while_another_holds_the_pass(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    owner_url: str,
    db: Database,
    now: datetime,
) -> None:
    customer = _aged(shop, service, now)
    other = psycopg.connect(owner_url, autocommit=True)
    try:
        other.execute("SELECT pg_advisory_lock(%s)", (ageing.LOCK_KEY,))
        assert ageing.run_pass(db, RULE_SET) is None
        assert owner.execute("SELECT count(*) FROM rfm.pass_run").fetchone() == (0,)
        assert _profile(owner, customer)[0] == "scored"
    finally:
        other.execute("SELECT pg_advisory_unlock(%s)", (ageing.LOCK_KEY,))
        other.close()
    assert ageing.run_pass(db, RULE_SET) is not None


def test_two_passes_never_run_at_once_in_the_record(
    owner: psycopg.Connection,
) -> None:
    insert = (
        "INSERT INTO rfm.pass_run (rule_set_id, as_of) VALUES (%s, clock_timestamp())"
    )
    owner.execute(insert, (RULE_SET,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner.execute(insert, (RULE_SET,))


def test_the_service_may_not_rewrite_the_history_of_a_pass(
    service: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        service.execute("DELETE FROM rfm.pass_run")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        service.execute("UPDATE rfm.pass_run SET as_of = clock_timestamp()")


def test_the_daily_thread_finishes_the_pass_an_instance_left_running(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
) -> None:
    customer = _aged(shop, service, now)
    instant = days_ago(now, 1)
    owner.execute(
        "INSERT INTO rfm.pass_run (rule_set_id, as_of, started_at)"
        " VALUES (%s, %s, %s)",
        (RULE_SET, instant, instant),
    )
    thread = ageing.DailyPass(db, RULE_SET, clock(2, 0))
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            row = owner.execute("SELECT status FROM rfm.pass_run").fetchone()
            if row == ("COMPLETED",):
                break
            time.sleep(0.1)
        assert owner.execute("SELECT status FROM rfm.pass_run").fetchone() == (
            "COMPLETED",
        )
    finally:
        thread.stop()
        thread.join(5)
    assert _profile(owner, customer)[:2] == ("unassigned", 0)
    assert _profile(owner, customer)[3] == instant


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (
            datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 2, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 1, 1, 2, 0, tzinfo=UTC),
            datetime(2026, 1, 2, 2, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 1, 1, 23, 59, tzinfo=UTC),
            datetime(2026, 1, 2, 2, 0, tzinfo=UTC),
        ),
    ],
)
def test_the_pass_is_scheduled_at_two_in_the_morning_utc(
    moment: datetime, expected: datetime
) -> None:
    assert ageing.next_start(moment, clock(2, 0)) == expected


# ---------- review of #1084 ----------


def test_an_event_that_recomputes_a_customer_after_the_batch_was_read_is_kept(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The customer is due when the pass reads its batch; a sale then arrives and its
    event recomputes the profile. The pass must not write its older view over it: the
    profile stays scored, `computed_at` does not go back, and nothing new reaches the
    outbox."""
    customer = _aged(shop, service, now)
    original = ageing._due
    fired: list[datetime] = []

    def sale_after_the_read(
        connection: psycopg.Connection,
        as_of: datetime,
        window_days: int,
        after: uuid.UUID | None,
    ) -> list[uuid.UUID]:
        batch = original(connection, as_of, window_days, after)
        if batch and not fired:
            moment = datetime.now(UTC)
            shop.sale(customer, moment - timedelta(seconds=1))
            _recompute(service, customer, moment)
            fired.append(moment)
        return batch

    monkeypatch.setattr(ageing, "_due", sale_after_the_read)
    run = ageing.run_pass(db, RULE_SET)

    assert fired and run is not None
    state, frequency, _, computed_at, next_change_at = _profile(owner, customer)
    assert (state, frequency) == ("scored", 1)
    assert computed_at == fired[0] and computed_at > run.as_of
    assert next_change_at > run.as_of
    assert shop.events(customer) == ["INSERT", "UPDATE"]
    assert (run.customers_examined, run.customers_changed) == (0, 0)


def test_a_customer_who_always_fails_does_not_stop_the_pass(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad, good = sorted([_aged(shop, service, now), _aged(shop, service, now)])
    original = ageing._refresh

    def refuse_one(database: object, run: ageing.Run, customer: uuid.UUID):
        if customer == bad:
            raise RuntimeError("this customer cannot be recomputed")
        return original(database, run, customer)

    monkeypatch.setattr(ageing, "_refresh", refuse_one)
    run = ageing.run_pass(db, RULE_SET)

    assert run is not None and run.status == "COMPLETED"
    assert (run.customers_examined, run.customers_changed) == (1, 1)
    assert _profile(owner, good)[0] == "unassigned"
    # The one that failed is still due, so the next pass tries it again.
    assert _profile(owner, bad)[0] == "scored"
    assert _profile(owner, bad)[4] <= run.as_of


def test_a_pass_that_fails_on_every_customer_stops_and_stays_running(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for _ in range(ageing.CONSECUTIVE_FAILURES + 1):
        _aged(shop, service, now)

    def database_down(*_: object) -> bool:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(ageing, "_refresh", database_down)
    with pytest.raises(RuntimeError):
        ageing.run_pass(db, RULE_SET)
    assert owner.execute("SELECT status FROM rfm.pass_run").fetchone() == ("RUNNING",)


def test_the_pass_leaves_the_pool_to_the_consumers_and_requests(
    shop: Shop,
    service: psycopg.Connection,
    db: Database,
    now: datetime,
) -> None:
    """The pool holds three connections. The lock of the pass is on a connection of
    its own, so with two of the pool's busy the pass still gets the third."""
    _aged(shop, service, now)
    with db.connection(), db.connection():
        run = ageing.run_pass(db, RULE_SET)
    assert run is not None and run.status == "COMPLETED"


def _left_running(owner: psycopg.Connection, instant: datetime) -> None:
    owner.execute(
        "INSERT INTO rfm.pass_run (rule_set_id, as_of, started_at)"
        " VALUES (%s, %s, %s)",
        (RULE_SET, instant, instant),
    )


def _finish(
    owner: psycopg.Connection, thread: ageing.DailyPass, seconds: float = 20
) -> None:
    thread.start()
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if owner.execute(
                "SELECT count(*) FROM rfm.pass_run WHERE status = 'RUNNING'"
            ).fetchone() == (0,):
                break
            time.sleep(0.05)
    finally:
        thread.stop()
        thread.join(5)


def test_a_failed_pass_is_retried_with_a_backoff_and_not_a_day_later(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer = _aged(shop, service, now)
    _left_running(owner, days_ago(now, 1))
    real = ageing.run_pass
    attempts: list[float] = []

    def flaky(database: Database, rule_set_id: int) -> ageing.Run | None:
        attempts.append(time.monotonic())
        if len(attempts) <= 3:
            raise RuntimeError("the database is down")
        return real(database, rule_set_id)

    monkeypatch.setattr(ageing, "run_pass", flaky)
    thread = ageing.DailyPass(
        db, RULE_SET, clock(2, 0), retry_first=0.05, retry_max=0.2
    )
    _finish(owner, thread)

    assert len(attempts) >= 4
    gaps = [b - a for a, b in zip(attempts, attempts[1:], strict=False)]
    assert gaps[0] >= 0.05 and gaps[1] >= 0.1 and gaps[2] >= 0.2 - 0.02
    assert _profile(owner, customer)[0] == "unassigned"


def test_after_a_resumed_pass_a_fresh_one_covers_what_fell_due_since(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    db: Database,
    now: datetime,
) -> None:
    """The resumed pass measures from the instant it began, a day ago, so a customer
    whose instant came after that is not due to it. A pass that starts when it ends
    measures from the present and takes that customer too."""
    instant = days_ago(now, 1)
    early = _aged(shop, service, now)
    late = shop.customer()
    shop.sale(late, days_ago(now, 180.5))
    _recompute(service, late, days_ago(now, 100))
    owner.execute(
        "UPDATE rfm.customer_profile SET next_change_at = %s WHERE customer_id = %s",
        (now - timedelta(hours=1), late),
    )
    assert _profile(owner, late)[4] > instant
    _left_running(owner, instant)

    _finish(owner, ageing.DailyPass(db, RULE_SET, clock(2, 0)))

    assert owner.execute(
        "SELECT status, as_of = %s FROM rfm.pass_run ORDER BY pass_run_id", (instant,)
    ).fetchall() == [("COMPLETED", True), ("COMPLETED", False)]
    assert _profile(owner, early)[0] == "unassigned"
    assert _profile(owner, late)[0] == "unassigned"


def test_a_pass_time_that_is_not_a_time_is_refused_at_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import config

    monkeypatch.setenv("DAILY_PASS_AT_UTC", "two o'clock")
    with pytest.raises(ValueError, match="DAILY_PASS_AT_UTC"):
        config._clock("DAILY_PASS_AT_UTC", clock(2, 0))
    monkeypatch.setenv("DAILY_PASS_AT_UTC", "03:30")
    assert config._clock("DAILY_PASS_AT_UTC", clock(2, 0)) == clock(3, 30)
