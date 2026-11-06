"""Segment parity between the web and segments-service (F20-09).

Both read the same `customer_segment_history`: the web directly, the service
through `api_segments.current_label_v1` and its cache, which
`stream:segments.v1.run_recorded` flushes. These cases compare what a user of
each sees: a customer's current segment on /catalog/customers/<id> against
GET /v1/segment-assignments/current, and a label's open members on
/segment-history-report/?label= against GET /v1/segment-assignments?label_code=.
Differences are printed in full.

The second case adds a customer with no sales and runs a segmentation through
the web, on the throwaway stack named by PARITY_DATABASE_URL; never point it
at the instance. That customer is how the case sees both states the seed has
none of: no open row before the run, and the run's unassigned result after it
(RN-21).
"""

from __future__ import annotations

import uuid

from tests.parity.compare import (
    NO_OPEN_ASSIGNMENT,
    UNASSIGNED_RESULT,
    diff_assignments,
    diff_members,
)
from tests.parity.stack import SEGMENTS_RUN_RECORDED_BOUND_SECONDS, poll

RUN_RECORDED = "stream:segments.v1.run_recorded"


def _report(found) -> str:
    return "\n".join(str(d) for d in found)


def _customers(db) -> list[str]:
    rows = db("SELECT customer_id::text FROM customer ORDER BY customer_id")
    assert rows, "the seed has no customers"
    return [r[0] for r in rows]


def _read_all(path, customers: list[str]) -> dict:
    return {c: path.current_segment(c) for c in customers}


def test_every_customer_has_the_same_current_segment(
    db, web, segments, differences
) -> None:
    customers = _customers(db)
    differences.extend(
        diff_assignments(_read_all(web, customers), _read_all(segments, customers))
    )
    print("\n" + _report(differences) if differences else "\nno difference")
    assert not differences, _report(differences)


def test_a_web_run_reaches_the_service_within_the_bound(
    db, web, segments, differences
) -> None:
    [[newcomer]] = db(
        "INSERT INTO customer (name, registration_channel_id) "
        "SELECT %s, min(channel_id) FROM channel RETURNING customer_id::text",
        ("Parity " + uuid.uuid4().hex[:8],),
    )
    customers = _customers(db)
    # Read through the service first, so whatever it caches is from before.
    before_run = _read_all(web, customers)
    assert before_run[newcomer].state == NO_OPEN_ASSIGNMENT, before_run[newcomer]
    differences.extend(diff_assignments(before_run, _read_all(segments, customers)))
    [[before]] = db("SELECT coalesce(max(run_id), 0) FROM segmentation_run")

    web.run_segmentation()

    [[run_id]] = db("SELECT max(run_id) FROM segmentation_run")
    assert run_id > before, "the web recorded no run"
    seen_by_web = _read_all(web, customers)
    assert any(
        a.run_id == run_id for a in seen_by_web.values()
    ), f"no customer's open row names run {run_id}"
    assert seen_by_web[newcomer].state == UNASSIGNED_RESULT, seen_by_web[newcomer]

    seen_by_service = poll(
        lambda: _read_all(segments, customers),
        seen_by_web,
        SEGMENTS_RUN_RECORDED_BOUND_SECONDS,
    )
    differences.extend(diff_assignments(seen_by_web, seen_by_service))
    # The event the service flushes on: relayed means it reached the stream.
    relayed = poll(
        lambda: db(
            "SELECT relayed_at IS NOT NULL FROM segments_events.outbox "
            "WHERE stream = %s AND entity_id = %s",
            (RUN_RECORDED, str(run_id)),
        ),
        [(True,)],
        SEGMENTS_RUN_RECORDED_BOUND_SECONDS,
    )
    print("\n" + _report(differences) if differences else "\nno difference")
    assert not differences, _report(differences)
    assert relayed == [(True,)], f"run {run_id}'s event: {relayed!r}"


def test_every_label_has_the_same_members(db, web, segments, differences) -> None:
    labels = [
        r[0]
        for r in db("SELECT label_code FROM segment_label ORDER BY ordinal_position")
    ]
    assert labels, "the seed has no labels"
    listed = 0
    for label in labels:
        on_web = web.label_members(label)
        listed += len(on_web)
        differences.extend(diff_members(label, on_web, segments.label_members(label)))
    print("\n" + _report(differences) if differences else "\nno difference")
    assert not differences, _report(differences)
    assert listed, "no label has an open member, so nothing was compared"
