"""Application logging.

One stream, stderr, and nothing else. Under systemd and gunicorn (F6-02)
`journald` captures stderr and gives us rotation, retention and `journalctl`
for free; a `RotatingFileHandler` on a single VM would be a worse copy of that
and one more path to keep writable. Locally, stderr is the terminal.

The format carries a timestamp, the level and the logger name so a line copied
out of `journalctl` still says when it happened and where it came from.
"""

from __future__ import annotations

import logging
import sys

from flask import Flask, g, has_request_context, request, session

_LOG_FORMAT = (
    "%(asctime)s %(levelname)-8s %(name)s "
    "reference=%(reference)s actor=%(actor)s client=%(client)s: %(message)s"
)


class RequestContext(logging.Filter):
    """Enrich service logs without giving services an HTTP dependency."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.reference = (
            getattr(g, "request_id", "-") if has_request_context() else "-"
        )
        record.actor = (
            repr(session.get("user_id", "-")) if has_request_context() else "-"
        )
        record.client = repr(request.remote_addr) if has_request_context() else "-"
        return True


def configure_logging(app: Flask) -> None:
    """Point every `web.*` logger at stderr at the configured level.

    Idempotent: the tests build several applications in one process, and
    `create_app` must not leave a stack of duplicate handlers behind.
    """
    level = logging.getLevelName(app.config["APP_CONFIG"].log_level.upper())
    if not isinstance(level, int):
        level = logging.INFO

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(RequestContext())

    for existing in list(app.logger.handlers):
        app.logger.removeHandler(existing)
    app.logger.setLevel(logging.NOTSET)
    logger = logging.getLogger("web")
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.setLevel(level)
