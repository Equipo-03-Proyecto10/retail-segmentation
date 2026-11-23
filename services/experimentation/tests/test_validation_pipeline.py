"""The experiment pipeline run end to end on known answers (F25-14, #845).

Every test builds its own world on its own copy of the seeded database and drives
the service's real path: an experiment is planned and assigned through the routes,
its sales are written by the table owner, and the result is read through the route.
Nothing here deals customers, computes an interval or attributes a sale by itself:
the arms, the frame, the statistics and the holders are the service's, and the
tests only write what the world did (customers, consent, segments, sales) and read
what the service answered.

Time is the one thing a test cannot wait for. The service assigns at the database
clock, and an experiment is read at its analysis instant, a window and a settle
period later, so a test that needs the primary look moves the assignments into the
past after the service wrote them (`World.backdate`), with the same setup the other
attribution tests use for it: the owner, with triggers off for that one statement.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest

from app import verify
from tests.conftest import ADMIN, MARKETING
from tests.test_attribution import START as ATTRIBUTION_START
from tests.test_attribution import World as AttributionWorld

pytestmark = pytest.mark.postgres

CALLER = (MARKETING, "MARKETING")
TODAY = datetime.now(UTC).date()
# A year ahead of the seeded experiments, which run around today.
FUTURE = TODAY + timedelta(days=400)
WINDOW = 7
ARMS = [
    {"kind": "CONTROL", "name": "No offer", "treatment_description": "Held back."},
    {"kind": "TREATMENT", "name": "Offer", "treatment_description": "Offered."},
]


def _code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


class World:
    """One campaign and customers who hold its label and have granted both consents.

    The label is HIBERNATING, which the seed gives to two customers: they withdraw
    PERSONALIZATION here, so every frame is made of the customers a test adds. Each
    batch of customers is new, so a frame holds a batch and nobody else.
    """

    label = "HIBERNATING"

    def __init__(self, owner: psycopg.Connection, api: Any) -> None:
        self.owner = owner
        self.api = api
        self.campaign = owner.execute(
            "SELECT max(campaign_id) + 1 FROM public.campaign"
        ).fetchone()[0]
        owner.execute(
            "INSERT INTO public.campaign (campaign_id, name, label_code, starts_on,"
            " ends_on, status) VALUES (%s, 'F25-14 validation', %s, %s, %s, 'DRAFT')",
            (self.campaign, self.label, TODAY, TODAY + timedelta(days=60)),
        )
        for (customer,) in owner.execute(
            "SELECT customer_id FROM api_segments.current_label_v1"
            " WHERE state = 'assigned' AND label_code = %s",
            (self.label,),
        ).fetchall():
            self.consent(customer, "PERSONALIZATION", "WITHDRAWN")
        # The seeded runs hold SEEDED sales, which an OBSERVED run may not span.
        self.run = owner.execute(
            "INSERT INTO segmentation_run (method, window_days, customer_count,"
            " data_origin) VALUES ('RFM_RULES', 90, 0, 'SEEDED') RETURNING run_id"
        ).fetchone()[0]
        self.batch = 0

    def consent(self, customer: Any, purpose: str, decision: str) -> None:
        self.owner.execute(
            "INSERT INTO preferences.consent_decision (customer_id, purpose, decision,"
            " recorded_by, source, basis)"
            " VALUES (%s, %s, %s, %s, 'STAFF_ON_BEHALF', 'EXPLICIT')",
            (customer, purpose, decision, ADMIN),
        )

    def add_customers(self, count: int) -> list[str]:
        """New customers in the campaign's segment, with both consents granted."""
        self.batch += 1
        channel = self.owner.execute("SELECT min(channel_id) FROM channel").fetchone()
        rows = self.owner.execute(
            "INSERT INTO customer (name, email, registration_channel_id)"
            " SELECT 'F25-14 ' || %(batch)s || '-' || g,"
            " 'f25-14-' || %(batch)s || '-' || g || '@example.test', %(channel)s"
            " FROM generate_series(1, %(count)s) AS g RETURNING customer_id",
            {"batch": self.batch, "channel": channel[0], "count": count},
        ).fetchall()
        customers = sorted(str(row[0]) for row in rows)
        self.owner.execute(
            "INSERT INTO customer_segment_history (customer_id, run_id, label_code)"
            " SELECT c, %s, %s FROM unnest(%s::uuid[]) AS c",
            (self.run, self.label, customers),
        )
        for purpose in ("PERSONALIZATION", "MARKETING_CONTACT"):
            self.owner.execute(
                "INSERT INTO preferences.consent_decision (customer_id, purpose,"
                " decision, recorded_by, source, basis)"
                " SELECT c, %s, 'GRANTED', %s, 'STAFF_ON_BEHALF', 'EXPLICIT'"
                " FROM unnest(%s::uuid[]) AS c",
                (purpose, ADMIN, customers),
            )
        return customers

    def plan(
        self,
        *,
        starts_on: date = FUTURE,
        metric: str = "CONVERSION",
        effect: float = 0.1,
        name: str = "F25-14",
        arms: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        reply = self.api(
            "POST",
            "/v1/experiments",
            as_user=CALLER,
            json_body={
                "name": name,
                "campaign_id": self.campaign,
                "primary_metric": metric,
                "starts_on": starts_on.isoformat(),
                "conversion_window_days": WINDOW,
                "minimum_detectable_effect": effect,
                "arms": arms or ARMS,
            },
        )
        assert reply.status_code == 201, reply.get_json()
        return reply.get_json()

    def assign(self, experiment: dict[str, Any] | int) -> Any:
        identifier = (
            experiment if isinstance(experiment, int) else experiment["experiment_id"]
        )
        return self.api(
            "POST",
            "/v1/experiment-assignments",
            as_user=CALLER,
            json_body={"experiment_id": identifier},
        )

    def backdate(self, experiment: dict[str, Any] | int) -> datetime:
        """Move an endpoint-created assignment to a completed test window."""
        identifier = (
            experiment if isinstance(experiment, int) else experiment["experiment_id"]
        )
        assigned_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
        self.owner.execute("SET session_replication_role = replica")
        try:
            self.owner.execute(
                "UPDATE experiment_assignment SET assigned_at = %s"
                " WHERE experiment_id = %s",
                (assigned_at, identifier),
            )
        finally:
            self.owner.execute("SET session_replication_role = origin")
        return assigned_at

    def sale(
        self,
        customer: str,
        when: datetime,
        *,
        origin: str = "OBSERVED",
        amount: int = 10,
    ) -> int:
        """Write one owner-side sale; the service discovers it through its view."""
        store, channel, product = self.owner.execute(
            "SELECT (SELECT min(store_id) FROM store),"
            " (SELECT min(channel_id) FROM channel),"
            " (SELECT min(product_id) FROM product)"
        ).fetchone()
        source = f"f25-14-{self.batch}-{customer}-{when.timestamp()}"
        with self.owner.transaction():
            transaction = self.owner.execute(
                "INSERT INTO transaction (source_transaction_id, customer_id,"
                " store_id, channel_id, occurred_at, total, data_origin)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING transaction_id",
                (source, customer, store, channel, when, amount, origin),
            ).fetchone()[0]
            self.owner.execute(
                "INSERT INTO transaction_line (transaction_id, product_id, quantity,"
                " unit_price) VALUES (%s, %s, 1, %s)",
                (transaction, product, amount),
            )
        return transaction

    def result(self, experiment: dict[str, Any] | int) -> dict[str, Any]:
        identifier = (
            experiment if isinstance(experiment, int) else experiment["experiment_id"]
        )
        reply = self.api("GET", f"/v1/experiment-results/{identifier}")
        assert reply.status_code == 200, reply.get_json()
        return reply.get_json()

    def assigned(self, experiment: dict[str, Any] | int) -> dict[str, int]:
        """Who holds which arm, as stored."""
        identifier = (
            experiment if isinstance(experiment, int) else experiment["experiment_id"]
        )
        return {
            str(customer): group
            for customer, group in self.owner.execute(
                "SELECT customer_id, group_id FROM experiment_assignment"
                " WHERE experiment_id = %s",
                (identifier,),
            )
        }


@pytest.fixture
def world(owner: psycopg.Connection, api: Any) -> World:
    return World(owner, api)


def _shared(owner: psycopg.Connection, first: int, second: int) -> int:
    """How many customers are assigned in both experiments."""
    return owner.execute(
        "SELECT count(*) FROM experiment_assignment AS x"
        " JOIN experiment_assignment AS y ON y.customer_id = x.customer_id"
        " WHERE x.experiment_id = %s AND y.experiment_id = %s",
        (first, second),
    ).fetchone()[0]


def _conversion(result: dict[str, Any]) -> dict[str, Any]:
    return next(
        comparison
        for comparison in result["comparisons"]
        if comparison["metric"] == "CONVERSION"
    )


# ---------------------------------------------------------- known-answer results


def test_a_a_known_answer_runs_through_assignment_sales_and_result(
    world: World,
) -> None:
    world.add_customers(200)
    experiment = world.plan(effect=0.3, name="A/A real path")
    assert world.assign(experiment).status_code == 201
    assigned = world.assigned(experiment)
    instant = world.backdate(experiment)
    by_arm: dict[int, list[str]] = {}
    for customer, arm in assigned.items():
        by_arm.setdefault(arm, []).append(customer)
    for customers in by_arm.values():
        for customer in sorted(customers)[:10]:
            world.sale(customer, instant + timedelta(days=1))

    result = world.result(experiment)
    comparison = _conversion(result)

    assert result["status"] == "COMPLETE"
    assert [arm["converted"] for arm in result["arms"]] == [10, 10]
    assert comparison["difference"] == 0
    assert comparison["p_value"] == 1
    assert comparison["lower"] <= 0 <= comparison["upper"]


def test_injected_effect_runs_through_assignment_sales_and_result(
    world: World,
) -> None:
    world.add_customers(400)
    experiment = world.plan(effect=0.3, name="Injected effect real path")
    assert world.assign(experiment).status_code == 201
    assigned = world.assigned(experiment)
    instant = world.backdate(experiment)
    arms = experiment["arms"]
    control = arms[0]["arm_id"]
    treatment = arms[1]["arm_id"]
    counts = {control: 20, treatment: 30}
    for arm, count in counts.items():
        customers = sorted(
            c for c, assigned_arm in assigned.items() if assigned_arm == arm
        )
        for customer in customers[:count]:
            world.sale(
                customer,
                instant + timedelta(days=1),
                origin="SEEDED",
            )

    result = world.result(experiment)
    comparison = _conversion(result)

    assert result["status"] == "COMPLETE" and result["synthetic"] is True
    assert comparison["difference"] == pytest.approx(0.05)
    assert comparison["lower"] <= 0.05 <= comparison["upper"]


# -------------------------------------------------------------- stability


def test_segment_and_consent_changes_do_not_change_a_verified_assignment(
    world: World,
) -> None:
    customers = world.add_customers(40)
    experiment = world.plan(effect=0.3, name="Stable assignment")
    assert world.assign(experiment).status_code == 201
    identifier = experiment["experiment_id"]
    before = world.assigned(experiment)

    changed = customers[:2]
    world.owner.execute(
        "UPDATE customer_segment_history SET valid_to = clock_timestamp()"
        " WHERE customer_id = ANY(%s::uuid[]) AND valid_to IS NULL",
        (changed,),
    )
    moved_run = world.owner.execute(
        "INSERT INTO segmentation_run (method, window_days, customer_count,"
        " data_origin) VALUES ('RFM_RULES', 90, 2, 'SEEDED') RETURNING run_id"
    ).fetchone()[0]
    world.owner.execute(
        "INSERT INTO customer_segment_history (customer_id, run_id, label_code)"
        " SELECT c, %s, 'CHAMPION' FROM unnest(%s::uuid[]) AS c",
        (moved_run, changed),
    )
    world.consent(changed[0], "PERSONALIZATION", "WITHDRAWN")
    world.consent(changed[1], "MARKETING_CONTACT", "WITHDRAWN")

    page = world.api(
        "GET", f"/v1/experiment-assignments?experiment_id={identifier}&size=100"
    )
    shown = world.api("GET", f"/v1/experiments/{identifier}").get_json()
    rows = page.get_json()["assignments"]
    document = {
        "experiment_id": identifier,
        "salt": shown["salt"],
        "arms": [arm["arm_id"] for arm in shown["arms"]],
        "assignments": rows,
    }

    assert page.status_code == 200
    assert world.assigned(experiment) == before
    assert {row["customer_id"]: row["arm_id"] for row in rows} == before
    assert verify.verify(document) == []


# ---------------------------------------------------------- counted once


def test_one_sale_qualifying_for_two_campaigns_is_counted_once_in_reports(
    owner: psycopg.Connection, api: Any
) -> None:
    attribution = AttributionWorld(owner)
    attribution.expose("first", ATTRIBUTION_START + timedelta(hours=1))
    attribution.expose("second", ATTRIBUTION_START + timedelta(hours=2))

    outcome = attribution.run(api)
    rows = attribution.held(api)
    reports = [
        api("GET", f"/v1/experiment-results/{scene.experiment}").get_json()
        for scene in (attribution.first, attribution.second)
    ]
    counted = sum(
        (
            0
            if report["attribution"] is None
            else report["attribution"]["attributed_sales"]
        )
        for report in reports
    )

    assert outcome["attributed"] == 1
    assert len(rows) == 1 and rows[0]["sale_id"] == attribution.sale
    assert counted == 1


# ------------------------------------------------------------------ contamination


@pytest.mark.parametrize("late_first", [False, True])
def test_an_experiment_assigned_long_after_its_start_is_checked_over_its_real_period(
    world: World, owner: psycopg.Connection, late_first: bool
) -> None:
    """ADR-0068 rule 6: an experiment's period runs from its start to its analysis
    instant. One planned two months ago and assigned today is analysed a window and
    a settle period from today, so it overlaps an experiment that starts tomorrow,
    though the dates written in the two plans do not meet. Whichever of the two is
    assigned second is refused, so no customer is in both."""
    customers = world.add_customers(40)
    late = world.plan(starts_on=TODAY - timedelta(days=60), effect=0.3, name="Late")
    soon = world.plan(starts_on=TODAY + timedelta(days=1), effect=0.3, name="Soon")
    # Written dates: [today - 60, today - 46] and [today + 1, today + 15] do not meet,
    # so rule 6 at planning removes nobody and both frames hold all forty.
    assert late["frame"]["size"] == soon["frame"]["size"] == 40
    first, second = (late, soon) if late_first else (soon, late)

    assert world.assign(first).status_code == 201
    refused = world.assign(second)

    assert _code(refused) == (409, "assignment_conflict")
    assert "40 customer(s)" in refused.get_json()["message"]
    assert world.assigned(second) == {}
    assert sorted(world.assigned(first)) == customers
    assert _shared(owner, late["experiment_id"], soon["experiment_id"]) == 0
