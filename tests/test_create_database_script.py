"""00_create_database.sql never prints the application role's password.

A `SELECT set_config(..., :'app_password', ...)` returns the value as a result
row, and psql prints it to whoever runs the script — on the instance, into the
operator's terminal. The password reaches the DO block through `SET` instead.
"""

from __future__ import annotations

from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / "sql/00_create_database.sql"
).read_text()


def _statements_with_the_password() -> list[str]:
    return [
        line.strip()
        for line in SCRIPT.splitlines()
        if ":'app_password'" in line and not line.lstrip().startswith("--")
    ]


def test_the_password_is_only_ever_set_never_selected() -> None:
    assert _statements_with_the_password() == [
        "SET mosaiq.app_password = :'app_password';"
    ]


def test_the_password_leaves_the_session_after_the_role_is_set() -> None:
    assert SCRIPT.index("END\n$$;") < SCRIPT.index("RESET mosaiq.app_password;")
