"""Domain events in the audit trail (F21-25, #803, ADR-0072).

`audit_events.domain_event` is append-only for every role, its one writer is
`api_audit.record_event_v1`, which attributes each event to the recording
`session_user` and refuses credentials (RN-29), and the seed covers each kind.
The static checks read the files; the `postgres` checks run against a database
built by the three scripts, as its owner and signed in as each service role.
"""

from __future__ import annotations

import csv
import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "sql/01_schema.sql").read_text()
SECTION = SCHEMA.split("-- BEGIN BOUNDARY audit-trail\n", 1)[1].split(
    "-- END BOUNDARY audit-trail", 1
)[0]
GRANTS = list(csv.DictReader((ROOT / "contracts/db/grants.csv").open()))
FUNCTION = (
    "api_audit.record_event_v1(uuid,text,text,timestamp with time zone,text,uuid,"
    "text,smallint,text,text,bigint,bigint,text,jsonb)"
)
KINDS = (
    "CALCULATION_RUN",
    "MODEL_PUBLISHED",
    "MODEL_ROLLED_BACK",
    "PERMISSION_CHANGED",
    "CRITICAL_ACTION",
    "HISTORY_READ",
    "AUDIT_READ",
)
ADMIN = "11111111-1111-1111-1111-000000000001"
SUBJECT = "11111111-1111-1111-1111-000000000003"
RECORD = (
    "SELECT * FROM api_audit.record_event_v1("
    "%s, %s, %s, now(), %s, %s, %s, %s::smallint, %s, %s, %s, %s, %s, %s::jsonb)"
)
ALTERATIONS = (
    ("UPDATE", "UPDATE audit_events.domain_event SET outcome = outcome"),
    ("DELETE", "DELETE FROM audit_events.domain_event"),
    ("TRUNCATE", "TRUNCATE audit_events.domain_event"),
    (
        "INSERT",
        "INSERT INTO audit_events.domain_event SELECT * FROM audit_events.domain_event"
        " ORDER BY seq DESC LIMIT 1",
    ),
)

# ---------- static ----------


def test_the_section_creates_the_store_before_its_writer_and_grants() -> None:
    order = [
        "CREATE SCHEMA IF NOT EXISTS audit_events;",
        "CREATE SCHEMA IF NOT EXISTS api_audit;",
        "CREATE TABLE IF NOT EXISTS audit_events.domain_event",
        "CREATE OR REPLACE TRIGGER domain_event_append_only",
        "CREATE OR REPLACE FUNCTION audit_events.append_v1(",
        "CREATE OR REPLACE FUNCTION api_audit.record_event_v1(",
        "GRANT SELECT ON audit_events.domain_event TO svc_audit_trail;",
    ]
    positions = [SECTION.index(statement) for statement in order]
    assert positions == sorted(positions)


def test_no_grant_in_the_section_or_the_manifest_can_alter_an_event() -> None:
    assert not re.search(
        r"GRANT[^;]*\b(UPDATE|DELETE|TRUNCATE)\b[^;]*domain_event", SECTION
    )
    on_table = {
        (g["role"], g["privilege"])
        for g in GRANTS
        if g["object"] == "audit_events.domain_event"
    }
    assert on_table == {
        ("definer_audit", "INSERT"),
        ("definer_audit", "SELECT"),
        ("svc_audit_trail", "SELECT"),
    }
    writers = {g["role"] for g in GRANTS if g["object"] == FUNCTION}
    assert "retail_app" not in writers
    assert {"svc_users", "svc_sales", "svc_segments", "svc_audit_trail"} <= writers


def test_the_table_is_seeded_not_exempted() -> None:
    exempt = (ROOT / "sql/seed-exempt.txt").read_text()
    assert "domain_event" not in exempt


# ---------- postgres ----------


def _as(owner_url: str, role: str) -> str:
    """Sign in as a service role itself (its default password is its name)."""
    parts = urlsplit(owner_url)
    netloc = f"{role}:{role}@{parts.hostname or '127.0.0.1'}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


@pytest.fixture
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


