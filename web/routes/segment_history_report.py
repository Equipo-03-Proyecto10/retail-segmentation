"""The filtered segment history and migration report (F12-02).

Read-only, gated on segment.read like the rest of the segmentation surface
(ADR-0010). Every filter -- run, label, period -- is optional and combines
with the others (RN-43); each row's explanation is built by F7-06's own
machinery, reused rather than duplicated. A run id that does not exist is
just a filter no row matches, exactly like any other filter combination with
nothing to show -- this page does not resolve "the" run the way F12-01's
dashboard or the migration matrix do, so there is no run to 404 on. Nothing
here reads or is told which method produced a run.
"""

from __future__ import annotations

from datetime import date

from flask import Blueprint, render_template, request

from web.db import get_connection
from web.db.segment_history_report import UNASSIGNED
from web.db.segments import get_label_ordinals, list_runs
from web.middleware.authz import SEGMENT_READ, requires
from web.services.catalog import INT_MAX
from web.services.segment_history_report import InvalidPeriod, ReportPage, build_report

bp = Blueprint("segment_history_report", __name__, url_prefix="/segment-history-report")

_RUN_OPTIONS = 100
_EMPTY = ReportPage((), 0, 1, 1)


def _a_date(raw: str | None) -> date | None:
    """Read an optional date, refusing an invalid one instead of dropping it."""
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


def _a_run(raw: str | None) -> int | None:
    """None when unset; the run id when it is one; -1 when the value given
    could not be a run id at all (refused, not silently dropped). A whole
    number past the column's range is one of those: PostgreSQL would answer
    it with NumericValueOutOfRange, a 500 rather than a refusal."""
    if not raw:
        return None
    if not (raw.isascii() and raw.isdigit()) or int(raw) > INT_MAX:
        return -1
    return int(raw)


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> str | tuple[str, int]:
    """List segment history rows, filtered and paged, each with its
    per-customer explanation."""
    connection = get_connection()
    ordinals = get_label_ordinals(connection)

    run_id = _a_run(request.args.get("run"))
    label = request.args.get("label") or None
    label_code = UNASSIGNED if label == "unassigned" else label

    context = dict(
        runs=list_runs(connection, page=1, per_page=_RUN_OPTIONS)[0],
        vocabulary=sorted(ordinals, key=ordinals.__getitem__),
        run=request.args.get("run", ""),
        label=label or "",
        period_start=request.args.get("period_start", ""),
        period_end=request.args.get("period_end", ""),
    )

    if run_id == -1:
        return (
            render_template(
                "segment_history_report/index.html",
                report=_EMPTY,
                error="Choose a run from the list.",
                **context,
            ),
            400,
        )

    try:
        period_start = _a_date(request.args.get("period_start"))
        period_end = _a_date(request.args.get("period_end"))
        report = build_report(
            connection,
            run_id=run_id,
            label_code=label_code,
            period_start=period_start,
            period_end=period_end,
            page=_a_page(request.args.get("page")),
        )
    except (ValueError, InvalidPeriod) as error:
        return (
            render_template(
                "segment_history_report/index.html",
                report=_EMPTY,
                error=str(error),
                **context,
            ),
            400,
        )

    return render_template(
        "segment_history_report/index.html", report=report, error=None, **context
    )
