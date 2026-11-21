"""Attribution holds whatever the order it runs in, and in both formats (F25-12, #843).

Review additions to test_attribution.py: the cases ADR-0069 names that the first set
did not drive. They use the same two-campaign world, on real PostgreSQL and the real
routes.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from lxml import etree

from tests.conftest import _with_database
from tests.test_attribution import MARKETING_CALLER, START, WINDOW, World

pytestmark = pytest.mark.postgres

CHECKS = Path(__file__).resolve().parents[3] / "contracts/db/check_attribution.sql"
XSD = (
    Path(__file__).resolve().parents[1]
    / "contracts/experimentation/experimentation.xsd"
)
XML = {"Accept": "application/xml"}


@pytest.fixture
def world(owner: psycopg.Connection) -> World:
    return World(owner)


def _attribute(api: Any, scene: Any) -> dict[str, Any]:
    reply = api(
        "POST",
        f"/v1/experiments/{scene.experiment}/attributions",
        as_user=MARKETING_CALLER,
    )
    assert reply.status_code == 200, reply.get_json()
    return reply.get_json()


def _violations(owner: psycopg.Connection) -> list[str]:
    url = _with_database(os.environ["TEST_DATABASE_URL"], owner.info.dbname)
    done = subprocess.run(
        ["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-d", url, "-f", str(CHECKS)],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.splitlines()


def test_the_holder_does_not_depend_on_which_experiment_is_attributed_first(
    api: Any, world: World
) -> None:
    """The second campaign's conversions were never evaluated and the first is
    attributed alone: the later touch of the second still holds the sale."""
    world.expose("first", START + timedelta(hours=1))
    world.expose("second", START + timedelta(hours=2))
    reply = api(
        "POST",
        f"/v1/experiments/{world.first.experiment}/conversions",
        as_user=MARKETING_CALLER,
    )
    assert reply.status_code == 200
    _attribute(api, world.first)
    (row,) = world.held(api)
    assert (
        row["campaign_id"] == 2 and row["assignment_id"] == world.assignment["second"]
    )


def test_a_competitor_whose_window_is_open_keeps_the_sale_pending_even_unexposed(
    api: Any, world: World, owner: psycopg.Connection
) -> None:
    """The first window has closed and the second has not, and the second has no
    exposure yet: the sale waits, because an exposure dated before the sale may still
    reach the second. When that window closes the later touch holds it."""
    now = datetime.now(UTC)
    for name, days in (("first", 7), ("second", 30)):
        scene = getattr(world, name)
        owner.execute(
            "UPDATE experiment SET conversion_window_days = %s"
            " WHERE experiment_id = %s",
            (days, scene.experiment),
        )
        owner.execute(
            "UPDATE experiment_assignment SET assigned_at = %s"
            " WHERE assignment_id = %s",
            (now - timedelta(days=10), world.assignment[name]),
        )
    owner.execute(
        "UPDATE transaction SET occurred_at = %s WHERE transaction_id = %s",
        (now - timedelta(days=9), world.sale),
    )
    world.expose("first", now - timedelta(days=9, hours=12))
    outcome = _attribute(api, world.first)
    assert (outcome["attributed"], outcome["pending"]) == (0, 1)
    assert world.held(api) == []

    world.expose("second", now - timedelta(days=9, hours=6))  # a late event
    owner.execute(
        "UPDATE experiment SET conversion_window_days = 10 WHERE experiment_id = %s",
        (world.second.experiment,),
    )
    outcome = _attribute(api, world.first)
    assert outcome["attributed"] == 1
    (row,) = world.held(api)
    assert row["campaign_id"] == 2


def test_a_voided_exposure_never_counts(
    api: Any, world: World, owner: psycopg.Connection
) -> None:
    world.expose("first", START + timedelta(hours=1))
    source = uuid.uuid4()
    owner.execute(
        "INSERT INTO experiment_exposure (assignment_id, exposed_at, source_event_id)"
        " VALUES (%s, %s, %s)",
        (world.assignment["second"], START + timedelta(hours=5), source),
    )
    owner.execute(
        "INSERT INTO experiments.exposure_notice (source_event_id, notice,"
        " assignment_id, occurred_at, disposition)"
        " VALUES (%s, 'VOIDED', NULL, %s, 'VOIDED')",
        (source, START),
    )
    world.run(api)
    (row,) = world.held(api)
    assert row["campaign_id"] == 1


def test_a_sale_whose_only_exposure_is_voided_is_held_by_nobody(
    api: Any, world: World, owner: psycopg.Connection
) -> None:
    source = uuid.uuid4()
    owner.execute(
        "INSERT INTO experiment_exposure (assignment_id, exposed_at, source_event_id)"
        " VALUES (%s, %s, %s)",
        (world.assignment["first"], START + timedelta(hours=1), source),
    )
    owner.execute(
        "INSERT INTO experiments.exposure_notice (source_event_id, notice,"
        " assignment_id, occurred_at, disposition)"
        " VALUES (%s, 'VOIDED', NULL, %s, 'VOIDED')",
        (source, START),
    )
    world.run(api)
    assert world.held(api) == []


def test_a_sale_on_the_last_instant_of_the_window_is_not_held(
    api: Any, world: World
) -> None:
    """The window is open on the right, as for a conversion."""
    edge = world.first.sale(3, START + timedelta(days=WINDOW))
    world.expose("first", START + timedelta(hours=1))
    world.expose("second", START + timedelta(hours=2))
    world.run(api)
    reply = api("GET", f"/v1/sale-attributions?sale_id={edge}")
    assert reply.get_json()["attributions"] == []


def test_a_reversed_sale_is_not_a_conversion_the_campaign_failed_to_hold(
    api: Any, world: World
) -> None:
    world.expose("first", START + timedelta(hours=1))
    world.expose("second", START + timedelta(hours=2))
    world.run(api)  # held by the second
    before = api("GET", f"/v1/experiment-results/{world.first.experiment}")
    assert before.get_json()["attribution"] is None  # the first holds nothing
    world.first.reverse(world.sale)
    after = api("GET", f"/v1/experiment-results/{world.second.experiment}")
    section = after.get_json()["attribution"]
    assert (section["attributed_sales"], section["attributed_revenue"]) == (0, "0.00")
    # The first's one conversion was cancelled: nothing is left to be unattributed.
    unheld = api("GET", f"/v1/experiment-results/{world.first.experiment}")
    assert unheld.get_json()["attribution"] is None


def test_the_three_operations_answer_in_xml_that_validates(
    api: Any, world: World
) -> None:
    world.expose("first", START + timedelta(hours=1))
    world.run(api)
    (row,) = world.held(api)
    schema = etree.XMLSchema(etree.parse(str(XSD)))
    run = api(
        "POST",
        f"/v1/experiments/{world.first.experiment}/attributions",
        as_user=MARKETING_CALLER,
        headers=XML,
    )
    page = api("GET", "/v1/sale-attributions", headers=XML)
    one = api("GET", f"/v1/sale-attributions/{row['attribution_id']}", headers=XML)
    result = api("GET", f"/v1/experiment-results/{world.first.experiment}", headers=XML)
    for reply in (run, page, one, result):
        assert reply.status_code == 200 and reply.mimetype == "application/xml"
        schema.assertValid(etree.fromstring(reply.data))
    assert b"<label>ATTRIBUTION</label>" in run.data
    assert b"<AttributionEvaluation" in run.data
    gone = api("GET", "/v1/sale-attributions/987654321", headers=XML)
    assert gone.status_code == 404 and b"sale_attribution_not_found" in gone.data


def test_roles_that_do_not_read_campaigns_and_no_token_are_refused(
    api: Any, world: World
) -> None:
    world.expose("first", START + timedelta(hours=1))
    world.run(api)
    (row,) = world.held(api)
    for role in (
        ("11111111-1111-1111-1111-000000000002", "STORE_MANAGER"),
        ("11111111-1111-1111-1111-000000000003", "SALES_ASSOCIATE"),
    ):
        for path in (
            "/v1/sale-attributions",
            f"/v1/sale-attributions/{row['attribution_id']}",
        ):
            reply = api("GET", path, as_user=role)
            assert (reply.status_code, reply.get_json()["code"]) == (
                403,
                "forbidden_role",
            )
        denied = api(
            "POST",
            f"/v1/experiments/{world.first.experiment}/attributions",
            as_user=role,
        )
        assert denied.status_code == 403
    assert api.client.get("/v1/sale-attributions").status_code == 401
    assert (
        api.client.post(
            f"/v1/experiments/{world.first.experiment}/attributions"
        ).status_code
        == 401
    )
    assert (
        api("PUT", f"/v1/sale-attributions/{row['attribution_id']}").status_code == 405
    )


def test_an_experiment_nobody_is_assigned_to_has_nothing_to_attribute(api: Any) -> None:
    reply = api("POST", "/v1/experiments/1/attributions", as_user=MARKETING_CALLER)
    assert reply.status_code in (200, 409)


def test_the_compliance_script_holds_on_the_seed(owner: psycopg.Connection) -> None:
    assert (
        owner.execute("SELECT count(*) FROM experiments.sale_attribution").fetchone()[0]
        >= 30
    )
    assert _violations(owner) == []


def test_the_compliance_script_catches_a_holder_that_is_not_the_latest_touch(
    api: Any, world: World, owner: psycopg.Connection
) -> None:
    first = world.expose("first", START + timedelta(hours=1))
    world.expose("second", START + timedelta(hours=2))
    world.run(api)
    owner.execute("SET session_replication_role = replica")
    owner.execute(
        "DELETE FROM experiments.sale_attribution WHERE sale_id = %s", (world.sale,)
    )
    owner.execute(
        "INSERT INTO experiments.sale_attribution (sale_id, assignment_id, exposure_id)"
        " VALUES (%s, %s, %s)",
        (world.sale, world.assignment["first"], first),
    )
    owner.execute("SET session_replication_role = origin")
    assert any("holder is not the latest touch" in v for v in _violations(owner))


def test_attributed_revenue_beyond_the_sales_is_caught(
    owner: psycopg.Connection,
) -> None:
    """A sale held twice, with the unique key off, doubles the attributed side only."""
    row = owner.execute(
        "SELECT sale_id, assignment_id, exposure_id FROM experiments.sale_attribution"
        " ORDER BY attribution_id LIMIT 1"
    ).fetchone()
    owner.execute("SET session_replication_role = replica")
    owner.execute(
        "ALTER TABLE experiments.sale_attribution"
        " DROP CONSTRAINT sale_attribution_sale_key"
    )
    owner.execute(
        "INSERT INTO experiments.sale_attribution (sale_id, assignment_id, exposure_id)"
        " VALUES (%s, %s, %s)",
        row,
    )
    owner.execute("SET session_replication_role = origin")
    assert any("held more than once" in v for v in _violations(owner))
