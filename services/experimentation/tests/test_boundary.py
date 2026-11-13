"""The experiments boundary of sql/01_schema.sql: the role holds the manifest only."""

from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg
import pytest

ROLE = "svc_experiments"
GRANTS = Path(__file__).resolve().parents[3] / "contracts/db/grants.csv"
# ADR-0026, ADR-0027 and ADR-0050: the facts are append-only for every role that
# is not the owner. An experiment and its arms are edited before assignment
# (ADR-0019) and never deleted (ADR-0029).
EXPECTED = {
    ("experiments", "USAGE"),
    ("api_experiments", "USAGE"),
    ("api_audit", "USAGE"),
    (
        "api_audit.record_event_v1(uuid,text,text,timestamp with time zone,text,uuid,"
        "text,smallint,text,text,bigint,bigint,text,jsonb)",
        "EXECUTE",
    ),
    ("public.experiment", "SELECT"),
    ("public.experiment", "INSERT"),
    ("public.experiment", "UPDATE"),
    ("public.experiment_group", "SELECT"),
    ("public.experiment_group", "INSERT"),
    ("public.experiment_group", "UPDATE"),
    ("public.experiment_assignment", "SELECT"),
    ("public.experiment_assignment", "INSERT"),
    ("public.experiment_exposure", "SELECT"),
    ("public.experiment_exposure", "INSERT"),
    ("public.experiment_conversion", "SELECT"),
    ("public.experiment_conversion", "INSERT"),
}
FACTS = ("experiment_assignment", "experiment_exposure", "experiment_conversion")


def test_the_manifest_names_the_schemas_and_the_five_experiment_tables() -> None:
    with GRANTS.open(newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["role"] == ROLE]
    assert {(r["object"], r["privilege"]) for r in rows} == EXPECTED


def _connect() -> psycopg.Connection:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return psycopg.connect(url)


@pytest.mark.postgres
def test_the_role_exists_and_its_schemas_are_closed_to_public() -> None:
    with _connect() as connection:
        role = connection.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole"
            " FROM pg_roles WHERE rolname = %s",
            (ROLE,),
        ).fetchone()
        assert role == (True, False, False, False)
        for schema in ("experiments", "api_experiments"):
            allowed = connection.execute(
                "SELECT has_schema_privilege(%s, %s, 'USAGE'),"
                " has_schema_privilege('public', %s, 'USAGE')",
                (ROLE, schema, schema),
            ).fetchone()
            assert allowed == (True, False)


@pytest.mark.postgres
def test_the_facts_are_append_only_and_nothing_is_deleted_by_the_role() -> None:
    with _connect() as connection:
        for table in FACTS:
            held = connection.execute(
                "SELECT has_table_privilege(%s, %s, 'INSERT'),"
                " has_table_privilege(%s, %s, 'UPDATE'),"
                " has_table_privilege(%s, %s, 'DELETE'),"
                " has_table_privilege(%s, %s, 'TRUNCATE')",
                (ROLE, f"public.{table}") * 4,
            ).fetchone()
            assert held == (True, False, False, False), table
        for table in ("experiment", "experiment_group"):
            held = connection.execute(
                "SELECT has_table_privilege(%s, %s, 'DELETE'),"
                " has_table_privilege(%s, %s, 'TRUNCATE')",
                (ROLE, f"public.{table}") * 2,
            ).fetchone()
            assert held == (False, False), table
