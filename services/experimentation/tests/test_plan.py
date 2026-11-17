"""Planning an experiment: plan, stored, shown, frozen (F25-09, ADR-0068).

Every test builds its scenario on its own copy of the seeded database, through
real writes by the tables' owners, so each frame, count and size is written out
by hand. The campaign targets POTENTIAL, held by seven customers h1 to h7 (in
id order). The scenario removes five of them, each for a different reason:

    h1  withdrew PERSONALIZATION    -> NO_PERSONALIZATION_CONSENT
    h2  withdrew MARKETING_CONTACT  -> NO_MARKETING_CONTACT_CONSENT
    h3  named in the campaign's exclusions -> CAMPAIGN_EXCLUSION
    h4  restricted every channel    -> CHANNEL_RESTRICTED
    h5  assigned in another experiment whose period overlaps -> OVERLAPPING_EXPERIMENT

and leaves h6 and h7. Only h6 bought in the baseline period (three days ago).
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
import yaml
from psycopg import sql

from tests.conftest import ADMIN, MARKETING, SERVICE

pytestmark = pytest.mark.postgres

NS = "{https://mosaiq.maxthecoder.online/xml/experimentation/v1}"
MARKETING_CALLER = (MARKETING, "MARKETING")
TODAY = datetime.now(UTC).date()
# The seeded experiments run around today and hold most seeded customers, which rule 6
# would remove from a frame that overlaps them, so every plan here starts a year away.
START = TODAY + timedelta(days=400)
ARMS = [
    {
        "kind": "CONTROL",
        "name": "No offer",
        "treatment_description": "Held back: receives no offer.",
    },
    {
        "kind": "TREATMENT",
        "name": "Offer",
        "treatment_description": "Offered 5 percent off.",
    },
]
SPEC = yaml.safe_load((SERVICE / "contracts/experimentation/openapi.yaml").read_text())[
    "components"
]["schemas"]


def _code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


def _details(reply: Any) -> list[dict[str, str]]:
    return (reply.get_json(silent=True) or {}).get("details", [])


class Scenario:
    """One campaign, seven holders of its label, and the five reasons above."""

    def __init__(self, owner: psycopg.Connection) -> None:
        self.owner = owner
        self.holders = [
            row[0]
            for row in owner.execute(
                "SELECT customer_id FROM api_segments.current_label_v1"
                " WHERE state = 'assigned' AND label_code = 'POTENTIAL'"
                " ORDER BY customer_id"
            )
        ]
        assert len(self.holders) == 7
        self.h = [str(customer) for customer in self.holders]
        self.campaign = owner.execute(
            "SELECT max(campaign_id) + 1 FROM public.campaign"
        ).fetchone()[0]
        owner.execute(
            "INSERT INTO public.campaign (campaign_id, name, label_code, starts_on,"
            " ends_on, status) VALUES (%s, 'F25-09 scenario', 'POTENTIAL', %s, %s,"
            " 'DRAFT')",
            (self.campaign, TODAY, TODAY + timedelta(days=60)),
        )
        for customer in self.holders:
            for purpose in ("PERSONALIZATION", "MARKETING_CONTACT"):
                self._consent(customer, purpose, "GRANTED")
        self._consent(self.holders[0], "PERSONALIZATION", "WITHDRAWN")
        self._consent(self.holders[1], "MARKETING_CONTACT", "WITHDRAWN")
        owner.execute(
            "INSERT INTO campaigns.campaign_excluded_customer"
            " (campaign_id, customer_id) VALUES (%s, %s)",
            (self.campaign, self.holders[2]),
        )
        for (channel,) in owner.execute("SELECT channel_id FROM public.channel"):
            owner.execute(
                "INSERT INTO preferences.restriction_decision (customer_id, kind,"
                " channel_id, decision, recorded_by, source)"
                " VALUES (%s, 'CHANNEL', %s, 'IMPOSED', %s, 'STAFF_ON_BEHALF')",
                (self.holders[3], channel, ADMIN),
            )
        self.earlier = self._earlier_experiment(self.holders[4])
        # Nothing the seed sold counts, except the one sale h6 made three days ago.
        owner.execute(
            "UPDATE public.transaction SET occurred_at = TIMESTAMPTZ '2000-01-01'"
            " WHERE customer_id = ANY(%s::uuid[])",
            (self.holders,),
        )
        self.sale = owner.execute(
            "UPDATE public.transaction SET occurred_at = now() - interval '3 days'"
            " WHERE transaction_id = (SELECT min(transaction_id)"
            " FROM public.transaction WHERE customer_id = %s) RETURNING transaction_id",
            (self.holders[5],),
        ).fetchone()[0]
        self.spend = float(
            owner.execute(
                "SELECT sum(net_amount) FROM api_sales.net_purchase_line_v1"
                " WHERE transaction_id = %s",
                (self.sale,),
            ).fetchone()[0]
        )

    def _consent(self, customer: Any, purpose: str, decision: str) -> None:
        self.owner.execute(
            "INSERT INTO preferences.consent_decision (customer_id, purpose, decision,"
            " recorded_by, source, basis)"
            " VALUES (%s, %s, %s, %s, 'STAFF_ON_BEHALF', 'EXPLICIT')",
            (customer, purpose, decision, ADMIN),
        )

    def _earlier_experiment(self, customer: Any) -> int:
        owner = self.owner
        experiment = owner.execute(
            "SELECT max(experiment_id) + 1 FROM public.experiment"
        ).fetchone()[0]
        group = owner.execute(
            "SELECT max(group_id) + 1 FROM public.experiment_group"
        ).fetchone()[0]
        owner.execute(
            "INSERT INTO public.experiment (experiment_id, name, campaign_id,"
            " target_metric, starts_on, ends_on, conversion_window_days, data_origin)"
            " VALUES (%s, 'Earlier experiment', NULL, 'CONVERSION', %s, %s, 14,"
            " 'OBSERVED')",
            (experiment, START - timedelta(days=5), START + timedelta(days=16)),
        )
        owner.execute(
            "INSERT INTO public.experiment_group (group_id, experiment_id, kind, name,"
            " treatment_description) VALUES (%s, %s, 'TREATMENT', 'T', 'T')",
            (group, experiment),
        )
        owner.execute(
            "INSERT INTO public.experiment_assignment (experiment_id, group_id,"
            " customer_id) VALUES (%s, %s, %s)",
            (experiment, group, customer),
        )
        return experiment

    def body(self, **changes: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": "Spring offer",
            "campaign_id": self.campaign,
            "primary_metric": "CONVERSION",
            "starts_on": START.isoformat(),
            "conversion_window_days": 14,
            "minimum_detectable_effect": 0.1,
            "arms": ARMS,
        }
        body.update(changes)
        return body


@pytest.fixture
def scenario(owner: psycopg.Connection) -> Scenario:
    return Scenario(owner)


def _plan(api: Any, body: dict[str, Any], **kwargs: Any) -> Any:
    return api(
        "POST", "/v1/experiments", as_user=MARKETING_CALLER, json_body=body, **kwargs
    )


def _digest(
    owner: psycopg.Connection, experiment: int, campaign: int, taken_at: str
) -> str:
    """The digest by its documented definition, from the stored rows alone."""
    customers = sorted(
        str(row[0])
        for row in owner.execute(
            "SELECT customer_id FROM experiments.experiment_frame_member"
            " WHERE experiment_id = %s",
            (experiment,),
        )
    )
    excluded_customers = sorted(
        str(row[0])
        for row in owner.execute(
            "SELECT customer_id FROM campaigns.campaign_excluded_customer"
            " WHERE campaign_id = %s",
            (campaign,),
        )
    )
    excluded_labels = sorted(
        row[0]
        for row in owner.execute(
            "SELECT label_code FROM campaigns.campaign_excluded_label"
            " WHERE campaign_id = %s",
            (campaign,),
        )
    )
    rule = owner.execute(
        "SELECT registered_on_or_before, min_purchases, purchase_window_days"
        " FROM campaigns.campaign_eligibility WHERE campaign_id = %s",
        (campaign,),
    ).fetchone() or (None, None, None)
    document = {
        "criteria": {
            "campaign_id": campaign,
            "excluded_customers": excluded_customers,
            "excluded_labels": excluded_labels,
            "label_code": "POTENTIAL",
            "rules": {
                "min_purchases": rule[1],
                "purchase_window_days": rule[2],
                "registered_on_or_before": rule[0].isoformat() if rule[0] else None,
            },
            "taken_at": taken_at,
        },
        "customers": customers,
    }
    text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def test_a_plan_is_frozen_stored_and_shown(
    api: Any, scenario: Scenario, owner: psycopg.Connection
) -> None:
    reply = _plan(api, scenario.body())

    assert reply.status_code == 201, reply.get_json()
    plan = reply.get_json()
    experiment = plan["experiment_id"]
    assert reply.headers["Location"] == f"/v1/experiments/{experiment}"
    assert plan["campaign_id"] == scenario.campaign
    assert plan["state"] == "PLANNED"
    assert plan["design_kind"] == "CONTROLLED"
    assert plan["assignment_rule"] == "V1"
    assert plan["data_origin"] == "OBSERVED"
    assert plan["assignment"] is None
    assert plan["planned_by"] == MARKETING
    assert [(a["position"], a["kind"], a["name"]) for a in plan["arms"]] == [
        (0, "CONTROL", "No offer"),
        (1, "TREATMENT", "Offer"),
    ]
    # Two customers, one per arm; 1 of 2 bought: p1 = 0.5, p2 = 0.6, k = 2.801585.
    # n = 7.848879 * (0.25 + 0.24) / 0.1^2 = 384.6, so 385. One per arm is not 385.
    design = plan["design"]
    assert design["primary_metric"] == "CONVERSION"
    assert design["secondary_metric"] == "REVENUE_PER_CUSTOMER"
    assert design["estimator"] == "DIFFERENCE_OF_PROPORTIONS"
    assert (design["alpha"], design["comparison_alpha"], design["power"]) == (
        0.05,
        0.05,
        0.8,
    )
    assert design["minimum_detectable_effect"] == 0.1
    assert design["baseline"] == 0.5
    assert design["baseline_std_dev"] is None
    assert design["required_per_arm"] == 385
    assert design["smallest_detectable_effect"] is None
    assert design["allocation"] == "EQUAL"
    assert design["settle_days"] == 7
    assert design["conversion_window_days"] == 14
    assert design["starts_on"] == START.isoformat()
    assert design["analysis_at"] is None
    assert plan["flags"] == ["UNDERPOWERED"]

    frame = plan["frame"]
    assert frame["size"] == 2
    assert frame["label_code"] == "POTENTIAL"
    assert frame["model_version_id"] is None
    assert frame["taken_at"] == plan["planned_at"]
    assert frame["exclusions"] == [
        {"reason": "CAMPAIGN_EXCLUSION", "count": 1},
        {"reason": "CHANNEL_RESTRICTED", "count": 1},
        {"reason": "NO_MARKETING_CONTACT_CONSENT", "count": 1},
        {"reason": "NO_PERSONALIZATION_CONSENT", "count": 1},
        {"reason": "OVERLAPPING_EXPERIMENT", "count": 1},
    ]
    assert len(plan["salt"]) == 64 and int(plan["salt"], 16) >= 0

    # The stored rows are the plan, and the frame is exactly h6 and h7.
    stored = {
        str(row[0])
        for row in owner.execute(
            "SELECT customer_id FROM experiments.experiment_frame_member"
            " WHERE experiment_id = %s",
            (experiment,),
        )
    }
    assert stored == {scenario.h[5], scenario.h[6]}
    assert frame["digest"] == _digest(
        owner, experiment, scenario.campaign, frame["taken_at"]
    )
    assert owner.execute(
        "SELECT target_metric, conversion_window_days, ends_on - starts_on"
        " FROM public.experiment WHERE experiment_id = %s",
        (experiment,),
    ).fetchone() == ("CONVERSION", 14, 21)

    # Shown: the same plan is read back, singly and in the list, newest first.
    assert api("GET", f"/v1/experiments/{experiment}").get_json() == plan
    listed = api("GET", "/v1/experiments?size=5").get_json()
    assert listed["experiments"][0] == plan
    assert listed["page_info"]["total"] == 32
    assert [e["experiment_id"] for e in listed["experiments"]] == sorted(
        (e["experiment_id"] for e in listed["experiments"]), reverse=True
    )


def test_a_plan_answers_the_contracts_shape_and_nothing_more(
    api: Any, scenario: Scenario
) -> None:
    plan = _plan(api, scenario.body()).get_json()

    def check(value: dict[str, Any], schema: str) -> None:
        definition = SPEC[schema]
        assert set(definition["required"]) <= set(value), schema
        assert set(value) <= set(definition["properties"]), schema

    check(plan, "Experiment")
    check(plan["design"], "Design")
    check(plan["frame"], "Frame")
    for arm in plan["arms"]:
        check(arm, "Arm")
    for exclusion in plan["frame"]["exclusions"]:
        check(exclusion, "FrameExclusion")


def test_the_xml_plan_is_the_same_plan(api: Any, scenario: Scenario) -> None:
    body = scenario.body()
    arms = "".join(
        f"<arms><kind>{a['kind']}</kind><name>{a['name']}</name>"
        f"<treatment_description>{a['treatment_description']}</treatment_description>"
        "</arms>"
        for a in ARMS
    )
    xml = (
        f'<ExperimentRequest xmlns="{NS[1:-1]}"><name>{body["name"]}</name>'
        f"<campaign_id>{scenario.campaign}</campaign_id>"
        "<primary_metric>CONVERSION</primary_metric>"
        f"<starts_on>{body['starts_on']}</starts_on>"
        "<conversion_window_days>14</conversion_window_days>"
        "<minimum_detectable_effect>0.1</minimum_detectable_effect>"
        f"{arms}</ExperimentRequest>"
    )

    reply = api(
        "POST",
        "/v1/experiments",
        as_user=MARKETING_CALLER,
        data=xml,
        content_type="application/xml",
        headers={"Accept": "application/xml"},
    )

    assert reply.status_code == 201, reply.get_data(as_text=True)
    assert reply.content_type.startswith("application/xml")
    root = ET.fromstring(reply.data)
    assert root.tag == NS + "Experiment"
    experiment = int(root.findtext(NS + "experiment_id"))
    as_json = api("GET", f"/v1/experiments/{experiment}").get_json()
    assert root.findtext(NS + "state") == as_json["state"] == "PLANNED"
    assert root.findtext(NS + "salt") == as_json["salt"]
    assert root.findtext(f"{NS}frame/{NS}digest") == as_json["frame"]["digest"]
    assert int(root.findtext(f"{NS}frame/{NS}size")) == 2
    assert int(root.findtext(f"{NS}design/{NS}required_per_arm")) == 385
    assert float(root.findtext(f"{NS}design/{NS}baseline")) == 0.5
    assert [e.text for e in root.findall(f"{NS}flags")] == ["UNDERPOWERED"]
    assert [
        e.findtext(NS + "reason") for e in root.findall(f"{NS}frame/{NS}exclusions")
    ] == [e["reason"] for e in as_json["frame"]["exclusions"]]
    shown = api(
        "GET", f"/v1/experiments/{experiment}", headers={"Accept": "application/xml"}
    )
    assert ET.fromstring(shown.data).findtext(NS + "salt") == as_json["salt"]


def test_a_revenue_plan_stores_the_mean_and_spread_of_the_frames_own_spend(
    api: Any, scenario: Scenario
) -> None:
    # h6 spent A = scenario.spend, h7 nothing: the mean is A / 2 and the sample
    # standard deviation sqrt(((A/2)^2 + (A/2)^2) / (2 - 1)) = A / sqrt(2).
    effect = 10 * scenario.spend
    plan = _plan(
        api,
        scenario.body(
            primary_metric="REVENUE_PER_CUSTOMER", minimum_detectable_effect=effect
        ),
    ).get_json()

    design = plan["design"]
    assert design["estimator"] == "DIFFERENCE_OF_MEANS"
    assert design["secondary_metric"] == "CONVERSION"
    assert design["baseline"] == pytest.approx(scenario.spend / 2, rel=1e-9)
    assert design["baseline_std_dev"] == pytest.approx(
        scenario.spend / math.sqrt(2), rel=1e-9
    )
    # n = 2 * 7.848879 * (A^2 / 2) / (10 A)^2 = 0.078: at least one per arm.
    assert design["required_per_arm"] == 1
    assert plan["flags"] == []
    # The smallest effect one customer per arm detects: k * s * sqrt(2) = 2.801585 * A.
    assert design["smallest_detectable_effect"] == pytest.approx(
        2.801585 * scenario.spend, abs=1e-4 + 5e-5
    )


def test_two_treatments_and_a_control_split_alpha_by_bonferroni(
    api: Any, scenario: Scenario
) -> None:
    arms = [*ARMS, {**ARMS[1], "name": "Offer B"}]

    plan = _plan(api, scenario.body(arms=arms)).get_json()

    assert plan["design_kind"] == "CONTROLLED"
    assert plan["design"]["alpha"] == 0.05
    assert plan["design"]["comparison_alpha"] == pytest.approx(0.025)
    assert [a["position"] for a in plan["arms"]] == [0, 1, 2]
    assert [a["kind"] for a in plan["arms"]] == ["CONTROL", "TREATMENT", "TREATMENT"]


def test_two_offers_without_a_control_are_one_comparison_at_full_alpha(
    api: Any, scenario: Scenario
) -> None:
    arms = [ARMS[1], {**ARMS[1], "name": "Offer B"}]

    plan = _plan(api, scenario.body(arms=arms)).get_json()

    assert plan["design_kind"] == "OFFER_COMPARISON"
    assert plan["design"]["comparison_alpha"] == 0.05
    assert [a["kind"] for a in plan["arms"]] == ["TREATMENT", "TREATMENT"]


def test_alpha_power_and_origin_are_taken_as_given_or_defaulted(
    api: Any, scenario: Scenario
) -> None:
    plan = _plan(
        api,
        scenario.body(alpha=0.10, power=0.90, data_origin="SEEDED", name="Own levels"),
    ).get_json()

    assert plan["data_origin"] == "SEEDED"
    assert (plan["design"]["alpha"], plan["design"]["power"]) == (0.1, 0.9)
    # z(0.95) + z(0.90) = 1.644854 + 1.281552 = 2.926406; n = k^2 * 0.49 / 0.1^2.
    k = 1.644854 + 1.281552
    assert plan["design"]["required_per_arm"] == math.ceil(k * k * 0.49 / 0.01)


def test_the_first_plan_keeps_its_customers_for_an_overlapping_period(
    api: Any, scenario: Scenario
) -> None:
    first = _plan(api, scenario.body(name="First")).get_json()
    second = _plan(api, scenario.body(name="Second")).get_json()

    assert first["frame"]["size"] == 2
    # h6 and h7 are in the first frame and h5 in the earlier experiment: nobody is left.
    assert second["frame"]["size"] == 0
    assert {e["reason"]: e["count"] for e in second["frame"]["exclusions"]} == {
        "CAMPAIGN_EXCLUSION": 1,
        "CHANNEL_RESTRICTED": 1,
        "NO_MARKETING_CONTACT_CONSENT": 1,
        "NO_PERSONALIZATION_CONSENT": 1,
        "OVERLAPPING_EXPERIMENT": 3,
    }
    # An empty frame has baseline 0: n = 7.848879 * (0 + 0.1 * 0.9) / 0.1^2 = 70.6: 71.
    assert second["design"]["baseline"] == 0.0
    assert second["design"]["required_per_arm"] == 71
    assert second["design"]["smallest_detectable_effect"] is None
    assert second["flags"] == ["UNDERPOWERED"]


def test_a_plan_for_a_later_period_may_use_the_same_customers_again(
    api: Any, scenario: Scenario
) -> None:
    _plan(api, scenario.body(name="First")).get_json()
    # [start + 200, start + 221] does not meet [start, start + 21].
    later = scenario.body(
        name="Later", starts_on=(START + timedelta(days=200)).isoformat()
    )

    plan = _plan(api, later).get_json()

    # The earlier experiment ends at start + 16 and the first frame's period at start +
    # 21, so neither overlaps [start + 200, ...): h5, h6 and h7 are all eligible.
    assert plan["frame"]["size"] == 3
    reasons = {e["reason"]: e["count"] for e in plan["frame"]["exclusions"]}
    assert "OVERLAPPING_EXPERIMENT" not in reasons
    assert reasons["CAMPAIGN_EXCLUSION"] == 1
    assert plan["design"]["baseline"] == pytest.approx(1 / 3)


def test_two_plans_at_once_never_share_a_customer(
    service: Any, token: Any, scenario: Scenario, owner: psycopg.Connection
) -> None:
    results: list[Any] = []

    def run(name: str) -> None:
        client = service.test_client()
        headers = {"Authorization": "Bearer " + token(*MARKETING_CALLER)}
        reply = client.post(
            "/v1/experiments", json=scenario.body(name=name), headers=headers
        )
        results.append(reply)

    threads = [threading.Thread(target=run, args=(f"Race {n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [r.status_code for r in results] == [201, 201]
    sizes = sorted(r.get_json()["frame"]["size"] for r in results)
    assert sizes == [0, 2]
    ids = [r.get_json()["experiment_id"] for r in results]
    assert len(set(ids)) == 2
    members = owner.execute(
        "SELECT customer_id, count(*) FROM experiments.experiment_frame_member"
        " WHERE experiment_id = ANY(%s) GROUP BY customer_id",
        (ids,),
    ).fetchall()
    assert len(members) == 2 and all(count == 1 for _, count in members)


@pytest.mark.parametrize(
    ("arms", "code"),
    [
        ([ARMS[0], ARMS[0], ARMS[1]], "control_count"),
        ([ARMS[0], {**ARMS[0], "name": "Second"}], "control_count"),
        (
            [ARMS[1], {**ARMS[1], "name": "B"}, {**ARMS[1], "name": "C"}],
            "treatment_count",
        ),
    ],
)
def test_arms_that_break_the_design_rule_are_refused(
    api: Any, scenario: Scenario, arms: list[dict[str, Any]], code: str
) -> None:
    reply = _plan(api, scenario.body(arms=arms))

    assert _code(reply) == (422, "invalid_body")
    assert _details(reply) == [{"field": "arms", "code": code}]


def test_a_campaign_that_does_not_exist_is_refused(
    api: Any, scenario: Scenario
) -> None:
    reply = _plan(api, scenario.body(campaign_id=987654))

    assert _code(reply) == (422, "invalid_body")
    assert _details(reply) == [{"field": "campaign_id", "code": "unknown_campaign"}]


@pytest.mark.parametrize("status", ["FINISHED", "CANCELLED"])
def test_a_finished_or_cancelled_campaign_is_closed(
    api: Any, scenario: Scenario, owner: psycopg.Connection, status: str
) -> None:
    owner.execute(
        "UPDATE public.campaign SET status = %s WHERE campaign_id = %s",
        (status, scenario.campaign),
    )

    reply = _plan(api, scenario.body())

    assert _code(reply) == (409, "campaign_closed")
    assert (
        owner.execute("SELECT count(*) FROM experiments.experiment_plan").fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"minimum_detectable_effect": 1.0},
        {"minimum_detectable_effect": 0.6},
    ],
)
def test_an_effect_that_takes_the_rate_past_one_is_out_of_range(
    api: Any, scenario: Scenario, changes: dict[str, Any]
) -> None:
    # 1.0 is refused up front; 0.6 over the frame's baseline of 0.5 gives 1.1.
    reply = _plan(api, scenario.body(**changes))

    assert _code(reply) == (422, "invalid_body")
    assert _details(reply) == [
        {"field": "minimum_detectable_effect", "code": "out_of_range"}
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"minimum_detectable_effect": 0},
        {"minimum_detectable_effect": -0.1},
        {"alpha": 0},
        {"alpha": 1},
        {"power": 1},
        {"power": 0},
        {"conversion_window_days": 0},
        {"conversion_window_days": 366},
        {"primary_metric": "AVERAGE_TICKET"},
        {"data_origin": "REAL"},
        {"campaign_id": 0},
        {"name": ""},
        {"starts_on": "not-a-date"},
        {"unexpected": 1},
        {"arms": [ARMS[0]]},
        {"arms": [*ARMS, *ARMS, *ARMS]},
    ],
)
def test_a_body_outside_the_contracts_schema_is_invalid(
    api: Any, scenario: Scenario, owner: psycopg.Connection, changes: dict[str, Any]
) -> None:
    reply = _plan(api, scenario.body(**changes))

    assert _code(reply) == (422, "invalid_body")
    assert (
        owner.execute("SELECT count(*) FROM experiments.experiment_plan").fetchone()[0]
        == 0
    )


def test_a_missing_field_is_invalid_and_a_broken_body_is_malformed(
    api: Any, scenario: Scenario
) -> None:
    body = scenario.body()
    del body["primary_metric"]
    assert _code(_plan(api, body)) == (422, "invalid_body")
    assert _code(_plan(api, None, data="{", content_type="application/json")) == (
        400,
        "malformed_body",
    )
    unsupported = _plan(api, None, data="x", content_type="text/plain")
    assert _code(unsupported) == (415, "unsupported_media_type")


def test_only_administrators_and_marketing_plan_and_four_roles_read(
    api: Any, scenario: Scenario
) -> None:
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
        planned = api(
            "POST",
            "/v1/experiments",
            as_user=(ADMIN, role),
            json_body=scenario.body(name=f"By {role}"),
        )
        read = api("GET", "/v1/experiments/1", as_user=(ADMIN, role))
        listed = api("GET", "/v1/experiments", as_user=(ADMIN, role))
        if role in ("ADMIN", "MARKETING"):
            assert planned.status_code == 201, role
        else:
            assert _code(planned) == (403, "forbidden_role"), role
        if role in ("ADMIN", "ANALYST", "MARKETING", "AUDITOR"):
            assert read.status_code == listed.status_code == 200, role
        else:
            assert _code(read) == (403, "forbidden_role"), role
            assert _code(listed) == (403, "forbidden_role"), role


def test_a_plan_is_frozen_for_the_service_the_owner_and_every_route(
    api: Any, scenario: Scenario, database: dict[str, str], owner: psycopg.Connection
) -> None:
    experiment = _plan(api, scenario.body()).get_json()["experiment_id"]

    for method in ("PUT", "PATCH", "DELETE"):
        reply = api(method, f"/v1/experiments/{experiment}", json_body={})
        assert _code(reply) == (405, "method_not_allowed"), method
    for table, change in (
        ("experiment_plan", "UPDATE experiments.experiment_plan SET power = 0.9"),
        ("experiment_plan", "DELETE FROM experiments.experiment_plan"),
        ("experiment_frame_member", "DELETE FROM experiments.experiment_frame_member"),
        (
            "experiment_frame_exclusion",
            "UPDATE experiments.experiment_frame_exclusion SET removed = 0",
        ),
        ("experiment_flag", "DELETE FROM experiments.experiment_flag"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation) as refused:
            owner.execute(change)
        assert refused.value.diag.constraint_name == "experiment_plan_frozen", table
    with psycopg.connect(database["service"], autocommit=True) as service:
        for change in (
            "UPDATE experiments.experiment_plan SET power = 0.9",
            "DELETE FROM experiments.experiment_frame_member",
        ):
            try:
                service.execute(change)
            except psycopg.errors.InsufficientPrivilege:
                continue
            raise AssertionError(f"the service was allowed: {change}")
    shown = api("GET", f"/v1/experiments/{experiment}").get_json()
    assert shown["design"]["power"] == 0.8


def _select_one(relation: str) -> Any:
    return sql.SQL("SELECT 1 FROM {} LIMIT 1").format(
        sql.Identifier(*relation.split("."))
    )


def test_the_service_reads_only_published_objects_for_the_frame(
    database: dict[str, str],
) -> None:
    with psycopg.connect(database["service"], autocommit=True) as service:
        for table in (
            "public.campaign",
            "campaigns.campaign_eligibility",
            "preferences.consent_decision",
            "public.customer_segment_history",
            "public.transaction",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                service.execute(_select_one(table))
        for view in (
            "api_campaigns.campaign_target_v1",
            "api_campaigns.campaign_excluded_customer_v1",
            "api_campaigns.campaign_excluded_label_v1",
            "api_segments.current_label_v1",
            "api_preferences.consent_state_v1",
            "api_preferences.restriction_v1",
            "api_customers.customer_ref_v1",
            "api_sales.channel_v1",
            "api_sales.net_purchase_line_v1",
        ):
            service.execute(_select_one(view))


def test_a_seeded_experiment_is_shown_as_the_web_left_it(
    api: Any, owner: psycopg.Connection
) -> None:
    experiment = api("GET", "/v1/experiments/3")

    assert experiment.status_code == 200
    shown = experiment.get_json()
    assert shown["assignment_rule"] == "V0"
    assert shown["frame"] is None and shown["salt"] is None
    assert shown["planned_at"] is None and shown["planned_by"] is None
    assert shown["flags"] == []
    design = shown["design"]
    assert design["primary_metric"] == "CONVERSION"
    assert all(
        design[key] is None
        for key in (
            "secondary_metric",
            "estimator",
            "alpha",
            "comparison_alpha",
            "power",
            "minimum_detectable_effect",
            "baseline",
            "baseline_std_dev",
            "required_per_arm",
            "smallest_detectable_effect",
            "allocation",
            "settle_days",
            "analysis_at",
        )
    )
    arms, assigned = owner.execute(
        "SELECT (SELECT count(*) FROM experiment_group WHERE experiment_id = 3),"
        " (SELECT count(*) FROM experiment_assignment WHERE experiment_id = 3)"
    ).fetchone()
    assert len(shown["arms"]) == arms
    assert shown["state"] == "ASSIGNED"
    assert shown["assignment"]["assigned_total"] == assigned
    assert shown["assignment"]["assigned_by"] is None
    assert shown["assignment"]["checks"] is None
    assert sum(a["assigned"] for a in shown["assignment"]["arms"]) == assigned


def test_the_list_filters_and_pages(
    api: Any, scenario: Scenario, owner: psycopg.Connection
) -> None:
    planned = _plan(api, scenario.body()).get_json()

    by_campaign = api("GET", f"/v1/experiments?campaign_id={scenario.campaign}")
    assert [e["experiment_id"] for e in by_campaign.get_json()["experiments"]] == [
        planned["experiment_id"]
    ]
    assert by_campaign.get_json()["page_info"]["total"] == 1
    states = {
        state: api("GET", f"/v1/experiments?state={state}").get_json()["page_info"][
            "total"
        ]
        for state in ("PLANNED", "ASSIGNED", "ANALYZED")
    }
    # The 30 seeded experiments and the earlier one are assigned; the plan is not.
    assert states == {"PLANNED": 1, "ASSIGNED": 31, "ANALYZED": 0}
    origins = {
        origin: api("GET", f"/v1/experiments?data_origin={origin}").get_json()[
            "page_info"
        ]["total"]
        for origin in ("OBSERVED", "SEEDED", "INJECTED")
    }
    assert sum(origins.values()) == 32
    assert origins["OBSERVED"] == 2  # the earlier experiment and this plan
    second = api("GET", "/v1/experiments?size=2&page=2").get_json()
    assert second["page_info"] == {"page": 2, "size": 2, "total": 32}
    assert len(second["experiments"]) == 2


@pytest.mark.parametrize(
    "query",
    ["campaign_id=0", "campaign_id=x", "state=DONE", "data_origin=real"],
)
def test_a_list_filter_outside_its_domain_is_refused(api: Any, query: str) -> None:
    reply = api("GET", f"/v1/experiments?{query}")

    assert _code(reply) == (422, "invalid_query")


def test_an_experiment_that_does_not_exist_is_not_found(api: Any) -> None:
    assert _code(api("GET", "/v1/experiments/999999")) == (404, "experiment_not_found")
    assert api("GET", "/v1/experiments/0").status_code in (404, 422)


def test_the_plan_is_attributed_to_its_caller_in_the_audit_trail(
    api: Any, scenario: Scenario, owner: psycopg.Connection
) -> None:
    experiment = _plan(api, scenario.body()).get_json()["experiment_id"]

    actor, action = owner.execute(
        "SELECT user_id, action FROM audit_log WHERE entity = 'experiment'"
        " AND entity_pk = %s ORDER BY audit_id DESC LIMIT 1",
        (str(experiment),),
    ).fetchone()
    assert (str(actor), action) == (MARKETING, "INSERT")


def test_a_plan_that_fails_midway_leaves_nothing_behind(
    api: Any, scenario: Scenario, owner: psycopg.Connection, database: dict[str, str]
) -> None:
    """A plan that fails midway leaves no experiment behind: it is one transaction."""
    owner.execute(
        "REVOKE INSERT ON experiments.experiment_frame_exclusion FROM svc_experiments"
    )
    owner.execute("REVOKE INSERT ON experiments.experiment_flag FROM svc_experiments")

    reply = _plan(api, scenario.body())

    assert reply.status_code >= 500
    assert (
        owner.execute(
            "SELECT count(*) FROM public.experiment WHERE name = 'Spring offer'"
        ).fetchone()[0]
        == 0
    )
    assert (
        owner.execute("SELECT count(*) FROM experiments.experiment_plan").fetchone()[0]
        == 0
    )
    assert (
        owner.execute(
            "SELECT count(*) FROM experiments.experiment_frame_member"
        ).fetchone()[0]
        == 0
    )


def test_the_contract_serves_what_this_story_plans() -> None:
    contract = yaml.safe_load(
        (SERVICE / "contracts/experimentation/openapi.yaml").read_text()
    )
    served = {
        op["operationId"]: op
        for item in contract["paths"].values()
        for op in item.values()
        if isinstance(op, dict) and "operationId" in op
    }
    for operation in ("planExperiment", "getExperiment", "listExperiments"):
        assert "x-mosaiq-status" not in served[operation], operation
