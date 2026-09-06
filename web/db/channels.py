"""Data access for channel — full CRUD."""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection
from psycopg.errors import ForeignKeyViolation, UniqueViolation


@dataclass(frozen=True)
class Channel:
    channel_id: int
    name: str


def list_channels(
    connection: Connection, *, search: str | None, page: int, per_page: int
) -> tuple[list[Channel], int]:
    """Return a page of channels, optionally filtered by name, and the total
    row count for building pagination controls."""
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        if search:
            pattern = f"%{search}%"
            cursor.execute(
                "SELECT channel_id, name FROM channel WHERE name ILIKE %s ORDER BY channel_id LIMIT %s OFFSET %s",
                (pattern, per_page, offset),
            )
        else:
            cursor.execute(
                "SELECT channel_id, name FROM channel ORDER BY channel_id LIMIT %s OFFSET %s",
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute("SELECT count(*) FROM channel WHERE name ILIKE %s", (pattern,))
        else:
            cursor.execute("SELECT count(*) FROM channel")
        total = cursor.fetchone()[0]

    channels = [Channel(*row) for row in rows]
    return channels, total


def get_channel(connection: Connection, channel_id: int) -> Channel | None:
    """Return one channel by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT channel_id, name FROM channel WHERE channel_id = %s", (channel_id,)
        )
        row = cursor.fetchone()

    return Channel(*row) if row else None


def create_channel(connection: Connection, *, channel_id: int, name: str) -> str | None:
    """Insert a new channel. Returns None on success, or an error message on
    a duplicate id/name."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO channel (channel_id, name) VALUES (%s, %s)",
                (channel_id, name),
            )
        connection.commit()
        return None
    except UniqueViolation:
        connection.rollback()
        return "A channel with that ID or name already exists."


def update_channel(connection: Connection, channel_id: int, *, name: str) -> str | None:
    """Update a channel's name. Returns None on success, or an error message
    on a duplicate name."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE channel SET name = %s WHERE channel_id = %s", (name, channel_id)
            )
        connection.commit()
        return None
    except UniqueViolation:
        connection.rollback()
        return "A channel with that name already exists."


def delete_channel(connection: Connection, channel_id: int) -> bool:
    """Delete a channel. Returns True on success, False if referenced by
    other rows (customer, transaction, etc.)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM channel WHERE channel_id = %s", (channel_id,))
        connection.commit()
        return True
    except ForeignKeyViolation:
        connection.rollback()
        return False