def _record(
    connection: psycopg.Connection,
    kind: str = "PERMISSION_CHANGED",
    details: dict | None = None,
    *,
    event_id: str | None = None,
    outcome: str = "SUCCESS",
    actor_type: str = "USER",
    actor: str | None = ADMIN,
    role: str | None = "ADMIN",
    subject_type: str | None = "user",
    subject_id: str | None = SUBJECT,
    correlation: str | None = None,
) -> tuple:
    return connection.execute(
        RECORD,
        (
            event_id or str(uuid.uuid4()),
            kind,
            outcome,
            actor_type,
            actor,
            role,
            None,
            subject_type,
            subject_id,
            None,
            None,
            correlation or "test-" + uuid.uuid4().hex,
            json.dumps(
                details
                if details is not None
                else {
                    "change": "ROLE_ASSIGNED",
                    "from_role": "ANALYST",
                    "to_role": "MARKETING",
                }
            ),
        ),
    ).fetchone()


def _refused(connection: psycopg.Connection, sqlstate: str, **kwargs) -> None:
    with pytest.raises(psycopg.Error) as caught:
        with connection.transaction():
            _record(connection, **kwargs)
    assert caught.value.sqlstate == sqlstate, caught.value


def _count(owner: psycopg.Connection) -> int:
    return owner.execute("SELECT count(*) FROM audit_events.domain_event").fetchone()[0]


@pytest.mark.postgres
def test_no_role_can_alter_an_event_and_the_owner_is_stopped_by_the_trigger(
    owner: psycopg.Connection,
) -> None:
    roles = [
        row[0]
        for row in owner.execute(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE %s OR rolname LIKE %s"
            " OR rolname = %s ORDER BY 1",
            ("svc\\_%", "definer\\_%", "retail_app"),
        )
    ]
    assert "svc_audit_trail" in roles and "definer_audit" in roles
    assert len(roles) > 20
    before = _count(owner)
    tried = 0
    with owner.transaction(force_rollback=True):
        for role in roles:
            for operation, statement in ALTERATIONS:
                # definer_audit is the one role that appends; it does so only
                # inside record_event_v1, and nobody can sign in as it.
                if role == "definer_audit" and operation == "INSERT":
                    continue
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with owner.transaction():
                        owner.execute(
                            sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role))
                        )
                        owner.execute(statement)
                tried += 1
        # The schema owner holds every privilege; the trigger still refuses.
        for _operation, statement in ALTERATIONS[:3]:
            with pytest.raises(psycopg.errors.CheckViolation) as caught:
                with owner.transaction():
                    owner.execute(statement)
            assert caught.value.diag.constraint_name == "domain_event_append_only"
    assert tried == len(roles) * len(ALTERATIONS) - 1
    assert _count(owner) == before


@pytest.mark.postgres
def test_an_event_is_attributed_to_the_recording_role_not_a_parameter(
    owner_url: str, owner: psycopg.Connection
) -> None:
    correlation = "attr-" + uuid.uuid4().hex
    with psycopg.connect(_as(owner_url, "svc_users"), autocommit=True) as users:
        recorded_at, entry_hash, created = _record(users, correlation=correlation)
    assert created and len(entry_hash) == 64
    row = owner.execute(
        "SELECT source, actor_type, actor_user_id::text, actor_role, recorded_at,"
        " prev_hash = (SELECT entry_hash FROM audit_events.domain_event"
        "              WHERE seq = d.seq - 1),"
        " entry_hash = audit_events.entry_hash_v1(d)"
        " FROM audit_events.domain_event AS d WHERE correlation_id = %s",
        (correlation,),
    ).fetchone()
    assert row == ("svc_users", "USER", ADMIN, "ADMIN", recorded_at, True, True)


@pytest.mark.parametrize(
    "role, kind, details, subject_type",
    [
        ("svc_sales", "PERMISSION_CHANGED", None, "user"),
        ("svc_audit_trail", "HISTORY_READ", {"history": "SALES"}, "customer"),
        ("svc_users", "AUDIT_READ", {"resource": "DOMAIN_EVENTS", "filters": []}, None),
        (
            "svc_segments",
            "MODEL_PUBLISHED",
            {"previous_version_id": None, "reason_code": "X"},
            "model_version",
        ),
        ("svc_campaigns", "PAGE_VIEWED", {}, None),
    ],
)
@pytest.mark.postgres
def test_a_role_records_only_the_kinds_it_is_mapped_to(
    owner_url: str, role: str, kind: str, details: dict | None, subject_type: str | None
) -> None:
    with psycopg.connect(_as(owner_url, role), autocommit=True) as connection:
        _refused(
            connection,
            "MA001",
            kind=kind,
            details=details,
            subject_type=subject_type,
            subject_id=None if subject_type is None else SUBJECT,
        )


