"""Application factory and entry point.

`flask --app web.app run` finds `create_app` on its own. `python -m web.app`
starts the same application on the port named in the environment.
"""

from __future__ import annotations

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from web.cli import register_commands
from web.config import FORM_OVERHEAD_BYTES, Config, load_dotenv_file
from web.db import DatabaseConnector
from web.db import init_app as init_database
from web.errors import register_error_handlers
from web.log import configure_logging
from web.middleware import register_middleware
from web.routes import register_blueprints
from web.security import configure_session
from web.services.status import APPLICATION_NAME


def _trust_forwarding_headers(app: Flask, hops: int) -> None:
    """Believe `X-Forwarded-*` from exactly `hops` proxies in front (F6-01).

    `hops` is 0 for a directly reachable app — bare `flask run`, or gunicorn
    with nothing ahead of it — and 1 behind the single NGINX on the instance or
    the compose proxy overlay. Reading these headers with no proxy present would
    let a client forge its own address in the logs.
    """
    if hops:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_port=hops
        )


def create_app(
    config: Config | None = None,
    *,
    database_connector: DatabaseConnector | None = None,
) -> Flask:
    """Build the application.

    Passing a `Config` bypasses the environment. A connector can also be
    injected so unit tests do not need a running PostgreSQL server.
    """
    if config is None:
        load_dotenv_file()
        config = Config.from_env()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    app.config["APP_CONFIG"] = config
    app.config["MAX_CONTENT_LENGTH"] = config.max_upload_bytes + FORM_OVERHEAD_BYTES
    # Debug mode is left off deliberately: it would replace the controlled
    # error pages (web/errors.py) with Werkzeug's interactive traceback.

    configure_logging(app)
    configure_session(app)
    _trust_forwarding_headers(app, config.trusted_proxy_hops)
    register_error_handlers(app)
    # After the error handlers: their `before_request` assigns the request id,
    # so a refusal logged by the authorization gate quotes the same reference
    # the visitor sees on the 403 page.
    register_middleware(app)
    init_database(app, database_connector)

    @app.context_processor
    def application_identity() -> dict[str, str]:
        """The product name belongs on every page, not in every route."""
        return {"application_name": APPLICATION_NAME}

    register_blueprints(app)
    register_commands(app)
    app.logger.info(
        "application_started environment=%r log_level=%r proxy_hops=%s "
        "upload_dir=%r max_upload_bytes=%s max_request_bytes=%s",
        config.environment,
        config.log_level,
        int(config.trusted_proxy_hops),
        config.upload_dir,
        config.max_upload_bytes,
        app.config["MAX_CONTENT_LENGTH"],
    )
    return app


if __name__ == "__main__":
    application = create_app()
    application.run(port=application.config["APP_CONFIG"].port)
