"""The campaign rules, ported from web/services/campaigns.py (ADR-0045, E-17).

RN-31: a campaign moves only DRAFT to ACTIVE or CANCELLED and ACTIVE to
FINISHED or CANCELLED; FINISHED and CANCELLED are terminal. RN-32: it is edited
only while it is a DRAFT. RN-22: it never ends before it starts, by the service
and by the table's CHECK (a 23514 under `campaign_check` is mapped by name).
RN-49: a move to ACTIVE is refused once `ends_on` is before PostgreSQL's
CURRENT_DATE, read in the move's own transaction. RN-24: it is refused while an
attached experiment is not ready, by `trg_campaign_activation_guard`, whose
23514 under `experiment_not_ready` is mapped by name. The guard reads tables
this service may not, as `definer_guard` (E-13).

Every function takes the request's connection and raises `ApiError`. The
outcomes are pinned to the web's by tests/golden/campaigns.json; where this port
answers with a code where the web has a sentence, tests/test_golden.py says so.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from mosaiq_kit import ApiError
from psycopg import Connection
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

DRAFT = "DRAFT"
ACTIVE = "ACTIVE"
FINISHED = "FINISHED"
CANCELLED = "CANCELLED"

# Which moves are allowed (RN-31); the schema's CHECK only says which statuses
# exist. The tuples are the order the contract lists them in.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    DRAFT: (ACTIVE, CANCELLED),
    ACTIVE: (FINISHED, CANCELLED),
    FINISHED: (),
    CANCELLED: (),
}

# The key the web takes before it allocates a campaign_id (E-14, recorded in
# contracts/db/advisory-locks.md). Both writers use it, so neither draws an id
# the other holds.
CREATE_LOCK_KEY = 221001
CREATE_ATTEMPTS = 3

# Whole statements, never built from pieces: every value is bound.
LIST = (
    "SELECT campaign_id, name, label_code, starts_on, ends_on, status"
    " FROM public.campaign"
    " WHERE (%(status)s::text IS NULL OR status = %(status)s)"
    " AND (%(label_code)s::text IS NULL OR label_code = %(label_code)s)"
    " ORDER BY campaign_id LIMIT %(limit)s OFFSET %(offset)s"
)
COUNT = (
    "SELECT count(*) FROM public.campaign"
    " WHERE (%(status)s::text IS NULL OR status = %(status)s)"
    " AND (%(label_code)s::text IS NULL OR label_code = %(label_code)s)"
)
GET = (
    "SELECT campaign_id, name, label_code, starts_on, ends_on, status"
    " FROM public.campaign WHERE campaign_id = %s"
)
GET_FOR_UPDATE = (
    "SELECT campaign_id, name, label_code, starts_on, ends_on, status"
    " FROM public.campaign WHERE campaign_id = %s FOR UPDATE"
)
INSERT = (
    "INSERT INTO public.campaign (campaign_id, name, label_code, starts_on,"
    " ends_on, status)"
    " SELECT COALESCE(max(campaign_id), 0) + 1, %s, %s, %s, %s, 'DRAFT'"
    " FROM public.campaign"
    " RETURNING campaign_id, name, label_code, starts_on, ends_on, status"
)
UPDATE_DRAFT = (
    "UPDATE public.campaign SET name = %s, label_code = %s, starts_on = %s,"
    " ends_on = %s WHERE campaign_id = %s AND status = 'DRAFT'"
    " RETURNING campaign_id, name, label_code, starts_on, ends_on, status"
)
READ_STATUS = (
    "SELECT status, ends_on < CURRENT_DATE FROM public.campaign"
    " WHERE campaign_id = %s"
)
CHANGE_STATUS = (
    "UPDATE public.campaign SET status = %s WHERE campaign_id = %s AND status = %s"
    " RETURNING campaign_id, name, label_code, starts_on, ends_on, status"
)


def campaign_not_found() -> ApiError:
    return ApiError(
        404, "campaign_not_found", "No campaign has the requested campaign_id."
    )


def ends_before_start() -> ApiError:
    return ApiError(
        422,
        "ends_before_start",
        "ends_on cannot be before starts_on.",
        [{"field": "ends_on", "code": "before_start"}],
    )


def unknown_label() -> ApiError:
    return ApiError(
        422,
        "unknown_label",
        "label_code is absent from the segment label vocabulary.",
        [{"field": "label_code", "code": "unknown"}],
    )


def campaign_not_draft() -> ApiError:
    return ApiError(409, "campaign_not_draft", "Only a DRAFT campaign may be edited.")


def status_changed() -> ApiError:
    return ApiError(
        409,
        "status_changed",
        "The campaign status no longer matches expected_status; reload it before"
        " retrying.",
    )


def campaign_ended() -> ApiError:
    return ApiError(
        409,
        "campaign_ended",
        "A campaign whose ends_on date has passed cannot move to ACTIVE.",
    )


def experiment_not_ready() -> ApiError:
    return ApiError(
        409,
        "experiment_not_ready",
        "Every attached experiment must have a valid treatment design before"
        " activation.",
    )


def illegal_transition(status: str, target: str) -> ApiError:
    allowed = TRANSITIONS[status]
    if allowed:
        tail = "it may move only to " + " or ".join(allowed) + "."
    else:
        tail = "it is terminal and cannot move."
    return ApiError(
        409,
        "illegal_transition",
        f"A {status} campaign cannot move to {target}; {tail}",
    )


def translate(error: Exception) -> ApiError:
    """A database refusal in the rule's words, by constraint name, never 500."""
    constraint = getattr(getattr(error, "diag", None), "constraint_name", None)
    if isinstance(error, CheckViolation) and constraint == "campaign_check":
        return ends_before_start()
    if isinstance(error, CheckViolation) and constraint == "experiment_not_ready":
        return experiment_not_ready()
    if isinstance(error, ForeignKeyViolation) and (
        constraint == "campaign_label_code_fkey"
    ):
        return unknown_label()
    raise error


