"""Controlled error responses.

Two things have to be true when something goes wrong (issue #73):

* the visitor sees a plain MOSAIQ page with a status code and nothing else —
  never a stack trace, never Werkzeug's interactive debugger;
* the failure leaves one log line with enough context — method, path, client,
  and a reference the visitor can quote — to find it in `journalctl`.

Debug mode would defeat the first point by replacing these handlers with the
traceback page, so `create_app` never turns it on.
"""

from __future__ import annotations

import secrets

from flask import Flask, g, render_template, request
from werkzeug.exceptions import HTTPException

_ERROR_TEMPLATE = "errors/error.html"


def _reference() -> str:
    """A short id shared between the log line and the page.

    Set once per request; recreated here for the routing 404, which is raised
    before `before_request` runs and so never got one.
    """
    if not getattr(g, "request_id", None):
        g.request_id = secrets.token_hex(4)
    return g.request_id


def register_error_handlers(app: Flask) -> None:
    """Attach the request-id hook and the error handlers."""

    @app.before_request
    def assign_request_id() -> None:
        g.request_id = secrets.token_hex(4)

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        """Render a known HTTP status (404, 403, 405, …) as a MOSAIQ page."""
        app.logger.info(
            "http_refused status=%s reference=%s method=%s path=%r",
            error.code,
            _reference(),
            request.method,
            request.path,
        )
        return (
            render_template(
                _ERROR_TEMPLATE,
                code=error.code,
                name=error.name,
                reference=_reference(),
            ),
            error.code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_exception(error: Exception):
        """Anything not an `HTTPException`: log it, then show a generic 500."""
        reference = _reference()
        app.logger.exception(
            "Unhandled exception [%s] during %s %s from %s",
            reference,
            request.method,
            request.path,
            request.remote_addr,
        )
        return (
            render_template(
                _ERROR_TEMPLATE,
                code=500,
                name="Internal Server Error",
                reference=reference,
            ),
            500,
        )