@pytest.mark.parametrize(
    "details",
    [
        {"change": "USER_DEACTIVATED", "password_hash": "x"},
        {"change": "USER_DEACTIVATED", "nested": {"api_token": "x"}},
        {"change": "USER_DEACTIVATED", "Secret": 1},
        {
            "change": "ROLE_ASSIGNED",
            "from_role": "ANALYST",
            "to_role": "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXZhbHVl",
        },
        {
            "change": "ROLE_ASSIGNED",
            "from_role": "ANALYST",
            "to_role": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
        },
        {"change": "STORES_ASSIGNED", "store_ids": [1], "credential": None},
    ],
)
@pytest.mark.postgres
def test_a_credential_anywhere_is_refused_and_writes_nothing(
    owner_url: str, owner: psycopg.Connection, details: dict
) -> None:
    before = _count(owner)
    with psycopg.connect(_as(owner_url, "svc_users"), autocommit=True) as users:
        _refused(users, "MA003", details=details)
    assert _count(owner) == before


@pytest.mark.postgres
def test_a_credential_in_the_subject_or_correlation_is_refused(owner_url: str) -> None:
    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXZhbHVl"
    with psycopg.connect(_as(owner_url, "svc_users"), autocommit=True) as users:
        _refused(users, "MA003", subject_id=token)
    with psycopg.connect(_as(owner_url, "svc_sales"), autocommit=True) as sales:
        _refused(
            sales,
            "MA003",
            kind="HISTORY_READ",
            details={"history": "SALES"},
            subject_type="customer",
            subject_id="$2b$12$abcdefghijklmnopqrstuv",
        )


@pytest.mark.parametrize(
    "details",
    [
        {"change": "ROLE_ASSIGNED", "from_role": "ANALYST"},
        {"change": "ROLE_ASSIGNED", "from_role": "ANALYST", "to_role": "ANALYST"},
        {
            "change": "ROLE_ASSIGNED",
            "from_role": "ANALYST",
            "to_role": "MARKETING",
            "email": "a@b.c",
        },
        {"change": "STORES_ASSIGNED", "store_ids": []},
        {"change": "STORES_ASSIGNED", "store_ids": [3, 3]},
        {"change": "STORES_ASSIGNED", "store_ids": [3], "to_role": "ADMIN"},
        {"change": "STORES_UNASSIGNED", "store_ids": [{"id": 3}]},
        {"change": "SOMETHING_ELSE"},
    ],
)
@pytest.mark.postgres
def test_details_hold_exactly_the_keys_and_values_the_kind_allows(
    owner_url: str, details: dict
) -> None:
    with psycopg.connect(_as(owner_url, "svc_users"), autocommit=True) as users:
        _refused(users, "MA002", details=details)


@pytest.mark.parametrize(
    "fields",
    [
        {"actor": None},
        {"role": None},
        {"actor_type": "SYSTEM"},
        {"outcome": "REFUSED"},
        {"subject_type": "customer"},
        {"subject_type": None, "subject_id": None},
    ],
)
@pytest.mark.postgres
def test_an_event_must_be_attributed_and_about_its_subject(
    owner_url: str, fields: dict
) -> None:
    with psycopg.connect(_as(owner_url, "svc_users"), autocommit=True) as users:
        _refused(users, "MA004", **fields)


