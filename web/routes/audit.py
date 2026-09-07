"""The audit log view (F3-11).

Read-only by construction: both routes are GET, and the service they call has
no function that writes. RNF-17 says the log is append-only; the application
is not given a way to try.
"""

from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, render_template, request

from web.db import get_connection
from web.middleware.authz import AUDIT_READ, requires
from web.services.audit import Page, read_entry, read_page

bp = Blueprint("audit", __name__, url_prefix="/audit")


def _a_date(raw: str | None) -> date | None:
    """Read an optional date, refusing invalid filters instead of dropping them."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError("Enter valid dates in YYYY-MM-DD format.") from None


def _a_page(raw: str | None) -> int:
    try:
        return max(1, int(raw or 1))
    except ValueError:
        return 1


@bp.get("/")
@requires(AUDIT_READ)
def index() -> str | tuple[str, int]:
    """List entries, newest first, filtered and paged."""
    entity = request.args.get("entity") or None
    try:
        date_from = _a_date(request.args.get("from"))
        date_to = _a_date(request.args.get("to"))
        if date_from and date_to and date_from > date_to:
            raise ValueError("The From date must be on or before the To date.")
    except ValueError as error:
        return (
            render_template(
                "audit/index.html",
                page=Page((), (entity,) if entity else (), 0, 1, 1),
                entity=entity,
                date_from=request.args.get("from", ""),
                date_to=request.args.get("to", ""),
                error=str(error),
            ),
            400,
        )

    page = read_page(
        get_connection(),
        entity=entity,
        date_from=date_from,
        date_to=date_to,
        page=_a_page(request.args.get("page")),
    )

    # An entity that is not one the log holds filters to nothing, which would
    # read as "no entries" rather than as "no such entity". Dropped instead.
    if entity is not None and entity not in page.entities:
        entity = None

    return render_template(
        "audit/index.html",
        page=page,
        entity=entity,
        date_from=request.args.get("from", ""),
        date_to=request.args.get("to", ""),
    )


@bp.get("/<int:audit_id>")
@requires(AUDIT_READ)
def detail(audit_id: int) -> str:
    """One entry, with before and after side by side."""
    entry = read_entry(get_connection(), audit_id)
    if entry is None:
        abort(404)
    return render_template("audit/detail.html", detail=entry)
