"""The five operations against PostgreSQL, as svc_campaigns (F14-28).

Seeded campaigns 1, 2, 3 and 4 are DRAFT, ACTIVE, FINISHED and CANCELLED. Every
test works on its own copy of the seeded database, and the dates a rule turns
on are written relative to PostgreSQL's own CURRENT_DATE, never to the test's
clock.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.conftest import ADMIN, ANALYST, AUDITOR, MARKETING

pytestmark = pytest.mark.postgres

DRAFT_BODY = {
    "name": "Holiday Loyalty Preview",
    "label_code": "LOYAL",
    "starts_on": "2099-11-01",
    "ends_on": "2099-12-31",
}
MARKETING_CALLER = (MARKETING, "MARKETING")


def _code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


def _new_draft(owner: psycopg.Connection, starts: int, ends: int) -> int:
    """A DRAFT written by the owner, dated by days from PostgreSQL's CURRENT_DATE."""
    return owner.execute(
        "INSERT INTO campaign (campaign_id, name, label_code, starts_on, ends_on,"
        " status) SELECT max(campaign_id) + 1, 'Dated draft', 'LOYAL',"
        " CURRENT_DATE + %s, CURRENT_DATE + %s, 'DRAFT' FROM campaign"
        " RETURNING campaign_id",
        (starts, ends),
    ).fetchone()[0]


def _status(owner: psycopg.Connection, campaign_id: int) -> str:
    return owner.execute(
        "SELECT status FROM campaign WHERE campaign_id = %s", (campaign_id,)
    ).fetchone()[0]


def _move(api: Any, campaign_id: int, to: str, expected: str, **kwargs: Any) -> Any:
    return api(
        "POST",
        f"/v1/campaigns/{campaign_id}/transitions",
        json_body={"to": to, "expected_status": expected},
        **kwargs,
    )


def _experiment(
    owner: psycopg.Connection, campaign_id: int, kinds: tuple[str, ...]
) -> int:
    experiment_id = owner.execute(
        "INSERT INTO experiment (experiment_id, name, campaign_id, target_metric,"
        " starts_on, ends_on, conversion_window_days, data_origin)"
        " SELECT coalesce(max(experiment_id), 0) + 1, 'Pending', %s, 'CONVERSION',"
        " DATE '2099-01-01', DATE '2099-12-31', 7, 'OBSERVED' FROM experiment"
        " RETURNING experiment_id",
        (campaign_id,),
    ).fetchone()[0]
    for kind in kinds:
        _group(owner, experiment_id, kind)
    return experiment_id


def _group(owner: psycopg.Connection, experiment_id: int, kind: str) -> None:
    owner.execute(
        "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
        " treatment_description) SELECT coalesce(max(group_id), 0) + 1, %s, %s,"
        " %s, 'Described.' FROM experiment_group",
        (experiment_id, kind, kind.title() + " arm"),
    )


def test_list_filters_and_paginates_in_id_order(api: Any) -> None:
    everything = api("GET", "/v1/campaigns?size=100").get_json()
    ids = [campaign["campaign_id"] for campaign in everything["campaigns"]]
    assert ids == sorted(ids) and ids[:4] == [1, 2, 3, 4]
    assert everything["page_info"]["total"] == len(ids) == 30

    active = api("GET", "/v1/campaigns?status=ACTIVE&size=100").get_json()
    assert {c["status"] for c in active["campaigns"]} == {"ACTIVE"}
    assert active["page_info"]["total"] == len(active["campaigns"]) > 0

    label = everything["campaigns"][0]["label_code"]
    by_label = api("GET", f"/v1/campaigns?label_code={label}&size=100").get_json()
    assert {c["label_code"] for c in by_label["campaigns"]} == {label}

    second = api("GET", "/v1/campaigns?page=2&size=10").get_json()
    assert [c["campaign_id"] for c in second["campaigns"]] == ids[10:20]
    assert second["page_info"] == {"page": 2, "size": 10, "total": 30}


def test_get_shows_the_status_and_its_allowed_moves(api: Any) -> None:
    expected = {
        1: ("DRAFT", ["ACTIVE", "CANCELLED"]),
        2: ("ACTIVE", ["FINISHED", "CANCELLED"]),
        3: ("FINISHED", []),
        4: ("CANCELLED", []),
    }
    for campaign_id, (status, moves) in expected.items():
        body = api("GET", f"/v1/campaigns/{campaign_id}").get_json()
        assert (body["status"], body["allowed_transitions"]) == (status, moves)


