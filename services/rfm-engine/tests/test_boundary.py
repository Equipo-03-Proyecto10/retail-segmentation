"""The rfm boundary of sql/01_schema.sql: its role holds the manifest and no more."""

from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg
import pytest

ROLE = "svc_rfm"
GRANTS = Path(__file__).resolve().parents[3] / "contracts/db/grants.csv"


def test_the_manifest_names_what_the_profile_needs_and_no_more() -> None:
    with GRANTS.open(newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["role"] == ROLE]
    updates = {
        f"rfm.customer_profile({column})"
        for column in (
            "state rule_set_id r_days f_count m_total r_score f_score m_score"
            " label_code via_fallback computed_at next_change_at"
        ).split()
    }
    assert {(r["object"], r["privilege"]) for r in rows} == {
        ("rfm", "USAGE"),
        ("rfm_events", "USAGE"),
        ("api_rfm", "USAGE"),
        ("api_sales", "USAGE"),
        ("api_sales.purchase_line_v1", "SELECT"),
        ("api_sales.net_purchase_line_v1", "SELECT"),
        ("rfm.rule_set", "SELECT"),
        ("rfm.rule_set_band", "SELECT"),
        ("rfm.customer_profile", "SELECT"),
        ("rfm.customer_profile", "INSERT"),
        ("rfm_events.outbox", "SELECT"),
        ("rfm_events.outbox", "DELETE"),
        ("rfm_events.outbox(relayed_at)", "UPDATE"),
    } | {(column, "UPDATE") for column in updates}


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
