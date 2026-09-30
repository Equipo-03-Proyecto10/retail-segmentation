"""Authentication routes: login, logout.

Follows the Blueprint pattern established in web/routes/home.py.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue

from web.db import get_connection
from web.middleware import public, requires
from web.middleware.authz import safe_next
from web.services.auth import authenticate, end_session, start_session
from web.services.login_throttle import account_key, client_key

bp = Blueprint("auth", __name__)

_GENERIC_ERROR = "Invalid email or password."
_THROTTLED_ERROR = "Too many sign-in attempts. Try again later."


def _throttle_keys(email: str) -> tuple[str, ...]:
    account = account_key(email)
    return tuple(key for key in (account, client_key(request.remote_addr)) if key)


@bp.route("/login", methods=["GET", "POST"])
@public
def login() -> ResponseReturnValue:
    # Where the visitor was going before the gate stopped them (RF-03). It
    # arrives in the query string on the redirect and travels back through the
    # form; `safe_next` refuses anything that is not a path on this site.
    destination = safe_next(request.values.get("next"))

    if request.method in ("GET", "HEAD"):
        return render_template("auth/login.html", next=destination)

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    throttle = current_app.extensions["login_throttle"]
    keys = _throttle_keys(email)
    decision = throttle.check(keys)
    if decision.blocked:
        current_app.logger.info("login_throttled retry_after=%s", decision.retry_after)
        response = render_template(
            "auth/login.html", error=_THROTTLED_ERROR, next=destination
        )
        return response, 429, {"Retry-After": str(decision.retry_after)}

    connection = get_connection()
    result = authenticate(connection, email, password)
    current_app.logger.info(
        "login_%s email=%r", "succeeded" if result.success else "refused", email[:254]
    )

    if not result.success:
        throttle.record_failure(keys)
        return (
            render_template("auth/login.html", error=_GENERIC_ERROR, next=destination),
            401,
        )

    # A valid account clears only its own failures. Clearing the client bucket
    # here would let a successful login for one account erase failed attempts
    # against every other account from the same address.
    account = account_key(email)
    if account is not None:
        throttle.clear((account,))
    session.clear()
    session.permanent = True
    session["sid"] = start_session(connection, result.user.user_id)
    session["user_id"] = str(result.user.user_id)
    session["role_id"] = result.user.role_id
    session["role_code"] = result.user.role_code
    session["name"] = result.user.name

    return redirect(destination or url_for("home.index"))


@bp.route("/logout", methods=["POST"])
@requires()
def logout() -> ResponseReturnValue:
    current_app.logger.info("logout_succeeded")
    # RF-02: revoke server-side first, so the cookie already in the browser
    # (or copied from it) stops working, not only the one sent back now.
    end_session(get_connection(), session.get("sid"))
    session.clear()
    return redirect(url_for("home.index"))
