"""Cross-cutting request handling.

A middleware runs for every request, before or after the view, and belongs to
no single module: `web.routes` reads one request, a middleware sees them all.
Authorization is the first of them (F4-01) and the reason this layer exists —
the point of putting the check here rather than at the top of each view is that
a view cannot forget to call it.

The layers below stay as `web/README.md` describes them: a middleware decides
whether the request reaches a route at all, and holds no business logic and no
SQL of its own.
"""

from __future__ import annotations

from flask import Flask

from web.middleware.authz import install as install_authorization
from web.middleware.authz import public, requires

__all__ = ["public", "register_middleware", "requires"]


def register_middleware(app: Flask) -> None:
    """Attach every middleware. One line per concern, as they land."""
    install_authorization(app)
