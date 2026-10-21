"""BOUNDARY check-ins in sql/01_schema.sql and its seed (F14-26, #479).

check-ins-service owns `checkins`: GPS check-ins whose verdict the server
decides (RN-56) and the append-only reviews that override it. The static
checks read the files; the `postgres` checks run against a database built by
the three scripts.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "sql/01_schema.sql").read_text()
SECTION = SCHEMA.split("-- BEGIN BOUNDARY check-ins\n", 1)[1].split(
    "-- END BOUNDARY check-ins", 1
)[0]
GRANTS = list(csv.DictReader((ROOT / "contracts/db/grants.csv").open()))
REASONS = (
    "within_geofence",
    "outside_geofence",
    "low_accuracy",
    "store_location_unknown",
    "no_fix",
)


def test_the_section_follows_users_and_precedes_the_verification() -> None:
    begin = SCHEMA.index("-- BEGIN BOUNDARY check-ins")
    assert SCHEMA.index("-- END BOUNDARY users") < begin
    assert begin < SCHEMA.index("-- BEGIN APPLICATION ROLE VERIFICATION")


def test_the_section_creates_its_objects_in_order() -> None:
    order = [
        "CREATE SCHEMA IF NOT EXISTS checkins;",
        "CREATE TABLE IF NOT EXISTS checkins.store_check_in",
        "CREATE TABLE IF NOT EXISTS checkins.check_in_review",
        "CREATE INDEX IF NOT EXISTS store_check_in_store_occurred",
        "GRANT USAGE ON SCHEMA checkins TO svc_checkins;",
    ]
    positions = [SECTION.index(statement) for statement in order]
    assert positions == sorted(positions)
    assert "CREATE SCHEMA IF NOT EXISTS checkins_events" not in SECTION
    assert "api_checkins" not in SECTION


def test_no_verified_column_is_stored() -> None:
    table = SECTION.split("CREATE TABLE IF NOT EXISTS checkins.store_check_in", 1)[
        1
    ].split(");", 1)[0]
    assert "verified" not in table


def test_every_check_is_named_and_guarded() -> None:
    for name in (
        "store_check_in_reason_check",
        "store_check_in_latitude_check",
        "store_check_in_longitude_check",
        "store_check_in_accuracy_m_check",
        "store_check_in_fix_check",
        "store_check_in_distance_check",
        "check_in_review_reason_check",
    ):
        assert f"AND conname = '{name}'" in SECTION
    assert all(f"'{reason}'" in SECTION for reason in REASONS)


def test_check_ins_are_append_only_in_the_section_and_the_manifest() -> None:
    assert not re.search(
        r"GRANT[^;]*\b(UPDATE|DELETE|TRUNCATE)\b[^;]*checkins", SECTION
    )
    rows = {
        (g["role"], g["object"], g["privilege"])
        for g in GRANTS
        if "checkins" in g["object"]
    }
    assert rows == {
        ("svc_checkins", "checkins", "USAGE"),
        ("svc_checkins", "checkins.store_check_in", "SELECT"),
        ("svc_checkins", "checkins.store_check_in", "INSERT"),
        ("svc_checkins", "checkins.check_in_review", "SELECT"),
        ("svc_checkins", "checkins.check_in_review", "INSERT"),
    }


def test_neither_table_is_exempt_from_the_seed_minimum() -> None:
    exempt = (ROOT / "sql/seed-exempt.txt").read_text()
    assert "checkins." not in exempt


# ---------- against PostgreSQL ----------


@pytest.mark.postgres
def test_privileges_match_the_story(owner_url: str) -> None:
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('svc_checkins', 'checkins.store_check_in',"
            "                           'UPDATE, DELETE, TRUNCATE'),"
            "       has_table_privilege('svc_checkins', 'checkins.check_in_review',"
            "                           'UPDATE, DELETE, TRUNCATE'),"
            "       has_schema_privilege('retail_app', 'checkins', 'USAGE'),"
            "       has_table_privilege('svc_checkins',"
            "                           'api_stores.store_directory_v1', 'SELECT')"
        )
        assert cur.fetchone() == (False, False, False, True)


@pytest.mark.postgres
def test_the_seed_covers_four_reasons_by_staff_and_one_reviewer(owner_url: str) -> None:
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT array_agg(DISTINCT c.reason ORDER BY c.reason),"
            "       bool_and(r.code IN ('STORE_MANAGER', 'ADMIN')), count(*)"
            "  FROM checkins.store_check_in AS c"
            "  JOIN app_user AS u USING (user_id) JOIN role AS r USING (role_id)"
        )
        reasons, staff_only, total = cur.fetchone()
        assert {"within_geofence", "outside_geofence", "low_accuracy", "no_fix"} <= set(
            reasons
        )
        assert staff_only and total >= 30
        cur.execute(
            "SELECT count(*), count(DISTINCT reviewed_by),"
            "       bool_and(reviewed_by = (SELECT user_id FROM app_user"
            "                               WHERE role_id = 1))"
            "  FROM checkins.check_in_review"
        )
        assert cur.fetchone() == (30, 1, True)


@pytest.mark.postgres
def test_every_seeded_verdict_agrees_with_its_fix_and_store(owner_url: str) -> None:
    """Recompute RN-56 from api_stores.store_directory_v1: no row may differ."""
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute("""
            WITH recomputed AS (
                SELECT c.check_in_id, c.reason, c.distance_m, c.accuracy_m,
                       c.latitude AS fix_latitude,
                       s.geofence_radius_m AS radius,
                       2 * 6371008.8 * asin(sqrt(
                           power(sin(radians(c.latitude - s.latitude) / 2), 2)
                         + cos(radians(s.latitude)) * cos(radians(c.latitude))
                           * power(sin(radians(c.longitude - s.longitude) / 2), 2)
                       )) AS distance
                  FROM checkins.store_check_in AS c
                  JOIN api_stores.store_directory_v1 AS s USING (store_id)
            )
            SELECT check_in_id FROM recomputed
             WHERE reason <> CASE
                       WHEN fix_latitude IS NULL THEN 'no_fix'
                       WHEN radius IS NULL THEN 'store_location_unknown'
                       WHEN accuracy_m > radius THEN 'low_accuracy'
                       WHEN distance <= radius THEN 'within_geofence'
                       ELSE 'outside_geofence' END
                OR abs(distance_m - distance) > 1
            """)
        assert cur.fetchall() == []


@pytest.mark.postgres
def test_the_web_cannot_delete_a_store_with_check_ins(owner_url: str) -> None:
    # A store of its own with no sales, so its check-in is the only row that can
    # refuse the delete. PostgreSQL 16 reports RESTRICT as 23503 and 18 as
    # 23001, so the constraint name is what is asserted.
    with psycopg.connect(owner_url) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO store (store_id, name, city, state)"
                    " SELECT max(store_id) + 1, 'Check-in probe', 'Monterrey',"
                    "        'Nuevo León' FROM store"
                    " RETURNING store_id"
                )
                store_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO checkins.store_check_in"
                    " (client_check_in_id, user_id, store_id, reason, occurred_at)"
                    " SELECT gen_random_uuid(), user_id, %s, 'no_fix', now()"
                    "   FROM app_user WHERE role_id = 1",
                    (store_id,),
                )
                cur.execute("SET ROLE retail_app")
                with pytest.raises(psycopg.errors.IntegrityError) as refused:
                    cur.execute("DELETE FROM store WHERE store_id = %s", (store_id,))
                assert (
                    refused.value.diag.constraint_name == "store_check_in_store_id_fkey"
                )
        finally:
            conn.rollback()


@pytest.mark.postgres
def test_a_partial_fix_and_a_blank_review_are_refused(owner_url: str) -> None:
    with psycopg.connect(owner_url) as conn:
        try:
            with conn.cursor() as cur:
                with pytest.raises(psycopg.errors.CheckViolation) as partial:
                    cur.execute(
                        "INSERT INTO checkins.store_check_in"
                        " (client_check_in_id, user_id, store_id, latitude, reason,"
                        "  occurred_at)"
                        " SELECT gen_random_uuid(), user_id, 1, 25.0, 'no_fix', now()"
                        "   FROM app_user WHERE role_id = 1"
                    )
                assert partial.value.diag.constraint_name == "store_check_in_fix_check"
        finally:
            conn.rollback()
        try:
            with conn.cursor() as cur:
                with pytest.raises(psycopg.errors.CheckViolation) as blank:
                    cur.execute(
                        "INSERT INTO checkins.check_in_review"
                        " (check_in_id, verified, reason, reviewed_by)"
                        " SELECT check_in_id, TRUE, '   ', user_id"
                        "   FROM checkins.store_check_in LIMIT 1"
                    )
                assert (
                    blank.value.diag.constraint_name == "check_in_review_reason_check"
                )
        finally:
            conn.rollback()
