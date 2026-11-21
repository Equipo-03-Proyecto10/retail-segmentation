"""A change of segment is applied under the published version and announced (F22-15,
ADR-0063, ADR-0087): the history row, the event, the run and the consent rule."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

import psycopg
import pytest
from flask import Flask

from app import recompute
from tests.conftest import Shop, days_ago

pytestmark = pytest.mark.postgres
RULE_SET = 1
ASSIGNMENT = "stream:segments.v1.assignment_changed"
Row = tuple[str | None, str, bool, int]


@pytest.fixture
def version(service: psycopg.Connection) -> recompute.Version:
    return recompute.in_force(service, RULE_SET)


def _apply(
    service: psycopg.Connection,
    customer: uuid.UUID,
    version: recompute.Version,
    as_of: datetime,
    cause: str = "SALE",
    batch: uuid.UUID | None = None,
) -> None:
    with service.transaction():
        recompute.customer(
            service,
            customer,
            version.rule_set_id,
            as_of,
            recompute.Applying(version, cause, batch or uuid.uuid4()),
        )


def _history(owner: psycopg.Connection, customer: uuid.UUID) -> list[Row]:
    """Label, cause, whether it is open and its run, oldest first. The seeded
    customers' rows are not ours: a test's customer is new."""
    return [
        (row[0], row[1], row[2], row[3])
        for row in owner.execute(
            "SELECT label_code, cause, valid_to IS NULL, run_id"
            " FROM public.customer_segment_history WHERE customer_id = %s"
            " ORDER BY valid_from, history_id",
            (customer,),
        ).fetchall()
    ]


def _events(owner: psycopg.Connection, customer: uuid.UUID) -> int:
    row = owner.execute(
        "SELECT count(*) FROM segments_events.outbox WHERE stream = %s"
        " AND entity_id = %s",
        (ASSIGNMENT, str(customer)),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _two_moments(
    shop: Shop,
    service: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
    customer: uuid.UUID,
) -> tuple[str, str]:
    """A sale 5 days old, scored at `now` and again 150 days later: the recency alone
    crosses a boundary of the version, so the label differs. Returns both labels."""
    shop.sale(customer, days_ago(now, 5))
    rules = recompute.require_published(service, version.rule_set_id)
    from app import profile, store

    def label(at: datetime) -> str:
        measures = store.measures(service, customer, rules.window_days, at)
        built = profile.build(rules, measures, at)
        assert built.label_code is not None
        return built.label_code

    early, late = label(now), label(now + timedelta(days=150))
    assert early != late, "the scenario must cross a boundary"
    return early, late


# --- Acceptance a: a boundary is crossed -------------------------------------


def test_crossing_a_boundary_closes_the_row_and_opens_the_next_in_one_instant(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    early, late = _two_moments(shop, service, version, now, customer)

    _apply(service, customer, version, now, "SALE")
    _apply(service, customer, version, now + timedelta(days=150), "AGEING")

    rows = _history(owner, customer)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (early, "SALE", False),
        (late, "AGEING", True),
    ], "the previous assignment is kept, closed, and one row is open"
    closed, opened = owner.execute(
        "SELECT (array_agg(valid_to ORDER BY history_id))[1],"
        "       (array_agg(valid_from ORDER BY history_id))[2]"
        " FROM public.customer_segment_history WHERE customer_id = %s",
        (customer,),
    ).fetchone() or (None, None)
    assert closed == opened, "closed at the instant the next opens"
    open_rows = owner.execute(
        "SELECT count(*) FROM public.customer_segment_history"
        " WHERE customer_id = %s AND valid_to IS NULL",
        (customer,),
    ).fetchone()
    assert open_rows == (1,)


def test_the_change_and_its_row_commit_together_or_not_at_all(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))

    class Abandon(Exception):
        pass

    with pytest.raises(Abandon):
        with service.transaction():
            recompute.customer(
                service,
                customer,
                version.rule_set_id,
                now,
                recompute.Applying(version, "SALE", uuid.uuid4()),
            )
            raise Abandon

    assert shop.profile(customer) is None, "no profile without the assignment"
    assert _history(owner, customer) == []
    assert _events(owner, customer) == 0, "a rolled-back change announces nothing"


# --- Acceptance b: no boundary crossed ---------------------------------------


def test_a_change_that_crosses_no_boundary_changes_no_assignment(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    _apply(service, customer, version, now)
    before = _history(owner, customer)
    runs = owner.execute("SELECT count(*) FROM public.segmentation_run").fetchone()

    # A day later: the recency moved, no boundary did.
    _apply(service, customer, version, now + timedelta(days=1), "AGEING")

    assert _history(owner, customer) == before
    assert (
        owner.execute("SELECT count(*) FROM public.segmentation_run").fetchone() == runs
    )
    assert _events(owner, customer) == 1, "only the first assignment was announced"


# --- Acceptance c: announced, with its cause and version ---------------------


def test_each_row_records_its_cause_and_the_version_and_the_event_is_in_the_outbox(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    _two_moments(shop, service, version, now, customer)
    batch = uuid.uuid4()
    _apply(service, customer, version, now, "SALE", batch)
    _apply(service, customer, version, now + timedelta(days=150), "AGEING")

    runs = owner.execute(
        "SELECT r.scope, r.executed_by, r.customer_count,"
        "       (r.parameters ->> 'model_version_id')::bigint,"
        "       r.parameters ->> 'cause', r.parameters ->> 'batch_id'"
        " FROM public.customer_segment_history AS h"
        " JOIN public.segmentation_run AS r USING (run_id)"
        " WHERE h.customer_id = %s ORDER BY h.history_id",
        (customer,),
    ).fetchall()
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in runs] == [
        ("INCREMENTAL", None, 1, version.version_id, "SALE"),
        ("INCREMENTAL", None, 1, version.version_id, "AGEING"),
    ]
    assert runs[0][5] == str(batch)
    assert _events(owner, customer) == 2, "opened, then changed"
    event = owner.execute(
        "SELECT event_type FROM segments_events.outbox WHERE stream = %s"
        " AND entity_id = %s",
        (ASSIGNMENT, str(customer)),
    ).fetchone()
    assert event == ("INSERT",)


def test_a_batch_is_one_run_and_a_redelivered_one_adds_nothing(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    first, second = shop.customer(), shop.customer()
    shop.sale(first, days_ago(now, 5))
    shop.sale(second, days_ago(now, 6))
    batch = uuid.uuid4()
    _apply(service, first, version, now, "AGEING", batch)
    _apply(service, second, version, now, "AGEING", batch)
    _apply(service, first, version, now, "AGEING", batch)  # redelivered

    runs = {_history(owner, first)[0][3], _history(owner, second)[0][3]}
    assert len(runs) == 1
    count = owner.execute(
        "SELECT customer_count FROM public.segmentation_run WHERE run_id = %s",
        (runs.pop(),),
    ).fetchone()
    assert count == (2,)


def test_a_web_run_still_reads_as_a_full_run_with_the_cause_of_a_full_run(
    owner: psycopg.Connection,
) -> None:
    row = owner.execute(
        "SELECT scope FROM public.segmentation_run WHERE parameters ->> 'batch_id'"
        " IS NULL ORDER BY run_id LIMIT 1"
    ).fetchone()
    assert row == ("FULL",)
    causes = owner.execute(
        "SELECT DISTINCT cause FROM public.customer_segment_history"
        " WHERE run_id IN (SELECT run_id FROM public.segmentation_run"
        "                  WHERE scope = 'FULL')"
    ).fetchall()
    assert ("FULL_RUN",) in causes


# --- Acceptance d: no purchases in the window --------------------------------


def test_a_customer_with_no_purchase_in_the_window_is_recorded_as_unassigned(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 400))  # outside the window
    _apply(service, customer, version, now)

    assert _history(owner, customer) == [
        (None, "SALE", True, _history(owner, customer)[0][3])
    ]
    state = owner.execute(
        "SELECT state, label_code FROM api_segments.current_label_v1"
        " WHERE customer_id = %s",
        (customer,),
    ).fetchone()
    assert state == ("unassigned_result", None)
    assert _events(owner, customer) == 1, "a first assignment is announced"


def test_an_unassigned_customer_who_buys_gets_a_label_and_the_unassigned_row_is_kept(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    _apply(service, customer, version, now)
    shop.sale(customer, days_ago(now, 1))
    _apply(service, customer, version, now + timedelta(seconds=1))

    rows = _history(owner, customer)
    assert [(r[0] is None, r[2]) for r in rows] == [(True, False), (False, True)]


# --- Acceptance e: consent excluding profiling (ADR-0066 section 2) ----------


def test_a_customer_who_withdrew_profiling_is_closed_and_reported_unassigned(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    _apply(service, customer, version, now)
    assert _history(owner, customer)[0][0] is not None

    shop.consent(customer, "WITHDRAWN")
    _apply(service, customer, version, now + timedelta(seconds=1), "CONSENT_GRANTED")
    rows = _history(owner, customer)
    assert [(r[0] is None, r[1], r[2]) for r in rows] == [
        (False, "SALE", False),
        (True, "CONSENT_WITHDRAWN", True),
    ], "the open assignment is closed and the customer is unassigned"
    profile = shop.profile(customer)
    assert profile is not None and profile[0] == "unassigned"
    assert profile[3] == 0, "nothing about their purchases is kept in the profile"

    # A later sale skips them: the unassigned result stands, no new row.
    shop.sale(customer, days_ago(now, 0))
    _apply(service, customer, version, now + timedelta(seconds=2), "SALE")
    assert _history(owner, customer) == rows


def test_a_customer_with_no_purchases_who_withdraws_changes_the_reason_not_the_label(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    _apply(service, customer, version, now)
    shop.consent(customer, "WITHDRAWN")
    _apply(service, customer, version, now + timedelta(seconds=1), "CONSENT_GRANTED")
    rows = _history(owner, customer)
    assert [r[1] for r in rows] == ["SALE", "CONSENT_WITHDRAWN"]
    assert _events(owner, customer) == 1, "the label did not change: one event"


def test_granting_profiling_again_scores_the_customer_from_the_next_change(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    shop.consent(customer, "WITHDRAWN")
    _apply(service, customer, version, now)
    shop.consent(customer, "GRANTED")
    _apply(service, customer, version, now + timedelta(seconds=1), "CONSENT_GRANTED")
    rows = _history(owner, customer)
    assert [(r[0] is None, r[1]) for r in rows] == [
        (True, "CONSENT_WITHDRAWN"),
        (False, "CONSENT_GRANTED"),
    ]


def test_a_customer_with_no_decision_is_segmented(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    _apply(service, customer, version, now)
    assert _history(owner, customer)[0][0] is not None


def test_the_consent_event_withdraws_without_waiting_for_a_sale(
    build_app: Callable[..., Flask],
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    _apply(service, customer, version, now)
    app = build_app()
    shop.consent(customer, "WITHDRAWN")
    entry = {
        "event_id": str(uuid.uuid4()),
        "event_type": "INSERT",
        "entity_id": str(customer),
        "occurred_at": "2026-11-01T12:00:00Z",
        "v": "1",
    }
    app.extensions["rfm_handle_consent"](entry)
    rows = _history(owner, customer)
    assert rows[-1][1:3] == ("CONSENT_WITHDRAWN", True)
    app.extensions["rfm_handle_consent"](entry)  # redelivered
    assert _history(owner, customer) == rows


# --- The events of a stream apply the change, and the function's refusals ----


def test_a_sale_event_applies_the_change_under_the_published_version(
    build_app: Callable[..., Flask],
    shop: Shop,
    owner: psycopg.Connection,
    now: datetime,
) -> None:
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 5))
    app = build_app()
    app.extensions["rfm_handle_sale"](
        {"event_id": str(uuid.uuid4()), "entity_id": str(sale)}
    )
    rows = _history(owner, customer)
    assert [(r[0] is not None, r[1], r[2]) for r in rows] == [(True, "SALE", True)]
    published = owner.execute(
        "SELECT version_id FROM api_models.published_version_v1"
        " WHERE method = 'RFM_RULES'"
    ).fetchone()
    recorded = owner.execute(
        "SELECT (parameters ->> 'model_version_id')::bigint"
        " FROM public.segmentation_run WHERE run_id = %s",
        (rows[0][3],),
    ).fetchone()
    assert recorded == published


def test_the_function_refuses_an_unknown_label_and_names_the_constraint(
    shop: Shop, service: psycopg.Connection
) -> None:
    customer = shop.customer()
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        service.execute(
            "SELECT api_segments.apply_assignment_v1(%s::uuid, %s::uuid,"
            " 'NOPE'::varchar, 3::smallint, 3::smallint, 3::smallint, now(), 1,"
            " 10::numeric, 'SALE', 1::bigint, 'RFM_RULES', 180)",
            (uuid.uuid4(), customer),
        )
    assert refused.value.diag.constraint_name == "unknown_label"


def test_the_function_refuses_an_unknown_customer_and_a_score_out_of_range(
    service: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        service.execute(
            "SELECT api_segments.apply_assignment_v1(%s::uuid, %s::uuid, NULL::varchar,"
            " NULL::smallint, NULL::smallint, NULL::smallint, NULL::timestamptz, 0,"
            " 0::numeric, 'SALE', 1::bigint, 'RFM_RULES', 180)",
            (uuid.uuid4(), uuid.uuid4()),
        )
    assert refused.value.diag.constraint_name == "unknown_customer"
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        service.execute(
            "SELECT api_segments.apply_assignment_v1(%s::uuid, %s::uuid,"
            " 'LOYAL'::varchar, 9::smallint, 3::smallint, 3::smallint, now(), 1,"
            " 10::numeric, 'SALE', 1::bigint, 'RFM_RULES', 180)",
            (uuid.uuid4(), uuid.uuid4()),
        )


def test_the_function_refuses_the_web_causes_and_a_batch_that_disagrees(
    shop: Shop, service: psycopg.Connection, version: recompute.Version, now: datetime
) -> None:
    customer, other = shop.customer(), shop.customer()
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        service.execute(
            "SELECT api_segments.apply_assignment_v1(%s::uuid, %s::uuid, NULL::varchar,"
            " NULL::smallint, NULL::smallint, NULL::smallint, NULL::timestamptz, 0,"
            " 0::numeric, 'FULL_RUN', 1::bigint, 'RFM_RULES', 180)",
            (uuid.uuid4(), customer),
        )
    batch = uuid.uuid4()
    _apply(service, customer, version, now, "SALE", batch)
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        _apply(service, other, version, now, "AGEING", batch)
    assert refused.value.diag.constraint_name == "batch_mismatch"


def test_the_schema_refuses_a_second_open_row_whoever_writes_it(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    """RN-20 does not rest on the function: a direct second open row is refused."""
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    _apply(service, customer, version, now)
    run = _history(owner, customer)[0][3]
    with pytest.raises(psycopg.errors.Error):
        owner.execute(
            "INSERT INTO public.customer_segment_history (customer_id, run_id,"
            " label_code, valid_from) VALUES (%s, %s, 'LOST', now())",
            (customer, run + 0),
        )


def test_two_changes_of_one_customer_at_once_leave_one_open_row(
    shop: Shop,
    service_url: str,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    """The per-customer lock: both transactions start together, one writes, the other
    waits and compares against what the first wrote."""
    import threading

    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    start = threading.Barrier(2)
    failures: list[BaseException] = []

    def change() -> None:
        try:
            with psycopg.connect(service_url) as connection:
                start.wait(10)
                with connection.transaction():
                    recompute.customer(
                        connection,
                        customer,
                        version.rule_set_id,
                        now,
                        recompute.Applying(version, "SALE", uuid.uuid4()),
                    )
        except BaseException as error:  # noqa: BLE001
            failures.append(error)

    threads = [threading.Thread(target=change) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert failures == []
    rows = _history(owner, customer)
    assert len(rows) == 1 and rows[0][2], "one row, and it is open"
