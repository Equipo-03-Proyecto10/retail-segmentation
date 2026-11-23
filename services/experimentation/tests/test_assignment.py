"""Assigning an experiment's frame, and showing it (F25-08, ADR-0068, RN-23, RN-42).

Every test builds its experiment on its own copy of the seeded database. The plan
and its frame are written by the table owner, as test_results does, so the frame, the
salt and every sale are known; the assignment is then made through the API and
checked against figures worked out here: the keys by `hmac` itself, the arms by the
documented ordering, never by importing the service's own dealing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
import redis
from lxml import etree

from app import verify
from tests.conftest import ADMIN, ANALYST, AUDITOR, MARKETING

pytestmark = pytest.mark.postgres

NS = "{https://mosaiq.maxthecoder.online/xml/experimentation/v1}"
# Far from the seeded experiments, which run around today, so no period overlaps.
START = datetime(2010, 3, 1, 12, 0, tzinfo=UTC)
# An experiment's period runs from its start to its analysis instant, and these are
# assigned today (F25-14, ADR-0068 rule 6): planned a year ahead, they do not meet
# the seeded experiments either.
STARTS_ON = datetime.now(UTC).date() + timedelta(days=400)
SALT = "5b" * 32
WINDOW = 7
CALLER = (MARKETING, "MARKETING")


def _code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


def _key(salt: str, experiment: int, customer: str) -> str:
    """The documented key, by hand: HMAC-SHA-256 keyed with the salt's bytes."""
    message = f"mosaiq-assign-v1|{experiment}|{customer}".encode()
    return hmac.new(bytes.fromhex(salt), message, hashlib.sha256).hexdigest()


class Scenario:
    """One planned experiment whose frame is `size` customers nobody has assigned."""

    def __init__(
        self,
        owner: psycopg.Connection,
        *,
        kinds: tuple[str, ...] = ("CONTROL", "TREATMENT"),
        size: int = 30,
        starts_on: Any = None,
        planned: bool = True,
    ) -> None:
        self.owner = owner
        self.experiment = self._new_experiment(starts_on or STARTS_ON)
        first = owner.execute(
            "SELECT max(group_id) + 1 FROM experiment_group"
        ).fetchone()[0]
        self.arms = [first + position for position in range(len(kinds))]
        for arm, kind in zip(self.arms, kinds, strict=True):
            owner.execute(
                "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
                " treatment_description) VALUES (%s, %s, %s, %s, %s)",
                (arm, self.experiment, kind, f"{kind.title()} {arm}", kind.title()),
            )
        self.customers = [
            str(row[0])
            for row in owner.execute(
                "SELECT customer_id FROM customer" " ORDER BY customer_id LIMIT %s",
                (size,),
            )
        ]
        assert len(self.customers) == size
        owner.execute(
            "UPDATE transaction SET occurred_at = TIMESTAMPTZ '2000-01-01'"
            " WHERE customer_id = ANY(%s::uuid[])",
            (self.customers,),
        )
        self.counter = 0
        self.taken_at = START - timedelta(days=1)
        if planned:
            owner.execute(
                "INSERT INTO experiments.experiment_plan (experiment_id, alpha,"
                " comparison_alpha, power, minimum_detectable_effect, baseline,"
                " baseline_std_dev, required_per_arm, smallest_detectable_effect,"
                " settle_days, frame_taken_at, frame_digest, label_code, salt,"
                " planned_at, planned_by) VALUES (%s, 0.05, 0.05, 0.8, 1, 2, 5, 10, 1,"
                " 7, %s, repeat('a', 64), 'CHAMPION', %s, %s, %s)",
                (self.experiment, self.taken_at, SALT, self.taken_at, ADMIN),
            )
            for customer in self.customers:
                owner.execute(
                    "INSERT INTO experiments.experiment_frame_member"
                    " (experiment_id, customer_id) VALUES (%s, %s)",
                    (self.experiment, customer),
                )

    def _new_experiment(self, starts_on: Any) -> int:
        experiment = self.owner.execute(
            "SELECT max(experiment_id) + 1 FROM experiment"
        ).fetchone()[0]
        self.owner.execute(
            "INSERT INTO experiment (experiment_id, name, campaign_id, target_metric,"
            " starts_on, conversion_window_days, data_origin)"
            " VALUES (%s, 'F25-08 assignment', NULL, 'CONVERSION', %s, %s, 'OBSERVED')",
            (experiment, starts_on, WINDOW),
        )
        return experiment

    def sale(self, customer: str, when: datetime, amount: int) -> None:
        self.counter += 1
        store, channel, product = self.owner.execute(
            "SELECT (SELECT min(store_id) FROM store),"
            " (SELECT min(channel_id) FROM channel),"
            " (SELECT min(product_id) FROM product)"
        ).fetchone()
        with self.owner.transaction():
            (transaction_id,) = self.owner.execute(
                "INSERT INTO transaction (source_transaction_id, customer_id,"
                " store_id, channel_id, occurred_at, total, data_origin)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'OBSERVED') RETURNING transaction_id",
                (
                    f"f25-08-{self.experiment}-{self.counter}",
                    customer,
                    store,
                    channel,
                    when,
                    amount,
                ),
            ).fetchone()
            self.owner.execute(
                "INSERT INTO transaction_line (transaction_id, product_id, quantity,"
                " unit_price) VALUES (%s, %s, 1, %s)",
                (transaction_id, product, amount),
            )

    def dealt(self) -> dict[str, int]:
        """The arm of every customer, by the documented ordering and `hmac`."""
        ordered = sorted((_key(SALT, self.experiment, c), c) for c in self.customers)
        return {c: self.arms[i % len(self.arms)] for i, (_, c) in enumerate(ordered)}

    def assign(self, api: Any, **body: Any) -> Any:
        return api(
            "POST",
            "/v1/experiment-assignments",
            as_user=CALLER,
            json_body={"experiment_id": self.experiment} | body,
        )

    def rows(self) -> dict[str, int]:
        return {
            str(c): g
            for c, g in self.owner.execute(
                "SELECT customer_id, group_id FROM experiment_assignment"
                " WHERE experiment_id = %s",
                (self.experiment,),
            )
        }