def test_unknown_id_is_404_on_every_operation_that_names_one(api: Any) -> None:
    assert _code(api("GET", "/v1/campaigns/999999")) == (404, "campaign_not_found")
    assert _code(api("PATCH", "/v1/campaigns/999999", json_body={"name": "x"})) == (
        404,
        "campaign_not_found",
    )
    assert _code(_move(api, 999999, "ACTIVE", "DRAFT")) == (404, "campaign_not_found")


def test_there_is_no_delete_route(api: Any) -> None:
    assert api("DELETE", "/v1/campaigns/1").status_code == 405


def test_create_is_born_draft_with_its_location(
    api: Any, owner: psycopg.Connection
) -> None:
    reply = api("POST", "/v1/campaigns", json_body=DRAFT_BODY)
    assert reply.status_code == 201
    body = reply.get_json()
    assert reply.headers["Location"] == f"/v1/campaigns/{body['campaign_id']}"
    assert body["campaign_id"] == 31
    assert body["status"] == "DRAFT"
    assert body["allowed_transitions"] == ["ACTIVE", "CANCELLED"]
    assert {k: body[k] for k in DRAFT_BODY} == DRAFT_BODY
    assert api("GET", f"/v1/campaigns/{body['campaign_id']}").get_json() == body


def test_create_trims_the_name_and_refuses_an_empty_one(api: Any) -> None:
    named = api("POST", "/v1/campaigns", json_body=DRAFT_BODY | {"name": "  Kept  "})
    assert named.get_json()["name"] == "Kept"
    blank = api("POST", "/v1/campaigns", json_body=DRAFT_BODY | {"name": "   "})
    assert _code(blank) == (422, "invalid_body")
    assert blank.get_json()["details"] == [{"field": "name", "code": "too_short"}]


def test_end_before_start_is_422_on_create_and_edit(api: Any) -> None:
    backwards = DRAFT_BODY | {"starts_on": "2099-02-01", "ends_on": "2099-01-01"}
    assert _code(api("POST", "/v1/campaigns", json_body=backwards)) == (
        422,
        "ends_before_start",
    )
    # Only one date is sent: the rule judges the stored start against it.
    edited = api("PATCH", "/v1/campaigns/1", json_body={"ends_on": "2000-01-01"})
    assert _code(edited) == (422, "ends_before_start")
    assert api("GET", "/v1/campaigns/1").get_json()["ends_on"] != "2000-01-01"


def test_the_table_refusals_are_mapped_by_constraint_name(
    owner: psycopg.Connection,
) -> None:
    """The database's own refusals, as the owner meets them, in the rule's words."""
    from app import rules

    insert = (
        "INSERT INTO campaign (campaign_id, name, label_code, starts_on, ends_on,"
        " status) VALUES (900, 'x', %s, %s, %s, 'DRAFT')"
    )
    with pytest.raises(psycopg.errors.CheckViolation) as backwards:
        owner.execute(insert, ("LOYAL", "2099-02-01", "2099-01-01"))
    assert rules.translate(backwards.value).code == "ends_before_start"
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as unknown:
        owner.execute(insert, ("NOT_A_LABEL", "2099-01-01", "2099-02-01"))
    assert rules.translate(unknown.value).code == "unknown_label"


def test_an_unknown_label_is_422_on_create_and_edit(api: Any) -> None:
    unknown = DRAFT_BODY | {"label_code": "NOT_A_LABEL"}
    reply = api("POST", "/v1/campaigns", json_body=unknown)
    assert _code(reply) == (422, "unknown_label")
    assert reply.get_json()["details"] == [{"field": "label_code", "code": "unknown"}]
    assert _code(api("PATCH", "/v1/campaigns/1", json_body={"label_code": "NO"})) == (
        422,
        "unknown_label",
    )


def test_an_edit_changes_only_what_it_names_while_draft(api: Any) -> None:
    before = api("GET", "/v1/campaigns/1").get_json()
    reply = api("PATCH", "/v1/campaigns/1", json_body={"name": "Renamed"})
    assert reply.status_code == 200
    after = reply.get_json()
    assert after == before | {"name": "Renamed"}


def test_an_edit_outside_draft_is_409(api: Any) -> None:
    for campaign_id in (2, 3, 4):
        reply = api(
            "PATCH", f"/v1/campaigns/{campaign_id}", json_body={"name": "Renamed"}
        )
        assert _code(reply) == (409, "campaign_not_draft")


