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

from flask import Flask

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(app: Flask) -> None:
    """Point `app.logger` at stderr at the configured level.

    Idempotent: the tests build several applications in one process, and
    `create_app` must not leave a stack of duplicate handlers behind.
    """
    level = logging.getLevelName(app.config["APP_CONFIG"].log_level.upper())
    if not isinstance(level, int):
        level = logging.INFO

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    for existing in list(app.logger.handlers):
        app.logger.removeHandler(existing)
    app.logger.addHandler(handler)
    app.logger.setLevel(level)