def clean_name(name: str) -> str:
    """The name as stored: trimmed, and refused when nothing is left.

    The schema's minLength counts the untrimmed value, so "   " passes it. The
    web refuses the same name ("Name is required").
    """
    cleaned = name.strip()
    if not cleaned:
        raise ApiError(
            422,
            "invalid_body",
            "The request body violates its schema.",
            [{"field": "name", "code": "too_short"}],
        )
    return cleaned


def representation(row: tuple[Any, ...]) -> dict[str, Any]:
    campaign_id, name, label_code, starts_on, ends_on, status = row
    return {
        "campaign_id": campaign_id,
        "name": name,
        "label_code": label_code,
        "starts_on": starts_on.isoformat(),
        "ends_on": ends_on.isoformat(),
        "status": status,
        "allowed_transitions": list(TRANSITIONS[status]),
    }


def list_campaigns(
    connection: Connection,
    *,
    status: str | None,
    label_code: str | None,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page in campaign_id order; a NULL filter switches its condition off."""
    filters = {"status": status, "label_code": label_code}
    with connection.cursor() as cursor:
        cursor.execute(LIST, filters | {"limit": size, "offset": (page - 1) * size})
        rows = cursor.fetchall()
        cursor.execute(COUNT, filters)
        total = cursor.fetchone()[0]
    return [representation(row) for row in rows], total


def get_campaign(
    connection: Connection, campaign_id: int, *, lock: bool = False
) -> tuple[Any, ...] | None:
    with connection.cursor() as cursor:
        cursor.execute(GET_FOR_UPDATE if lock else GET, (campaign_id,))
        return cursor.fetchone()


def require_campaign(connection: Connection, campaign_id: int) -> dict[str, Any]:
    row = get_campaign(connection, campaign_id)
    if row is None:
        raise campaign_not_found()
    return representation(row)


def parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(
            422,
            "invalid_body",
            "The request body violates its schema.",
            [{"field": field, "code": "invalid_format"}],
        ) from None


def create_campaign_once(
    connection: Connection,
    *,
    name: str,
    label_code: str,
    starts_on: date,
    ends_on: date,
) -> dict[str, Any]:
    """One attempt: the lock, max+1 and the INSERT, as one DRAFT row.

    The lock is transaction-scoped, so a refused or failed attempt releases it
    with its rollback. A UniqueViolation on `campaign_pkey` reaches the caller,
    which retries (E-14).
    """
    if ends_on < starts_on:
        raise ends_before_start()
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (CREATE_LOCK_KEY,))
        cursor.execute(INSERT, (name, label_code, starts_on, ends_on))
        return representation(cursor.fetchone())


def is_id_collision(error: UniqueViolation) -> bool:
    return getattr(getattr(error, "diag", None), "constraint_name", None) == (
        "campaign_pkey"
    )


def update_draft(
    connection: Connection, campaign_id: int, changes: dict[str, Any]
) -> dict[str, Any]:
    """Edit a DRAFT (RN-32). Fields not named keep their stored value."""
    current = get_campaign(connection, campaign_id, lock=True)
    if current is None:
        raise campaign_not_found()
    _, name, label_code, starts_on, ends_on, status = current
    if status != DRAFT:
        raise campaign_not_draft()
    name = clean_name(changes["name"]) if "name" in changes else name
    label_code = changes.get("label_code", label_code)
    if "starts_on" in changes:
        starts_on = parse_date(changes["starts_on"], "starts_on")
    if "ends_on" in changes:
        ends_on = parse_date(changes["ends_on"], "ends_on")
    if ends_on < starts_on:
        raise ends_before_start()
    with connection.cursor() as cursor:
        cursor.execute(
            UPDATE_DRAFT, (name, label_code, starts_on, ends_on, campaign_id)
        )
        row = cursor.fetchone()
    if row is None:
        raise campaign_not_draft()
    return representation(row)


def transition(
    connection: Connection, campaign_id: int, *, to: str, expected_status: str
) -> dict[str, Any]:
    """Move a campaign one step, conditional on the status that was read.

    The order of refusals is the contract's: status_changed, illegal_transition,
    campaign_ended, then the conditional UPDATE, whose zero rows is
    status_changed again and whose guard refusal is experiment_not_ready.
    """
    with connection.cursor() as cursor:
        cursor.execute(READ_STATUS, (campaign_id,))
        found = cursor.fetchone()
        if found is None:
            raise campaign_not_found()
        status, ended = found
        if status != expected_status:
            raise status_changed()
        if to not in TRANSITIONS[status]:
            raise illegal_transition(status, to)
        if to == ACTIVE and ended:
            raise campaign_ended()
        cursor.execute(CHANGE_STATUS, (to, campaign_id, status))
        row = cursor.fetchone()
    if row is None:
        raise status_changed()
    return representation(row)