@pytest.mark.postgres
def test_a_store_assignment_change_is_recorded_with_its_store_ids(
    owner_url: str, owner: psycopg.Connection
) -> None:
    # users-service's PUT /v1/store-assignments/{user_id} (#756) calls this
    # in its transaction; the function accepts both directions today.
    added, removed = ("stores-" + uuid.uuid4().hex for _ in range(2))
    with psycopg.connect(_as(owner_url, "svc_users"), autocommit=True) as users:
        _record(
            users,
            details={"change": "STORES_ASSIGNED", "store_ids": [4, 9]},
            correlation=added,
        )
        _record(
            users,
            details={"change": "STORES_UNASSIGNED", "store_ids": [2]},
            correlation=removed,
        )
    rows = owner.execute(
        "SELECT correlation_id, source, subject_id, details"
        " FROM audit_events.domain_event"
        " WHERE correlation_id IN (%s, %s) ORDER BY seq",
        (added, removed),
    ).fetchall()
    assert rows == [
        (
            added,
            "svc_users",
            SUBJECT,
            {"change": "STORES_ASSIGNED", "store_ids": [4, 9]},
        ),
        (
            removed,
            "svc_users",
            SUBJECT,
            {"change": "STORES_UNASSIGNED", "store_ids": [2]},
        ),
    ]


@pytest.mark.postgres
def test_a_repeated_event_id_returns_the_stored_row_and_writes_nothing(
    owner_url: str, owner: psycopg.Connection
) -> None:
    event_id = str(uuid.uuid4())
    with psycopg.connect(_as(owner_url, "svc_users"), autocommit=True) as users:
        first = _record(users, event_id=event_id)
        before = _count(owner)
        second = _record(users, event_id=event_id)
    assert first[2] is True and second[2] is False
    assert second[:2] == first[:2]
    assert _count(owner) == before
    # The same id from another producer is a conflict, not a silent repeat.
    with psycopg.connect(_as(owner_url, "svc_sales"), autocommit=True) as sales:
        _refused(
            sales,
            "MA005",
            event_id=event_id,
            kind="HISTORY_READ",
            details={"history": "SALES"},
            subject_type="customer",
        )


@pytest.mark.postgres
def test_an_event_rolled_back_with_its_act_leaves_no_row(
    owner_url: str, owner: psycopg.Connection
) -> None:
    event_id = str(uuid.uuid4())
    with psycopg.connect(_as(owner_url, "svc_users")) as users:
        _record(users, event_id=event_id)
        users.rollback()
    assert (
        owner.execute(
            "SELECT count(*) FROM audit_events.domain_event WHERE event_id = %s",
            (event_id,),
        ).fetchone()[0]
        == 0
    )


@pytest.mark.postgres
def test_the_web_can_neither_record_nor_read_an_event(app_url: str) -> None:
    with psycopg.connect(app_url, autocommit=True) as web:
        for statement in (
            "SELECT count(*) FROM audit_events.domain_event",
            "SELECT api_audit.record_event_v1(NULL, NULL, NULL, NULL, NULL, NULL, NULL,"
            " NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                web.execute(statement)


@pytest.mark.postgres
def test_the_seed_covers_every_kind_on_one_valid_chain(
    owner: psycopg.Connection,
) -> None:
    seeded = dict(
        owner.execute(
            "SELECT kind, count(*) FROM audit_events.domain_event"
            " WHERE correlation_id LIKE %s GROUP BY kind",
            ("seed-%",),
        ).fetchall()
    )
    assert set(seeded) == set(KINDS)
    assert sum(seeded.values()) >= 30
    outcomes = {
        row[0]
        for row in owner.execute(
            "SELECT DISTINCT outcome FROM audit_events.domain_event"
            " WHERE correlation_id LIKE %s",
            ("seed-%",),
        )
    }
    assert outcomes == {"SUCCESS", "REFUSED"}
    broken = owner.execute(
        "SELECT count(*) FROM ("
        " SELECT entry_hash <> audit_events.entry_hash_v1(d) AS bad_hash,"
        "        prev_hash <> coalesce(lag(entry_hash) OVER (ORDER BY seq),"
        "                              repeat('0', 64)) AS bad_link"
        " FROM audit_events.domain_event AS d) AS c"
        " WHERE bad_hash OR bad_link"
    ).fetchone()[0]
    assert broken == 0
    first = owner.execute(
        "SELECT seq, prev_hash FROM audit_events.domain_event ORDER BY seq LIMIT 1"
    ).fetchone()
    assert first == (1, "0" * 64)
