"""Data access for channel — full CRUD."""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection


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
                """
                SELECT channel_id, name
                FROM channel
                WHERE name ILIKE %s
                ORDER BY channel_id
                LIMIT %s OFFSET %s
                """,
                (pattern, per_page, offset),
            )
        else:
            cursor.execute(
                """
                SELECT channel_id, name
                FROM channel
                ORDER BY channel_id
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute(
                "SELECT count(*) FROM channel WHERE name ILIKE %s", (pattern,)
            )
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


def create_channel(connection: Connection, *, channel_id: int, name: str) -> None:
    """Create one channel; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO channel (channel_id, name) VALUES (%s, %s)",
            (channel_id, name),
        )


def update_channel(connection: Connection, channel_id: int, *, name: str) -> None:
    """Update one channel; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE channel SET name = %s WHERE channel_id = %s", (name, channel_id)
        )


def delete_channel(connection: Connection, channel_id: int) -> None:
    """Delete one channel; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM channel WHERE channel_id = %s", (channel_id,))
