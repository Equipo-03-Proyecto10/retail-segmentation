"""Running the segment recalculation (F3-10).

Only the administrator reaches this: RN-05, because a run rewrites a column on
every customer and an analyst who can trigger it can change what every report
says. The refusal is the authorization middleware's, not this module's.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from web.db import get_connection
from web.middleware.authz import SEGMENT_RUN_EXECUTE, requires
from web.services.segmentation import (
    DEFAULT_WINDOW_DAYS,
    InvalidWindow,
    parse_window,
    run,
)

bp = Blueprint("segment_run", __name__, url_prefix="/segment-run")


@bp.get("/")
@requires(SEGMENT_RUN_EXECUTE)
def index() -> str:
    """Explain what a run does, and offer to do it."""
    return render_template("segment_run/index.html", window=DEFAULT_WINDOW_DAYS)


@bp.post("/")
@requires(SEGMENT_RUN_EXECUTE)
def execute() -> tuple[str, int] | str:
    """Run the recalculation and report what it did."""
    try:
        window = parse_window(request.form.get("window"))
    except InvalidWindow as refusal:
        return (
            render_template(
                "segment_run/index.html",
                window=request.form.get("window", DEFAULT_WINDOW_DAYS),
                error=str(refusal),
            ),
            400,
        )

    result = run(get_connection(), window)

    # Rendered rather than redirected: the numbers are the point of the page,
    # and a resubmitted run is safe by construction — the same sales produce
    # the same assignment, and the second run writes nothing.
    return render_template("segment_run/index.html", window=window, result=result)
