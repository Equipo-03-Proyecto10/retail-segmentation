"""CSRF defence for every state-changing request (#294, ADR-0025).

A synchroniser token, not `flask-wtf`: the same choice ADR-0021 made for
k-means, for the same reason -- this is small enough that a dependency costs
more than writing it. The session cookie's own `SameSite=Lax`
(web/security.py) stays the first layer; this is the second, together with an
`Origin` check, because `SameSite` alone leaves the exposure the issue names
(other same-site subdomains, older browsers, a future `SameSite=None`).
"""

from __future__ import annotations

import hmac
import secrets
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, abort, current_app, request, session
from werkzeug.wrappers import Response

_SESSION_KEY = "csrf_token"
_FIELD_NAME = "csrf_token"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def csrf_token() -> str:
    """The current session's token, generated once and reused for its life.

    Called from every form's hidden field and exposed to templates by the
    context processor `install` registers, so it works on the anonymous
    login page too: writing to `session` here is what makes Flask issue the
    cookie in the first place, signed-in or not.
    """
    token = session.get(_SESSION_KEY)
    if token is None:
        token = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def _same_origin(origin: str) -> bool:
    """Compare an `Origin` header's scheme and host to the request's own.

    `request.host_url` reflects `X-Forwarded-Host` once `ProxyFix` is
    trusted for the configured hop count (web/app.py `_trust_forwarding_headers`,
    F6-01), so this holds behind the instance's NGINX as well as directly.
    """
    theirs = urlsplit(origin)
    ours = urlsplit(request.host_url)
    return (theirs.scheme, theirs.netloc) == (ours.scheme, ours.netloc)


def verify_csrf() -> Response | None:
    """Refuse a state-changing request with no valid token or a foreign
    Origin, before it reaches the authorization gate or the view.

    Runs for every unsafe method, signed in or not -- the login form is one
    of the 21 sites this defends. A missing `Origin` header does not refuse
    by itself (older browsers do not send one); the token check is what
    unsafe requests still cannot skip.
    """
    endpoint = request.endpoint
    if (
        request.method not in _UNSAFE_METHODS
        or endpoint is None
        or endpoint == "static"
    ):
        return None

    origin = request.headers.get("Origin")
    if origin is not None and not _same_origin(origin):
        _refuse("origin mismatch", endpoint)

    expected = session.get(_SESSION_KEY)
    submitted = request.form.get(_FIELD_NAME)
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        _refuse("missing or invalid csrf token", endpoint)

    return None


def _refuse(reason: str, endpoint: str) -> None:
    current_app.logger.warning(
        "CSRF check failed: %s. endpoint=%s path=%s method=%s",
        reason,
        endpoint,
        request.path,
        request.method,
    )
    abort(403)


def _before_request() -> Response | None:
    """What Flask actually calls.

    A separate function from `verify_csrf`, so a test that stubs the check
    off (`tests/conftest.py`, mirroring `resolve_session`/`_principal_for` for
    ADR-0022) patches the module-level name and this looks it up fresh on
    every request -- not a reference to the original function `install`
    captured once at registration time, which a patch after `create_app` runs
    could never reach.
    """
    return verify_csrf()


def install(app: Flask) -> None:
    """Attach the check and expose `csrf_token()` to every template."""
    app.before_request(_before_request)

    @app.context_processor
    def csrf_context() -> dict[str, Any]:
        return {"csrf_token": csrf_token}
