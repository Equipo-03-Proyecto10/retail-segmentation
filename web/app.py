"""Application factory and entry point.

`flask --app web.app run` finds `create_app` on its own. `python -m web.app`
starts the same application on the port named in the environment.
"""

from __future__ import annotations

from flask import Flask

from web.config import Config, load_dotenv_file
from web.errors import register_error_handlers
from web.log import configure_logging
from web.routes import register_blueprints
from web.services.status import APPLICATION_NAME


def create_app(config: Config | None = None) -> Flask:
    """Build the application.

    Passing a `Config` bypasses the environment entirely, which is how the
    tests run on a machine that has no `.env`.
    """
    if config is None:
        load_dotenv_file()
        config = Config.from_env()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    app.config["APP_CONFIG"] = config
    # Debug mode is left off deliberately: it would replace the controlled
    # error pages (web/errors.py) with Werkzeug's interactive traceback.

    configure_logging(app)
    register_error_handlers(app)

    @app.context_processor
    def application_identity() -> dict[str, str]:
        """The product name belongs on every page, not in every route."""
        return {"application_name": APPLICATION_NAME}

    register_blueprints(app)
    return app


if __name__ == "__main__":
    application = create_app()
    application.run(port=application.config["APP_CONFIG"].port)
