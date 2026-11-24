"""`api_models.run_version_v1`: the version of each segmentation run (#812, ADR-0064).

segments-service reads it to say which model version wrote each side of a migration. The
published-version view cannot do that: it names the one version that is current, and a
migration needs the version of an older run.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (ROOT / "contracts/db/api_models.run_version.v1.md").read_text()
LINK_CONTRACT = (ROOT / "contracts/db/api_models.link_run_version.v1.md").read_text()
SECTION = (
    (ROOT / "sql/01_schema.sql")
    .read_text()
    .split("-- BEGIN BOUNDARY model-registry\n", 1)[1]
    .split("-- END BOUNDARY model-registry", 1)[0]
)
# The backfill of the runs rfm-engine's changes recorded before they were linked
# (ADR-0092, #1208), exactly as the operator's reapply of the section runs it.
_BACKFILL = re.search(
    r"^INSERT INTO models\.run_version \(run_id, version_id\)\nSELECT .*?;$",
    SECTION,
    re.M | re.S,
)
assert _BACKFILL is not None
BACKFILL = _BACKFILL.group(0)

pytestmark = pytest.mark.postgres


def test_the_contract_names_its_object_producer_consumer_and_columns() -> None:
    assert "| Object | `api_models.run_version_v1` |" in CONTRACT
    assert "| Producer | model-registry |" in CONTRACT
    assert "| Consumers | segments, recommendations, analytics-kpis |" in CONTRACT
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


def test_its_three_consumers_read_it_and_no_other_service_does(database) -> None:
    assert _read_as(database, "svc_segments")
    # recommendations reads the model version of a /v2 decision (#827).
    assert _read_as(database, "svc_recommendations")
    # analytics-kpis names the versions behind a figure grouped by segment (F26-03).
    assert _read_as(database, "svc_kpis")
    for role in ("svc_sales", "svc_experiments", "retail_app"):
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


def test_the_link_function_contract_names_its_object_owner_and_caller() -> None:
    assert "| Object | `api_models.link_run_version_v1` |" in LINK_CONTRACT
    assert "| Owning boundary | model-registry |" in LINK_CONTRACT
    assert "| Consumer | segments (`definer_segments`) |" in LINK_CONTRACT
    assert "`api_models.link_run_version_v1(bigint,bigint)`" in LINK_CONTRACT


def test_only_the_function_that_records_runs_may_link_one(database) -> None:
    callers = {
        row[0]
        for row in database.execute(
            "SELECT r.rolname FROM pg_roles AS r"
            " WHERE (starts_with(r.rolname, 'svc_')"
            "        OR starts_with(r.rolname, 'definer_')"
            "        OR r.rolname = 'retail_app')"
            " AND has_function_privilege(r.oid,"
            "     'api_models.link_run_version_v1(bigint,bigint)', 'EXECUTE')"
        ).fetchall()
    }
    assert callers == {"definer_models", "definer_segments"}


def _pending_run(connection, parameters: dict) -> int:
    """A run recorded the way apply_assignment_v1 recorded one before #1208: its
    version in its parameters and no link."""
    return connection.execute(
        "INSERT INTO public.segmentation_run"
        " (method, window_days, parameters, customer_count, data_origin, scope)"
        " VALUES ('RFM_RULES', 180, %s, 1,"
        "         public.fn_segmentation_window_origin(180), 'INCREMENTAL')"
        " RETURNING run_id",
        (Jsonb(parameters),),
    ).fetchone()[0]


def test_the_backfill_links_each_pending_incremental_run_once(owner_url) -> None:
    with psycopg.connect(owner_url) as connection:
        try:
            (version,) = connection.execute(
                "SELECT version_id FROM api_models.published_version_v1"
            ).fetchone()
            pending = _pending_run(
                connection,
                {"batch_id": str(uuid.uuid4()), "model_version_id": version},
            )
            unknown = _pending_run(
                connection,
                {"batch_id": str(uuid.uuid4()), "model_version_id": 10**12},
            )
            web = _pending_run(connection, {"model_version_id": version})

            def links() -> list[tuple[int, int]]:
                return connection.execute(
                    "SELECT run_id, version_id FROM models.run_version"
                    " WHERE run_id = ANY(%s) ORDER BY run_id",
                    ([pending, unknown, web],),
                ).fetchall()

            (total,) = connection.execute(
                "SELECT count(*) FROM models.run_version"
            ).fetchone()
            connection.execute(BACKFILL)
            assert links() == [
                (pending, version)
            ], "the pending run is linked; an unknown version and a web run are not"
            connection.execute(BACKFILL)
            assert links() == [(pending, version)]
            assert connection.execute(
                "SELECT count(*) FROM models.run_version"
            ).fetchone() == (total + 1,), "a second pass adds nothing"
        finally:
            connection.rollback()
