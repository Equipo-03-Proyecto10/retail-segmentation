"""Data access for in-app notifications (AN-04, #419; ADR-0047).

A notification is an event (`notification`) and, for each user it was written
for, a read state (`notification_recipient`). Nothing here stores or builds a
sentence: a row comes back with the facts of its subject, a load, a run, an
experiment or a user, and `web.services.notifications` turns them into words.

Every read takes the `kinds` its reader may see. The delivery permission lives
in the permission matrix in code (ADR-0007), not in a column, so the service
works out from the reader's current role which kinds that is and the query
filters by them: a user whose role lost a permission stops reading what it
delivered, without a row being touched (ADR-0047). An empty list reads nothing.

Writes leave transaction ownership to services (ADR-0014).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg import Connection


@dataclass(frozen=True)
class NotificationRow:
    """One notification as one recipient reads it, with its subject's facts.

    Exactly one of the four subject groups is filled in, the one `kind` names;
    the others are None.
    """

    notification_id: int
    kind: str
    created_at: datetime
    read_at: datetime | None
    sales_load_id: int | None
    sales_load_filename: str | None
    sales_load_accepted: int | None
    sales_load_rejected: int | None
    run_id: int | None
    run_method: str | None
    run_customer_count: int | None
    experiment_id: int | None
    experiment_name: str | None
    experiment_ends_on: date | None
    subject_user_id: UUID | None
    subject_user_name: str | None


def count_notifications(
    connection: Connection[Any], *, user_id: UUID | str, kinds: Sequence[str]
) -> int:
    """How many notifications this user may read, read or not."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM notification_recipient AS r
            JOIN notification AS n ON n.notification_id = r.notification_id
            WHERE r.user_id = %s AND n.kind = ANY(%s::text[])
            """,
            (str(user_id), list(kinds)),
        )
        return int(cursor.fetchone()[0])


def count_unread(
    connection: Connection[Any], *, user_id: UUID | str, kinds: Sequence[str]
) -> int:
    """How many of them are unread: the figure on the navigation badge."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM notification_recipient AS r
            JOIN notification AS n ON n.notification_id = r.notification_id
            WHERE r.user_id = %s AND r.read_at IS NULL
              AND n.kind = ANY(%s::text[])
            """,
            (str(user_id), list(kinds)),
        )
        return int(cursor.fetchone()[0])


def list_notifications(
    connection: Connection[Any],
    *,
    user_id: UUID | str,
    kinds: Sequence[str],
    limit: int,
    offset: int,
) -> list[NotificationRow]:
    """One page of this user's notifications, newest first.

    Ordered by the identity, which follows creation order and is what
    `idx_notification_recipient_inbox` is built on. The subject tables are
    joined on the one foreign key the event sets; the other three joins find
    nothing and leave their columns NULL.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT n.notification_id, n.kind, n.created_at, r.read_at,
                   n.sales_load_id, sl.filename, sl.accepted_count,
                   sl.rejected_count,
                   n.run_id, sr.method, sr.customer_count,
                   n.experiment_id, ex.name, ex.ends_on,
                   n.subject_user_id, su.name
            FROM notification_recipient AS r
            JOIN notification AS n ON n.notification_id = r.notification_id
            LEFT JOIN sales_load AS sl ON sl.load_id = n.sales_load_id
            LEFT JOIN segmentation_run AS sr ON sr.run_id = n.run_id
            LEFT JOIN experiment AS ex ON ex.experiment_id = n.experiment_id
            LEFT JOIN app_user AS su ON su.user_id = n.subject_user_id
            WHERE r.user_id = %s AND n.kind = ANY(%s::text[])
            ORDER BY r.notification_id DESC
            LIMIT %s OFFSET %s
            """,
            (str(user_id), list(kinds), limit, offset),
        )
        return [NotificationRow(*row) for row in cursor.fetchall()]


def get_notification(
    connection: Connection[Any],
    *,
    user_id: UUID | str,
    notification_id: int,
    kinds: Sequence[str],
) -> NotificationRow | None:
    """One of this user's notifications, or None.

    None is the answer for an id that does not exist, for one that was written
    for somebody else and for one of a kind the reader's role no longer holds the
    permission for. The three are indistinguishable on purpose (RN-62): the
    caller answers 404 to all of them and so reveals nothing about the others.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT n.notification_id, n.kind, n.created_at, r.read_at,
                   n.sales_load_id, sl.filename, sl.accepted_count,
                   sl.rejected_count,
                   n.run_id, sr.method, sr.customer_count,
                   n.experiment_id, ex.name, ex.ends_on,
                   n.subject_user_id, su.name
            FROM notification_recipient AS r
            JOIN notification AS n ON n.notification_id = r.notification_id
            LEFT JOIN sales_load AS sl ON sl.load_id = n.sales_load_id
            LEFT JOIN segmentation_run AS sr ON sr.run_id = n.run_id
            LEFT JOIN experiment AS ex ON ex.experiment_id = n.experiment_id
            LEFT JOIN app_user AS su ON su.user_id = n.subject_user_id
            WHERE r.user_id = %s AND r.notification_id = %s
              AND n.kind = ANY(%s::text[])
            """,
            (str(user_id), notification_id, list(kinds)),
        )
        row = cursor.fetchone()
    return NotificationRow(*row) if row else None


def mark_read(
    connection: Connection[Any],
    *,
    user_id: UUID | str,
    notification_id: int,
    kinds: Sequence[str],
) -> bool:
    """Mark one of this user's notifications read; False when it is not theirs.

    The first reading time is kept: marking a notification that is already read
    changes nothing and still answers True, so a second click or a replayed
    request is harmless. A row that does not exist, belongs to another user or
    is of a kind the reader may no longer read matches nothing and answers False.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE notification_recipient AS r
               SET read_at = COALESCE(r.read_at, now())
              FROM notification AS n
             WHERE n.notification_id = r.notification_id
               AND r.user_id = %s AND r.notification_id = %s
               AND n.kind = ANY(%s::text[])
            """,
            (str(user_id), notification_id, list(kinds)),
        )
        return cursor.rowcount > 0


def mark_all_read(
    connection: Connection[Any], *, user_id: UUID | str, kinds: Sequence[str]
) -> int:
    """Mark every unread notification this user may read as read; how many.

    Notifications of a kind the reader's role no longer holds the permission for
    are left as they are: they are not shown, so they are not "all", and they
    are unread again if the permission returns.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE notification_recipient AS r
               SET read_at = now()
              FROM notification AS n
             WHERE n.notification_id = r.notification_id
               AND r.user_id = %s AND r.read_at IS NULL
               AND n.kind = ANY(%s::text[])
            """,
            (str(user_id), list(kinds)),
        )
        return cursor.rowcount
