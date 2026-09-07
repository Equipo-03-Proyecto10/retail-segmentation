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
from datetime import date, datetime
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


@dataclass(frozen=True)
class AuditEntryDetail:
    """One entry with the payloads the triggers captured.

    `data_before` and `data_after` are whatever `fn_audit()` wrote: the row as
    jsonb, minus `password_hash`, which the trigger strips before the entry is
    ever written. An INSERT has no before, a DELETE has no after.
    """

    audit_id: int
    entity: str
    entity_pk: str
    action: str
    actor_name: str | None
    executed_at: datetime
    data_before: dict[str, Any] | None
    data_after: dict[str, Any] | None


def count_entries(
    connection: Connection[Any],
    *,
    entity: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> int:
    """How many entries match the filters, for the pagination footer."""
    parameters = {"entity": entity or None, "date_from": date_from, "date_to": date_to}
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT count(*) FROM audit_log AS a
            WHERE (%(entity)s::text IS NULL OR a.entity = %(entity)s)
              AND (%(date_from)s::date IS NULL OR a.executed_at >= %(date_from)s)
              AND (%(date_to)s::date IS NULL
                   OR a.executed_at < %(date_to)s::date + INTERVAL '1 day')
            """,
            parameters,
        )
        return int(cursor.fetchone()[0])


def search_entries(
    connection: Connection[Any],
    *,
    entity: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int,
    offset: int,
) -> list[AuditEntry]:
    """One page of entries, newest first."""
    parameters = {"entity": entity or None, "date_from": date_from, "date_to": date_to}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.audit_id, a.entity, a.entity_pk, a.action,
                   u.name, a.executed_at
            FROM audit_log AS a
            LEFT JOIN app_user AS u ON u.user_id = a.user_id
            WHERE (%(entity)s::text IS NULL OR a.entity = %(entity)s)
              AND (%(date_from)s::date IS NULL OR a.executed_at >= %(date_from)s)
              AND (%(date_to)s::date IS NULL
                   OR a.executed_at < %(date_to)s::date + INTERVAL '1 day')
            ORDER BY a.audit_id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            parameters | {"limit": limit, "offset": offset},
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


def get_entry(connection: Connection[Any], audit_id: int) -> AuditEntryDetail | None:
    """One entry with its payloads, or None when there is no such entry."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.audit_id, a.entity, a.entity_pk, a.action,
                   u.name, a.executed_at, a.data_before, a.data_after
            FROM audit_log AS a
            LEFT JOIN app_user AS u ON u.user_id = a.user_id
            WHERE a.audit_id = %s
            """,
            (audit_id,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return AuditEntryDetail(
        audit_id=row[0],
        entity=row[1],
        entity_pk=row[2],
        action=row[3],
        actor_name=row[4],
        executed_at=row[5],
        data_before=row[6],
        data_after=row[7],
    )


def audited_entities(connection: Connection[Any]) -> list[str]:
    """The entities the log actually holds, for the filter's options."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT entity FROM audit_log ORDER BY entity")
        return [row[0] for row in cursor.fetchall()]
