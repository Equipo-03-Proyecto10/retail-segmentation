"""Campaign transitions, through web/services/campaigns.py.

Seeded campaigns 1, 2, 3 and 4 are DRAFT, ACTIVE, FINISHED and CANCELLED, and
every seeded campaign ends in early 2026, so on the fixed business date
(cases.CAMPAIGN_TODAY) a seeded draft has ended. Two drafts are added inside
their own rolled-back case, dated in 2099: one with no experiment, and one
whose experiment has a control group and no treatment. Campaign and experiment
ids are allocated as the next free id, not from a sequence, so an added draft
is campaign 31 on every capture.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import psycopg

from tests.golden.cases import Case, Context, accepted, invalid, one, refused
from web.services.campaigns import (
    ACTIONS,
    TRANSITIONS,
    CampaignInput,
    CampaignRefused,
    InvalidTransition,
    create_campaign,
    transition,
    update_draft,
    validate_campaign,
)

SEEDED = {"DRAFT": 1, "ACTIVE": 2, "FINISHED": 3, "CANCELLED": 4}
REFUSALS = (InvalidTransition, CampaignRefused)
DRAFT_2099 = CampaignInput(
    "Golden draft", "LOYAL", date(2099, 1, 1), date(2099, 12, 31)
)


def _status(connection: psycopg.Connection, campaign_id: int) -> str:
    return one(
        connection, "SELECT status FROM campaign WHERE campaign_id = %s", (campaign_id,)
    )


def _attempt(
    call: Callable[[psycopg.Connection], Any],
    observe: Callable[[psycopg.Connection], dict[str, Any]],
) -> Callable[[psycopg.Connection], dict[str, Any]]:
    def run(connection: psycopg.Connection) -> dict[str, Any]:
        try:
            call(connection)
        except REFUSALS as error:
            return refused(error)
        return accepted(**observe(connection))

    return run


def _transition_note(source: str, action: str, context: Context) -> str | None:
    """Where campaigns-service answers a refused move with a code, not a message."""
    target = ACTIONS[action]
    if target in TRANSITIONS[source]:
        if target == "ACTIVE":
            return (
                "campaigns-service answers 409 campaign_ended where the web raises"
                " InvalidTransition with a sentence: the draft ended before the"
                " business date."
            )
        return None
    return (
        "campaigns-service answers 409 illegal_transition where the web raises"
        " InvalidTransition with a sentence. It takes the target status `to` and"
        " an `expected_status`, not the web's action name."
    )


def _transition_case(source: str, action: str, context: Context) -> Case:
    campaign_id = SEEDED[source]
    return Case(
        f"transition-{source.lower()}-{action}",
        {
            "call": "transition",
            "campaign_id": campaign_id,
            "from": source,
            "action": action,
            "to": ACTIONS[action],
            "today": context.today.isoformat(),
        },
        _attempt(
            lambda c: transition(c, campaign_id, action, today=context.today),
            lambda c: {"status": _status(c, campaign_id)},
        ),
        _transition_note(source, action, context),
    )


def _draft_input(data: CampaignInput) -> dict[str, Any]:
    return {
        "name": data.name,
        "label_code": data.label_code,
        "starts_on": data.starts_on.isoformat(),
        "ends_on": data.ends_on.isoformat(),
    }


def _activate_new_draft(
    with_control_only_experiment: bool, context: Context
) -> Callable:
    def run(connection: psycopg.Connection) -> dict[str, Any]:
        campaign_id = create_campaign(connection, DRAFT_2099)
        if with_control_only_experiment:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO experiment (experiment_id, name, campaign_id,"
                    " target_metric, starts_on, ends_on, conversion_window_days,"
                    " data_origin)"
                    " SELECT coalesce(max(experiment_id), 0) + 1, %s, %s, 'CONVERSION',"
                    " %s, %s, 7, 'OBSERVED' FROM experiment"
                    " RETURNING experiment_id",
                    (
                        "Golden control only",
                        campaign_id,
                        DRAFT_2099.starts_on,
                        DRAFT_2099.ends_on,
                    ),
                )
                experiment_id = cursor.fetchone()[0]
                cursor.execute(
                    "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
                    " treatment_description)"
                    " SELECT coalesce(max(group_id), 0) + 1, %s, 'CONTROL', %s, %s"
                    " FROM experiment_group",
                    (experiment_id, "Golden holdout", "No treatment delivered."),
                )
        try:
            transition(connection, campaign_id, "activate", today=context.today)
        except InvalidTransition as error:
            return refused(error)
        return accepted(status=_status(connection, campaign_id))

    return run


def build(connection: psycopg.Connection, context: Context) -> list[Case]:
    cases = [
        Case(
            "transition-table",
            {"read": "TRANSITIONS"},
            lambda c: {
                "outcome": "table",
                "transitions": {
                    source: sorted(targets)
                    for source, targets in sorted(TRANSITIONS.items())
                },
            },
            None,
        )
    ]
    cases += [
        _transition_case(source, action, context)
        for source in SEEDED
        for action in sorted(ACTIONS)
    ]
    edit = CampaignInput("Campaign edited", "LOYAL", date(2026, 1, 2), date(2026, 2, 1))
    backwards = CampaignInput(
        "Campaign backwards", "LOYAL", date(2099, 2, 1), date(2099, 1, 1)
    )
    cases += [
        Case(
            "edit-draft",
            {"call": "update_draft", "campaign_id": 1, **_draft_input(edit)},
            _attempt(
                lambda c: update_draft(c, 1, edit),
                lambda c: {
                    "name": one(
                        c, "SELECT name FROM campaign WHERE campaign_id = %s", (1,)
                    )
                },
            ),
        ),
        Case(
            "edit-active-campaign",
            {"call": "update_draft", "campaign_id": 2, **_draft_input(edit)},
            _attempt(lambda c: update_draft(c, 2, edit), lambda c: {}),
            "campaigns-service answers 409 campaign_not_draft where the web raises"
            " InvalidTransition with a sentence.",
        ),
        Case(
            "validate-end-before-start",
            {
                "call": "validate_campaign",
                "name": "Campaign backwards",
                "label_code": "LOYAL",
                "starts_on": "2099-02-01",
                "ends_on": "2099-01-01",
            },
            lambda c: invalid(
                validate_campaign(
                    name="Campaign backwards",
                    label_code="LOYAL",
                    starts_on="2099-02-01",
                    ends_on="2099-01-01",
                )[1]
            ),
            "campaigns-service answers 422 ends_before_start where the web's form"
            " validation returns a message for the ends_on field.",
        ),
        Case(
            "edit-draft-end-before-start",
            {"call": "update_draft", "campaign_id": 1, **_draft_input(backwards)},
            _attempt(lambda c: update_draft(c, 1, backwards), lambda c: {}),
            "campaigns-service answers 422 ends_before_start where the web raises"
            " CampaignRefused with a message for the ends_on field.",
        ),
        Case(
            "activate-draft-with-no-experiment",
            {
                "call": "transition",
                "action": "activate",
                "setup": "a new draft with no experiment",
                **_draft_input(DRAFT_2099),
                "today": context.today.isoformat(),
            },
            _activate_new_draft(False, context),
        ),
        Case(
            "activate-draft-with-control-only-experiment",
            {
                "call": "transition",
                "action": "activate",
                "setup": "a new draft whose experiment has a control and no treatment",
                **_draft_input(DRAFT_2099),
                "today": context.today.isoformat(),
            },
            _activate_new_draft(True, context),
            "campaigns-service answers 409 experiment_not_ready, refused by"
            " trg_campaign_activation_guard (E-13), where the web raises"
            " InvalidTransition with a sentence from activation_refusal.",
        ),
    ]
    return cases


def as_of(context: Context) -> dict[str, Any]:
    return {"today": context.today.isoformat()}