def test_the_contract_refuses_a_status_in_the_body(api: Any) -> None:
    reply = api("PATCH", "/v1/campaigns/1", json_body={"status": "ACTIVE"})
    assert _code(reply) == (422, "invalid_body")


@pytest.mark.parametrize(
    ("campaign_id", "source", "to", "outcome"),
    [
        (1, "DRAFT", "ACTIVE", "CAMPAIGN_ENDED"),  # the seeded draft has ended
        (1, "DRAFT", "CANCELLED", "CANCELLED"),
        (1, "DRAFT", "FINISHED", "illegal_transition"),
        (1, "DRAFT", "DRAFT", "illegal_transition"),
        (2, "ACTIVE", "FINISHED", "FINISHED"),
        (2, "ACTIVE", "CANCELLED", "CANCELLED"),
        (2, "ACTIVE", "ACTIVE", "illegal_transition"),
        (2, "ACTIVE", "DRAFT", "illegal_transition"),
        (3, "FINISHED", "ACTIVE", "illegal_transition"),
        (3, "FINISHED", "CANCELLED", "illegal_transition"),
        (3, "FINISHED", "FINISHED", "illegal_transition"),
        (3, "FINISHED", "DRAFT", "illegal_transition"),
        (4, "CANCELLED", "ACTIVE", "illegal_transition"),
        (4, "CANCELLED", "FINISHED", "illegal_transition"),
        (4, "CANCELLED", "CANCELLED", "illegal_transition"),
        (4, "CANCELLED", "DRAFT", "illegal_transition"),
    ],
)
def test_all_sixteen_moves(
    api: Any,
    owner: psycopg.Connection,
    campaign_id: int,
    source: str,
    to: str,
    outcome: str,
) -> None:
    # Seeded campaigns end in the past, so a legal move to ACTIVE is refused by
    # RN-49 here; the open-dated case is the next test.
    reply = _move(api, campaign_id, to, source)
    if outcome == "illegal_transition":
        assert _code(reply) == (409, "illegal_transition")
        assert _status(owner, campaign_id) == source
    elif outcome == "CAMPAIGN_ENDED":
        assert _code(reply) == (409, "campaign_ended")
        assert _status(owner, campaign_id) == source
    else:
        assert reply.status_code == 200
        assert reply.get_json()["status"] == outcome == _status(owner, campaign_id)


def test_a_draft_that_has_not_ended_activates(
    api: Any, owner: psycopg.Connection
) -> None:
    created = api("POST", "/v1/campaigns", json_body=DRAFT_BODY).get_json()
    reply = _move(api, created["campaign_id"], "ACTIVE", "DRAFT")
    assert reply.status_code == 200
    assert reply.get_json()["status"] == "ACTIVE"
    assert reply.get_json()["allowed_transitions"] == ["FINISHED", "CANCELLED"]


def test_status_changed_is_checked_before_the_move(
    api: Any, owner: psycopg.Connection
) -> None:
    # Campaign 2 is ACTIVE; the caller believes it is a DRAFT.
    assert _code(_move(api, 2, "ACTIVE", "DRAFT")) == (409, "status_changed")
    assert _code(_move(api, 2, "CANCELLED", "DRAFT")) == (409, "status_changed")
    assert _status(owner, 2) == "ACTIVE"


def test_a_draft_ended_yesterday_cannot_activate_one_ending_today_can(
    api: Any, owner: psycopg.Connection
) -> None:
    yesterday = _new_draft(owner, -10, -1)
    today = _new_draft(owner, -10, 0)
    assert _code(_move(api, yesterday, "ACTIVE", "DRAFT")) == (409, "campaign_ended")
    assert _status(owner, yesterday) == "DRAFT"
    assert _move(api, today, "ACTIVE", "DRAFT").status_code == 200
    assert _status(owner, today) == "ACTIVE"


def test_an_ended_campaign_can_still_be_cancelled(
    api: Any, owner: psycopg.Connection
) -> None:
    ended = _new_draft(owner, -10, -1)
    assert _move(api, ended, "CANCELLED", "DRAFT").status_code == 200


def test_an_experiment_that_is_not_ready_blocks_activation(
    api: Any, owner: psycopg.Connection
) -> None:
    campaign_id = _new_draft(owner, 0, 30)
    experiment_id = _experiment(owner, campaign_id, ("CONTROL",))

    reply = _move(api, campaign_id, "ACTIVE", "DRAFT")
    assert _code(reply) == (409, "experiment_not_ready")
    assert _status(owner, campaign_id) == "DRAFT"

    _group(owner, experiment_id, "TREATMENT")
    assert _move(api, campaign_id, "ACTIVE", "DRAFT").status_code == 200
    assert _status(owner, campaign_id) == "ACTIVE"


