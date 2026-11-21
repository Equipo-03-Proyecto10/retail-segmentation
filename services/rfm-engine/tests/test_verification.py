"""The incremental result is proved against a full recalculation (F22-08, ADR-0063).

A scripted history of sales, reversals, late sales and duplicate deliveries is applied
through the real event handlers, and the verification recomputes everyone from the net
purchases at one instant and compares. Any difference is a failure that names the
customer. The reversals are written as svc_sales writes them, so what the engine
reads is the net view and nothing is faked.

The tests narrow the customers a verification compares to the ones they made (the
seeded customers have sales and no profile, and would all differ); the operation and
the schedule always verify everyone.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from datetime import time as clock
from pathlib import Path
from typing import Any

import psycopg
import pytest
from flask import Flask
from mosaiq_kit.database import Database

from app import recompute, verification
from tests.conftest import Shop, days_ago

RULE_SET = 1
JSON = {"Content-Type": "application/json", "Accept": "application/json"}
pytestmark = pytest.mark.postgres


@pytest.fixture
def db(service_url: str) -> Iterator[Database]:
    database = Database(service_url)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture(autouse=True)
def clean_records(owner: psycopg.Connection) -> Iterator[None]:
    """Records are final for every role, so the owner clears them with triggers off."""

    def clear() -> None:
        owner.execute("SET session_replication_role = replica")
        try:
            owner.execute("DELETE FROM rfm.recalculation_difference")
            owner.execute("DELETE FROM rfm.recalculation")
            owner.execute("DELETE FROM rfm.pass_run")
        finally:
            owner.execute("SET session_replication_role = origin")

    clear()
    yield
    clear()


def _admin(owner: psycopg.Connection) -> tuple[str, str]:
    row = owner.execute(
        "SELECT min(u.user_id::text) FROM public.app_user AS u"
        " JOIN public.role AS r USING (role_id) WHERE u.is_active AND r.code = 'ADMIN'"
    ).fetchone()
    assert row is not None and row[0]
    return row[0], "ADMIN"


def _verify(
    db: Database,
    owner: psycopg.Connection,
    customers: list[uuid.UUID] | None,
    *,
    trigger: str = "ON_DEMAND",
    rule_set: int | None = None,
) -> dict[str, Any]:
    """Start a verification and run it to its end, over these customers only."""
    actor = _admin(owner) if trigger == "ON_DEMAND" else None
    with db.transaction() as connection:
        row = verification.start(
            connection,
            trigger=trigger,
            started_by=None if actor is None else actor[0],
            rule_set_id=rule_set,
            in_force=RULE_SET,
        )
    return verification.execute(db, row["recalculation_id"], actor, only=customers)


def _differences(db: Database, recalculation_id: int) -> list[dict[str, Any]]:
    with db.connection() as connection:
        items, _ = verification.differences(
            connection, recalculation_id, page=1, size=100
        )
        connection.commit()
    return items


def _named(db: Database, finished: dict[str, Any]) -> str:
    """A failure message that names every customer that differs, and how."""
    return "; ".join(
        f"{item['customer_id']}: {', '.join(item['fields'])}"
        f" (incremental {item['incremental']}, recomputed {item['recomputed']})"
        for item in _differences(db, finished["recalculation_id"])
    )


def _entry(sale: int, reversal: int) -> dict[str, str]:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "INSERT",
        "entity_id": f"{sale}:{reversal}",
        "occurred_at": "2026-11-01T12:00:00Z",
        "v": "1",
    }


def _reversal_of(owner: psycopg.Connection, sale: int) -> int:
    row = owner.execute(
        "SELECT max(reversal_id) FROM sales.sale_reversal WHERE transaction_id = %s",
        (sale,),
    ).fetchone()
    assert row is not None and row[0] is not None
    return int(row[0])


@pytest.fixture
def app(build_app: Callable[..., Flask]) -> Flask:
    return build_app()


class Events:
    """Delivers the sale and reversal events the way the consumers do."""

    def __init__(self, app: Flask, owner: psycopg.Connection):
        self.app = app
        self.owner = owner

    def recorded(self, sale: int) -> None:
        self.app.extensions["rfm_handle_sale"](
            {"event_id": str(uuid.uuid4()), "entity_id": str(sale)}
        )

    def reversed(self, sale: int) -> None:
        self.app.extensions["rfm_handle_reversal"](
            _entry(sale, _reversal_of(self.owner, sale))
        )


@pytest.fixture
def events(app: Flask, owner: psycopg.Connection) -> Events:
    return Events(app, owner)


# ---------------------------------------------------------------- the reference


def test_a_verification_recomputes_everyone_and_writes_no_profile(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customers = [shop.customer() for _ in range(3)]
    for position, customer in enumerate(customers):
        events.recorded(shop.sale(customer, days_ago(now, 10 + position)))
    snapshot = owner.execute(
        "SELECT * FROM rfm.customer_profile WHERE customer_id = ANY(%s)"
        " ORDER BY customer_id",
        (customers,),
    ).fetchall()
    outbox = owner.execute("SELECT count(*) FROM rfm_events.outbox").fetchone()

    finished = _verify(db, owner, customers)

    assert finished["state"] == "COMPLETED", finished
    assert finished["outcome"] == "EQUIVALENT", _named(db, finished)
    assert finished["customers_checked"] == 3
    assert finished["customers_different"] == 0
    assert (
        owner.execute(
            "SELECT * FROM rfm.customer_profile WHERE customer_id = ANY(%s)"
            " ORDER BY customer_id",
            (customers,),
        ).fetchall()
        == snapshot
    )
    assert owner.execute("SELECT count(*) FROM rfm_events.outbox").fetchone() == outbox


def test_the_verification_uses_the_rule_set_and_the_instant_it_records(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    events.recorded(shop.sale(customer, days_ago(now, 5)))

    finished = _verify(db, owner, [customer])

    assert finished["rule_set_id"] == RULE_SET
    assert finished["as_of"] >= now
    assert finished["started_at"] >= finished["as_of"] - timedelta(seconds=1)
    assert finished["started_by"] == _admin(owner)[0]
    assert finished["trigger"] == "ON_DEMAND"


# ----------------------------------------------------------- the scripted history


def test_a_scripted_history_applied_incrementally_has_zero_differences(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customers = [shop.customer() for _ in range(7)]
    a, b, c, d, e, f, g = customers

    # a: three sales, one event delivered twice.
    first = shop.sale(a, days_ago(now, 40), quantity=2, unit_price="30.00")
    events.recorded(first)
    events.recorded(first)
    events.recorded(shop.sale(a, days_ago(now, 12), quantity=5, unit_price="80.00"))
    events.recorded(shop.sale(a, days_ago(now, 3), quantity=1, unit_price="9.99"))

    # b: a sale cancelled in full, the reversal delivered twice, then another sale.
    cancelled = shop.sale(b, days_ago(now, 6), quantity=3, unit_price="50.00")
    events.recorded(cancelled)
    shop.reverse(cancelled)
    events.reversed(cancelled)
    events.reversed(cancelled)
    events.recorded(shop.sale(b, days_ago(now, 20), quantity=1, unit_price="45.00"))

    # c: a partial return lowers M and leaves F alone.
    kept = shop.sale(c, days_ago(now, 9), quantity=4, unit_price="25.00")
    events.recorded(kept)
    shop.return_units(kept, 1)
    events.reversed(kept)

    # d: a late sale recorded after a newer one, delivered out of order.
    newer = shop.sale(d, days_ago(now, 2), quantity=1, unit_price="15.00")
    late = shop.sale(d, days_ago(now, 90), quantity=2, unit_price="60.00")
    events.recorded(newer)
    events.recorded(late)

    # e: a sale that never was in the window, and one that left it.
    events.recorded(shop.sale(e, days_ago(now, 200)))

    # f: every sale returned in full leaves a customer with nothing to count.
    only = shop.sale(f, days_ago(now, 30), quantity=1, unit_price="70.00")
    events.recorded(only)
    shop.reverse(only)
    events.reversed(only)

    # g: a profile written five days ago, whose R has aged since by itself and
    # still scores the same, so the profile is right and not due.
    old = shop.sale(g, days_ago(now, 12), quantity=2, unit_price="33.00")
    with db.transaction() as connection:
        recompute.from_sale(connection, old, RULE_SET, days_ago(now, 5))

    finished = _verify(db, owner, customers)

    assert finished["outcome"] == "EQUIVALENT", _named(db, finished)
    assert finished["customers_checked"] == len(customers)


def test_a_sale_whose_event_never_arrived_is_a_difference_that_names_the_customer(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    healthy, stale = shop.customer(), shop.customer()
    events.recorded(shop.sale(healthy, days_ago(now, 4)))
    events.recorded(shop.sale(stale, days_ago(now, 30), quantity=2, unit_price="40.00"))
    shop.sale(stale, days_ago(now, 2), quantity=3, unit_price="100.00")  # no event

    finished = _verify(db, owner, [healthy, stale])

    assert finished["outcome"] == "DIFFERENT"
    assert (finished["customers_checked"], finished["customers_different"]) == (2, 1)
    (item,) = _differences(db, finished["recalculation_id"])
    assert item["customer_id"] == str(stale)
    assert {"f_count", "m_total"} <= set(item["fields"])
    assert item["incremental"]["f_count"] == 1
    assert item["recomputed"]["f_count"] == 2
    assert item["incremental"]["m_total"] == "80.00"
    assert item["recomputed"]["m_total"] == "380.00"


def test_a_reversal_whose_event_never_arrived_is_found(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 8), quantity=2, unit_price="50.00")
    events.recorded(sale)
    shop.reverse(sale)  # the reversal's event is lost

    finished = _verify(db, owner, [customer])

    assert finished["outcome"] == "DIFFERENT"
    (item,) = _differences(db, finished["recalculation_id"])
    assert item["customer_id"] == str(customer)
    assert item["incremental"]["state"] == "scored"
    assert item["recomputed"]["state"] == "unassigned"


def test_a_customer_with_purchases_and_no_profile_is_pending_and_differs(
    shop: Shop, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    _completed_pass(owner, days_ago(now, 30))
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 4))

    finished = _verify(db, owner, [customer])

    (item,) = _differences(db, finished["recalculation_id"])
    assert item["incremental"]["state"] == "pending"
    assert item["recomputed"]["state"] == "scored"
    assert "state" in item["fields"]


def test_a_customer_with_no_purchase_and_no_profile_is_not_a_difference(
    shop: Shop, db: Database, owner: psycopg.Connection
) -> None:
    customer = shop.customer()

    finished = _verify(db, owner, [customer])

    assert finished["outcome"] == "EQUIVALENT"


# ------------------------------------------------------------ what the pass owns


def _aged_customer(shop: Shop, db: Database, now: datetime) -> uuid.UUID:
    """One sale that was in the window when the profile was written and has left it
    since: the profile is due, and is no longer right until the pass moves it."""
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 200))
    with db.transaction() as connection:
        recompute.from_sale(connection, sale, RULE_SET, days_ago(now, 100))
    return customer


def _completed_pass(owner: psycopg.Connection, as_of: datetime) -> None:
    owner.execute(
        "INSERT INTO rfm.pass_run (rule_set_id, as_of, status, started_at, finished_at)"
        " VALUES (%s, %s, 'COMPLETED', %s, %s)",
        (RULE_SET, as_of, as_of, as_of),
    )


def test_a_profile_that_fell_due_after_the_last_pass_is_left_to_the_next_one(
    shop: Shop, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = _aged_customer(shop, db, now)  # due since about 20 days ago
    _completed_pass(owner, days_ago(now, 30))

    finished = _verify(db, owner, [customer])

    assert finished["outcome"] == "EQUIVALENT"
    assert finished["customers_checked"] == 0


def test_a_profile_the_last_pass_should_have_moved_is_a_difference(
    shop: Shop, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = _aged_customer(shop, db, now)
    _completed_pass(owner, days_ago(now, 10))

    finished = _verify(db, owner, [customer])

    assert finished["outcome"] == "DIFFERENT"
    (item,) = _differences(db, finished["recalculation_id"])
    assert item["customer_id"] == str(customer)
    assert item["incremental"]["state"] == "scored"
    assert item["recomputed"]["state"] == "unassigned"


# --------------------------------------------------------- the acceptance evidence


def test_the_acceptance_run_shows_the_customer_before_after_and_the_reference(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    events.recorded(
        shop.sale(customer, days_ago(now, 45), quantity=2, unit_price="30.00")
    )
    before = shop.profile(customer)
    sale = shop.sale(customer, days_ago(now, 1), quantity=6, unit_price="120.00")
    events.recorded(sale)  # the recorded sale
    after = shop.profile(customer)

    finished = _verify(db, owner, [customer])

    assert before is not None and after is not None
    assert before != after, "the sale must have moved the profile"
    assert finished["outcome"] == "EQUIVALENT", _named(db, finished)
    with db.connection() as connection:
        reference = verification.reference(
            connection, customer, finished["rule_set_id"], finished["as_of"]
        )
        connection.commit()
    # State, F, M, scores and label of the incremental profile are the reference's.
    assert (after[0], after[3], after[4], after[5], after[6], after[7], after[8]) == (
        reference.state,
        reference.f_count,
        reference.m_total,
        reference.r_score,
        reference.f_score,
        reference.m_score,
        reference.label_code,
    )
    out = os.environ.get("RFM_EVIDENCE_OUT")
    if out:
        Path(out).write_text(_evidence(sale, before, after, reference, finished))


def _shown(reference: verification.Values, name: str) -> object:
    return getattr(reference, name) if hasattr(reference, name) else "-"


def _evidence(
    sale: int,
    before: tuple[object, ...],
    after: tuple[object, ...],
    reference: verification.Values,
    finished: dict[str, Any],
) -> str:
    names = [
        "state",
        "rule_set_id",
        "r_days",
        "f_count",
        "m_total",
        "r_score",
        "f_score",
        "m_score",
        "label_code",
        "via_fallback",
    ]
    rows = "\n".join(
        f"| `{name}` | {b} | {a} | {_shown(reference, name)} |"
        for name, b, a in zip(names, before, after, strict=True)
    )
    return (
        "# F22-08 (#782): acceptance run, one recorded sale\n\n"
        f"Generated by `tests/test_verification.py::"
        "test_the_acceptance_run_shows_the_customer_before_after_and_the_reference`"
        " against PostgreSQL.\n\n"
        f"Sale {sale} was recorded and its event delivered. The profile was read "
        "before the sale's event and after it, and a verification recomputed the "
        "customer from the net purchases under the same rule set at one instant "
        f"(`as_of` {finished['as_of'].isoformat()}).\n\n"
        "| Value | Before | After (incremental) | Reference (full recomputation) |\n"
        "|---|---|---|---|\n"
        f"{rows}\n\n"
        f"Outcome: `{finished['outcome']}`, {finished['customers_checked']} customer "
        f"checked, {finished['customers_different']} different.\n\n"
        "The reference is measured at `as_of`. The incremental profile's `r_days` is "
        "measured to the instant it was computed, and a verification compares it "
        "that way (ADR-0086).\n"
    )


# ------------------------------------------------------------------ the operations


class Admin:
    def __init__(self, app: Flask, token: Callable[..., dict[str, str]]):
        self.client = app.test_client()
        self.token = token

    def call(self, method: str, path: str, role: str = "ADMIN", **kw: Any) -> Any:
        headers = {**self.token(role), **JSON, **kw.pop("headers", {})}
        return self.client.open(path, method=method, headers=headers, **kw)

    def wait(self, recalculation_id: int) -> dict[str, Any]:
        for _ in range(200):
            body = self.call(
                "GET", f"/v1/rfm-recalculations/{recalculation_id}"
            ).get_json()
            if body["state"] != "RUNNING":
                return body
            time.sleep(0.1)
        raise AssertionError("the verification never finished")


@pytest.fixture
def admin(app: Flask, token: Callable[..., dict[str, str]]) -> Admin:
    return Admin(app, token)


def test_an_administrator_starts_a_verification_and_polls_it(
    shop: Shop, events: Events, admin: Admin, owner: psycopg.Connection, now: datetime
) -> None:
    _completed_pass(owner, days_ago(now, 30))
    stale = shop.customer()
    shop.sale(stale, days_ago(now, 2))  # recorded, its event lost

    started = admin.call("POST", "/v1/rfm-recalculations", json={})

    assert started.status_code == 201
    body = started.get_json()
    assert body["state"] == "RUNNING" and body["trigger"] == "ON_DEMAND"
    assert body["outcome"] is None and body["finished_at"] is None
    done = admin.wait(body["recalculation_id"])
    assert done["state"] == "COMPLETED"
    assert done["outcome"] == "DIFFERENT"
    page = admin.call(
        "GET", f"/v1/rfm-recalculations/{done['recalculation_id']}/differences?size=100"
    ).get_json()
    assert page["page_info"]["total"] == done["customers_different"]
    mine = [i for i in page["differences"] if i["customer_id"] == str(stale)]
    assert len(mine) == 1 and mine[0]["incremental"]["state"] == "pending"
    xml = admin.call(
        "GET",
        f"/v1/rfm-recalculations/{done['recalculation_id']}/differences?size=100",
        headers={"Accept": "application/xml"},
    )
    assert xml.status_code == 200 and str(stale) in xml.get_data(as_text=True)


def test_reads_belong_to_analyst_and_auditor_and_the_start_to_the_administrator(
    admin: Admin, owner: psycopg.Connection
) -> None:
    assert (
        admin.call("POST", "/v1/rfm-recalculations", "ANALYST", json={}).status_code
        == 403
    )
    assert (
        admin.call("POST", "/v1/rfm-recalculations", "AUDITOR", json={}).status_code
        == 403
    )
    for role in ("ANALYST", "AUDITOR", "ADMIN"):
        assert admin.call("GET", "/v1/rfm-recalculations", role).status_code == 200
    assert admin.call("GET", "/v1/rfm-recalculations", "MARKETING").status_code == 403


def test_one_verification_runs_at_a_time(
    admin: Admin, owner: psycopg.Connection, owner_url: str
) -> None:
    """Another process runs one: it holds 299001 and its row is RUNNING."""
    running = psycopg.connect(owner_url, autocommit=True)
    running.execute("SELECT pg_advisory_lock(%s)", (verification.LOCK_KEY,))
    owner.execute(
        "INSERT INTO rfm.recalculation (trigger_kind, rule_set_id, as_of, started_by)"
        " VALUES ('SCHEDULE', %s, now(), NULL)",
        (RULE_SET,),
    )

    try:
        refused = admin.call("POST", "/v1/rfm-recalculations", json={})
    finally:
        running.close()

    assert refused.status_code == 409
    assert refused.get_json()["code"] == "recalculation_in_progress"
    listed = admin.call("GET", "/v1/rfm-recalculations?state=RUNNING").get_json()
    assert listed["page_info"]["total"] == 1
    finished = admin.call(
        "GET",
        f"/v1/rfm-recalculations/{listed['recalculations'][0]['recalculation_id']}/differences",
    )
    assert finished.status_code == 409
    assert finished.get_json()["code"] == "recalculation_not_finished"


def test_an_unknown_recalculation_or_rule_set_is_not_found(admin: Admin) -> None:
    for path in (
        "/v1/rfm-recalculations/999999",
        "/v1/rfm-recalculations/999999/differences",
    ):
        reply = admin.call("GET", path)
        assert (reply.status_code, reply.get_json()["code"]) == (
            404,
            "recalculation_not_found",
        )
    reply = admin.call("POST", "/v1/rfm-recalculations", json={"rule_set_id": 999999})
    assert (reply.status_code, reply.get_json()["code"]) == (404, "rule_set_not_found")


def test_a_draft_is_never_a_reference(admin: Admin, owner: psycopg.Connection) -> None:
    draft = owner.execute(
        "SELECT rule_set_id FROM rfm.rule_set WHERE state = 'DRAFT' LIMIT 1"
    ).fetchone()
    assert draft is not None

    reply = admin.call("POST", "/v1/rfm-recalculations", json={"rule_set_id": draft[0]})

    assert (reply.status_code, reply.get_json()["code"]) == (
        409,
        "no_published_rule_set",
    )


# ------------------------------------------------------- the schedule, the alert


def test_a_scheduled_verification_that_finds_a_difference_alerts_and_is_audited(
    shop: Shop,
    db: Database,
    owner: psycopg.Connection,
    now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _completed_pass(owner, days_ago(now, 30))
    stale = shop.customer()
    shop.sale(stale, days_ago(now, 2))  # recorded, its event lost
    weekly = verification.WeeklyVerification(db, RULE_SET, 6, clock(3, 0))

    with caplog.at_level(logging.WARNING, logger="app.verification"):
        finished = weekly.once()

    assert finished is not None
    assert finished["trigger"] == "SCHEDULE" and finished["started_by"] is None
    assert finished["outcome"] == "DIFFERENT"
    alerts = [
        r for r in caplog.records if "rfm_verification_differences" in r.getMessage()
    ]
    assert len(alerts) == 1 and alerts[0].levelno == logging.ERROR
    assert f"recalculation_id={finished['recalculation_id']}" in alerts[0].getMessage()
    event = owner.execute(
        "SELECT kind, outcome, actor_type, actor_user_id, source, subject_type,"
        " details ->> 'status', (details ->> 'input_count')::int,"
        " (details ->> 'output_count')::int, rule_set_version"
        " FROM audit_events.domain_event WHERE subject_id = %s",
        (f"rfm-verification-{finished['recalculation_id']}",),
    ).fetchall()
    assert event == [
        (
            "CALCULATION_RUN",
            "SUCCESS",
            "SYSTEM",
            None,
            "svc_rfm",
            "calculation",
            "COMPLETED",
            finished["customers_checked"],
            finished["customers_different"],
            finished["rule_set_id"],
        )
    ]


def test_an_on_demand_verification_is_audited_as_the_administrator(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    events.recorded(shop.sale(customer, days_ago(now, 3)))

    finished = _verify(db, owner, [customer])

    row = owner.execute(
        "SELECT actor_type, actor_user_id::text, actor_role,"
        " (details ->> 'output_count')::int"
        " FROM audit_events.domain_event WHERE subject_id = %s",
        (f"rfm-verification-{finished['recalculation_id']}",),
    ).fetchone()
    assert row == ("USER", _admin(owner)[0], "ADMIN", 0)


def test_a_verification_that_finds_nothing_raises_no_alert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    finished = {
        "recalculation_id": 1,
        "trigger": "SCHEDULE",
        "state": "COMPLETED",
        "rule_set_id": 1,
        "customers_checked": 5,
        "customers_different": 0,
        "failure_code": None,
    }

    with caplog.at_level(logging.INFO, logger="app.verification"):
        verification._alert(finished)

    assert not caplog.records


def test_a_verification_that_fails_is_recorded_audited_and_alerted(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
    owner: psycopg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*_: Any, **__: Any) -> list[uuid.UUID]:
        raise psycopg.OperationalError("the source went away")

    monkeypatch.setattr(verification, "_universe", broken)

    with caplog.at_level(logging.WARNING, logger="app.verification"):
        finished = _verify(db, owner, None, trigger="SCHEDULE")

    assert finished["state"] == "FAILED"
    assert finished["failure_code"] == "source_unavailable"
    assert finished["outcome"] is None and finished["customers_checked"] is None
    assert any("rfm_verification_failed" in r.getMessage() for r in caplog.records)
    row = owner.execute(
        "SELECT details ->> 'status' FROM audit_events.domain_event"
        " WHERE subject_id = %s",
        (f"rfm-verification-{finished['recalculation_id']}",),
    ).fetchone()
    assert row == ("FAILED",)


def test_a_verification_left_running_by_a_dead_instance_is_marked_failed(
    db: Database, owner: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    owner.execute(
        "INSERT INTO rfm.recalculation (trigger_kind, rule_set_id, as_of, started_by)"
        " VALUES ('SCHEDULE', %s, now(), NULL)",
        (RULE_SET,),
    )

    with caplog.at_level(logging.WARNING, logger="app.verification"):
        assert verification.reap_orphans(db) == 1

    row = owner.execute(
        "SELECT recalculation_id, state, failure_code FROM rfm.recalculation"
    ).fetchone()
    assert row is not None and row[1:] == ("FAILED", "interrupted")
    alerts = [r for r in caplog.records if "rfm_verification_failed" in r.getMessage()]
    assert len(alerts) == 1 and alerts[0].levelno == logging.ERROR
    audited = owner.execute(
        "SELECT actor_type, details ->> 'status' FROM audit_events.domain_event"
        " WHERE subject_id = %s",
        (f"rfm-verification-{row[0]}",),
    ).fetchall()
    assert audited == [("SYSTEM", "FAILED")]


def test_the_week_starts_on_its_weekday_at_its_time() -> None:
    sunday_3 = clock(3, 0)
    monday = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    assert verification.next_start(monday, 6, sunday_3) == datetime(
        2026, 11, 22, 3, 0, tzinfo=UTC
    )
    just_before = datetime(2026, 11, 22, 2, 59, tzinfo=UTC)
    assert verification.next_start(just_before, 6, sunday_3) == datetime(
        2026, 11, 22, 3, 0, tzinfo=UTC
    )
    at = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)
    assert verification.next_start(at, 6, sunday_3) == datetime(
        2026, 11, 29, 3, 0, tzinfo=UTC
    )


# ------------------------------------------------------------------ the records


def test_a_finished_verification_and_its_differences_are_never_changed(
    shop: Shop,
    db: Database,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    now: datetime,
) -> None:
    _completed_pass(owner, days_ago(now, 30))
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 3))
    finished = _verify(db, owner, [customer])
    assert finished["customers_different"] == 1
    rid = finished["recalculation_id"]

    for who in (service, owner):
        for statement in (
            "UPDATE rfm.recalculation SET customers_different = 0"
            " WHERE recalculation_id = %s",
            "DELETE FROM rfm.recalculation WHERE recalculation_id = %s",
            "UPDATE rfm.recalculation_difference SET inc_state = 'scored'"
            " WHERE recalculation_id = %s",
            "DELETE FROM rfm.recalculation_difference WHERE recalculation_id = %s",
        ):
            with pytest.raises(psycopg.Error):
                who.execute(statement, (rid,))


def test_a_verification_asked_of_everyone_compares_every_customer_with_a_profile(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    events.recorded(shop.sale(customer, days_ago(now, 3)))

    finished = _verify(db, owner, None)

    # Everyone: more customers than the one made here (the seeded ones have no profile).
    assert finished["customers_checked"] > 1
    assert str(customer) not in {
        item["customer_id"] for item in _differences(db, finished["recalculation_id"])
    }


def test_the_reference_is_a_pure_function_of_the_net_purchases(
    shop: Shop, events: Events, db: Database, now: datetime
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 3), quantity=2, unit_price="10.00")
    events.recorded(sale)
    shop.reverse(sale)
    as_of = now + timedelta(minutes=1)

    with db.connection() as connection:
        first = verification.reference(connection, customer, RULE_SET, as_of)
        second = verification.reference(connection, customer, RULE_SET, as_of)
        connection.commit()

    assert first == second
    assert first.state == "unassigned"


def test_a_customers_removal_takes_their_differences_with_it(
    shop: Shop, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    _completed_pass(owner, days_ago(now, 30))
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 3))
    finished = _verify(db, owner, [customer])
    assert finished["customers_different"] == 1

    shop.clean()

    left = owner.execute(
        "SELECT count(*) FROM rfm.recalculation_difference WHERE customer_id = %s",
        (customer,),
    ).fetchone()
    assert left == (0,)


# ------------------------------------------------------ what the review of #1123 found


def test_before_any_pass_a_customer_with_purchases_and_no_profile_is_the_passs(
    shop: Shop, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    """No pass has given them the profile it owes them yet, so it is no defect of the
    incremental update; nor is a due profile."""
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 4))
    aged = _aged_customer(shop, db, now)

    finished = _verify(db, owner, [customer, aged])

    assert finished["outcome"] == "EQUIVALENT"
    assert finished["customers_checked"] == 0


def test_a_profile_held_under_another_rule_set_is_not_compared(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    events.recorded(shop.sale(customer, days_ago(now, 3)))
    owner.execute(
        "UPDATE rfm.customer_profile SET rule_set_id = 2, f_count = f_count + 5"
        " WHERE customer_id = %s",
        (customer,),
    )

    finished = _verify(db, owner, [customer])

    assert finished["outcome"] == "EQUIVALENT"
    assert finished["customers_checked"] == 0


def test_a_profile_computed_after_the_instant_is_not_compared(
    shop: Shop, events: Events, db: Database, owner: psycopg.Connection, now: datetime
) -> None:
    customer = shop.customer()
    events.recorded(shop.sale(customer, days_ago(now, 3)))
    owner.execute(
        "UPDATE rfm.customer_profile SET computed_at = now() + interval '1 hour',"
        " f_count = f_count + 5 WHERE customer_id = %s",
        (customer,),
    )

    finished = _verify(db, owner, [customer])

    assert finished["customers_checked"] == 0


def test_a_reap_while_a_verification_starts_does_not_end_it(
    shop: Shop, db: Database, owner: psycopg.Connection
) -> None:
    """The lock is held from before the row is recorded, so a reap by another
    instance, or by the weekly thread, finds it held and leaves the row alone."""
    row, lock = verification.begin(
        db, trigger="SCHEDULE", started_by=None, rule_set_id=None, in_force=RULE_SET
    )

    assert verification.reap_orphans(db) == 0
    state = owner.execute(
        "SELECT state FROM rfm.recalculation WHERE recalculation_id = %s",
        (row["recalculation_id"],),
    ).fetchone()
    assert state == ("RUNNING",)

    finished = verification.execute(
        db, row["recalculation_id"], None, only=[shop.customer()], lock=lock
    )
    assert finished["state"] == "COMPLETED"


def test_a_start_reaps_what_a_stopped_process_left_and_runs(
    admin: Admin, owner: psycopg.Connection
) -> None:
    owner.execute(
        "INSERT INTO rfm.recalculation (trigger_kind, rule_set_id, as_of, started_by)"
        " VALUES ('SCHEDULE', %s, now(), NULL)",
        (RULE_SET,),
    )

    started = admin.call("POST", "/v1/rfm-recalculations", json={})

    assert started.status_code == 201
    left = owner.execute(
        "SELECT state, failure_code FROM rfm.recalculation"
        " WHERE recalculation_id < %s",
        (started.get_json()["recalculation_id"],),
    ).fetchall()
    assert left == [("FAILED", "interrupted")]
    admin.wait(started.get_json()["recalculation_id"])


def test_a_verification_that_fails_half_way_keeps_no_differences(
    shop: Shop,
    db: Database,
    owner: psycopg.Connection,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _completed_pass(owner, days_ago(now, 30))
    first = shop.customer()
    shop.sale(first, days_ago(now, 4))  # pending: a difference
    original = verification._universe
    calls: list[int] = []

    def then_broken(*args: Any, **kwargs: Any) -> list[uuid.UUID]:
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("the second batch fails")
        return original(*args, **kwargs)

    monkeypatch.setattr(verification, "_universe", then_broken)

    finished = _verify(db, owner, [first])

    assert finished["state"] == "FAILED"
    assert finished["failure_code"] == "internal_error"
    rows = owner.execute(
        "SELECT count(*) FROM rfm.recalculation_difference WHERE recalculation_id = %s",
        (finished["recalculation_id"],),
    ).fetchone()
    assert rows == (0,)
    audited = owner.execute(
        "SELECT (details ->> 'input_count')::int, (details ->> 'output_count')::int"
        " FROM audit_events.domain_event WHERE subject_id = %s",
        (f"rfm-verification-{finished['recalculation_id']}",),
    ).fetchone()
    assert audited == (1, 1), "the counts reached before it failed"


def test_a_pool_that_cannot_reach_the_database_is_source_unavailable() -> None:
    from mosaiq_kit import ApiError

    assert (
        verification._failure_code(ApiError(503, "database_unavailable", "away"))
        == "source_unavailable"
    )
    assert verification._failure_code(RuntimeError("bug")) == "internal_error"


def test_the_list_refuses_a_filter_the_contract_does_not_allow(admin: Admin) -> None:
    for query, field in (
        ("rule_set_id=abc", "rule_set_id"),
        ("rule_set_id=0", "rule_set_id"),
        ("rule_set_id=99999999999999999999", "rule_set_id"),
        ("state=DONE", "state"),
        ("trigger=CRON", "trigger"),
    ):
        answer = admin.call("GET", f"/v1/rfm-recalculations?{query}")
        assert answer.status_code == 422, query
        body = answer.get_json()
        assert body["code"] == "invalid_query"
        assert [d["field"] for d in body["details"]] == [field]


def test_a_weekly_start_that_fails_is_retried_and_alerted(
    shop: Shop,
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mosaiq_kit import ApiError

    original = verification.begin
    attempts: list[int] = []

    def away_once(*args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise ApiError(503, "database_unavailable", "away")
        return original(*args, **kwargs)

    monkeypatch.setattr(verification, "begin", away_once)
    weekly = verification.WeeklyVerification(
        db, RULE_SET, 6, clock(3, 0), retry_first=0.01, retry_max=0.02
    )

    with caplog.at_level(logging.ERROR, logger="app.verification"):
        finished = weekly.until_started()

    assert finished is not None and finished["state"] == "COMPLETED"
    assert len(attempts) == 2
    assert any(
        "rfm_verification_failed" in r.getMessage() and "not_started" in r.getMessage()
        for r in caplog.records
    )


def test_a_finished_verification_gains_no_difference(
    shop: Shop, db: Database, owner: psycopg.Connection, service: psycopg.Connection
) -> None:
    finished = _verify(db, owner, [shop.customer()])
    assert finished["outcome"] == "EQUIVALENT"

    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        with service.transaction():
            service.execute(
                "INSERT INTO rfm.recalculation_difference (recalculation_id,"
                " customer_id, inc_state, rec_state) VALUES (%s, %s, 'pending',"
                " 'scored')",
                (finished["recalculation_id"], shop.customer()),
            )
    assert refused.value.diag.constraint_name == "recalculation_is_final"
    with pytest.raises(psycopg.errors.CheckViolation):
        with service.transaction():
            service.execute(
                "INSERT INTO rfm.recalculation (trigger_kind, rule_set_id, as_of,"
                " state, finished_at, customers_checked, customers_different)"
                " VALUES ('SCHEDULE', %s, now(), 'COMPLETED', now(), 0, 0)",
                (RULE_SET,),
            )
