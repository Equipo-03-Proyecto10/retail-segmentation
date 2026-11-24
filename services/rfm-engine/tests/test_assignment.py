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
    found = recompute.in_force(service, RULE_SET)
    assert found is not None and found.method == "RFM_RULES"
    return found


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
        "       r.parameters ->> 'cause', r.parameters ->> 'batch_id',"
        "       (r.parameters ->> 'rule_set_id')::bigint"
        " FROM public.customer_segment_history AS h"
        " JOIN public.segmentation_run AS r USING (run_id)"
        " WHERE h.customer_id = %s ORDER BY h.history_id",
        (customer,),
    ).fetchall()
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in runs] == [
        ("INCREMENTAL", None, 1, version.version_id, "SALE"),
        ("INCREMENTAL", None, 1, version.version_id, "AGEING"),
    ]
    applying = recompute.Applying(version, "SALE", batch)
    assert runs[0][5] == str(recompute.effective_batch(applying, "SALE"))
    assert {r[6] for r in runs} == {version.rule_set_id}, "the rule set scored under"
    assert _events(owner, customer) == 2, "opened, then changed"
    event = owner.execute(
        "SELECT event_type FROM segments_events.outbox WHERE stream = %s"
        " AND entity_id = %s",
        (ASSIGNMENT, str(customer)),
    ).fetchone()
    assert event == ("INSERT",)


def test_an_incremental_run_is_linked_to_the_version_it_was_scored_under(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    """The run a sale records is linked in models.run_version to the published
    version, in the transaction that records it, so segments reads its version like a
    full run's (#1208, ADR-0092). A rolled-back change leaves no link either."""
    customer = shop.customer()
    _two_moments(shop, service, version, now, customer)
    _apply(service, customer, version, now, "SALE")
    _apply(service, customer, version, now + timedelta(days=150), "AGEING")

    links = owner.execute(
        "SELECT r.scope, v.version_id, (r.parameters ->> 'model_version_id')::bigint"
        " FROM public.customer_segment_history AS h"
        " JOIN public.segmentation_run AS r USING (run_id)"
        " LEFT JOIN api_models.run_version_v1 AS v USING (run_id)"
        " WHERE h.customer_id = %s ORDER BY h.history_id",
        (customer,),
    ).fetchall()
    assert links == [
        ("INCREMENTAL", version.version_id, version.version_id),
        ("INCREMENTAL", version.version_id, version.version_id),
    ]

    rolled_back = shop.customer()
    shop.sale(rolled_back, days_ago(now, 5))
    (before,) = owner.execute("SELECT count(*) FROM models.run_version").fetchone()
    with pytest.raises(RuntimeError):
        with service.transaction():
            recompute.customer(
                service,
                rolled_back,
                version.rule_set_id,
                now,
                recompute.Applying(version, "SALE", uuid.uuid4()),
            )
            raise RuntimeError("roll back")
    assert _history(owner, rolled_back) == []
    assert owner.execute("SELECT count(*) FROM models.run_version").fetchone() == (
        before,
    )


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
    assert _events(owner, customer) == 2, "the reason changed, so it is announced"
    reason = owner.execute(
        "SELECT state, unassigned_reason FROM api_segments.current_label_v1"
        " WHERE customer_id = %s",
        (customer,),
    ).fetchone()
    assert reason == ("unassigned_result", "PROFILING_WITHDRAWN")


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
            " 10::numeric, 'SALE', 1::bigint, 'RFM_RULES', 180, 1::bigint)",
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
            " 0::numeric, 'SALE', 1::bigint, 'RFM_RULES', 180, 1::bigint)",
            (uuid.uuid4(), uuid.uuid4()),
        )
    assert refused.value.diag.constraint_name == "unknown_customer"
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        service.execute(
            "SELECT api_segments.apply_assignment_v1(%s::uuid, %s::uuid,"
            " 'LOYAL'::varchar, 9::smallint, 3::smallint, 3::smallint, now(), 1,"
            " 10::numeric, 'SALE', 1::bigint, 'RFM_RULES', 180, 1::bigint)",
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
            " 0::numeric, 'FULL_RUN', 1::bigint, 'RFM_RULES', 180, 1::bigint)",
            (uuid.uuid4(), customer),
        )
    batch = uuid.uuid4()
    _apply(service, customer, version, now, "SALE", batch)
    named = recompute.effective_batch(
        recompute.Applying(version, "SALE", batch), "SALE"
    )
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        service.execute(
            "SELECT api_segments.apply_assignment_v1(%s::uuid, %s::uuid,"
            " 'LOYAL'::varchar, 3::smallint, 3::smallint, 3::smallint, now(), 1,"
            " 10::numeric, 'AGEING', %s::bigint, 'RFM_RULES', 180, %s::bigint)",
            (named, other, version.version_id, version.rule_set_id),
        )
    assert refused.value.diag.constraint_name == "batch_mismatch"


