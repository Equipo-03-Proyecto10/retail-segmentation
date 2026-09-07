"""HTTP layer.

A route reads the request, calls a service, and renders a template. It holds no
business logic and no SQL; those live in `web.services` and `web.db`.
"""

from flask import Flask

from web.routes.admin import bp as admin_bp
from web.routes.audit import bp as audit_bp
from web.routes.auth import bp as auth_bp
from web.routes.catalog import bp as catalog_bp
from web.routes.home import bp as home_bp
from web.routes.placeholders import campaigns_bp, reports_bp
from web.routes.segment_run import bp as segment_run_bp


def register_blueprints(app: Flask) -> None:
    """Attach every blueprint. One line per module, as the modules land."""
    app.register_blueprint(home_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(segment_run_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(reports_bp)
