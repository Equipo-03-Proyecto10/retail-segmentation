"""HTTP layer.

A route reads the request, calls a service, and renders a template. It holds no
business logic and no SQL; those live in `web.services` and `web.db`.
"""

from flask import Flask

from web.routes.auth import bp as auth_bp
from web.routes.home import home_bp


def register_blueprints(app: Flask) -> None:
    """Attach every blueprint. One line per module, as the modules land."""
    app.register_blueprint(home_bp)
    app.register_blueprint(auth_bp)
