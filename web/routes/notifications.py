"""The notification inbox and the navigation badge (AN-04, #419; ADR-0047).

Every route here is declared `@requires()`: any signed-in user may open their
own inbox. What a notification says is another matter and is gated on its own
delivery permission when it is read (`web.services.notifications`), so no new
permission exists and none is needed.

A notification is private (RN-62). One that does not exist, that was written for
somebody else, or that the reader's role may no longer read is a 404 on every
route that takes an id, never a 403: a 403 would confirm that the id exists.
Reading a notification does not mark it read; that is an explicit `POST`, so a
prefetch, a `HEAD` or a link preview cannot change what the user has seen.

The unread count shown in the navigation of every page is a context processor
of this blueprint. It fails soft: a counter that cannot be read hides the badge
and logs the failure, and the page it sits on is still served.
"""

from __future__ import annotations

from typing import Any

import psycopg
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from web.db import get_connection
from web.middleware.authz import current_role_code, is_signed_in, requires, safe_next
from web.parsing import BIGINT_MAX, page_number
from web.routes.pagination import redirect_last_page
from web.services.notifications import (
    badge_text,
    mark_all_notifications_read,
    mark_notification_read,
    read_inbox,
    read_notification,
    unread_count,
)

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


def _unread_for_this_page() -> int | None:
    """The signed-in user's unread count, or None when there is none to show.

    None covers a visitor who is not signed in and a count that cannot be read.
    The second is deliberate (ADR-0047): a page must not be lost to a counter,
    so a database failure is logged and the badge is left out. This is a read
    for display; a failure to *write* a notification is a different matter and
    rolls its operation back.
    """
    if not is_signed_in():
        return None
    try:
        return unread_count(
            get_connection(),
            user_id=session["user_id"],
            role_code=current_role_code(),
        )
    except psycopg.Error as error:
        current_app.logger.warning(
            "notification_count_failed reason=%s", type(error).__name__
        )
        return None


@bp.app_context_processor
def notification_badge() -> dict[str, Any]:
    """Offer every template the unread count and its badge text."""
    unread = _unread_for_this_page()
    return {
        "unread_notifications": unread,
        "unread_badge_text": badge_text(unread) if unread else None,
    }


@bp.get("/")
@requires()
def index() -> str | Response:
    """The signed-in user's inbox, newest first, twenty to a page."""
    page = page_number(request.args.get("page"))
    inbox = read_inbox(
        get_connection(),
        user_id=session["user_id"],
        role_code=current_role_code(),
        page=page,
    )
    if response := redirect_last_page(page, inbox.page_count):
        return response
    return render_template("notifications/index.html", inbox=inbox)


# The id is bounded to what the column holds, so a number past it is no route at
# all and a 404, instead of a database error.
_ID = f"<int(min=1, max={BIGINT_MAX}):notification_id>"


@bp.get(f"/{_ID}")
@requires()
def detail(notification_id: int) -> str:
    """One notification of the signed-in user's own, or a 404."""
    item = read_notification(
        get_connection(),
        user_id=session["user_id"],
        role_code=current_role_code(),
        notification_id=notification_id,
    )
    if item is None:
        abort(404)
    return render_template("notifications/detail.html", item=item)


def _back() -> str:
    """Where to return to after a state change: a same-site path the form
    carried, else the inbox. Never a path taken on trust (`safe_next`)."""
    return safe_next(request.form.get("next")) or url_for("notifications.index")


@bp.post(f"/{_ID}/read")
@requires()
def read(notification_id: int) -> Response:
    """Mark one notification read, or a 404 when it is not the user's."""
    found = mark_notification_read(
        get_connection(),
        user_id=session["user_id"],
        role_code=current_role_code(),
        notification_id=notification_id,
    )
    if not found:
        abort(404)
    return redirect(_back(), code=303)


@bp.post("/read-all")
@requires()
def read_all() -> Response:
    """Mark everything the user may read as read."""
    marked = mark_all_notifications_read(
        get_connection(),
        user_id=session["user_id"],
        role_code=current_role_code(),
    )
    flash(
        (
            "No unread notifications."
            if marked == 0
            else f"{marked} notification{'s' if marked != 1 else ''} marked read."
        ),
        "success" if marked else "info",
    )
    return redirect(url_for("notifications.index"), code=303)
