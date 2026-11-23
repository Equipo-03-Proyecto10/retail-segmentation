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
    pass_updates = {
        f"rfm.pass_run({column})"
        for column in "status finished_at customers_examined customers_changed".split()
    }
    recalculation_updates = {
        f"rfm.recalculation({column})"
        for column in (
            "state finished_at customers_checked customers_different failure_code"
        ).split()
    }
    rule_set_updates = {
        f"rfm.rule_set({column})"
        for column in (
            "name window_days recency_boundaries frequency_boundaries"
            " monetary_boundaries fallback_label_code cancelled_sales"
            " returned_sales state published_at"
        ).split()
    }
    assert {(r["object"], r["privilege"]) for r in rows} == {
        ("rfm", "USAGE"),
        ("rfm_events", "USAGE"),
        ("api_rfm", "USAGE"),
        ("api_sales", "USAGE"),
        ("api_sales.purchase_line_v1", "SELECT"),
        ("api_sales.net_purchase_line_v1", "SELECT"),
        ("api_customers", "USAGE"),
        ("api_customers.customer_ref_v1", "SELECT"),
        ("api_audit", "USAGE"),
        (
            "api_audit.record_event_v1(uuid,text,text,timestamp with time zone,text,"
            "uuid,text,smallint,text,text,bigint,bigint,text,jsonb)",
            "EXECUTE",
        ),
        ("api_segments", "USAGE"),
        (
            "api_segments.apply_assignment_v1(uuid,uuid,character varying,smallint,"
            "smallint,smallint,timestamp with time zone,integer,numeric,text,bigint,"
            "text,integer,bigint)",
            "EXECUTE",
        ),
        ("api_preferences", "USAGE"),
        ("api_preferences.consent_state_v1", "SELECT"),
        ("api_models", "USAGE"),
        ("api_models.published_version_v1", "SELECT"),
        ("rfm.rule_set", "SELECT"),
        ("rfm.rule_set", "INSERT"),
        ("rfm.rule_set_band", "SELECT"),
        ("rfm.rule_set_band", "INSERT"),
        ("rfm.rule_set_band", "DELETE"),
        ("rfm.customer_profile", "SELECT"),
        ("rfm.customer_profile", "INSERT"),
        ("rfm.pass_run", "SELECT"),
        ("rfm.pass_run", "INSERT"),
        ("rfm.recalculation", "SELECT"),
        ("rfm.recalculation", "INSERT"),
        ("rfm.recalculation_difference", "SELECT"),
        ("rfm.recalculation_difference", "INSERT"),
        ("rfm_events.outbox", "SELECT"),
        ("rfm_events.outbox", "DELETE"),
        ("rfm_events.outbox(relayed_at)", "UPDATE"),
    } | {
        (column, "UPDATE")
        for column in updates | pass_updates | rule_set_updates | recalculation_updates
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
