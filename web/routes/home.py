"""The landing page."""

from flask import Blueprint, current_app, render_template

from web.config import Config
from web.services.status import application_status

home_bp = Blueprint("home", __name__)


@home_bp.get("/")
def index() -> str:
    """Render the landing page."""
    config: Config = current_app.config["APP_CONFIG"]
    return render_template("home.html", status=application_status(config))
