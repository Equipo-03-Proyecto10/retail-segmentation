"""Experiment setup (F11-03).

Reading needs `campaign.read`; setup and every change need `campaign.write`,
the permissions docs/analytics-permission-map.md assigns to experiments. The
setup rules are the service's; a route only reads the request and renders the
outcome.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from web.db import get_connection
from web.db.experiment_conversions import list_attributions
from web.db.experiments import get_experiment, list_campaign_choices, list_experiments
from web.middleware.authz import CAMPAIGN_READ, CAMPAIGN_WRITE, requires
from web.routes.pagination import redirect_last_page
from web.services import experiment_conversions as conversion_service
from web.services import experiments as service
from web.services.catalog import parse_pagination
from web.services.pagination import page_count

bp = Blueprint("experiments", __name__, url_prefix="/experiments")

_PER_PAGE = 20


def _render_form(
    experiment: object,
    errors: dict[str, str],
    status: int = 200,
    *,
    existing: object = None,
) -> ResponseReturnValue:
    """`existing` is the stored experiment when editing: what the form shows as
    fixed (its origin, its groups, and any rule its assignments locked)."""
    return (
        render_template(
            "experiments/form.html",
            experiment=experiment,
            existing=existing,
            errors=errors,
            campaigns=list_campaign_choices(get_connection()),
            target_metrics=service.TARGET_METRICS,
            data_origins=service.DATA_ORIGINS,
            treatment_groups_max=service.TREATMENT_GROUPS_MAX,
        ),
        status,
    )


def _form_values(*, creating: bool) -> dict[str, str]:
    values = {
        "name": request.form.get("name", "").strip(),
        "campaign_id": request.form.get("campaign_id", ""),
        "target_metric": request.form.get("target_metric", ""),
        "starts_on": request.form.get("starts_on", ""),
        "ends_on": request.form.get("ends_on", ""),
        "conversion_window_days": request.form.get(
            "conversion_window_days", ""
        ).strip(),
    }
    if creating:
        values["data_origin"] = request.form.get("data_origin", "")
        values["treatment_groups"] = request.form.get("treatment_groups", "").strip()
    return values


@bp.get("/", endpoint="index")
@requires(CAMPAIGN_READ)
def index() -> ResponseReturnValue:
    page = parse_pagination(request.args.get("page"))
    experiment_page, total = list_experiments(
        get_connection(), page=page, per_page=_PER_PAGE
    )
    total_pages = page_count(total, _PER_PAGE)
    if response := redirect_last_page(page, total_pages):
        return response
    return render_template(
        "experiments/index.html",
        experiments=experiment_page,
        page=page,
        total_pages=total_pages,
        target_metrics=service.TARGET_METRICS,
        is_synthetic=service.is_synthetic,
    )


@bp.route("/new", methods=["GET", "POST"])
@requires(CAMPAIGN_WRITE)
def create() -> ResponseReturnValue:
    if request.method in ("GET", "HEAD"):
        return _render_form(None, {})

    values = _form_values(creating=True)
    data, errors = service.validate_experiment(**values)
    if data is None:
        return _render_form(values, errors, 400)
    try:
        experiment_id = service.create_experiment(get_connection(), data)
    except service.ExperimentRefused as refusal:
        return _render_form(values, {refusal.field: str(refusal)}, 409)
    flash(
        f"Experiment {experiment_id} created with one control and "
        f"{data.treatment_groups} treatment group"
        f"{'' if data.treatment_groups == 1 else 's'}.",
        "success",
    )
    return redirect(url_for("experiments.index"))


@bp.route("/<int:experiment_id>/edit", methods=["GET", "POST"])
@requires(CAMPAIGN_WRITE)
def edit(experiment_id: int) -> ResponseReturnValue:
    existing = get_experiment(get_connection(), experiment_id)
    if existing is None:
        abort(404)

    if request.method in ("GET", "HEAD"):
        return _render_form(existing, {}, existing=existing)

    values = _form_values(creating=False)
    data, errors = service.validate_experiment(**values)
    if data is None:
        return _render_form(values, errors, 400, existing=existing)
    try:
        service.update_experiment(get_connection(), experiment_id, data)
    except service.ExperimentNotFound:
        abort(404)
    except service.ExperimentRefused as refusal:
        return _render_form(
            values, {refusal.field: str(refusal)}, 409, existing=existing
        )
    flash(f"Experiment {experiment_id} updated.", "success")
    return redirect(url_for("experiments.index"))


@bp.route("/<int:experiment_id>/assign", methods=["GET", "POST"])
@requires(CAMPAIGN_WRITE)
def assign(experiment_id: int) -> ResponseReturnValue:
    """Preview the split, then assign on confirmation (F11-04).

    The preview and the write use the same checks, so a refusal reads the same
    on either; the write re-checks under the experiment's lock.
    """
    connection = get_connection()
    try:
        if request.method in ("GET", "HEAD"):
            plan = service.plan_assignment(connection, experiment_id)
            return render_template("experiments/assign.html", plan=plan, error=None)
        plan = service.assign(connection, experiment_id)
    except service.ExperimentNotFound:
        abort(404)
    except service.AssignmentRefused as refusal:
        return (
            render_template(
                "experiments/assign.html",
                plan=None,
                experiment=get_experiment(connection, experiment_id),
                error=str(refusal),
            ),
            409,
        )
    sizes = ", ".join(f"{arm.kind.lower()} {len(arm.customers)}" for arm in plan.arms)
    flash(
        f"Experiment {experiment_id}: {plan.population} customers assigned ({sizes}).",
        "success",
    )
    return redirect(url_for("experiments.index"))


_ATTRIBUTIONS_PER_PAGE = 50


def _render_conversion(
    experiment_id: int, *, error: str | None = None, status: int = 200
) -> ResponseReturnValue:
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    try:
        summary = conversion_service.conversion_summary(connection, experiment_id)
    except service.ExperimentNotFound:
        abort(404)
    attributions, total = list_attributions(
        connection, experiment_id, page=page, per_page=_ATTRIBUTIONS_PER_PAGE
    )
    total_pages = page_count(total, _ATTRIBUTIONS_PER_PAGE)
    if response := redirect_last_page(page, total_pages):
        return response
    return (
        render_template(
            "experiments/conversion.html",
            summary=summary,
            attributions=attributions,
            total=total,
            page=page,
            total_pages=total_pages,
            error=error,
            is_synthetic=service.is_synthetic,
        ),
        status,
    )


@bp.get("/<int:experiment_id>/conversion", endpoint="conversion")
@requires(CAMPAIGN_READ)
def conversion(experiment_id: int) -> ResponseReturnValue:
    return _render_conversion(experiment_id)


@bp.post("/<int:experiment_id>/conversion", endpoint="evaluate_conversion")
@requires(CAMPAIGN_WRITE)
def evaluate_conversion(experiment_id: int) -> ResponseReturnValue:
    try:
        added = conversion_service.evaluate(get_connection(), experiment_id)
    except service.ExperimentNotFound:
        abort(404)
    except conversion_service.ConversionRefused as refusal:
        return _render_conversion(experiment_id, error=str(refusal), status=409)
    flash(
        f"Experiment {experiment_id}: {added} new conversion"
        f"{'' if added == 1 else 's'} recorded.",
        "success",
    )
    return redirect(url_for("experiments.conversion", experiment_id=experiment_id))
