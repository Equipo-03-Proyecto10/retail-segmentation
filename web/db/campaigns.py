"""Data access for campaign and its target-label vocabulary (F11-02).

Every statement is parameterized; the lifecycle rules live in
web/services/campaigns.py. Write functions leave transaction ownership to the
service (ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg import Connection

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


def list_campaigns(
    connection: Connection, *, page: int, per_page: int
) -> tuple[list[Campaign], int]:
    """Return one page of campaigns, newest first, and the total row count."""
    offset = (page - 1) * per_page
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT campaign_id, name, label_code, starts_on, ends_on, status "
            "FROM campaign ORDER BY campaign_id DESC LIMIT %s OFFSET %s",
            (per_page, offset),
        )
        rows = cursor.fetchall()
        cursor.execute("SELECT count(*) FROM campaign")
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
) -> int:
    """Insert a DRAFT campaign under the next free id and return that id.

    campaign_id has no identity (the issue's "no schema change"), so the id is
    allocated here, under an advisory lock held until the service commits.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_CREATE_LOCK_KEY,))
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
