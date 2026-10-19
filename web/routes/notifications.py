"""The notification inbox and the navigation badge (AN-04, #419; ADR-0047).

Every route here is declared `@requires()`: any signed-in user may open their
own inbox. What a notification says is another matter and is gated on its own
delivery permission when it is read (`web.services.notifications`), so no new
permission exists and none is needed.

The unread count shown in the navigation of every page is a context processor
of this blueprint. It fails soft: a counter that cannot be read hides the badge
and logs the failure, and the page it sits on is still served.
"""

from __future__ import annotations

from typing import Any

import psycopg
from flask import Blueprint, Response, current_app, render_template, request, session

from web.db import get_connection
from web.middleware.authz import current_role_code, is_signed_in, requires
from web.parsing import page_number
from web.routes.pagination import redirect_last_page
from web.services.notifications import badge_text, read_inbox, unread_count

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