@pytest.fixture
def scene(owner: psycopg.Connection) -> Scenario:
    return Scenario(owner)


# ------------------------------------------------------------------ the assignment


def test_the_frame_is_assigned_as_the_documented_recomputation_says(
    api: Any, scene: Scenario
) -> None:
    reply = scene.assign(api)

    assert reply.status_code == 201, reply.get_json()
    summary = reply.get_json()
    assert summary["experiment_id"] == scene.experiment
    assert summary["assigned_total"] == 30
    assert summary["assigned_by"] == MARKETING
    assert summary["acknowledged_underpowered"] is False
    assert [(a["arm_id"], a["kind"], a["assigned"]) for a in summary["arms"]] == [
        (scene.arms[0], "CONTROL", 15),
        (scene.arms[1], "TREATMENT", 15),
    ]
    # Anyone recomputing from the frame and the published salt gets every customer
    # in the same arm (criterion 1).
    assert scene.rows() == scene.dealt()
    assert summary["checks"]["recomputation_matches"] is True


def test_three_arms_differ_by_at_most_one_customer(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(owner, kinds=("CONTROL", "TREATMENT", "TREATMENT"), size=29)

    assert scene.assign(api).status_code == 201

    counts = sorted(list(scene.rows().values()).count(arm) for arm in scene.arms)
    assert counts == [9, 10, 10]
    assert scene.rows() == scene.dealt()


def test_one_instant_for_every_customer(api: Any, scene: Scenario) -> None:
    summary = scene.assign(api).get_json()

    instants = {
        row[0]
        for row in scene.owner.execute(
            "SELECT assigned_at FROM experiment_assignment WHERE experiment_id = %s",
            (scene.experiment,),
        )
    }
    assert len(instants) == 1
    assert datetime.fromisoformat(summary["assigned_at"]) == instants.pop()


def test_the_list_gives_each_customers_key_an_auditor_recomputes(
    api: Any, scene: Scenario
) -> None:
    scene.assign(api)

    reply = api(
        "GET",
        f"/v1/experiment-assignments?experiment_id={scene.experiment}&size=100",
        as_user=(AUDITOR, "AUDITOR"),
    )

    assert reply.status_code == 200
    page = reply.get_json()
    assert page["page_info"]["total"] == 30
    dealt = scene.dealt()
    for item in page["assignments"]:
        assert item["assignment_key"] == _key(
            SALT, scene.experiment, item["customer_id"]
        )
        assert item["arm_id"] == dealt[item["customer_id"]]
        assert item["exposure_count"] == 0
    # The service's own verifier accepts the document an auditor would build.
    document = {
        "experiment_id": scene.experiment,
        "salt": SALT,
        "arms": scene.arms,
        "assignments": [
            {
                "customer_id": item["customer_id"],
                "arm_id": item["arm_id"],
                "assignment_key": item["assignment_key"],
            }
            for item in page["assignments"]
        ],
    }
    assert verify.verify(document) == []
    document["assignments"][0]["arm_id"] = next(
        arm for arm in scene.arms if arm != document["assignments"][0]["arm_id"]
    )
    assert verify.verify(document) != []


def test_the_list_filters(api: Any, scene: Scenario) -> None:
    scene.assign(api)
    base = f"/v1/experiment-assignments?experiment_id={scene.experiment}&size=100"

    control = api("GET", base + "&arm_kind=CONTROL").get_json()
    one = api("GET", base + f"&customer_id={scene.customers[0]}").get_json()
    arm = api("GET", base + f"&arm_id={scene.arms[1]}").get_json()

    assert control["page_info"]["total"] == 15
    assert {i["arm_kind"] for i in control["assignments"]} == {"CONTROL"}
    assert [i["customer_id"] for i in one["assignments"]] == [scene.customers[0]]
    assert arm["page_info"]["total"] == 15
    assert api("GET", "/v1/experiment-assignments?arm_kind=OTHER").status_code == 422
    assert api("GET", "/v1/experiment-assignments?customer_id=x").status_code == 422


def test_the_same_assignment_in_xml(api: Any, scene: Scenario) -> None:
    reply = api(
        "POST",
        "/v1/experiment-assignments",
        as_user=CALLER,
        data=f"<AssignmentRequest xmlns='{NS[1:-1]}'>"
        f"<experiment_id>{scene.experiment}</experiment_id></AssignmentRequest>",
        headers={"Content-Type": "application/xml", "Accept": "application/xml"},
    )

    assert reply.status_code == 201, reply.get_data(as_text=True)
    root = ET.fromstring(reply.get_data())
    assert root.findtext(f"{NS}assigned_total") == "30"
    assert scene.rows() == scene.dealt()


# ------------------------------------------------------------------ the refusals


def test_an_assigned_experiment_is_refused_a_second_time(
    api: Any, scene: Scenario
) -> None:
    assert scene.assign(api).status_code == 201
    before = scene.rows()

    assert _code(scene.assign(api)) == (409, "already_assigned")
    assert scene.rows() == before


def test_an_experiment_that_does_not_exist_is_not_found(api: Any) -> None:
    reply = api(
        "POST",
        "/v1/experiment-assignments",
        as_user=CALLER,
        json_body={"experiment_id": 999_999},
    )

    assert _code(reply) == (404, "experiment_not_found")


def test_an_experiment_without_a_frame_has_nothing_to_assign(
    api: Any, owner: psycopg.Connection
) -> None:
    scene = Scenario(owner, planned=False)

    assert _code(scene.assign(api)) == (409, "empty_frame")
    assert scene.rows() == {}


def test_an_underpowered_plan_is_assigned_only_on_acknowledgement(
    api: Any, scene: Scenario
) -> None:
    scene.owner.execute(
        "INSERT INTO experiments.experiment_flag (experiment_id, flag)"
        " VALUES (%s, 'UNDERPOWERED')",
        (scene.experiment,),
    )

    assert _code(scene.assign(api)) == (409, "underpowered_not_acknowledged")
    assert scene.rows() == {}

    reply = scene.assign(api, acknowledge_underpowered=True)

    assert reply.status_code == 201
    assert reply.get_json()["acknowledged_underpowered"] is True
    shown = api("GET", f"/v1/experiments/{scene.experiment}").get_json()
    assert shown["assignment"]["acknowledged_underpowered"] is True
    assert shown["assignment"]["assigned_by"] == MARKETING


def test_a_customer_assigned_elsewhere_in_an_overlapping_period_stops_the_assignment(
    api: Any, scene: Scenario
) -> None:
    # Another experiment, over the same dates, already holds two of the frame (RN-23).
    other = scene._new_experiment(STARTS_ON)
    group = scene.owner.execute(
        "SELECT max(group_id) + 1 FROM experiment_group"
    ).fetchone()[0]
    scene.owner.execute(
        "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
        " treatment_description) VALUES (%s, %s, 'TREATMENT', 'T', 'T')",
        (group, other),
    )
    for customer in scene.customers[:2]:
        scene.owner.execute(
            "INSERT INTO experiment_assignment (experiment_id, group_id, customer_id)"
            " VALUES (%s, %s, %s)",
            (other, group, customer),
        )

    reply = scene.assign(api)

    assert _code(reply) == (409, "assignment_conflict")
    assert "2 customer(s)" in reply.get_json()["message"]
    assert scene.rows() == {}


def test_an_experiment_of_a_period_that_does_not_overlap_may_share_customers(
    api: Any, scene: Scenario
) -> None:
    other = scene._new_experiment(STARTS_ON - timedelta(days=400))
    group = scene.owner.execute(
        "SELECT max(group_id) + 1 FROM experiment_group"
    ).fetchone()[0]
    scene.owner.execute(
        "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
        " treatment_description) VALUES (%s, %s, 'TREATMENT', 'T', 'T')",
        (group, other),
    )
    scene.owner.execute(
        "UPDATE experiment SET ends_on = %s WHERE experiment_id = %s",
        (STARTS_ON - timedelta(days=390), other),
    )
    scene.owner.execute(
        "INSERT INTO experiment_assignment (experiment_id, group_id, customer_id,"
        " assigned_at) VALUES (%s, %s, %s, %s)",
        (other, group, scene.customers[0], START - timedelta(days=400)),
    )

    assert scene.assign(api).status_code == 201


# ------------------------------------------------------------------ the checks


def test_the_checks_are_stored_and_shown(api: Any, scene: Scenario) -> None:
    summary = scene.assign(api).get_json()

    checks = summary["checks"]
    assert checks["sample_ratio"] == {
        "status": "PASSED",
        "p_value": 1.0,
        "threshold": 0.001,
    }
    assert [
        (b["measure"], b["arm_id"], b["reference_arm_id"]) for b in checks["balance"]
    ] == [
        (m, scene.arms[1], scene.arms[0])
        for m in ("PRE_RECENCY_DAYS", "PRE_PURCHASE_COUNT", "PRE_NET_SPEND")
    ]
    # Nobody bought in the window: every difference is zero and nothing is flagged.
    assert all(b["standardized_mean_difference"] == 0 for b in checks["balance"])
    assert not any(b["flagged"] for b in checks["balance"])
    stored = scene.owner.execute(
        "SELECT recomputation_matches, sample_ratio_p_value"
        " FROM experiments.experiment_assignment_record WHERE experiment_id = %s",
        (scene.experiment,),
    ).fetchone()
    assert stored == (True, 1.0)
    shown = api("GET", f"/v1/experiments/{scene.experiment}").get_json()
    assert shown["assignment"]["checks"] == checks
    assert shown["flags"] == []


def test_an_imbalance_before_assignment_flags_the_experiment_and_its_results(
    api: Any, scene: Scenario
) -> None:
    # Every future treatment customer bought, one to three times for 100, in the
    # week before the frame instant; no control customer bought at all.
    treated = [c for c, arm in scene.dealt().items() if arm == scene.arms[1]]
    for index, customer in enumerate(treated):
        for days in range(2, 3 + index % 3):
            scene.sale(customer, scene.taken_at - timedelta(days=days), 100)

    summary = scene.assign(api).get_json()

    balance = {b["measure"]: b for b in summary["checks"]["balance"]}
    assert balance["PRE_PURCHASE_COUNT"]["flagged"] is True
    assert balance["PRE_NET_SPEND"]["flagged"] is True
    assert balance["PRE_PURCHASE_COUNT"]["standardized_mean_difference"] > 0.25
    shown = api("GET", f"/v1/experiments/{scene.experiment}").get_json()
    assert "BALANCE_FLAGGED" in shown["flags"]
    result = api("GET", f"/v1/experiment-results/{scene.experiment}").get_json()
    assert "BALANCE_FLAGGED" in result["flags"]
    # A diagnostic: it flags and warns, and changes no assignment.
    assert scene.rows() == scene.dealt()


def test_a_failed_sample_ratio_flags_the_experiment_when_read(
    api: Any, scene: Scenario
) -> None:
    scene.assign(api)
    # Stored by the assignment, the failing check reads back from the record
    # whatever else changes; a p-value under 0.001 is FAILED and flags the plan.
    scene.owner.execute(
        "ALTER TABLE experiments.experiment_assignment_record" " DISABLE TRIGGER USER"
    )
    scene.owner.execute(
        "UPDATE experiments.experiment_assignment_record"
        " SET sample_ratio_p_value = 0.0004 WHERE experiment_id = %s",
        (scene.experiment,),
    )
    shown = api("GET", f"/v1/experiments/{scene.experiment}").get_json()

    assert shown["assignment"]["checks"]["sample_ratio"]["status"] == "FAILED"


# ------------------------------------------------------------------ every write path


def _refused(connection: psycopg.Connection, statement: str, *params: Any) -> str:
    """The SQLSTATE and constraint a statement is refused with; fails if it is not."""
    with pytest.raises(psycopg.Error) as caught:
        with connection.transaction():
            connection.execute(statement, params)
    return caught.value.sqlstate or ""


@pytest.mark.parametrize("who", ["owner", "web", "service"])
def test_an_assigned_customer_never_changes_arm_by_any_route(
    api: Any, scene: Scenario, database: dict[str, str], who: str
) -> None:
    scene.assign(api)
    before = scene.rows()
    customer = scene.customers[0]
    other_arm = next(a for a in scene.arms if a != before[customer])
    with psycopg.connect(database[who], autocommit=True) as connection:
        update = _refused(
            connection,
            "UPDATE public.experiment_assignment SET group_id = %s"
            " WHERE experiment_id = %s AND customer_id = %s",
            other_arm,
            scene.experiment,
            customer,
        )
        delete = _refused(
            connection,
            "DELETE FROM public.experiment_assignment WHERE experiment_id = %s",
            scene.experiment,
        )
    assert update and delete
    assert scene.rows() == before


def test_the_owner_is_refused_by_the_schema_not_by_privileges(
    api: Any, scene: Scenario
) -> None:
    scene.assign(api)

    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        scene.owner.execute(
            "UPDATE public.experiment_assignment SET group_id = %s"
            " WHERE experiment_id = %s",
            (scene.arms[0], scene.experiment),
        )
    assert caught.value.diag.constraint_name == "experiment_assignment_never_rewritten"
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        scene.owner.execute(
            "DELETE FROM public.experiment_assignment WHERE experiment_id = %s",
            (scene.experiment,),
        )
    assert caught.value.diag.constraint_name == "experiment_assignment_never_rewritten"


def test_the_web_cannot_assign_into_a_planned_experiment(
    scene: Scenario, database: dict[str, str]
) -> None:
    with psycopg.connect(database["web"], autocommit=True) as web:
        with pytest.raises(psycopg.errors.CheckViolation) as caught:
            web.execute(
                "INSERT INTO public.experiment_assignment (experiment_id, group_id,"
                " customer_id) VALUES (%s, %s, %s)",
                (scene.experiment, scene.arms[0], scene.customers[0]),
            )
    assert (
        caught.value.diag.constraint_name == "experiment_assignment_planned_by_service"
    )
    assert scene.rows() == {}


def test_the_web_still_assigns_its_own_experiments(
    owner: psycopg.Connection, database: dict[str, str]
) -> None:
    scene = Scenario(owner, planned=False)
    with psycopg.connect(database["web"], autocommit=True) as web:
        web.execute(
            "INSERT INTO public.experiment_assignment (experiment_id, group_id,"
            " customer_id) VALUES (%s, %s, %s)",
            (scene.experiment, scene.arms[0], scene.customers[0]),
        )
    assert list(scene.rows().values()) == [scene.arms[0]]


def test_a_customer_is_assigned_once_per_experiment(api: Any, scene: Scenario) -> None:
    scene.assign(api)

    with pytest.raises(psycopg.errors.UniqueViolation):
        scene.owner.execute(
            "INSERT INTO public.experiment_assignment (experiment_id, group_id,"
            " customer_id) VALUES (%s, %s, %s)",
            (scene.experiment, scene.arms[0], scene.customers[0]),
        )


@pytest.mark.parametrize(
    "table",
    ["experiment_assignment_record", "experiment_assignment_balance"],
)
def test_what_was_checked_is_frozen(api: Any, scene: Scenario, table: str) -> None:
    scene.assign(api)
    statements = {
        "experiment_assignment_record": (
            "UPDATE experiments.experiment_assignment_record SET recomputation_matches"
            " = false WHERE experiment_id = %s",
            "DELETE FROM experiments.experiment_assignment_record"
            " WHERE experiment_id = %s",
        ),
        "experiment_assignment_balance": (
            "UPDATE experiments.experiment_assignment_balance"
            " SET standardized_mean_difference = 0 WHERE experiment_id = %s",
            "DELETE FROM experiments.experiment_assignment_balance"
            " WHERE experiment_id = %s",
        ),
    }[table]
    for statement in statements:
        with pytest.raises(psycopg.errors.CheckViolation) as caught:
            scene.owner.execute(statement, (scene.experiment,))
        assert caught.value.diag.constraint_name == "experiment_plan_frozen"


def test_a_failed_assignment_leaves_nothing_behind(api: Any, scene: Scenario) -> None:
    # A customer who no longer exists in the frame's table cannot be written: the
    # whole assignment, record included, rolls back together.
    scene.owner.execute(
        "ALTER TABLE experiments.experiment_assignment_record DISABLE TRIGGER USER"
    )
    scene.owner.execute(
        "INSERT INTO experiments.experiment_assignment_record (experiment_id,"
        " assigned_by, acknowledged_underpowered, recomputation_matches,"
        " sample_ratio_p_value) VALUES (%s, %s, false, true, 1)",
        (scene.experiment, ADMIN),
    )

    reply = scene.assign(api)

    assert _code(reply) == (409, "already_assigned")
    assert scene.rows() == {}


# ------------------------------------------------------------------ roles and contract


@pytest.mark.parametrize(
    ("user", "role", "status"),
    [
        (ADMIN, "ADMIN", 201),
        (MARKETING, "MARKETING", 201),
        (ANALYST, "ANALYST", 403),
        (AUDITOR, "AUDITOR", 403),
    ],
)
def test_the_roles_that_may_assign(
    api: Any, scene: Scenario, user: str, role: str, status: int
) -> None:
    reply = api(
        "POST",
        "/v1/experiment-assignments",
        as_user=(user, role),
        json_body={"experiment_id": scene.experiment},
    )

    assert reply.status_code == status


def test_a_body_outside_the_contract_is_refused(api: Any, scene: Scenario) -> None:
    for body in ({}, {"experiment_id": 0}, {"experiment_id": scene.experiment, "x": 1}):
        reply = api(
            "POST", "/v1/experiment-assignments", as_user=CALLER, json_body=body
        )
        assert reply.status_code == 422, body
    assert scene.rows() == {}


def test_the_contract_serves_both_operations() -> None:
    import yaml

    from tests.conftest import SERVICE

    contract = yaml.safe_load(
        (SERVICE / "contracts/experimentation/openapi.yaml").read_text()
    )
    served = {
        op["operationId"]: op
        for item in contract["paths"].values()
        for op in item.values()
        if isinstance(op, dict) and "operationId" in op
    }
    for operation in (
        "assignExperiment",
        "listExperimentAssignments",
        "getCurrentAssignment",
    ):
        assert "x-mosaiq-status" not in served[operation], operation


# ---------------------------------------------------------- current floor read


def _campaign(scene: Scenario) -> int:
    campaign = scene.owner.execute(
        "SELECT max(campaign_id) + 1 FROM campaign"
    ).fetchone()[0]
    scene.owner.execute(
        "INSERT INTO campaign"
        " (campaign_id, name, label_code, starts_on, ends_on, status)"
        " VALUES (%s, 'F25-17 current arm',"
        " (SELECT min(label_code) FROM segment_label), %s, %s, 'ACTIVE')",
        (campaign, START.date(), (START + timedelta(days=30)).date()),
    )
    scene.owner.execute(
        "UPDATE experiment SET campaign_id = %s WHERE experiment_id = %s",
        (campaign, scene.experiment),
    )
    return campaign


def _current(api: Any, customer: str, campaign: int, **kwargs: Any) -> Any:
    return api(
        "GET",
        "/v1/experiment-assignments/current"
        f"?customer_id={customer}&campaign_id={campaign}",
        **kwargs,
    )


@pytest.mark.parametrize("media_type", ["application/json", "application/xml"])
@pytest.mark.parametrize("kind", ["CONTROL", "TREATMENT"])
def test_the_frozen_current_arm_is_served_in_both_representations(
    api: Any, scene: Scenario, media_type: str, kind: str
) -> None:
    campaign = _campaign(scene)
    assert scene.assign(api).status_code == 201
    dealt = scene.dealt()
    arm = next(
        arm
        for arm in scene.arms
        if scene.owner.execute(
            "SELECT kind FROM experiment_group WHERE group_id = %s", (arm,)
        ).fetchone()[0]
        == kind
    )
    customer = next(customer for customer, group in dealt.items() if group == arm)

    reply = _current(api, customer, campaign, headers={"Accept": media_type})

    assert reply.status_code == 200
    if media_type == "application/json":
        body = reply.get_json()
        assert body["customer_id"] == customer
        assert body["campaign_id"] == campaign
        assert body["experiment_id"] == scene.experiment
        assert body["arm_id"] == arm
        assert body["arm_kind"] == kind
        assert body["assignment_key"] == _key(SALT, scene.experiment, customer)
        assert body["exposure_count"] == 0
        assert body["first_exposed_at"] is None
    else:
        contract = Path(__file__).resolve().parents[1] / "contracts/experimentation"
        schema = etree.XMLSchema(etree.parse(str(contract / "experimentation.xsd")))
        root = etree.fromstring(reply.data)
        schema.assertValid(root)
        assert root.findtext(NS + "customer_id") == customer
        assert root.findtext(NS + "arm_kind") == kind


@pytest.mark.parametrize("media_type", ["application/json", "application/xml"])
def test_outside_the_frame_and_a_campaign_without_an_assignment_are_not_found(
    api: Any, scene: Scenario, media_type: str
) -> None:
    unassigned_experiment = scene.experiment
    scene = Scenario(scene.owner, size=29)
    campaign = _campaign(scene)
    assert scene.assign(api).status_code == 201
    outsider = scene.owner.execute(
        "SELECT customer_id::text FROM customer"
        " WHERE customer_id <> ALL(%s::uuid[]) ORDER BY customer_id LIMIT 1",
        (scene.customers,),
    ).fetchone()[0]
    no_assignment = campaign + 1
    scene.owner.execute(
        "INSERT INTO campaign"
        " (campaign_id, name, label_code, starts_on, ends_on, status)"
        " VALUES (%s, 'F25-17 no assignment',"
        " (SELECT min(label_code) FROM segment_label), %s, %s, 'ACTIVE')",
        (no_assignment, START.date(), (START + timedelta(days=30)).date()),
    )
    scene.owner.execute(
        "UPDATE experiment SET campaign_id = %s WHERE experiment_id = %s",
        (no_assignment, unassigned_experiment),
    )

    for customer, asked_campaign in (
        (outsider, campaign),
        (scene.customers[0], no_assignment),
    ):
        reply = _current(api, customer, asked_campaign, headers={"Accept": media_type})
        assert reply.status_code == 404
        assert reply.headers["X-Correlation-ID"]
        if media_type == "application/json":
            assert reply.get_json()["code"] == "assignment_not_found"
        else:
            root = ET.fromstring(reply.data)
            assert (
                root.findtext("{https://mosaiq.maxthecoder.online/xml/common/v1}code")
                == "assignment_not_found"
            )


def test_an_analysed_experiment_has_no_current_arm(api: Any, scene: Scenario) -> None:
    campaign = _campaign(scene)
    assert scene.assign(api).status_code == 201
    scene.owner.execute(
        "INSERT INTO experiments.experiment_result"
        " (experiment_id, computed_at, sample_ratio_p_value, synthetic)"
        " VALUES (%s, %s, 1, false)",
        (scene.experiment, START),
    )

    assert _code(_current(api, scene.customers[0], campaign)) == (
        404,
        "assignment_not_found",
    )


def test_current_assignment_roles_and_invalid_requests(
    api: Any, scene: Scenario
) -> None:
    campaign = _campaign(scene)
    assert scene.assign(api).status_code == 201
    roles = [
        "ADMIN",
        "ANALYST",
        "STORE_MANAGER",
        "MARKETING",
        "INVENTORY_PLANNER",
        "AUDITOR",
        "CUSTOMER",
        "SALES_ASSOCIATE",
        "LOYALTY_OFFICER",
        "COMMERCIAL_SUPERVISOR",
    ]
    for role in roles:
        reply = _current(
            api,
            scene.customers[0],
            campaign,
            as_user=(ADMIN, role),
        )
        if role in ("ADMIN", "STORE_MANAGER", "SALES_ASSOCIATE"):
            assert reply.status_code == 200, role
        else:
            assert _code(reply) == (403, "forbidden_role"), role

    for query in (
        f"campaign_id={campaign}",
        f"customer_id=not-a-uuid&campaign_id={campaign}",
        f"customer_id={scene.customers[0]}&campaign_id=0",
    ):
        reply = api("GET", "/v1/experiment-assignments/current?" + query)
        assert _code(reply) == (422, "invalid_query"), query
    assert (
        _current(
            api,
            scene.customers[0],
            campaign,
            headers={"Accept": "text/plain"},
        ).status_code
        == 406
    )
    assert _code(_current(api, scene.customers[0], 2**63 - 1)) == (
        404,
        "assignment_not_found",
    )


@pytest.mark.parametrize("media_type", ["application/json", "application/xml"])
def test_current_assignment_shared_refusals_use_each_representation(
    service: Any,
    api: Any,
    scene: Scenario,
    sessions: Any,
    monkeypatch: pytest.MonkeyPatch,
    media_type: str,
) -> None:
    campaign = _campaign(scene)
    assert scene.assign(api).status_code == 201
    path = (
        "/v1/experiment-assignments/current"
        f"?customer_id={scene.customers[0]}&campaign_id={campaign}"
    )

    unauthorized = service.test_client().get(path, headers={"Accept": media_type})
    assert unauthorized.status_code == 401
    if media_type == "application/json":
        assert unauthorized.get_json()["code"] == "invalid_token"
    else:
        assert (
            ET.fromstring(unauthorized.data).findtext(
                "{https://mosaiq.maxthecoder.online/xml/common/v1}code"
            )
            == "invalid_token"
        )

    forbidden = _current(
        api,
        scene.customers[0],
        campaign,
        as_user=(ADMIN, "AUDITOR"),
        headers={"Accept": media_type},
    )
    assert forbidden.status_code == 403
    invalid = api(
        "GET",
        "/v1/experiment-assignments/current?campaign_id=1",
        headers={"Accept": media_type},
    )
    assert invalid.status_code == 422

    class Pipeline:
        def set(self, *args: Any, **kwargs: Any) -> Pipeline:
            return self

        def incr(self, *args: Any, **kwargs: Any) -> Pipeline:
            return self

        @staticmethod
        def execute() -> list[Any]:
            return [True, 1]

    kit = service.extensions["mosaiq_kit"]
    monkeypatch.setattr(
        sessions, "pipeline", lambda **_kwargs: Pipeline(), raising=False
    )
    kit.rate_limit_enabled = True
    kit.rate_limit_requests = 0
    limited = _current(
        api,
        scene.customers[0],
        campaign,
        headers={"Accept": media_type},
    )
    assert limited.status_code == 429
    kit.rate_limit_enabled = False

    def unavailable(_key: str) -> int:
        raise redis.RedisError("unavailable")

    monkeypatch.setattr(sessions, "exists", unavailable)
    unavailable_reply = _current(
        api,
        scene.customers[0],
        campaign,
        headers={"Accept": media_type},
    )
    assert unavailable_reply.status_code == 503


def test_reading_the_current_arm_writes_no_experiment_row(
    api: Any, scene: Scenario
) -> None:
    campaign = _campaign(scene)
    assert scene.assign(api).status_code == 201
    counts = """
SELECT (SELECT count(*) FROM public.experiment),
       (SELECT count(*) FROM public.experiment_group),
       (SELECT count(*) FROM public.experiment_assignment),
       (SELECT count(*) FROM public.experiment_exposure),
       (SELECT count(*) FROM public.experiment_conversion),
       (SELECT count(*) FROM experiments.experiment_plan),
       (SELECT count(*) FROM experiments.experiment_frame_member),
       (SELECT count(*) FROM experiments.experiment_frame_exclusion),
       (SELECT count(*) FROM experiments.experiment_flag),
       (SELECT count(*) FROM experiments.experiment_assignment_record),
       (SELECT count(*) FROM experiments.experiment_assignment_balance),
       (SELECT count(*) FROM experiments.experiment_result),
       (SELECT count(*) FROM experiments.experiment_result_arm),
       (SELECT count(*) FROM experiments.experiment_result_comparison),
       (SELECT count(*) FROM experiments.experiment_result_balance),
       (SELECT count(*) FROM experiments.sale_attribution),
       (SELECT count(*) FROM experiments.exposure_notice)
"""
    before = scene.owner.execute(counts).fetchone()

    assert _current(api, scene.customers[0], campaign).status_code == 200

    assert scene.owner.execute(counts).fetchone() == before


_ = (json, uuid)
