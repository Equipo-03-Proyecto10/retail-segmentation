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
from web.services.auth import authenticate

bp = Blueprint("auth", __name__)

_GENERIC_ERROR = "Invalid email or password."


@bp.route("/login", methods=["GET", "POST"])
@public
def login() -> ResponseReturnValue:
    # Where the visitor was going before the gate stopped them (RF-03). It
    # arrives in the query string on the redirect and travels back through the
    # form; `safe_next` refuses anything that is not a path on this site.
    destination = safe_next(request.values.get("next"))

    if request.method == "GET":
        return render_template("auth/login.html", next=destination)

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    connection = get_connection()
    result = authenticate(connection, email, password)
    current_app.logger.info(
        "login_%s email=%r", "succeeded" if result.success else "refused", email[:254]
    )

    if not result.success:
        return (
            render_template("auth/login.html", error=_GENERIC_ERROR, next=destination),
            401,
        )

    session.clear()
    session["user_id"] = str(result.user.user_id)
    session["role_id"] = result.user.role_id
    session["role_code"] = result.user.role_code
    session["name"] = result.user.name

    return redirect(destination or url_for("home.index"))


@bp.route("/logout", methods=["POST"])
@requires()
def logout() -> ResponseReturnValue:
    current_app.logger.info("logout_succeeded")
    session.clear()
    return redirect(url_for("home.index"))
