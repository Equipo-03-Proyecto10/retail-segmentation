"""Authentication routes: login, logout.

Follows the Blueprint pattern established in web/routes/home.py.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, session, url_for

from web.db import get_connection
from web.services.auth import authenticate

bp = Blueprint("auth", __name__)

_GENERIC_ERROR = "Invalid email or password."


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    connection = get_connection()
    result = authenticate(connection, email, password)

    if not result.success:
        return render_template("auth/login.html", error=_GENERIC_ERROR), 401

    session.clear()
    session["user_id"] = str(result.user.user_id)
    session["role_id"] = result.user.role_id
    session["name"] = result.user.name

    return redirect(url_for("home.index"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home.index"))
