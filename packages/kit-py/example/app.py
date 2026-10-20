"""Minimal contract-driven service used by kit smoke tests."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from mosaiq_kit import ApiError, Kit, Page, Representation, configure_logging, money


def create_app() -> Flask:
    app = Flask(__name__)
    configure_logging()
    holder: dict[str, Kit] = {}

    def database_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            return connection.execute("SELECT %s", (1,)).fetchone() == (1,)

    kit = Kit(
        app,
        service="kit-example",
        suffix="kit_example",
        contracts_dir=Path(__file__).parent / "contracts",
        ready_checks=[database_ready],
    )
    holder["kit"] = kit

    @kit.operation("listNotes")
    def list_notes() -> Page:
        offset = (kit.page - 1) * kit.size
        with kit.db.connection() as connection:
            rows = connection.execute(
                "SELECT value, %s || value::text, (value::numeric / %s), "
                "NULL::text, now() "
                "FROM generate_series(%s::integer, %s::integer) AS value "
                "ORDER BY value LIMIT %s OFFSET %s",
                ("Note ", 100, 1, 45, kit.size, offset),
            ).fetchall()
        notes = [
            {
                "note_id": row[0],
                "title": row[1],
                "amount": money(row[2]),
                "created_by": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]
        return Page(
            "Notes",
            "Note",
            "notes",
            notes,
            page=kit.page,
            size=kit.size,
            total=45,
        )

    @kit.operation("getNote")
    def get_note(note_id: int) -> Representation:
        with kit.db.connection() as connection:
            row = connection.execute(
                "SELECT %s, %s || %s::text, (%s::numeric / %s), "
                "NULL::text, now() WHERE %s BETWEEN %s AND %s",
                (note_id, "Note ", note_id, note_id, 100, note_id, 1, 45),
            ).fetchone()
        if row is None:
            raise ApiError(404, "note_not_found", "The note does not exist.")
        return Representation(
            "Note",
            {
                "note_id": row[0],
                "title": row[1],
                "amount": money(row[2]),
                "created_by": row[3],
                "created_at": row[4],
            },
        )

    @kit.operation("createNote")
    def create_note() -> Representation:
        with kit.db.transaction() as connection:
            created_by, created_at = connection.execute(
                "SELECT current_setting(%s, true), now()",
                ("mosaiq.user_id",),
            ).fetchone()
        return Representation(
            "Note",
            {
                "note_id": 46,
                "title": kit.body["title"],
                "amount": kit.body["amount"],
                "created_by": created_by,
                "created_at": created_at,
            },
            status=201,
            headers={"Location": "/v1/notes/46"},
        )

    return app


app = create_app()
