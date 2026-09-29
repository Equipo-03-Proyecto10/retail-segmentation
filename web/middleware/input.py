"""Refuse a request that carries a NUL byte (#347).

PostgreSQL text cannot hold U+0000, and psycopg says so with a `DataError` when
one reaches a statement. Every form and search box would need to catch that, and
the ones that did not (sign-in, every create and edit form, every `q=`) answered
500. A NUL is never legitimate in what a person types or a link carries, so the
request is refused once, here, with the branded 400 page, before any view runs.

It runs after CSRF and authorization on purpose: a forged or unauthorized request
is still refused for that reason, and this check does not tell a stranger which
values the application would have rejected.
"""

from __future__ import annotations

from flask import Flask, abort, request

NUL = "\x00"


def _refuse_nul() -> None:
    """400 when a query value, form value, field name or path segment has a NUL."""
    for source in (request.args, request.form):
        for key, value in source.items(multi=True):
            if NUL in key or NUL in value:
                abort(400)
    for value in (request.view_args or {}).values():
        if isinstance(value, str) and NUL in value:
            abort(400)


def install(app: Flask) -> None:
    app.before_request(_refuse_nul)
