"""BOUNDARY users in sql/01_schema.sql (F14-04, #421).

users-service publishes `api_users.credential_v1` to auth (E-15) and captures
every access change to an account, the web's included, so auth can end that
user's sessions (ADR-0044). Accounts are never deleted through the service
(RN-04). The static checks read the files; the `postgres` checks run against a
database built by the three scripts.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "sql/01_schema.sql").read_text()
SECTION = SCHEMA.split("-- BEGIN BOUNDARY users\n", 1)[1].split(
    "-- END BOUNDARY users", 1
)[0]
GRANTS = list(csv.DictReader((ROOT / "contracts/db/grants.csv").open()))

ADMIN = "11111111-1111-1111-1111-000000000001"
ANALYST = "11111111-1111-1111-1111-000000000014"


def test_the_section_sits_between_the_legacy_sections_and_the_verification() -> None:
    begin = SCHEMA.index("-- BEGIN BOUNDARY users")
    assert SCHEMA.index("-- END BOUNDARY sales") < begin
    assert begin < SCHEMA.index("-- BEGIN APPLICATION ROLE VERIFICATION")


def test_the_section_creates_its_objects_in_order() -> None:
    order = [
        "CREATE SCHEMA IF NOT EXISTS api_users;",
        "CREATE SCHEMA IF NOT EXISTS users_events;",
        "CREATE TABLE IF NOT EXISTS users_events.outbox",
        "CREATE OR REPLACE VIEW api_users.credential_v1",
        "CREATE OR REPLACE FUNCTION users_events.capture_access_changed()",
        "CREATE OR REPLACE TRIGGER trg_users_capture_access_changed",
        "GRANT SELECT, INSERT ON public.app_user TO svc_users;",
    ]
    positions = [SECTION.index(statement) for statement in order]
    assert positions == sorted(positions)
    assert "WITH (security_invoker = off)" in SECTION
    assert (
        "ALTER FUNCTION users_events.capture_access_changed() OWNER TO definer_users;"
        in SECTION
    )


def test_only_access_changes_are_captured() -> None:
    assert re.search(
        r"AFTER UPDATE OF is_active, role_id, password_hash OR DELETE"
        r" ON public\.app_user",
        SECTION,
    )
    assert "'stream:users.v1.access_changed'" in SECTION
    assert "pg_notify('users_outbox', '')" in SECTION


def test_svc_users_is_never_granted_delete_on_app_user() -> None:
    assert not re.search(r"GRANT[^;]*DELETE[^;]*ON public\.app_user", SECTION)
    rows = {(g["role"], g["object"], g["privilege"]) for g in GRANTS}
    assert ("svc_users", "public.app_user", "DELETE") not in rows
    assert ("svc_users", "public.app_user", "INSERT") in rows


def test_only_auth_and_users_read_the_credential_view_in_the_manifest() -> None:
    readers = {
        g["role"]
        for g in GRANTS
        if g["object"] == "api_users.credential_v1" and g["privilege"] == "SELECT"
    }
    assert readers == {"svc_auth", "svc_users"}


def test_the_outbox_is_exempt_from_the_seed_minimum() -> None:
    exempt = (ROOT / "sql/seed-exempt.txt").read_text().splitlines()
    assert "users_events.outbox" in exempt


# ---------- against PostgreSQL ----------


def _captured(cur: psycopg.Cursor, user_id: str) -> int:
    cur.execute(
        "SELECT count(*) FROM users_events.outbox WHERE entity_id = %s", (user_id,)
    )
    return cur.fetchone()[0]


@pytest.mark.postgres
def test_web_writes_capture_access_changes_and_nothing_else(owner_url: str) -> None:
    with psycopg.connect(owner_url) as conn:
        try:
            with conn.cursor() as cur:
                # The web's own role, so the capture is proved for the other writer.
                cur.execute("SET ROLE retail_app")
                cur.execute(
                    "UPDATE app_user SET name = name || ' ' WHERE user_id = %s",
                    (ANALYST,),
                )
                cur.execute("RESET ROLE")
                assert _captured(cur, ANALYST) == 0

                cur.execute("SET ROLE retail_app")
                cur.execute(
                    "UPDATE app_user SET is_active = FALSE WHERE user_id = %s",
                    (ANALYST,),
                )
                cur.execute(
                    "UPDATE app_user SET password_hash = 'not-a-real-hash'"
                    " WHERE user_id = %s",
                    (ANALYST,),
                )
                cur.execute(
                    "UPDATE app_user"
                    " SET role_id = (SELECT role_id FROM role WHERE code = 'AUDITOR')"
                    " WHERE user_id = %s",
                    (ANALYST,),
                )
                cur.execute("RESET ROLE")
                assert _captured(cur, ANALYST) == 3
                cur.execute(
                    "SELECT DISTINCT stream, event_type FROM users_events.outbox"
                    " WHERE entity_id = %s",
                    (ANALYST,),
                )
                assert cur.fetchall() == [("stream:users.v1.access_changed", "UPDATE")]
        finally:
            conn.rollback()


@pytest.mark.postgres
def test_the_credential_view_is_auths_and_users_alone(owner_url: str) -> None:
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rolname FROM pg_roles"
            " WHERE (rolname LIKE 'svc\\_%' OR rolname = 'retail_app')"
            "   AND has_table_privilege(oid, 'api_users.credential_v1', 'SELECT')"
            " ORDER BY rolname"
        )
        assert [row[0] for row in cur.fetchall()] == ["svc_auth", "svc_users"]
        cur.execute(
            "SELECT has_table_privilege('svc_users', 'public.app_user', 'DELETE'),"
            "       has_table_privilege('svc_auth', 'public.app_user', 'SELECT')"
        )
        assert cur.fetchone() == (False, False)


@pytest.mark.postgres
def test_auth_reads_credentials_through_the_view_only(owner_url: str) -> None:
    with psycopg.connect(owner_url) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SET ROLE svc_auth")
                cur.execute(
                    "SELECT email_lower, is_active, role_code"
                    " FROM api_users.credential_v1 WHERE user_id = %s",
                    (ADMIN,),
                )
                email, active, role = cur.fetchone()
                assert email == email.lower() and active is True and role == "ADMIN"
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cur.execute("SELECT 1 FROM public.app_user LIMIT 1")
        finally:
            conn.rollback()
