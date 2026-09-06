"""Session-cookie hardening (F4-04, RNF-05).

The application starts issuing a session cookie the moment F3-03 adds login.
These are the attributes that keep that cookie from being read by page scripts,
carried on cross-site requests, or sent in clear text.
"""

from __future__ import annotations

from flask import Flask

from web.config import Config


def configure_session(app: Flask) -> None:
    """Apply the RNF-05 session-cookie attributes to `app`."""
    config: Config = app.config["APP_CONFIG"]
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,  # not reachable from JavaScript
        SESSION_COOKIE_SAMESITE="Lax",  # not sent on cross-site form posts
        # `Secure` is configuration, not inference: NGINX terminates TLS in
        # front of the app (F6-01/F6-03), so the process only ever sees plain
        # HTTP and cannot tell whether the browser used HTTPS.
        SESSION_COOKIE_SECURE=config.session_cookie_secure,
    )
