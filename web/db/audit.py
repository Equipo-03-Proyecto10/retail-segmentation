"""The audit log: telling PostgreSQL who is acting, and reading back what it recorded.

`fn_audit()` in sql/01_schema.sql reads `mosaiq.user_id` from the connection
and writes it as `audit_log.user_id`. Nothing else fills that column: the
triggers fire inside the database, where the Flask session does not reach, so
the actor has to be handed over explicitly on the connection the request uses.

Set to an empty string when nobody is signed in, which the trigger's
`NULLIF(..., '')::uuid` turns into a NULL actor — the honest answer for a
seed or a maintenance script, and the reason the column is nullable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import Connection

AUDIT_ACTOR_SETTING = "mosaiq.user_id"


def set_audit_actor(connection: Connection[Any], user_id: str | None) -> None:
    """Name the acting user on `connection` for the audit triggers.

    `set_config` rather than `SET`: a `SET` statement cannot take a parameter,
    and building it by interpolation would put a request value into SQL text,
    which is the one thing AGENTS.md never allows.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config(%s, %s, false)",
            (AUDIT_ACTOR_SETTING, user_id or ""),
        )


@dataclass(frozen=True)
class AuditEntry:
    """One row of the append-only log, with the actor's name resolved.

    `actor_name` is None when `audit_log.user_id` is NULL, which is the honest
    state for everything the seed and any maintenance script did. The view
    renders that as unattributed rather than inventing an actor.
    """

    audit_id: int
    entity: str
    entity_pk: str
    action: str
    actor_name: str | None
    executed_at: datetime


def recent_entries(connection: Connection[Any], limit: int) -> list[AuditEntry]:
    """The newest entries, most recent first."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.audit_id, a.entity, a.entity_pk, a.action,
                   u.name, a.executed_at
            FROM audit_log AS a
            LEFT JOIN app_user AS u ON u.user_id = a.user_id
            ORDER BY a.audit_id DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()

    return [
        AuditEntry(
            audit_id=row[0],
            entity=row[1],
            entity_pk=row[2],
            action=row[3],
            actor_name=row[4],
            executed_at=row[5],
        )
        for row in rows
    ]