def test_no_experiment_and_two_treatment_arms_without_control_are_ready(
    api: Any, owner: psycopg.Connection
) -> None:
    bare = _new_draft(owner, 0, 30)
    paired = _new_draft(owner, 0, 30)
    _experiment(owner, paired, ("TREATMENT", "TREATMENT"))
    assert _move(api, bare, "ACTIVE", "DRAFT").status_code == 200
    assert _move(api, paired, "ACTIVE", "DRAFT").status_code == 200


@pytest.mark.parametrize(
    "kinds", [("TREATMENT",), ("TREATMENT", "TREATMENT", "TREATMENT"), ()]
)
def test_without_a_control_the_arms_must_be_exactly_two_treatments(
    api: Any, owner: psycopg.Connection, kinds: tuple[str, ...]
) -> None:
    campaign_id = _new_draft(owner, 0, 30)
    _experiment(owner, campaign_id, kinds)
    assert _code(_move(api, campaign_id, "ACTIVE", "DRAFT")) == (
        409,
        "experiment_not_ready",
    )


def test_one_unready_experiment_is_enough(api: Any, owner: psycopg.Connection) -> None:
    campaign_id = _new_draft(owner, 0, 30)
    _experiment(owner, campaign_id, ("CONTROL", "TREATMENT"))
    _experiment(owner, campaign_id, ("CONTROL",))
    assert _code(_move(api, campaign_id, "ACTIVE", "DRAFT")) == (
        409,
        "experiment_not_ready",
    )


def test_a_move_through_the_web_shows_on_the_next_read(
    api: Any, owner: psycopg.Connection
) -> None:
    assert api("GET", "/v1/campaigns/2").get_json()["status"] == "ACTIVE"
    owner.execute("SET ROLE retail_app")
    owner.execute("UPDATE campaign SET status = 'FINISHED' WHERE campaign_id = 2")
    owner.execute("RESET ROLE")
    assert api("GET", "/v1/campaigns/2").get_json()["status"] == "FINISHED"
    assert api("GET", "/v1/campaigns").get_json()["campaigns"][1]["status"] == (
        "FINISHED"
    )


def test_analyst_and_auditor_read_but_never_write(api: Any) -> None:
    for caller in ((ANALYST, "ANALYST"), (AUDITOR, "AUDITOR")):
        assert api("GET", "/v1/campaigns", as_user=caller).status_code == 200
        assert api("GET", "/v1/campaigns/1", as_user=caller).status_code == 200
        writes = [
            api("POST", "/v1/campaigns", as_user=caller, json_body=DRAFT_BODY),
            api("PATCH", "/v1/campaigns/1", as_user=caller, json_body={"name": "x"}),
            _move(api, 1, "CANCELLED", "DRAFT", as_user=caller),
        ]
        assert [_code(reply) for reply in writes] == [(403, "forbidden_role")] * 3


def test_store_manager_and_inventory_planner_are_refused_everything(
    api: Any,
) -> None:
    for role in ("STORE_MANAGER", "INVENTORY_PLANNER"):
        caller = (ADMIN, role)
        replies = [
            api("GET", "/v1/campaigns", as_user=caller),
            api("GET", "/v1/campaigns/1", as_user=caller),
            api("POST", "/v1/campaigns", as_user=caller, json_body=DRAFT_BODY),
            api("PATCH", "/v1/campaigns/1", as_user=caller, json_body={"name": "x"}),
            _move(api, 1, "CANCELLED", "DRAFT", as_user=caller),
        ]
        assert [_code(reply) for reply in replies] == [(403, "forbidden_role")] * 5


def test_marketing_writes(api: Any) -> None:
    created = api(
        "POST", "/v1/campaigns", as_user=MARKETING_CALLER, json_body=DRAFT_BODY
    )
    assert created.status_code == 201
    campaign_id = created.get_json()["campaign_id"]
    assert (
        api(
            "PATCH",
            f"/v1/campaigns/{campaign_id}",
            as_user=MARKETING_CALLER,
            json_body={"name": "Edited"},
        ).status_code
        == 200
    )
    assert (
        _move(api, campaign_id, "CANCELLED", "DRAFT", as_user=MARKETING_CALLER)
    ).status_code == 200
