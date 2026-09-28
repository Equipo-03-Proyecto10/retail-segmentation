"""One account per mailbox, whatever the letter case (#288)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web.db.users import get_user_by_email, update_password_hash
from web.services.users import create_user, normalize_email

SCHEMA = Path(__file__).resolve().parents[1] / "sql/01_schema.sql"


@pytest.mark.parametrize(
    "raw",
    ["USER2@mosaiq-demo.com", "User2@Mosaiq-Demo.com", " user2@mosaiq-demo.com "],
)
def test_every_spelling_of_a_mailbox_normalises_to_one(raw: str) -> None:
    assert normalize_email(raw) == "user2@mosaiq-demo.com"


def test_create_user_stores_the_normalised_address() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(2,), ("new-user-id",)]

    create_user(
        connection,
        name="New user",
        email=" New.User@Mosaiq-Demo.com ",
        password="Password123!",
        role_code="ANALYST",
    )

    _statement, parameters = cursor.execute.call_args_list[1].args
    assert parameters[2] == "new.user@mosaiq-demo.com"


def _statement(cursor) -> str:
    return " ".join(cursor.execute.call_args.args[0].lower().split())


def test_sign_in_compares_the_address_without_case() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None

    get_user_by_email(connection, "USER2@mosaiq-demo.com")

    assert "lower(u.email) = lower(%s)" in _statement(cursor)


def test_password_rotation_finds_the_account_without_case() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    update_password_hash(connection, "USER2@mosaiq-demo.com", "hash")

    assert "lower(email) = lower(%s)" in _statement(cursor)


def test_the_schema_enforces_one_account_per_mailbox_case_insensitively() -> None:
    schema = SCHEMA.read_text()
    table = schema.split("CREATE TABLE app_user (")[1].split("\n);")[0]

    assert "UNIQUE" not in table
    assert (
        "CREATE UNIQUE INDEX ux_app_user_email_lower ON app_user (lower(email));"
        in schema
    )
