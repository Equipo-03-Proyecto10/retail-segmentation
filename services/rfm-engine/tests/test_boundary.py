"""The rfm boundary of sql/01_schema.sql: its role holds the manifest and no more."""

from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg
import pytest

ROLE = "svc_rfm"
GRANTS = Path(__file__).resolve().parents[3] / "contracts/db/grants.csv"


def test_the_manifest_names_only_schema_usage_until_a_table_exists() -> None:
    with GRANTS.open(newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["role"] == ROLE]
    assert {(r["object"], r["privilege"]) for r in rows} == {
        ("rfm", "USAGE"),
        ("rfm_events", "USAGE"),
        ("api_rfm", "USAGE"),
    }


@pytest.mark.postgres
def test_the_role_exists_and_its_schemas_are_closed_to_public() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    with psycopg.connect(url) as connection:
        role = connection.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole"
            " FROM pg_roles WHERE rolname = %s",
            (ROLE,),
        ).fetchone()
        assert role == (True, False, False, False)
        for schema in ("rfm", "rfm_events", "api_rfm"):
            allowed = connection.execute(
                "SELECT has_schema_privilege(%s, %s, 'USAGE'),"
                " has_schema_privilege('public', %s, 'USAGE')",
                (ROLE, schema, schema),
            ).fetchone()
            assert allowed == (True, False)