def test_the_schema_refuses_a_second_open_row_whoever_writes_it(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    """RN-20 does not rest on the function: a direct second open row, under another
    run so the one-row-per-run key is not what refuses it, is refused by the open-row
    index or the no-overlap constraint, and by nothing else."""
    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    _apply(service, customer, version, now)
    run = _history(owner, customer)[0][3]
    other = owner.execute(
        "SELECT run_id FROM public.segmentation_run WHERE run_id <> %s"
        " ORDER BY run_id LIMIT 1",
        (run,),
    ).fetchone()
    assert other is not None
    with pytest.raises(
        (psycopg.errors.UniqueViolation, psycopg.errors.ExclusionViolation)
    ) as refused:
        owner.execute(
            "INSERT INTO public.customer_segment_history (customer_id, run_id,"
            " label_code, valid_from) VALUES (%s, %s, 'LOST', now())",
            (customer, other[0]),
        )
    assert refused.value.diag.constraint_name in (
        "ux_customer_segment_history_open",
        "ex_customer_segment_history_no_overlap",
    )


def test_two_changes_of_one_customer_at_once_leave_one_open_row(
    shop: Shop,
    service_url: str,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    """The function on its own, called from two connections at once with batches of
    their own: one opens the row, the other waits on 300001 and finds it unchanged."""
    import threading

    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    start = threading.Barrier(2)
    failures: list[BaseException] = []

    results: list[str] = []

    def change() -> None:
        try:
            with psycopg.connect(service_url) as connection:
                start.wait(10)
                with connection.transaction():
                    row = connection.execute(
                        "SELECT api_segments.apply_assignment_v1(%s::uuid, %s::uuid,"
                        " 'LOYAL'::varchar, 3::smallint, 3::smallint, 3::smallint,"
                        " now(), 1, 10::numeric, 'SALE', %s::bigint, 'RFM_RULES',"
                        " 180, %s::bigint)",
                        (
                            uuid.uuid4(),
                            customer,
                            version.version_id,
                            version.rule_set_id,
                        ),
                    ).fetchone()
                    assert row is not None
                    results.append(row[0])
        except BaseException as error:  # noqa: BLE001
            failures.append(error)

    threads = [threading.Thread(target=change) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert failures == []
    assert sorted(results) == ["opened", "unchanged"]
    rows = _history(owner, customer)
    assert len(rows) == 1 and rows[0][2], "one row, and it is open"


# --- What the review of #1124 found ------------------------------------------


def _entry(entity: object) -> dict[str, str]:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "INSERT",
        "entity_id": str(entity),
        "occurred_at": "2026-11-01T12:00:00Z",
        "v": "1",
    }


def test_a_withdrawn_customer_inside_a_pass_does_not_stop_it(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    service_url: str,
    version: recompute.Version,
    now: datetime,
) -> None:
    """The pass applies its customers as AGEING, and a customer who withdrew PROFILING
    (with no consent event read yet) as CONSENT_WITHDRAWN. A run has one cause, so each
    is a batch of its own and neither refuses the other."""
    from mosaiq_kit.database import Database

    from app import ageing

    aged, withdrawn = shop.customer(), shop.customer()
    for customer in (aged, withdrawn):
        shop.sale(customer, days_ago(now, 200))
        _apply(service, customer, version, days_ago(now, 100))
    shop.consent(withdrawn, "WITHDRAWN")
    owner.execute("DELETE FROM rfm.pass_run")
    db = Database(service_url)
    try:
        run = ageing.run_pass(db, version.rule_set_id, version)
    finally:
        db.close()
        owner.execute("DELETE FROM rfm.pass_run")

    assert run is not None and run.status == "COMPLETED"
    assert _history(owner, aged)[-1][:3] == (None, "AGEING", True)
    assert _history(owner, withdrawn)[-1][:3] == (None, "CONSENT_WITHDRAWN", True)
    assert _history(owner, aged)[-1][3] != _history(owner, withdrawn)[-1][3]


def test_a_batch_under_another_version_is_a_run_of_its_own(
    shop: Shop,
    service: psycopg.Connection,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    """A pass resumed after a publication records what it changes since under the new
    version, in a run of its own, instead of refusing every customer."""
    first, second = shop.customer(), shop.customer()
    shop.sale(first, days_ago(now, 5))
    shop.sale(second, days_ago(now, 6))
    batch = uuid.uuid4()
    _apply(service, first, version, now, "AGEING", batch)
    # Another version the registry holds: the run is linked to it (ADR-0092), so it
    # must exist.
    other = owner.execute(
        "SELECT min(version_id) FROM models.model_version WHERE version_id <> %s",
        (version.version_id,),
    ).fetchone()
    assert other is not None and other[0] is not None
    later = recompute.Version(other[0], version.rule_set_id, version.window_days)
    _apply(service, second, later, now, "AGEING", batch)
    assert _history(owner, first)[0][3] != _history(owner, second)[0][3]


def test_a_change_waits_for_a_web_run_that_holds_its_lock(
    shop: Shop,
    service_url: str,
    owner_url: str,
    owner: psycopg.Connection,
    version: recompute.Version,
    now: datetime,
) -> None:
    """The web's run takes 285001, reads its instant, and closes and opens rows at
    it. A change arriving meanwhile waits for the run instead of committing a row the
    run's instant could not close, so neither transaction fails."""
    import threading
    import time

    customer = shop.customer()
    shop.sale(customer, days_ago(now, 5))
    failures: list[BaseException] = []

    def change() -> None:
        try:
            with psycopg.connect(service_url) as connection:
                with connection.transaction():
                    recompute.customer(
                        connection,
                        customer,
                        version.rule_set_id,
                        None,
                        recompute.Applying(version, "SALE", uuid.uuid4()),
                    )
        except BaseException as error:  # noqa: BLE001
            failures.append(error)

    with psycopg.connect(owner_url) as web:
        with web.transaction():
            web.execute("SELECT pg_advisory_xact_lock(285001)")
            instant = web.execute("SELECT clock_timestamp()").fetchone()
            assert instant is not None
            thread = threading.Thread(target=change)
            thread.start()
            time.sleep(0.5)
            assert thread.is_alive(), "the change waits for the web's run"
            web.execute(
                "UPDATE public.customer_segment_history SET valid_to = %s"
                " WHERE valid_to IS NULL AND customer_id = %s",
                (instant[0], customer),
            )
            web.execute(
                "INSERT INTO public.customer_segment_history (customer_id, run_id,"
                " label_code, valid_from) VALUES (%s,"
                " (SELECT min(run_id) FROM public.segmentation_run), 'LOST', %s)",
                (customer, instant[0]),
            )
        thread.join(30)

    assert failures == []
    rows = owner.execute(
        "SELECT valid_to IS NULL, valid_to >= valid_from"
        " FROM public.customer_segment_history WHERE customer_id = %s",
        (customer,),
    ).fetchall()
    assert sum(1 for open_, _ in rows if open_) == 1
    assert all(ordered for open_, ordered in rows if not open_)


def test_under_a_k_means_version_profiles_are_kept_and_only_a_withdrawal_applies(
    build_app: Callable[..., Flask],
    monkeypatch: pytest.MonkeyPatch,
    shop: Shop,
    owner: psycopg.Connection,
    now: datetime,
) -> None:
    """One version is published at a time. Under a K-means one rfm-engine has no
    centroids, so a sale still writes the profile (no entry is lost) and moves no
    label, and a withdrawal is still recorded, under that version and its window."""
    from app import store

    # A K-means version the registry holds: the run is linked to it (ADR-0092).
    found = owner.execute(
        "SELECT min(version_id) FROM models.model_version WHERE method = %s",
        ("KMEANS",),
    ).fetchone()
    assert found is not None and found[0] is not None
    kmeans = found[0]
    monkeypatch.setattr(
        store, "published_version", lambda c: (kmeans, "KMEANS", None, 90)
    )
    kept, left = shop.customer(), shop.customer()
    sale = shop.sale(kept, days_ago(now, 5))
    shop.sale(left, days_ago(now, 5))
    app = build_app()

    app.extensions["rfm_handle_sale"](_entry(sale))
    assert shop.profile(kept) is not None
    assert _history(owner, kept) == []

    shop.consent(left, "WITHDRAWN")
    app.extensions["rfm_handle_consent"](_entry(left))
    rows = _history(owner, left)
    assert [(r[0], r[1]) for r in rows] == [(None, "CONSENT_WITHDRAWN")]
    run = owner.execute(
        "SELECT method, window_days, (parameters ->> 'model_version_id')::bigint"
        " FROM public.segmentation_run WHERE run_id = %s",
        (rows[0][3],),
    ).fetchone()
    assert run == ("KMEANS", 90, kmeans)


def test_with_no_version_published_the_profile_is_written_and_nothing_applied(
    build_app: Callable[..., Flask],
    monkeypatch: pytest.MonkeyPatch,
    shop: Shop,
    owner: psycopg.Connection,
    now: datetime,
) -> None:
    from app import store

    monkeypatch.setattr(store, "published_version", lambda c: None)
    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 5))
    build_app().extensions["rfm_handle_sale"](_entry(sale))
    assert shop.profile(customer) is not None
    assert _history(owner, customer) == []


def test_a_consent_event_records_consent_granted_only_when_profiling_is_granted(
    build_app: Callable[..., Flask],
    shop: Shop,
    owner: psycopg.Connection,
    now: datetime,
) -> None:
    """The entry does not say which purpose moved. A customer with no PROFILING
    decision was not granted anything, so what moves them is the clock."""
    undecided, granted = shop.customer(), shop.customer()
    for customer in (undecided, granted):
        shop.sale(customer, days_ago(now, 5))
    shop.consent(granted, "GRANTED")
    app = build_app()
    for customer in (undecided, granted):
        app.extensions["rfm_handle_consent"](_entry(customer))
    assert _history(owner, undecided)[0][1] == "AGEING"
    assert _history(owner, granted)[0][1] == "CONSENT_GRANTED"
