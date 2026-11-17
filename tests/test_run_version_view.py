"""`api_models.run_version_v1`: the version of each segmentation run (#812, ADR-0064).

segments-service reads it to say which model version wrote each side of a migration. The
published-version view cannot do that: it names the one version that is current, and a
migration needs the version of an older run.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (ROOT / "contracts/db/api_models.run_version.v1.md").read_text()

pytestmark = pytest.mark.postgres


def test_the_contract_names_its_object_producer_consumer_and_columns() -> None:
    assert "| Object | `api_models.run_version_v1` |" in CONTRACT
    assert "| Producer | model-registry |" in CONTRACT
    assert "| Consumers | segments |" in CONTRACT
    table = CONTRACT.split("## Columns", 1)[1].split("## Compatibility", 1)[0]
    assert re.findall(r"^\| `(\w+)` \|", table, re.M) == [
        "run_id",
        "version_id",
        "linked_at",
    ]


@pytest.fixture(scope="module")
def database(owner_url: str):
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


def _read_as(connection, role: str):
    with connection.cursor() as cur:
        cur.execute(f"SET ROLE {role}")
        try:
            cur.execute(
                "SELECT run_id, version_id, linked_at FROM api_models.run_version_v1"
            )
            return cur.fetchall()
        finally:
            cur.execute("RESET ROLE")


def test_the_view_is_the_registrys_link_row_for_row(database) -> None:
    columns = database.execute(
        "SELECT a.attname FROM pg_attribute a"
        " WHERE a.attrelid = 'api_models.run_version_v1'::regclass AND a.attnum > 0"
        " AND NOT a.attisdropped ORDER BY a.attnum"
    ).fetchall()
    assert [c[0] for c in columns] == ["run_id", "version_id", "linked_at"]
    linked = database.execute("SELECT count(*) FROM models.run_version").fetchone()[0]
    assert linked >= 30
    assert (
        database.execute(
            "SELECT count(*) FROM api_models.run_version_v1 v"
            " JOIN models.run_version t USING (run_id, version_id, linked_at)"
        ).fetchone()[0]
        == linked
    )


def test_segments_reads_it_and_no_other_service_does(database) -> None:
    assert _read_as(database, "svc_segments")
    for role in ("svc_recommendations", "svc_sales", "svc_experiments", "retail_app"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _read_as(database, role)


def test_segments_still_cannot_read_the_registrys_tables(database) -> None:
    with database.cursor() as cur:
        cur.execute("SET ROLE svc_segments")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT 1 FROM models.run_version")
        finally:
            cur.execute("RESET ROLE")
