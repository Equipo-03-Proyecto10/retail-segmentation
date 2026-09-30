"""Session-cookie hardening (F4-04, RNF-05).

The application starts issuing a session cookie the moment F3-03 adds login.
These are the attributes that keep that cookie from being read by page scripts,
carried on cross-site requests, or sent in clear text.
"""

from __future__ import annotations

from datetime import timedelta

from flask import Flask, Response

from web.config import Config

HSTS_VALUE = "max-age=31536000; includeSubDomains"
SECURITY_HEADERS = {
    "Strict-Transport-Security": HSTS_VALUE,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


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
        # The database is authoritative for both limits. This browser-side
        # expiry is an additional bound for copied cookies and is not refreshed
        # on every request, so activity cannot turn an absolute session into a
        # rolling one.
        PERMANENT_SESSION_LIFETIME=timedelta(
            seconds=config.session_absolute_timeout_seconds
        ),
        SESSION_REFRESH_EACH_REQUEST=False,
    )


def configure_security_headers(app: Flask) -> None:
    """Set browser security policy at the application boundary.

    NGINX repeats these headers at the deployment boundary. Keeping the Flask
    copy means direct Gunicorn, test, and alternate-proxy responses retain the
    same policy instead of relying on one particular serving path.
    """

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response
