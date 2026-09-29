"""The application role cannot rewrite the audit log (#290, RN-30, ADR-0024)."""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA = (Path(__file__).resolve().parents[1] / "sql/01_schema.sql").read_text()
REVOKE = "REVOKE UPDATE, DELETE ON audit_log FROM retail_app;"


def test_the_schema_revokes_update_and_delete_from_the_application_role() -> None:
    assert REVOKE in SCHEMA


def test_the_revoke_comes_after_the_table_it_names() -> None:
    assert SCHEMA.index("CREATE TABLE audit_log") < SCHEMA.index(REVOKE)


def test_the_self_test_excepts_audit_log_from_the_all_privileges_check() -> None:
    assert re.search(r"c\.relname NOT IN \([^)]*'audit_log'", SCHEMA)


def test_the_self_test_proves_update_and_delete_are_refused() -> None:
    assert "PASS: UPDATE audit_log refused" in SCHEMA
    assert "PASS: DELETE audit_log refused" in SCHEMA
