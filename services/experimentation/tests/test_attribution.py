"""Sale attribution against PostgreSQL: one holder, apart from effect (F25-12).

ADR-0069. Every test builds its own experiments on its own copy of the seeded
database, so each holder is worked out by hand. Times are fixed instants, never
the clock, except the one test that needs a window that is still open.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import errors

from tests.conftest import ANALYST, AUDITOR, MARKETING
from tests.test_conversions import Scenario as _Base

pytestmark = pytest.mark.postgres

MARKETING_CALLER = (MARKETING, "MARKETING")
START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
WINDOW = 7


class World:
    """Two experiments with a campaign each, and one customer in both treatments.

    Customer 3 is assigned to the treatment arm of both at START, in experiment
    `first` before `second`, so `first` holds the lower assignment id.
    """

    def __init__(self, owner: psycopg.Connection) -> None:
        self.owner = owner
        self.first = _Base(owner)
        self.second = _Base(owner)
        self.customer = self.first.customers[3]
        self.sale = self.first.sale(3, START + timedelta(days=1))
        self.assignment = {}
        for name, scene, campaign in (
            ("first", self.first, 1),
            ("second", self.second, 2),
        ):
            owner.execute(
                "UPDATE experiment SET campaign_id = %s WHERE experiment_id = %s",
                (campaign, scene.experiment),
            )
            self.assignment[name] = owner.execute(
                "SELECT assignment_id FROM experiment_assignment"
                " WHERE experiment_id = %s AND customer_id = %s",
                (scene.experiment, self.customer),
            ).fetchone()[0]

    def expose(self, name: str, at: datetime) -> int:
        return self.owner.execute(
            "INSERT INTO experiment_exposure (assignment_id, exposed_at)"
            " VALUES (%s, %s) RETURNING exposure_id",
            (self.assignment[name], at),
        ).fetchone()[0]

    def run(self, api: Any) -> dict[str, Any]:
        for scene in (self.first, self.second):
            reply = api(
                "POST",
                f"/v1/experiments/{scene.experiment}/conversions",
                as_user=MARKETING_CALLER,
            )
            assert reply.status_code == 200, reply.get_json()
        reply = api(
            "POST",
            f"/v1/experiments/{self.first.experiment}/attributions",
            as_user=MARKETING_CALLER,
        )
        assert reply.status_code == 200, reply.get_json()
        return reply.get_json()

    def held(self, api: Any) -> list[dict[str, Any]]:
        reply = api("GET", f"/v1/sale-attributions?sale_id={self.sale}")
        assert reply.status_code == 200, reply.get_json()
        return reply.get_json()["attributions"]


@pytest.fixture
def world(owner: psycopg.Connection) -> World:
    return World(owner)


def _holder_campaign(world: World, api: Any) -> int:
    (row,) = world.held(api)
    return row["campaign_id"]


def test_a_sale_of_two_campaigns_has_one_holder_the_latest_exposure(
    api: Any, world: World
) -> None:
    world.expose("first", START + timedelta(hours=1))
    world.expose("second", START + timedelta(hours=2))
    outcome = world.run(api)
    assert outcome["attributed"] == 1
    (row,) = world.held(api)
    assert row["campaign_id"] == 2
    assert row["experiment_id"] == world.second.experiment
    assert row["assignment_id"] == world.assignment["second"]
    assert row["label"] == "ATTRIBUTION"
    assert row["counts"] is True and row["net_amount"] == "20.00"


def test_a_tie_goes_to_the_lower_assignment_id(api: Any, world: World) -> None:
    at = START + timedelta(hours=3)
    world.expose("second", at)
    world.expose("first", at)
    world.run(api)
    (row,) = world.held(api)
    assert row["assignment_id"] == min(world.assignment.values())
    assert row["experiment_id"] == world.first.experiment


def test_the_schema_refuses_a_second_holder_and_what_adr_0069_excludes(
    api: Any, world: World, owner: psycopg.Connection
) -> None:
    exposed_first = world.expose("first", START + timedelta(hours=1))
    exposed_second = world.expose("second", START + timedelta(hours=2))
    world.run(api)
    insert = (
        "INSERT INTO experiments.sale_attribution (sale_id, assignment_id, exposure_id)"
        " VALUES (%s, %s, %s)"
    )
    with pytest.raises(errors.UniqueViolation):
        owner.execute(insert, (world.sale, world.assignment["first"], exposed_first))
    # A fresh sale: the control assignment, a no-campaign experiment, an exposure
    # that does not precede the sale and a sale of someone else are all refused.
    other = world.first.sale(3, START + timedelta(days=2))
    control = owner.execute(
        "SELECT assignment_id FROM experiment_assignment WHERE experiment_id = %s"
        " AND group_id = %s LIMIT 1",
        (world.first.experiment, world.first.control),
    ).fetchone()[0]
    late = world.expose("first", START + timedelta(days=3))
    with pytest.raises(errors.CheckViolation) as excinfo:
        owner.execute(insert, (other, control, exposed_first))
    assert excinfo.value.diag.constraint_name in {
        "sale_attribution_treatment_only",
        "sale_attribution_exposure_before_sale",
    }
    with pytest.raises(errors.CheckViolation) as excinfo:
        owner.execute(insert, (other, world.assignment["first"], late))
    assert excinfo.value.diag.constraint_name == "sale_attribution_exposure_before_sale"
    owner.execute(
        "UPDATE experiment SET campaign_id = NULL WHERE experiment_id = %s",
        (world.first.experiment,),
    )
    with pytest.raises(errors.CheckViolation) as excinfo:
        owner.execute(insert, (other, world.assignment["first"], exposed_first))
    assert excinfo.value.diag.constraint_name == "sale_attribution_campaign_required"
    stranger = world.first.sale(4, START + timedelta(days=2))
    with pytest.raises(errors.CheckViolation) as excinfo:
        owner.execute(insert, (stranger, world.assignment["second"], exposed_second))
    assert excinfo.value.diag.constraint_name == "sale_attribution_sale_in_window"


def test_a_holder_is_never_changed_or_deleted_even_by_the_owner(
    api: Any, world: World, owner: psycopg.Connection
) -> None:
    world.expose("first", START + timedelta(hours=1))
    world.run(api)
    with pytest.raises(errors.CheckViolation):
        owner.execute(
            "UPDATE experiments.sale_attribution SET assignment_id = assignment_id"
        )
    with pytest.raises(errors.CheckViolation):
        owner.execute("DELETE FROM experiments.sale_attribution")


def test_running_again_holds_the_same_sales(api: Any, world: World) -> None:
    world.expose("first", START + timedelta(hours=1))
    first = world.run(api)
    before = world.held(api)
    again = world.run(api)
    assert (first["attributed"], again["attributed"]) == (1, 0)
    assert again["already_attributed"] == 1 and again["pending"] == 0
    assert again["label"] == "ATTRIBUTION"
    (after,) = world.held(api)
    assert {k: v for k, v in after.items() if k != "as_of"} == {
        k: v for k, v in before[0].items() if k != "as_of"
    }


def test_a_cancelled_sale_keeps_its_holder_and_stops_counting(
    api: Any, world: World, owner: psycopg.Connection
) -> None:
    world.expose("first", START + timedelta(hours=1))
    world.run(api)
    (before,) = world.held(api)
    conversions = owner.execute(
        "SELECT conversion_id, assignment_id, transaction_id, converted_at"
        " FROM experiment_conversion ORDER BY conversion_id"
    ).fetchall()
    world.first.reverse(world.sale)
    (after,) = world.held(api)
    assert after["attribution_id"] == before["attribution_id"]
    assert after["assignment_id"] == before["assignment_id"]
    assert after["counts"] is False and after["net_amount"] == "0.00"
    assert (
        owner.execute(
            "SELECT conversion_id, assignment_id, transaction_id, converted_at"
            " FROM experiment_conversion ORDER BY conversion_id"
        ).fetchall()
        == conversions
    )
    assert world.run(api)["attributed"] == 0
    assert _holder_campaign(world, api) == before["campaign_id"]
    only_counting = api("GET", "/v1/sale-attributions?counts=true").get_json()
    assert before["attribution_id"] not in [
        row["attribution_id"] for row in only_counting["attributions"]
    ]


def test_an_exposure_recorded_later_does_not_displace_the_holder(
    api: Any, world: World
) -> None:
    world.expose("first", START + timedelta(hours=1))
    world.run(api)
    world.expose("second", START + timedelta(hours=20))
    world.run(api)
    assert _holder_campaign(world, api) == 1


def test_a_sale_in_an_open_window_stays_pending(
    api: Any, world: World, owner: psycopg.Connection
) -> None:
    now = datetime.now(UTC)
    # Moving an assignment in time is a test's setup, which RN-42's guard (#839) refuses
    # to everyone but a session that has the triggers off.
    owner.execute("SET session_replication_role = replica")
    owner.execute(
        "UPDATE experiment_assignment SET assigned_at = %s WHERE assignment_id = %s",
        (now - timedelta(hours=3), world.assignment["first"]),
    )
    owner.execute("SET session_replication_role = origin")
    owner.execute(
        "UPDATE transaction SET occurred_at = %s WHERE transaction_id = %s",
        (now - timedelta(hours=1), world.sale),
    )
    world.expose("first", now - timedelta(hours=2))
    outcome = world.run(api)
    assert (outcome["attributed"], outcome["pending"]) == (0, 1)
    assert world.held(api) == []


def test_every_attribution_says_it_is_attribution(api: Any, world: World) -> None:
    world.expose("first", START + timedelta(hours=1))
    world.run(api)
    (row,) = world.held(api)
    shown = api("GET", f"/v1/sale-attributions/{row['attribution_id']}")
    assert shown.status_code == 200
    assert shown.get_json()["label"] == "ATTRIBUTION"
    assert shown.get_json()["as_of"]
    result = api("GET", f"/v1/experiment-results/{world.first.experiment}")
    section = result.get_json()["attribution"]
    assert section["label"] == "ATTRIBUTION" and section["as_of"]
    assert section["attributed_sales"] == 1
    assert section["attributed_revenue"] == "20.00"
    assert section["arms"][0]["attributed_customers"] == 1
    # The other experiment's result does not carry this sale as its own.
    other = api("GET", f"/v1/experiment-results/{world.second.experiment}")
    assert other.get_json()["attribution"] is None


def test_an_unknown_attribution_and_a_bad_filter_are_refused(api: Any) -> None:
    missing = api("GET", "/v1/sale-attributions/987654321")
    assert (missing.status_code, missing.get_json()["code"]) == (
        404,
        "sale_attribution_not_found",
    )
    bad = api("GET", "/v1/sale-attributions?sale_id=abc")
    assert bad.status_code == 422


def test_only_admin_and_marketing_run_attribution(api: Any, world: World) -> None:
    path = f"/v1/experiments/{world.first.experiment}/attributions"
    for role, user in (("ANALYST", ANALYST), ("AUDITOR", AUDITOR)):
        reply = api("POST", path, as_user=(user, role))
        assert reply.status_code == 403, role
    assert api("POST", "/v1/experiments/987654/attributions").status_code == 404


def test_attributed_revenue_never_exceeds_net_sales_in_any_week(
    owner: psycopg.Connection,
) -> None:
    rows = owner.execute(
        "SELECT count(*) FROM experiments.sale_attribution"
    ).fetchone()[0]
    assert rows >= 30
    over = owner.execute(
        "WITH held AS ("
        " SELECT date_trunc('week', n.occurred_at) AS week, sum(n.net_total) AS held"
        "   FROM experiments.sale_attribution AS s"
        "   JOIN public.net_sale AS n ON n.transaction_id = s.sale_id GROUP BY 1),"
        " sold AS ("
        " SELECT date_trunc('week', occurred_at) AS week, sum(net_total) AS sold"
        "   FROM public.net_sale GROUP BY 1)"
        " SELECT held.week FROM held JOIN sold USING (week) WHERE held.held > sold.sold"
    ).fetchall()
    assert over == []
