"""The campaign lifecycle (F11-02): state is explicit, never implied by dates.

A campaign is created as a DRAFT, may be edited while it is one, and then moves
along a fixed set of transitions. FINISHED and CANCELLED are terminal. Each
transition is one audited UPDATE (RN-28), so "the transition is recorded" is the
audit trigger's job, not a second table this module would have to keep in step.

The service owns the transaction and the rules; SQL stays in web/db/campaigns.py
(ADR-0003, ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg import Connection
from psycopg.errors import CheckViolation, ForeignKeyViolation, IntegrityError

from web.db import campaigns
from web.db.transactions import atomic

DRAFT = "DRAFT"
ACTIVE = "ACTIVE"
FINISHED = "FINISHED"
CANCELLED = "CANCELLED"

# Deliberately more than the schema's CHECK: the CHECK says which statuses
# exist, this says which moves between them are allowed (RN-31).
TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({ACTIVE, CANCELLED}),
    ACTIVE: frozenset({FINISHED, CANCELLED}),
    FINISHED: frozenset(),
    CANCELLED: frozenset(),
}

# What a form button says -> the status it asks for.
ACTIONS: dict[str, str] = {
    "activate": ACTIVE,
    "complete": FINISHED,
    "cancel": CANCELLED,
}

_NAME_MAX = 120
_LABEL_MAX = 40


class CampaignNotFound(Exception):
    """The id names no campaign."""


class InvalidTransition(Exception):
    """The requested move is not allowed from the campaign's current status."""


class CampaignRefused(Exception):
    """A write refused with a message for one form field ('' = the whole form)."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class CampaignInput:
    name: str
    label_code: str
    starts_on: date
    ends_on: date


def not_a_draft(campaign_id: int, status: str) -> str:
    """The refusal shown when an edit is attempted on a campaign that moved on."""
    return f"Campaign {campaign_id} is {status.lower()}; only a draft can be edited."


def _parse_date(raw: str, label: str) -> tuple[date | None, str | None]:
    if not raw:
        return None, f"{label} is required."
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return None, f"{label} must be a date (YYYY-MM-DD)."


def validate_campaign(
    *, name: str, label_code: str, starts_on: str, ends_on: str
) -> tuple[CampaignInput | None, dict[str, str]]:
    """Validate the draft form. Whether the label exists is the FK's concern."""
    errors: dict[str, str] = {}

    if not name or not name.strip():
        errors["name"] = "Name is required."
    elif len(name) > _NAME_MAX:
        errors["name"] = f"Name must be {_NAME_MAX} characters or fewer."

    if not label_code:
        errors["label_code"] = "Choose the segment label this campaign targets."
    elif len(label_code) > _LABEL_MAX:
        errors["label_code"] = "That label is not in the vocabulary."

    start, start_error = _parse_date(starts_on, "Start date")
    end, end_error = _parse_date(ends_on, "End date")
    if start_error:
        errors["starts_on"] = start_error
    if end_error:
        errors["ends_on"] = end_error
    if start and end and end < start:
        errors["ends_on"] = "End date cannot be before the start date."

    if errors:
        return None, errors
    return CampaignInput(name.strip(), label_code, start, end), {}


def _refusal(error: IntegrityError) -> CampaignRefused:
    """Turn a database refusal into a message the form can show."""
    if isinstance(error, ForeignKeyViolation):
        return CampaignRefused("label_code", "That label is not in the vocabulary.")
    if isinstance(error, CheckViolation):
        return CampaignRefused("ends_on", "End date cannot be before the start date.")
    return CampaignRefused("", "That campaign was refused by a database constraint.")


@atomic
def _create(connection: Connection, data: CampaignInput) -> int:
    return campaigns.create_campaign(
        connection,
        name=data.name,
        label_code=data.label_code,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
    )


def create_campaign(connection: Connection, data: CampaignInput) -> int:
    """Create a DRAFT campaign and return its id."""
    try:
        return _create(connection, data)
    except IntegrityError as error:
        raise _refusal(error) from error


@atomic
def _update(connection: Connection, campaign_id: int, data: CampaignInput) -> None:
    current = campaigns.get_campaign(connection, campaign_id)
    if current is None:
        raise CampaignNotFound(campaign_id)
    if current.status != DRAFT:
        raise InvalidTransition(not_a_draft(campaign_id, current.status))
    edited = campaigns.update_draft(
        connection,
        campaign_id,
        name=data.name,
        label_code=data.label_code,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
    )
    if not edited:
        raise InvalidTransition(
            f"Campaign {campaign_id} left draft while you were editing it; "
            "reload to see its current state."
        )


def update_draft(connection: Connection, campaign_id: int, data: CampaignInput) -> None:
    """Edit a campaign, but only while it is a draft (its target included)."""
    try:
        _update(connection, campaign_id, data)
    except IntegrityError as error:
        raise _refusal(error) from error


def _refuse_move(campaign_id: int, status: str, target: str) -> InvalidTransition:
    allowed = sorted(TRANSITIONS[status])
    if not allowed:
        return InvalidTransition(
            f"Campaign {campaign_id} is {status.lower()}; no further transition "
            "is permitted."
        )
    options = " or ".join(name.lower() for name in allowed)
    return InvalidTransition(
        f"Campaign {campaign_id} is {status.lower()} and cannot become "
        f"{target.lower()}; from {status.lower()} it can only become {options}."
    )


@atomic
def transition(connection: Connection, campaign_id: int, action: str) -> str:
    """Apply one lifecycle action and return the campaign's new status.

    Refuses with InvalidTransition rather than ignoring an illegal move, and
    guards against a concurrent change by making the UPDATE conditional on the
    status this call read.
    """
    target = ACTIONS[action]
    current = campaigns.get_campaign(connection, campaign_id)
    if current is None:
        raise CampaignNotFound(campaign_id)
    if target not in TRANSITIONS[current.status]:
        raise _refuse_move(campaign_id, current.status, target)
    if not campaigns.change_status(
        connection, campaign_id, current=current.status, new=target
    ):
        raise InvalidTransition(
            f"Campaign {campaign_id} changed while you were working; reload "
            "to see its current state."
        )
    return target
