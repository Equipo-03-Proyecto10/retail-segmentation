"""The web's segment reports beside incremental changes (F22-17, #813).

ADR-0063 lets rfm-engine record a batch of changes as one `INCREMENTAL` run whose
`customer_count` is the customers it changed, and ADR-0087 says the web's reports
must then select `FULL` runs. A one-customer run must never become the dashboard's
snapshot (RN-41), the "previous run" of a migration, a run the comparison offers,
or a run the history lists as though it were a population.

These run against the database the three scripts load. Each test writes inside a
transaction that always rolls back, as the owner, so nothing is left behind: the
rows are what `api_segments.apply_assignment_v1` writes (an `INCREMENTAL` run, the
customer's open row closed, a new open row with no segment and cause `SALE`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from web.db.customers import list_customers, list_customers_in_segment
from web.db.model_comparison import list_runs_of_method
from web.db.segmentation_dashboard import get_previous_run
from web.db.segments import get_current_assignment, get_run, list_runs
from web.services.segment_migration import compute_migration, order_runs
from web.services.segmentation_dashboard import build_dashboard

pytestmark = pytest.mark.postgres


@dataclass(frozen=True)
class World:
    connection: psycopg.Connection
    full: int  # the newest full run the seed holds
    incremental: int  # one customer's change, after it
    later_full: int  # a full run after both
    customer: str  # the customer the incremental run moved
    label: str  # the label they hold now, no segment named


def _one(connection: psycopg.Connection, sql: str, params: tuple = ()) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def _run(connection: psycopg.Connection, at, scope: str, count: int) -> int:
    return _one(
        connection,
        "INSERT INTO segmentation_run"
        " (method, window_days, parameters, customer_count, run_at, scope, data_origin)"
        " VALUES ('RFM_RULES', 180, '{}'::jsonb, %s, %s, %s, 'SEEDED')"
        " RETURNING run_id",
        (count, at, scope),
    )[0]


@pytest.fixture
def world(owner_url: str) -> Iterator[World]:
    connection = psycopg.connect(owner_url)
    try:
        full, run_at = _one(
            connection,
            "SELECT run_id, run_at FROM segmentation_run WHERE scope = 'FULL'"
            " ORDER BY run_at DESC, run_id DESC LIMIT 1",
        )
        customer, label = _one(
            connection,
            "SELECT customer_id::text, label_code FROM customer_segment_history"
            " WHERE valid_to IS NULL AND label_code IS NOT NULL"
            " ORDER BY customer_id LIMIT 1",
        )
        (moved_to,) = _one(
            connection,
            "SELECT label_code FROM segment_label WHERE label_code <> %s"
            " ORDER BY ordinal_position LIMIT 1",
            (label,),
        )
        changed_at = run_at + timedelta(days=1)
        incremental = _run(connection, changed_at, "INCREMENTAL", 1)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE customer_segment_history SET valid_to = %s"
                " WHERE customer_id = %s AND valid_to IS NULL",
                (changed_at, customer),
            )
            cursor.execute(
                "INSERT INTO customer_segment_history"
                " (customer_id, run_id, segment_id, label_code, valid_from, cause)"
                " VALUES (%s, %s, NULL, %s, %s, 'SALE')",
                (customer, incremental, moved_to, changed_at),
            )
        later_full = _run(connection, run_at + timedelta(days=2), "FULL", 30)
        yield World(connection, full, incremental, later_full, customer, moved_to)
    finally:
        connection.rollback()
        connection.close()


# ---------- the dashboard (RN-41) and the pages that pick a run ----------


def test_the_dashboard_still_reads_the_newest_full_run(world: World) -> None:
    """On the unchanged code the newest run is the one-customer change."""
    with_no_later_full = world.connection
    with_no_later_full.execute(
        "DELETE FROM segmentation_run WHERE run_id = %s", (world.later_full,)
    )

    dashboard = build_dashboard(world.connection, None)

    assert dashboard.run.run_id == world.full
    assert dashboard.run.scope == "FULL"


def test_a_migration_compares_a_run_with_the_full_run_before_it(world: World) -> None:
    previous = get_previous_run(world.connection, world.later_full)

    assert previous is not None and previous.run_id == world.full


def test_a_change_is_not_a_snapshot_to_put_on_the_dashboard(world: World) -> None:
    from web.services.segment_migration import NotASnapshot

    with pytest.raises(NotASnapshot, match=str(world.incremental)):
        build_dashboard(world.connection, world.incremental)


def test_a_change_is_not_a_run_to_migrate_from_or_to(world: World) -> None:
    from web.services.segment_migration import NotASnapshot

    with pytest.raises(NotASnapshot):
        order_runs(world.connection, world.full, world.incremental)
    with pytest.raises(NotASnapshot):
        compute_migration(world.connection, world.incremental, world.later_full)


def test_two_full_runs_still_migrate_around_the_change(world: World) -> None:
    migrations = compute_migration(world.connection, world.full, world.later_full)

    assert len(migrations) >= 30


def test_the_pickers_offer_full_runs_only(world: World) -> None:
    full_runs, _ = list_runs(world.connection, page=1, per_page=100, scope="FULL")
    offered = {run.run_id for run in full_runs}
    compared = {
        run.run_id
        for run in list_runs_of_method(world.connection, "RFM_RULES", limit=100)
    }

    assert world.incremental not in offered and world.full in offered
    assert world.incremental not in compared and world.later_full in compared


def test_the_history_can_list_both_and_says_which_each_is(world: World) -> None:
    runs, total = list_runs(world.connection, page=1, per_page=100)
    scopes = {run.run_id: run.scope for run in runs}
    _, incremental_total = list_runs(
        world.connection, page=1, per_page=100, scope="INCREMENTAL"
    )

    assert scopes[world.incremental] == "INCREMENTAL"
    assert scopes[world.full] == "FULL"
    assert incremental_total == 1 and total > incremental_total
    assert get_run(world.connection, world.incremental).customer_count == 1


# ---------- a customer changed since the last full run ----------


def test_the_web_shows_the_customer_as_the_segments_api_reads_them(
    world: World,
) -> None:
    """GET /v1/segment-assignments/current reads api_segments.current_label_v1;
    the web's own reads of the open row give the same label."""
    state, label, run_id = _one(
        world.connection,
        "SELECT state, label_code, run_id FROM api_segments.current_label_v1"
        " WHERE customer_id = %s",
        (world.customer,),
    )
    assignment = get_current_assignment(world.connection, world.customer)
    listed = {
        str(customer.customer_id): customer.label_name
        for customer in list_customers(
            world.connection, search=None, page=1, per_page=1000
        )[0]
    }
    (label_name,) = _one(
        world.connection,
        "SELECT name FROM segment_label WHERE label_code = %s",
        (world.label,),
    )

    assert (state, label, run_id) == ("assigned", world.label, world.incremental)
    assert assignment is not None and assignment.label_code == world.label
    assert listed[world.customer] == label_name


def test_a_segment_page_counts_who_holds_its_label_without_naming_it(
    world: World,
) -> None:
    """ADR-0087: a change names no segment, so the segment's own list misses
    them; the page counts them by the label the segment carries and says so."""
    from web.db.customers import count_label_holders_outside_segment

    (segment_id,) = _one(
        world.connection,
        "SELECT min(segment_id) FROM segment WHERE label_code = %s",
        (world.label,),
    )
    members, _ = list_customers_in_segment(
        world.connection, segment_id, page=1, per_page=1000
    )

    assert world.customer not in {member.customer_id for member in members}
    assert count_label_holders_outside_segment(world.connection, segment_id) >= 1
