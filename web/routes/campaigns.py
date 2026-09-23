"""Campaign workflow (F11-02).

Reading needs `campaign.read`; every change needs `campaign.write`. Both are
existing permissions (docs/analytics-permission-map.md), and the default-deny
gate refuses a view that declares neither. The lifecycle rules are the
service's; a route only reads the request and renders the outcome.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue

from web.db import get_connection
from web.db.campaigns import get_campaign, list_campaigns, list_labels
from web.middleware.authz import CAMPAIGN_READ, CAMPAIGN_WRITE, requires
from web.routes.pagination import redirect_last_page
from web.services import campaigns as service
from web.services.catalog import parse_pagination
from web.services.pagination import page_count

bp = Blueprint("campaigns", __name__, url_prefix="/campaigns")

_PER_PAGE = 20

_DONE = {"activate": "activated", "complete": "completed", "cancel": "cancelled"}


def _render_index(
    *, page: int, error: str | None = None, status: int = 200
) -> ResponseReturnValue:
    campaign_page, total = list_campaigns(
        get_connection(), page=page, per_page=_PER_PAGE
    )
    total_pages = page_count(total, _PER_PAGE)
    if response := redirect_last_page(page, total_pages):
        return response
    return (
        render_template(
            "campaigns/index.html",
            campaigns=campaign_page,
            page=page,
            total_pages=total_pages,
            transitions=service.TRANSITIONS,
            error=error,
        ),
        status,
    )


def _render_form(campaign, errors: dict[str, str], status: int = 200):
    return (
        render_template(
            "campaigns/form.html",
            campaign=campaign,
            errors=errors,
            labels=list_labels(get_connection()),
        ),
        status,
    )


def _form_values() -> dict[str, str]:
    return {
        "name": request.form.get("name", "").strip(),
        "label_code": request.form.get("label_code", ""),
        "starts_on": request.form.get("starts_on", ""),
        "ends_on": request.form.get("ends_on", ""),
    }


@bp.get("/", endpoint="index")
@requires(CAMPAIGN_READ)
def index() -> ResponseReturnValue:
    return _render_index(page=parse_pagination(request.args.get("page")))


@bp.route("/new", methods=["GET", "POST"])
@requires(CAMPAIGN_WRITE)
def create() -> ResponseReturnValue:
    if request.method == "GET":
        return _render_form(None, {})

    values = _form_values()
    data, errors = service.validate_campaign(**values)
    if data is None:
        return _render_form(values, errors, 400)
    try:
        service.create_campaign(get_connection(), data)
    except service.CampaignRefused as refusal:
        return _render_form(values, {refusal.field: str(refusal)}, 409)
    flash("Campaign created as a draft.", "success")
    return redirect(url_for("campaigns.index"))


@bp.route("/<int:campaign_id>/edit", methods=["GET", "POST"])
@requires(CAMPAIGN_WRITE)
def edit(campaign_id: int) -> ResponseReturnValue:
    campaign = get_campaign(get_connection(), campaign_id)
    if campaign is None:
        abort(404)

    if request.method == "GET":
        if campaign.status != service.DRAFT:
            return _render_index(
                page=1,
                error=(
                    f"Campaign {campaign_id} is {campaign.status.lower()}; only a "
                    "draft can be edited."
                ),
                status=409,
            )
        return _render_form(campaign, {})

    values = _form_values()
    data, errors = service.validate_campaign(**values)
    if data is None:
        return _render_form(dict(values, campaign_id=campaign_id), errors, 400)
    try:
        service.update_draft(get_connection(), campaign_id, data)
    except service.CampaignNotFound:
        abort(404)
    except service.InvalidTransition as refusal:
        return _render_index(page=1, error=str(refusal), status=409)
    except service.CampaignRefused as refusal:
        return _render_form(
            dict(values, campaign_id=campaign_id), {refusal.field: str(refusal)}, 409
        )
    flash("Campaign updated.", "success")
    return redirect(url_for("campaigns.index"))


@bp.post("/<int:campaign_id>/<action>")
@requires(CAMPAIGN_WRITE)
def change_status(campaign_id: int, action: str) -> ResponseReturnValue:
    if action not in service.ACTIONS:
        abort(404)
    try:
        service.transition(get_connection(), campaign_id, action)
    except service.CampaignNotFound:
        abort(404)
    except service.InvalidTransition as refusal:
        return _render_index(page=1, error=str(refusal), status=409)
    flash(f"Campaign {campaign_id} {_DONE[action]}.", "success")
    return redirect(url_for("campaigns.index"))
