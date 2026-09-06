"""Telling PostgreSQL who is acting, so the audit triggers can record it.

`fn_audit()` in sql/01_schema.sql reads `mosaiq.user_id` from the connection
and writes it as `audit_log.user_id`. Nothing else fills that column: the
triggers fire inside the database, where the Flask session does not reach, so
the actor has to be handed over explicitly on the connection the request uses.

Set to an empty string when nobody is signed in, which the trigger's
`NULLIF(..., '')::uuid` turns into a NULL actor — the honest answer for a
seed or a maintenance script, and the reason the column is nullable.
"""

from __future__ import annotations

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
