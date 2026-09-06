"""Authorization: the permission matrix and the gate that enforces it (F4-01).

Every route declares what reaching it requires, and this module refuses
everything else *before the view function runs*. The declaration is the point:
a route with no declaration is refused too, so a page added tomorrow cannot be
left unprotected by forgetting a decorator. `tests/test_authz.py` walks the URL
map and fails the build when one is missing.

The matrix below transcribes `docs/requirements.md` §3, which stays the
canonical statement of who may do what. It lives in code rather than in a
`permission` table because it is a specification the team agrees on and
deploys, not data an administrator edits at runtime; the reasoning, and the
alternative that was rejected, are in
`docs/adr/0007-permissions-in-code-with-a-default-deny-middleware.md`.

**What this module does not do.** The matrix qualifies two cells with `own`
(a customer reaches their own row) and `read, own store` (a store manager reads
their own store's reports). Those are row scoping, not route gating, and the
gate here cannot express them: it decides whether a request reaches a view, not
which rows the view then selects. `CUSTOMER` therefore holds `user.self` rather
than `user.read`, and narrowing the query behind it belongs to the story that
writes the query — F3-05 (#65) and F3-11 (#103). A gate that appeared to filter
rows and did not would be worse than one that says plainly that it does not.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlsplit

from flask import (
    Flask,
    abort,
    current_app,
    has_request_context,
    redirect,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response

from web.db import set_connection_initializer
from web.db.audit import set_audit_actor

# ---------- the permission vocabulary ----------

# One name per (section, level) pair in the matrix. Kept as a flat set so a
# typo in a `@requires` call is a test failure rather than a silently
# unsatisfiable permission that locks a page for everybody.
CATALOG_READ = "catalog.read"
CATALOG_WRITE = "catalog.write"
INVENTORY_WRITE = "inventory.write"
USER_READ = "user.read"
USER_WRITE = "user.write"
USER_SELF = "user.self"
SEGMENT_READ = "segment.read"
SEGMENT_WRITE = "segment.write"
CAMPAIGN_READ = "campaign.read"
CAMPAIGN_WRITE = "campaign.write"
SEGMENT_RUN_EXECUTE = "segment_run.execute"
REPORT_READ = "report.read"
AUDIT_READ = "audit.read"

ALL_PERMISSIONS = frozenset(
    {
        CATALOG_READ,
        CATALOG_WRITE,
        INVENTORY_WRITE,
        USER_READ,
        USER_WRITE,
        USER_SELF,
        SEGMENT_READ,
        SEGMENT_WRITE,
        CAMPAIGN_READ,
        CAMPAIGN_WRITE,
        SEGMENT_RUN_EXECUTE,
        REPORT_READ,
        AUDIT_READ,
    }
)

# ---------- the matrix ----------

# Keyed by `role.code` as seeded in sql/02_seed_30_per_table.sql. A role added
# to the seed without a row here holds nothing and reaches nothing, which is
# the safe direction; the test that compares these keys against the seed makes
# it visible rather than mysterious.
PERMISSIONS: dict[str, frozenset[str]] = {
    "ADMIN": frozenset(
        {
            CATALOG_READ,
            CATALOG_WRITE,
            USER_READ,
            USER_WRITE,
            SEGMENT_READ,
            SEGMENT_WRITE,
            CAMPAIGN_READ,
            CAMPAIGN_WRITE,
            SEGMENT_RUN_EXECUTE,
            REPORT_READ,
            AUDIT_READ,
            INVENTORY_WRITE,
        }
    ),
    "ANALYST": frozenset({CATALOG_READ, SEGMENT_READ, CAMPAIGN_READ, REPORT_READ}),
    "STORE_MANAGER": frozenset({CATALOG_READ, REPORT_READ}),
    "MARKETING": frozenset(
        {
            CATALOG_READ,
            SEGMENT_READ,
            CAMPAIGN_READ,
            CAMPAIGN_WRITE,
            REPORT_READ,
        }
    ),
    "INVENTORY_PLANNER": frozenset({CATALOG_READ, INVENTORY_WRITE, REPORT_READ}),
    "AUDITOR": frozenset(
        {
            CATALOG_READ,
            USER_READ,
            SEGMENT_READ,
            CAMPAIGN_READ,
            REPORT_READ,
            AUDIT_READ,
        }
    ),
    # `user.self` is the matrix's `own` in the Users column: the loyalty
    # customer reaches their own row and nothing else. It is deliberately not
    # granted to ADMIN, who holds the section outright — a page every signed-in
    # user may open, whatever their role, declares `@requires()` instead.
    "CUSTOMER": frozenset({USER_SELF}),
}


def permissions_for(role_code: str | None) -> frozenset[str]:
    """What a role may do. An unknown or absent role holds nothing."""
    if role_code is None:
        return frozenset()
    return PERMISSIONS.get(role_code, frozenset())


# ---------- what a route declares ----------

AUTHZ_ATTRIBUTE = "__authz_requirement__"

View = TypeVar("View", bound=Callable[..., Any])


@dataclass(frozen=True)
class Requirement:
    """What reaching one view demands.

    `anonymous_allowed` is the sign-in page and the landing page: everything
    else needs a session, and needs every permission in `permissions`.
    """

    anonymous_allowed: bool
    permissions: frozenset[str]


def public(view: View) -> View:
    """Declare a view reachable without signing in.

    Written out deliberately rather than assumed: the gate refuses anything
    undeclared, so making a page public is a decision somebody makes and a
    reviewer sees in the diff.
    """
    setattr(view, AUTHZ_ATTRIBUTE, Requirement(True, frozenset()))
    return view


def requires(*permissions: str) -> Callable[[View], View]:
    """Declare the permissions a view demands. No arguments means signed in."""
    unknown = set(permissions) - ALL_PERMISSIONS
    if unknown:
        raise ValueError(
            f"Unknown permission(s): {', '.join(sorted(unknown))}. "
            f"Permissions come from the matrix in web/middleware/authz.py."
        )

    def declare(view: View) -> View:
        setattr(view, AUTHZ_ATTRIBUTE, Requirement(False, frozenset(permissions)))
        return view

    return declare


def requirement_of(view: Callable[..., Any] | None) -> Requirement | None:
    """The declaration stamped on a view, or None when it carries none."""
    if view is None:
        return None
    requirement = getattr(view, AUTHZ_ATTRIBUTE, None)
    return requirement if isinstance(requirement, Requirement) else None


# ---------- the signed-in user ----------


def is_signed_in() -> bool:
    return bool(session.get("user_id"))


def current_role_code() -> str | None:
    return session.get("role_code")


def current_permissions() -> frozenset[str]:
    """What the signed-in user may do. Nobody signed in means nothing."""
    if not is_signed_in():
        return frozenset()
    return permissions_for(current_role_code())


def can(permission: str) -> bool:
    """Template-facing check, used to drive the menu."""
    return permission in current_permissions()


# ---------- returning where the visitor was going (RF-03) ----------


def safe_next(target: str | None) -> str | None:
    """Accept a same-site path only.

    `next` arrives from the query string and a form field, which means it
    arrives from whoever wrote the link. Anything with a scheme, a host, or a
    leading `//` — the protocol-relative form a browser reads as a host — would
    turn the sign-in page into an open redirect, so only a plain absolute path
    on this site is honoured.
    """
    if not target:
        return None
    if "\\" in target or "\r" in target or "\n" in target:
        return None

    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    if not parts.path.startswith("/") or parts.path.startswith("//"):
        return None

    return target


def _current_target() -> str:
    """The path the refused request was asking for, query string included."""
    return request.full_path if request.query_string else request.path


# ---------- the menu (driven by permissions, not by role) ----------


@dataclass(frozen=True)
class MenuEntry:
    """One navigation entry: a label, the endpoint, and what it demands."""

    label: str
    endpoint: str
    permission: str | None


@dataclass(frozen=True)
class MenuItem:
    """A navigation entry as one visitor sees it, on one page."""

    label: str
    endpoint: str
    is_current: bool


# One entry per section of the matrix in docs/requirements.md §3, named for the
# endpoint the story that builds it registers. An entry whose blueprint has not
# landed yet is skipped, so this list is a plan the menu grows into rather than
# a set of broken links: F3-04 (#64) lights up Catalogs, F3-06 (#66) Users,
# F3-10 (#102) the segment run, F3-11 (#103) the audit log. The shell itself is
# F3-12 (#106); what lives here is the permission filtering it inherits.
NAVIGATION: tuple[MenuEntry, ...] = (
    MenuEntry("Home", "home.index", None),
    MenuEntry("Catalogs", "catalog.index", CATALOG_READ),
    MenuEntry("Users", "users.index", USER_READ),
    MenuEntry("Segments", "segments.index", SEGMENT_READ),
    MenuEntry("Campaigns", "campaigns.index", CAMPAIGN_READ),
    MenuEntry("Segment run", "segment_run.index", SEGMENT_RUN_EXECUTE),
    MenuEntry("Reports", "reports.index", REPORT_READ),
    MenuEntry("Audit log", "audit.index", AUDIT_READ),
)


def menu() -> list[MenuItem]:
    """The entries this visitor may reach, in declaration order.

    The current section is marked by blueprint rather than by endpoint, so a
    detail page inside a section still marks the section it belongs to.
    """
    granted = current_permissions()
    registered = current_app.view_functions
    section = request.blueprint if has_request_context() else None

    return [
        MenuItem(
            label=entry.label,
            endpoint=entry.endpoint,
            is_current=entry.endpoint.split(".")[0] == section,
        )
        for entry in NAVIGATION
        if entry.endpoint in registered
        and (entry.permission is None or entry.permission in granted)
    ]


# ---------- the gate ----------


def _acting_user_id() -> str | None:
    """Who the audit triggers should credit, or None when nobody is acting.

    A connection is not always opened by a request: a maintenance script and
    the application's own startup probe run inside an application context with
    no session at all, and there the actor is genuinely unknown rather than
    anonymous-but-present.
    """
    if not has_request_context():
        return None
    return session.get("user_id")


def _refuse(reason: str, endpoint: str) -> None:
    """Log one line naming who was refused, where, and why, then abort."""
    current_app.logger.warning(
        "Access denied: %s. user=%s role=%s endpoint=%s path=%s method=%s",
        reason,
        session.get("user_id", "anonymous"),
        current_role_code() or "none",
        endpoint,
        request.path,
        request.method,
    )
    abort(403)


def authorize() -> Response | None:
    """Refuse the request before the view runs, unless it is allowed.

    Registered as a `before_request` hook, after the one that assigns the
    request id, so a refusal and the page the visitor sees quote the same
    reference.
    """
    endpoint = request.endpoint

    # No endpoint means routing found nothing: Flask's 404 handler answers.
    # `static` is Flask's own file server and carries no declaration.
    if endpoint is None or endpoint == "static":
        return None

    requirement = requirement_of(current_app.view_functions.get(endpoint))

    if requirement is None:
        # Default-deny, and the reason this middleware exists. The log line is
        # an error rather than a warning because it is a defect in the route,
        # not a visitor doing something they may not.
        current_app.logger.error(
            "Endpoint %s declares no authorization requirement and was refused. "
            "Decorate the view with @public or @requires(...) in "
            "web/middleware/authz.py terms.",
            endpoint,
        )
        abort(403)

    if requirement.anonymous_allowed:
        return None

    if not is_signed_in():
        # A GET can be replayed after signing in; a POST body cannot survive
        # the round trip, so it is refused rather than silently dropped.
        if request.method == "GET":
            return redirect(url_for("auth.login", next=_current_target()))
        _refuse("not signed in", endpoint)

    missing = requirement.permissions - current_permissions()
    if missing:
        _refuse(f"missing {', '.join(sorted(missing))}", endpoint)

    return None


def install(app: Flask) -> None:
    """Attach the gate and the template values the menu is built from."""
    app.before_request(authorize)

    # The audit triggers in sql/01_schema.sql read `mosaiq.user_id` off the
    # connection, and only this layer knows who is signed in. Without this the
    # log would record every change the application makes as unattributed,
    # which is the one thing an audit trail may not do (RF-14).
    set_connection_initializer(
        app, lambda connection: set_audit_actor(connection, _acting_user_id())
    )

    @app.context_processor
    def authorization_context() -> dict[str, Any]:
        """A template asks what the visitor may do, never what role they are."""
        return {
            "menu": menu(),
            "can": can,
            "signed_in": is_signed_in(),
            "current_user_name": session.get("name"),
        }
