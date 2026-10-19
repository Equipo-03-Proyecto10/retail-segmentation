"""In-app notifications: what a user may read, and the words it is told in
(AN-04, #419; ADR-0047).

An event is stored once, as a subject and a kind, with no text. This module
writes the events a request causes, decides which kinds a user may read, turns a
stored event into a sentence and a link when it is read, and counts what is
unread.

**Time-based.** An experiment's end is caused by no request, so it is announced
lazily, by `announce_experiment_ends`, which the notifications blueprint runs on
signed-in `GET` requests (ADR-0047, RN-64).

**Writing.** `notify_sales_load`, `notify_segment_run` and `notify_user_deactivated`
are called by the service that causes the event, from inside its own transaction
(ADR-0014): they join it, never commit on their own, and a failure propagates, so
the operation is rolled back with its notification (RN-61). The recipients are the
active users whose role holds the kind's delivery permission, the user who caused
the event among them, as they are at that moment.

**Who may read what.** Each kind has a delivery permission, the one that made
its holders recipients in the first place. It is checked again every time a
notification is read, against the reader's current role, so a role that lost the
permission stops seeing the notification in the inbox and the count, and a
request for it by id is a 404. A notification the reader was never written for
is the same 404 (RN-62).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

from web.db.notifications import (
    NotificationRow,
    announce_ended_experiments,
    count_notifications,
    count_unread,
    get_notification,
    insert_notification,
    insert_recipients,
    list_notifications,
    mark_all_read,
    mark_read,
)
from web.db.transactions import atomic
from web.middleware.authz import (
    CAMPAIGN_READ,
    SALES_INGEST_EXECUTE,
    SEGMENT_READ,
    USER_WRITE,
    permissions_for,
    roles_holding,
)
from web.services.pagination import page_count

SALES_LOAD_FINISHED = "SALES_LOAD_FINISHED"
SEGMENT_RUN_COMPLETED = "SEGMENT_RUN_COMPLETED"
EXPERIMENT_ENDED = "EXPERIMENT_ENDED"
USER_DEACTIVATED = "USER_DEACTIVATED"

# The permission whose holders are told of each kind (ADR-0047). The same map
# decides the recipients when an event is written and what a reader may see when
# it is read.
DELIVERY_PERMISSION: dict[str, str] = {
    SALES_LOAD_FINISHED: SALES_INGEST_EXECUTE,
    SEGMENT_RUN_COMPLETED: SEGMENT_READ,
    EXPERIMENT_ENDED: CAMPAIGN_READ,
    USER_DEACTIVATED: USER_WRITE,
}

_LABELS = {
    SALES_LOAD_FINISHED: "Sales load finished",
    SEGMENT_RUN_COMPLETED: "Segmentation run completed",
    EXPERIMENT_ENDED: "Experiment ended",
    USER_DEACTIVATED: "User deactivated",
}

# RN-63: the inbox is paginated at twenty, and the badge stops counting at 99.
PAGE_SIZE = 20
BADGE_CAP = 99


def visible_kinds(role_code: str | None) -> list[str]:
    """The kinds a role may read: those whose delivery permission it holds now."""
    granted = permissions_for(role_code)
    return sorted(
        kind
        for kind, permission in DELIVERY_PERMISSION.items()
        if permission in granted
    )


def badge_text(unread: int) -> str | None:
    """What the navigation badge says: nothing for none, `99+` past the cap."""
    if unread <= 0:
        return None
    return f"{BADGE_CAP}+" if unread > BADGE_CAP else str(unread)


@dataclass(frozen=True)
class InboxItem:
    """One notification, in words, ready to render.

    `link_endpoint` and `link_arguments` name the page the notification is
    about, for the template to turn into a URL; this module knows no routes'
    paths, only their endpoint names.
    """

    notification_id: int
    kind: str
    label: str
    message: str
    created_at: datetime
    read_at: datetime | None
    link_endpoint: str
    link_arguments: dict[str, Any]
    link_text: str
    details: tuple[tuple[str, str], ...] = ()

    @property
    def is_unread(self) -> bool:
        return self.read_at is None


@dataclass(frozen=True)
class Inbox:
    """One page of a user's notifications, and enough to render the pager.

    `receives_any` is False for a role whose permissions deliver nothing, so an
    empty inbox can say that, instead of promising that something will come.
    """

    items: tuple[InboxItem, ...]
    total: int
    unread: int
    page: int
    page_count: int
    receives_any: bool

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def describe(row: NotificationRow) -> InboxItem:
    """The sentence and the link for a stored event, built from its subject.

    Nothing is cached: a corrected load count reads correctly from the next
    page view (ADR-0047).
    """
    if row.kind == SALES_LOAD_FINISHED:
        message = (
            f"The sales load “{row.sales_load_filename}” finished: "
            f"{_plural(row.sales_load_accepted or 0, 'row')} accepted, "
            f"{row.sales_load_rejected or 0} rejected."
        )
        endpoint, arguments, text = (
            "sales_import.detail",
            {"load_id": row.sales_load_id},
            "Open the import report",
        )
        details = (
            ("File", str(row.sales_load_filename)),
            ("Rows accepted", str(row.sales_load_accepted)),
            ("Rows rejected", str(row.sales_load_rejected)),
        )
    elif row.kind == SEGMENT_RUN_COMPLETED:
        message = (
            f"Segmentation run #{row.run_id} ({row.run_method}) completed for "
            f"{_plural(row.run_customer_count or 0, 'customer')}."
        )
        endpoint, arguments, text = (
            "run_history.detail",
            {"run_id": row.run_id},
            "Open the run",
        )
        details = (
            ("Run", f"#{row.run_id}"),
            ("Method", str(row.run_method)),
            ("Customers", str(row.run_customer_count)),
        )
    elif row.kind == EXPERIMENT_ENDED:
        ended = row.experiment_ends_on.isoformat() if row.experiment_ends_on else "?"
        message = (
            f"The experiment “{row.experiment_name}” reached its end date, {ended}."
        )
        endpoint, arguments, text = (
            "experiments.uplift",
            {"experiment_id": row.experiment_id},
            "Open the results",
        )
        details = (("Experiment", str(row.experiment_name)), ("Ended on", ended))
    elif row.kind == USER_DEACTIVATED:
        message = f"{row.subject_user_name} was deactivated."
        endpoint, arguments, text = (
            "admin.edit_user_view",
            {"user_id": row.subject_user_id},
            "Open the account",
        )
        details = (("User", str(row.subject_user_name)),)
    else:
        raise ValueError(f"Unknown notification kind {row.kind!r}.")

    return InboxItem(
        notification_id=row.notification_id,
        kind=row.kind,
        label=_LABELS[row.kind],
        message=message,
        created_at=row.created_at,
        read_at=row.read_at,
        link_endpoint=endpoint,
        link_arguments=arguments,
        link_text=text,
        details=details,
    )


def unread_count(
    connection: Connection[Any], *, user_id: UUID | str, role_code: str | None
) -> int:
    """How many notifications the user has not read, among those they may read."""
    return count_unread(connection, user_id=user_id, kinds=visible_kinds(role_code))


def read_inbox(
    connection: Connection[Any],
    *,
    user_id: UUID | str,
    role_code: str | None,
    page: int,
) -> Inbox:
    """One page of the inbox, newest first.

    A page past the last holds no rows; the route sends the reader to the last
    one, as every other listing does.
    """
    kinds = visible_kinds(role_code)
    page = max(page, 1)
    total = count_notifications(connection, user_id=user_id, kinds=kinds)
    rows = list_notifications(
        connection,
        user_id=user_id,
        kinds=kinds,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    return Inbox(
        items=tuple(describe(row) for row in rows),
        total=total,
        unread=count_unread(connection, user_id=user_id, kinds=kinds),
        page=page,
        page_count=page_count(total, PAGE_SIZE),
        receives_any=bool(kinds),
    )


def read_notification(
    connection: Connection[Any],
    *,
    user_id: UUID | str,
    role_code: str | None,
    notification_id: int,
) -> InboxItem | None:
    """One notification in words, or None.

    None is the single answer for a notification that does not exist, that was
    written for another user, or that the reader's role may no longer read
    (RN-62): the route answers 404 to all three, never 403, so asking by id
    reveals nothing about which of them it was.
    """
    row = get_notification(
        connection,
        user_id=user_id,
        notification_id=notification_id,
        kinds=visible_kinds(role_code),
    )
    return describe(row) if row else None


@atomic
def mark_notification_read(
    connection: Connection[Any],
    *,
    user_id: UUID | str,
    role_code: str | None,
    notification_id: int,
) -> bool:
    """Mark one notification read; False when it is not the user's to read.

    Marking one that is already read changes nothing and answers True.
    """
    return mark_read(
        connection,
        user_id=user_id,
        notification_id=notification_id,
        kinds=visible_kinds(role_code),
    )


@atomic
def mark_all_notifications_read(
    connection: Connection[Any], *, user_id: UUID | str, role_code: str | None
) -> int:
    """Mark everything the user may read as read; how many were unread."""
    return mark_all_read(connection, user_id=user_id, kinds=visible_kinds(role_code))


# ---------- writing the events a request causes ----------


def _write(
    connection: Connection[Any],
    kind: str,
    *,
    sales_load_id: int | None = None,
    run_id: int | None = None,
    subject_user_id: UUID | str | None = None,
) -> int:
    """Write one event and its recipients; how many users it was written for.

    Joins the caller's transaction. A failure here is not caught: the caller's
    operation fails with it and is rolled back (ADR-0014, RN-61).
    """
    notification_id = insert_notification(
        connection,
        sales_load_id=sales_load_id,
        run_id=run_id,
        subject_user_id=subject_user_id,
    )
    recipients = insert_recipients(
        connection,
        notification_id=notification_id,
        role_codes=roles_holding(DELIVERY_PERMISSION[kind]),
    )
    logging.getLogger(__name__).info(
        "notification_written kind=%s notification=%s recipients=%s",
        kind,
        notification_id,
        recipients,
    )
    return recipients


@atomic
def notify_sales_load(connection: Connection[Any], load_id: int) -> int:
    """Announce a recorded sales load to the holders of `sales_ingest.execute`."""
    return _write(connection, SALES_LOAD_FINISHED, sales_load_id=load_id)


@atomic
def notify_segment_run(connection: Connection[Any], run_id: int) -> int:
    """Announce a recorded segmentation run to the holders of `segment.read`."""
    return _write(connection, SEGMENT_RUN_COMPLETED, run_id=run_id)


@atomic
def notify_user_deactivated(connection: Connection[Any], user_id: UUID | str) -> int:
    """Announce a deactivation made in the web to the holders of `user.write`.

    Only the web's own service layer calls this (Q-14): a deactivation through
    users-service, and the command line's bulk deactivation of demonstration
    accounts, create no notification.
    """
    return _write(connection, USER_DEACTIVATED, subject_user_id=user_id)


# RN-64: only an experiment that ended within this many days is announced.
EXPERIMENT_END_LOOKBACK_DAYS = 14


@atomic
def announce_experiment_ends(connection: Connection[Any]) -> int:
    """Announce every experiment that has ended and has not been announced.

    Returns how many experiments were announced by this call, usually none. It
    is called on signed-in `GET` requests only, by the blueprint's hook, and is
    safe to call from every request and every worker (see
    `web.db.notifications.announce_ended_experiments`). The recipients are the
    active holders of `campaign.read`, as they are now.
    """
    events, recipients = announce_ended_experiments(
        connection,
        role_codes=roles_holding(DELIVERY_PERMISSION[EXPERIMENT_ENDED]),
        lookback_days=EXPERIMENT_END_LOOKBACK_DAYS,
    )
    if events:
        logging.getLogger(__name__).info(
            "notification_written kind=%s experiments=%s recipients=%s",
            EXPERIMENT_ENDED,
            events,
            recipients,
        )
    return events
