"""Structured request context and secret redaction for service logs."""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar

from flask import has_request_context, request

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")
_actor: ContextVar[str] = ContextVar("actor", default="-")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_TOKEN = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_PASSWORD = re.compile(r"(?i)\bpassword\s*=\s*[^\s,;]+")


def _redact(value: str) -> str:
    value = _BEARER.sub("Bearer [redacted]", value)
    value = _TOKEN.sub("[token]", value)
    return _PASSWORD.sub("password=[redacted]", value)


def set_request_context(correlation_id: str, actor: str = "-") -> None:
    """Set fields attached to every log record in the current context."""

    _correlation_id.set(correlation_id)
    _actor.set(actor)


class RedactionFilter(logging.Filter):
    """Add request fields and remove credentials from the rendered message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = _redact(
                logging.Formatter().formatException(record.exc_info)
            )
            record.exc_info = None
        if record.stack_info:
            record.stack_info = _redact(record.stack_info)
        in_request = has_request_context()
        record.correlation_id = _correlation_id.get() if in_request else "-"
        record.actor = _actor.get() if in_request else "-"
        record.client = (
            request.headers.get("X-Real-IP") or request.remote_addr or "-"
            if in_request
            else "-"
        )
        return True


def configure_logging(level: str = "INFO") -> logging.Handler:
    """Install the kit's single stderr handler on the root logger."""

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_mosaiq_kit", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler._mosaiq_kit = True  # type: ignore[attr-defined]
    handler.addFilter(RedactionFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s correlation=%(correlation_id)s "
            "actor=%(actor)s client=%(client)s %(name)s %(message)s"
        )
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
    return handler
