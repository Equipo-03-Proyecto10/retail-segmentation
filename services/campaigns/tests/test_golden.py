"""The port held to the web's captured outcomes (ADR-0045, E-17).

tests/golden/campaigns.json is what web/services/campaigns.py did on each case,
as captured by `python -m tests.golden.capture --service campaigns`. Each case
is replayed here through the service's HTTP surface on a fresh copy of the
seed. An accepted case must be accepted and leave the same state behind; a
refused case must be refused, in the service's own code. Wherever the service
answers with a code where the web has a sentence, the case carries a
`service_differs` note (checked below) and the service's code is asserted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app import rules

pytestmark = pytest.mark.postgres
FIXTURE = json.loads((Path(__file__).parent / "golden/campaigns.json").read_text())
CASES = {case["id"]: case for case in FIXTURE["cases"]}
TABLE = CASES["transition-table"]["outcome"]["transitions"]
STATUSES = ("DRAFT", "ACTIVE", "FINISHED", "CANCELLED")
SEEDED = {"DRAFT": 1, "ACTIVE": 2, "FINISHED": 3, "CANCELLED": 4}
MOVES = [
    name
    for name in CASES
    if name.startswith("transition-") and name != "transition-table"
]
EDITS = ("edit-draft", "edit-active-campaign", "edit-draft-end-before-start")
FORM = ("name", "label_code", "starts_on", "ends_on")


def _code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


def _move(api: Any, campaign_id: int, to: str, expected: str) -> Any:
    return api(
        "POST",
        f"/v1/campaigns/{campaign_id}/transitions",
        json_body={"to": to, "expected_status": expected},
    )


def _status(owner: psycopg.Connection, campaign_id: int) -> str:
    return owner.execute(
        "SELECT status FROM campaign WHERE campaign_id = %s", (campaign_id,)
    ).fetchone()[0]


def test_the_fixture_is_the_campaigns_port() -> None:
    assert FIXTURE["header"]["port"] == "campaigns"
    assert len(MOVES) == 12


def test_the_transition_table_is_the_webs() -> None:
    assert {k: sorted(v) for k, v in rules.TRANSITIONS.items()} == TABLE


@pytest.mark.parametrize("source", STATUSES)
@pytest.mark.parametrize("target", STATUSES)
def test_all_sixteen_pairs_are_allowed_exactly_as_the_web_allows_them(
    source: str, target: str, api: Any, owner: psycopg.Connection
) -> None:
    campaign_id = SEEDED[source]
    reply = _move(api, campaign_id, target, source)
    if target not in TABLE[source]:
        assert _code(reply) == (409, "illegal_transition")
        assert _status(owner, campaign_id) == source
    elif target == "ACTIVE":
        # The only allowed move to ACTIVE is from the seeded draft, which has
        # ended (RN-49): the web refuses it on the captured business date too.
        assert _code(reply) == (409, "campaign_ended")
    else:
        assert reply.status_code == 200
        assert _status(owner, campaign_id) == target


@pytest.mark.parametrize("case_id", MOVES)
def test_each_captured_move_has_the_same_outcome(
    case_id: str, api: Any, owner: psycopg.Connection
) -> None:
    case = CASES[case_id]
    given, web = case["input"], case["outcome"]
    reply = _move(api, given["campaign_id"], given["to"], given["from"])
    if web["outcome"] == "accepted":
        assert reply.status_code == 200
        assert _status(owner, given["campaign_id"]) == web["observed"]["status"]
        assert "service_differs" not in case
        return
    ended = "ended on" in web["reason"]
    assert _code(reply) == (409, "campaign_ended" if ended else "illegal_transition")
    assert _status(owner, given["campaign_id"]) == given["from"]
    assert "service_differs" in case


@pytest.mark.parametrize("case_id", EDITS)
def test_each_captured_edit_has_the_same_outcome(
    case_id: str, api: Any, owner: psycopg.Connection
) -> None:
    case = CASES[case_id]
    given, web = case["input"], case["outcome"]
    reply = api(
        "PATCH",
        f"/v1/campaigns/{given['campaign_id']}",
        json_body={key: given[key] for key in FORM},
    )
    if web["outcome"] == "accepted":
        assert reply.status_code == 200
        name = owner.execute(
            "SELECT name FROM campaign WHERE campaign_id = %s", (given["campaign_id"],)
        ).fetchone()[0]
        assert name == web["observed"]["name"]
        return
    assert "service_differs" in case
    if case_id == "edit-active-campaign":
        assert _code(reply) == (409, "campaign_not_draft")
    else:
        assert _code(reply) == (422, "ends_before_start")
        assert web["field"] == "ends_on"


def test_the_validation_refusal_is_ends_before_start_on_create(api: Any) -> None:
    case = CASES["validate-end-before-start"]
    given = case["input"]
    assert case["outcome"]["errors"] == {
        "ends_on": "End date cannot be before the start date."
    }
    reply = api("POST", "/v1/campaigns", json_body={key: given[key] for key in FORM})
    assert _code(reply) == (422, "ends_before_start")
    assert "service_differs" in case


def test_the_captured_duplicate_create_leaves_one_draft(
    api: Any, owner: psycopg.Connection
) -> None:
    case = CASES["create-duplicate-draft"]
    given, web = case["input"], case["outcome"]
    body = {key: given[key] for key in FORM}

    assert api("POST", "/v1/campaigns", json_body=body).status_code == 201
    assert _code(api("POST", "/v1/campaigns", json_body=body)) == (
        409,
        "duplicate_draft",
    )
    assert web["observed"] == {"count": 1, "first": 31, "second": None}
    assert (
        owner.execute(
            "SELECT count(*) FROM campaign WHERE name = %s AND label_code = %s"
            " AND starts_on = %s AND ends_on = %s AND status = 'DRAFT'",
            tuple(body.values()),
        ).fetchone()[0]
        == 1
    )
    assert "service_differs" in case


def _draft_2099(api: Any, given: dict[str, Any]) -> int:
    reply = api("POST", "/v1/campaigns", json_body={key: given[key] for key in FORM})
    assert reply.status_code == 201
    return reply.get_json()["campaign_id"]


def test_an_open_dated_draft_with_no_experiment_activates(
    api: Any, owner: psycopg.Connection
) -> None:
    case = CASES["activate-draft-with-no-experiment"]
    assert case["outcome"] == {"outcome": "accepted", "observed": {"status": "ACTIVE"}}
    campaign_id = _draft_2099(api, case["input"])
    assert _move(api, campaign_id, "ACTIVE", "DRAFT").status_code == 200
    assert _status(owner, campaign_id) == "ACTIVE"


def test_a_draft_whose_experiment_has_no_treatment_is_refused_as_the_web_refuses(
    api: Any, owner: psycopg.Connection
) -> None:
    case = CASES["activate-draft-with-control-only-experiment"]
    assert case["outcome"]["outcome"] == "refused"
    assert "has no treatment group" in case["outcome"]["reason"]
    assert "service_differs" in case
    campaign_id = _draft_2099(api, case["input"])
    owner.execute(
        "INSERT INTO experiment (experiment_id, name, campaign_id, target_metric,"
        " starts_on, ends_on, conversion_window_days, data_origin)"
        " SELECT coalesce(max(experiment_id), 0) + 1, 'Golden control only', %s,"
        " 'CONVERSION', DATE '2099-01-01', DATE '2099-12-31', 7, 'OBSERVED'"
        " FROM experiment",
        (campaign_id,),
    )
    owner.execute(
        "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
        " treatment_description) SELECT coalesce(max(group_id), 0) + 1,"
        " (SELECT max(experiment_id) FROM experiment), 'CONTROL', 'Golden holdout',"
        " 'No treatment delivered.' FROM experiment_group"
    )
    assert _code(_move(api, campaign_id, "ACTIVE", "DRAFT")) == (
        409,
        "experiment_not_ready",
    )
    assert _status(owner, campaign_id) == "DRAFT"


def test_every_refused_case_is_either_noted_or_has_the_same_words() -> None:
    """A refusal the service words differently must say so in the fixture."""
    silent = [
        name
        for name, case in CASES.items()
        if case["outcome"]["outcome"] == "refused" and "service_differs" not in case
    ]
    assert silent == []
