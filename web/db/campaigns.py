"""Data access for campaign and its target-label vocabulary (F11-02).

Every statement is parameterized; the lifecycle rules live in
web/services/campaigns.py. Write functions leave transaction ownership to the
service (ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg import Connection

from web.db.search import ilike_pattern

# Takes a transaction-scoped advisory lock, so two concurrent creates cannot
# both read the same max(campaign_id). Advisory rather than LOCK TABLE: it
# serializes creators without blocking status updates on existing campaigns.
# The value is an arbitrary application-wide constant for this one purpose.
_CREATE_LOCK_KEY = 221_001


@dataclass(frozen=True)
class Campaign:
    campaign_id: int
    name: str
    label_code: str
    starts_on: date
    ends_on: date
    status: str


@dataclass(frozen=True)
class SegmentLabel:
    label_code: str
    name: str


# Each filter is optional: a NULL parameter switches its condition off, so one
# statement serves every combination (#538).
_CAMPAIGN_FILTER = """
     WHERE (%(search)s::text IS NULL OR name ILIKE %(search)s)
       AND (%(status)s::text IS NULL OR status = %(status)s)
"""

_LIST_CAMPAIGNS = (
    "SELECT campaign_id, name, label_code, starts_on, ends_on, status "
    "FROM campaign"
    + _CAMPAIGN_FILTER
    + "ORDER BY campaign_id DESC LIMIT %(limit)s OFFSET %(offset)s"
)

_COUNT_CAMPAIGNS = "SELECT count(*) FROM campaign" + _CAMPAIGN_FILTER


def list_campaigns(
    connection: Connection,
    *,
    page: int,
    per_page: int,
    search: str | None = None,
    status: str | None = None,
) -> tuple[list[Campaign], int]:
    """Return one page of campaigns, newest first, and the total row count.

    `search` matches the campaign name, literally and case-insensitively;
    `status` keeps one lifecycle status. Either may be None.
    """
    parameters = {
        "search": ilike_pattern(search) if search else None,
        "status": status,
    }
    offset = (page - 1) * per_page
    with connection.cursor() as cursor:
        cursor.execute(
            _LIST_CAMPAIGNS, parameters | {"limit": per_page, "offset": offset}
        )
        rows = cursor.fetchall()
        cursor.execute(_COUNT_CAMPAIGNS, parameters)
        total = cursor.fetchone()[0]
    return [Campaign(*row) for row in rows], total


def get_campaign(connection: Connection, campaign_id: int) -> Campaign | None:
    """Return one campaign, or None when the id names nothing."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT campaign_id, name, label_code, starts_on, ends_on, status "
            "FROM campaign WHERE campaign_id = %s",
            (campaign_id,),
        )
        row = cursor.fetchone()
    return None if row is None else Campaign(*row)


def list_labels(connection: Connection) -> list[SegmentLabel]:
    """Return the label vocabulary, best segment first (ADR-0018 ordering)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT label_code, name FROM segment_label ORDER BY ordinal_position"
        )
        rows = cursor.fetchall()
    return [SegmentLabel(*row) for row in rows]


def create_campaign(
    connection: Connection,
    *,
    name: str,
    label_code: str,
    starts_on: date,
    ends_on: date,
) -> int | None:
    """Insert a DRAFT campaign under the next free id and return that id, or
    None when a draft with the same name, label and dates already exists
    (#296: a double-click or a resubmitted form, not a second campaign).

    campaign_id has no identity (the issue's "no schema change"), so the id is
    allocated here, under an advisory lock held until the service commits;
    the same lock makes the duplicate check and the insert see one
    consistent state instead of two submissions both passing it.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_CREATE_LOCK_KEY,))
        cursor.execute(
            """
            SELECT 1 FROM campaign
             WHERE name = %s AND label_code = %s
               AND starts_on = %s AND ends_on = %s AND status = 'DRAFT'
            """,
            (name, label_code, starts_on, ends_on),
        )
        if cursor.fetchone() is not None:
            return None
        cursor.execute(
            """
            INSERT INTO campaign (campaign_id, name, label_code, starts_on,
                                  ends_on, status)
            SELECT COALESCE(max(campaign_id), 0) + 1, %s, %s, %s, %s, 'DRAFT'
            FROM campaign
            RETURNING campaign_id
            """,
            (name, label_code, starts_on, ends_on),
        )
        return cursor.fetchone()[0]


def update_draft(
    connection: Connection,
    campaign_id: int,
    *,
    name: str,
    label_code: str,
    starts_on: date,
    ends_on: date,
) -> bool:
    """Rewrite a campaign's fields while it is still a draft.

    Returns False when no draft matched, so the service can tell a campaign that
    moved on from one that never existed without a second, racy read.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE campaign
            SET name = %s, label_code = %s, starts_on = %s, ends_on = %s
            WHERE campaign_id = %s AND status = 'DRAFT'
            """,
            (name, label_code, starts_on, ends_on, campaign_id),
        )
        return cursor.rowcount == 1


def change_status(
    connection: Connection, campaign_id: int, *, current: str, new: str
) -> bool:
    """Move a campaign from `current` to `new`, only if it is still `current`.

    The status in the WHERE clause is the guard: two people transitioning the
    same campaign at once cannot both succeed, and the audit trigger records
    exactly one before/after pair per real change.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE campaign SET status = %s " "WHERE campaign_id = %s AND status = %s",
            (new, campaign_id, current),
        )
        return cursor.rowcount == 1
