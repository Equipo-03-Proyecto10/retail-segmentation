"""The landing page: a public front door, and the shell's home once signed in."""

from flask import Blueprint, current_app, redirect, render_template, session, url_for

from web.config import Config
from web.db import get_connection
from web.middleware import public
from web.middleware.authz import current_permissions, is_signed_in
from web.services.dashboard import dashboard_for
from web.services.status import application_status

home_bp = Blueprint("home", __name__)


@home_bp.get("/")
@public
def index() -> str:
    """Render the landing page, or the shell's home for a signed-in visitor.

    One URL rather than two: `Home` in the navigation, the redirect after
    signing in and the link on the error page all mean the same place, and a
    second dashboard route would make each of them choose.
    """
    if not is_signed_in():
        config: Config = current_app.config["APP_CONFIG"]
        return render_template("home.html", status=application_status(config))

    dashboard = dashboard_for(
        get_connection(), session["user_id"], current_permissions()
    )
    if dashboard is None:
        # The account was deleted while its session was still valid. Sign the
        # visitor out rather than showing a shell with nobody in it.
        session.clear()
        config = current_app.config["APP_CONFIG"]
        return render_template("home.html", status=application_status(config))

    return render_template("dashboard.html", dashboard=dashboard)


@home_bp.get("/favicon.ico")
@public
def favicon():
    """Handle browsers that request the conventional icon URL."""
    return redirect(url_for("static", filename="favicon.svg"), code=301)
