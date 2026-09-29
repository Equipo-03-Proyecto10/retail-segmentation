"""Running the segment recalculation (F3-10).

Only the administrator reaches this: RN-05, because a run rewrites a column on
every customer and an analyst who can trigger it can change what every report
says. The refusal is the authorization middleware's, not this module's.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from web.db import get_connection
from web.middleware.authz import SEGMENT_RUN_EXECUTE, requires
from web.services.cluster_labels import VocabularySizeMismatch
from web.services.kmeans import InvalidParameters, KMeansParams
from web.services.segmentation import (
    DEFAULT_WINDOW_DAYS,
    InvalidWindow,
    parse_window,
    run,
    run_kmeans,
)

bp = Blueprint("segment_run", __name__, url_prefix="/segment-run")


def _int_field(raw: str, label: str) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise InvalidParameters(f"{label} must be a whole number.") from None


def _float_field(raw: str, label: str) -> float:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        raise InvalidParameters(f"{label} must be a number.") from None


@bp.get("/")
@requires(SEGMENT_RUN_EXECUTE)
def index() -> str:
    """Explain what a run does, and offer to do it."""
    return render_template("segment_run/index.html", window=DEFAULT_WINDOW_DAYS)


@bp.post("/")
@requires(SEGMENT_RUN_EXECUTE)
def execute() -> tuple[str, int] | str:
    """Run the recalculation and report what it did."""
    method = request.form.get("method", "RFM_RULES")
    form_values = dict(request.form)

    try:
        window = parse_window(request.form.get("window", ""))
    except InvalidWindow as refusal:
        return (
            render_template(
                "segment_run/index.html",
                window=request.form.get("window", DEFAULT_WINDOW_DAYS),
                method=method,
                form_values=form_values,
                error=str(refusal),
            ),
            400,
        )

    kmeans_params: KMeansParams | None = None
    if method == "KMEANS":
        try:
            kmeans_params = KMeansParams(
                k=_int_field(request.form.get("k", ""), "K"),
                seed=_int_field(request.form.get("seed", ""), "Seed"),
                max_iterations=_int_field(
                    request.form.get("max_iterations", "100"), "Iteration limit"
                ),
                tolerance=_float_field(
                    request.form.get("tolerance", "0.0001"), "Tolerance"
                ),
            )
        except InvalidParameters as refusal:
            return (
                render_template(
                    "segment_run/index.html",
                    window=window,
                    method=method,
                    form_values=form_values,
                    error=str(refusal),
                ),
                400,
            )
    elif method != "RFM_RULES":
        return (
            render_template(
                "segment_run/index.html",
                window=window,
                method="RFM_RULES",
                form_values=form_values,
                error="Choose RFM rules or K-means.",
            ),
            400,
        )

    if request.form.get("confirm") != "yes":
        return render_template(
            "segment_run/confirm.html",
            window=window,
            method=method,
            kmeans_params=kmeans_params,
        )

    connection = get_connection()
    try:
        if method == "KMEANS":
            result = run_kmeans(connection, window, kmeans_params)
        else:
            result = run(connection, window)
    except (InvalidParameters, VocabularySizeMismatch) as refusal:
        return (
            render_template(
                "segment_run/index.html",
                window=window,
                method=method,
                form_values=form_values,
                error=str(refusal),
            ),
            400,
        )

    # Rendered rather than redirected: the numbers are the point of the page,
    # and a resubmitted run is harmless by construction — the same sales
    # produce the same assignment, so a second run changes no segment; it only
    # records one more run in the history (ADR-0017).
    return render_template(
        "segment_run/index.html", window=window, method=method, result=result
    )
