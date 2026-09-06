"""What the signed-in landing page reports (F3-12).

The shell shows real figures, and it shows only the figures the caller's
permissions already entitle them to. That is not the access control — the
authorization middleware refuses the routes themselves — but a dashboard that
told a loyalty customer how many products exist would be leaking exactly what
the permission matrix says they may not read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from web.db.audit import AuditEntry, recent_entries
from web.db.metrics import count_rows
from web.db.users import AppUser, get_user_by_id
from web.middleware.authz import AUDIT_READ, CATALOG_READ, USER_READ

RECENT_AUDIT_ENTRIES = 5


@dataclass(frozen=True)
class Figure:
    """One counted number, with the words to put next to it."""

    label: str
    value: int


@dataclass(frozen=True)
class Dashboard:
    """Everything the landing page renders for one signed-in visitor."""

    user: AppUser
    figures: tuple[Figure, ...]
    recent_audit: tuple[AuditEntry, ...]


def dashboard_for(
    connection: Connection[Any],
    user_id: str,
    permissions: frozenset[str],
) -> Dashboard | None:
    """Gather the landing page for this user, or None if they no longer exist.

    The user is read fresh rather than taken from the session, so a renamed or
    re-roled account shows what is true now rather than what was true at
    sign-in.
    """
    user = get_user_by_id(connection, user_id)
    if user is None:
        return None

    figures: list[Figure] = []
    if CATALOG_READ in permissions:
        figures.append(Figure("Customers", count_rows(connection, "customer")))
        figures.append(Figure("Products", count_rows(connection, "product")))
        figures.append(Figure("Stores", count_rows(connection, "store")))
    if USER_READ in permissions:
        figures.append(Figure("Active users", count_rows(connection, "app_user")))

    audit: tuple[AuditEntry, ...] = ()
    if AUDIT_READ in permissions:
        audit = tuple(recent_entries(connection, RECENT_AUDIT_ENTRIES))

    return Dashboard(user=user, figures=tuple(figures), recent_audit=audit)
