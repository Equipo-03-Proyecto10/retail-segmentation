"""A permission change commits with its domain event, or not at all (F21-25).

ADR-0072: users-service records `PERMISSION_CHANGED` through
`api_audit.record_event_v1` in the transaction of the change. Each test runs on
its own copy of the seeded database, so the events it finds are its own.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.conftest import ADMIN, ANALYST, STORE_MANAGER

pytestmark = pytest.mark.postgres

REVOKE = (
    "REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1("
    "uuid, text, text, timestamptz, text, uuid, text,"
    " smallint, text, text, bigint, bigint, text, jsonb) FROM svc_users"
)
GRANT = (
    "GRANT EXECUTE ON FUNCTION api_audit.record_event_v1("
    "uuid, text, text, timestamptz, text, uuid, text,"
    " smallint, text, text, bigint, bigint, text, jsonb) TO svc_users"
)


@pytest.fixture
def since(owner: psycopg.Connection) -> int:
    """The chain position before the test: its events are the ones after it."""
    return owner.execute(
        "SELECT coalesce(max(seq), 0) FROM audit_events.domain_event"
    ).fetchone()[0]


def _events(owner: psycopg.Connection, since: int) -> list[tuple[Any, ...]]:
    """The permission events recorded after `since`, oldest first."""
    return owner.execute(
        "SELECT actor_type, actor_user_id::text, actor_role, source, subject_type,"
        " subject_id, outcome, details FROM audit_events.domain_event"
        " WHERE kind = %s AND seq > %s ORDER BY seq",
        ("PERMISSION_CHANGED", since),
    ).fetchall()


def _role(owner: psycopg.Connection, user_id: str) -> str:
    return owner.execute(
        "SELECT r.code FROM app_user u JOIN role r USING (role_id)"
        " WHERE u.user_id = %s",
        (user_id,),
    ).fetchone()[0]


def test_a_role_change_commits_with_its_event(
    api: Any, owner: psycopg.Connection, since: int
) -> None:
    reply = api(
        "PATCH",
        f"/v1/users/{STORE_MANAGER}",
        json_body={"role_code": "MARKETING"},
        headers={"X-Correlation-ID": "role-change-1"},
    )

    assert reply.status_code == 200
    assert _events(owner, since) == [
        (
            "USER",
            ADMIN,
            "ADMIN",
            "svc_users",
            "user",
            STORE_MANAGER,
            "SUCCESS",
            {
                "change": "ROLE_ASSIGNED",
                "from_role": "STORE_MANAGER",
                "to_role": "MARKETING",
            },
        )
    ]
    correlation = owner.execute(
        "SELECT correlation_id FROM audit_events.domain_event"
        " WHERE kind = %s AND subject_id = %s AND seq > %s",
        ("PERMISSION_CHANGED", STORE_MANAGER, since),
    ).fetchone()
    assert correlation == ("role-change-1",)


def test_an_edit_that_changes_no_permission_records_nothing(
    api: Any, owner: psycopg.Connection, since: int
) -> None:
    reply = api("PATCH", f"/v1/users/{STORE_MANAGER}", json_body={"name": "Renamed"})
    same = api(
        "PATCH", f"/v1/users/{STORE_MANAGER}", json_body={"role_code": "STORE_MANAGER"}
    )
    password = api(
        "PUT",
        f"/v1/users/{STORE_MANAGER}/password",
        json_body={"new_password": "a-new-long-password-123"},
    )

    assert (reply.status_code, same.status_code, password.status_code) == (
        200,
        200,
        204,
    )
    assert _events(owner, since) == []


def test_deactivation_and_reactivation_commit_with_their_events(
    api: Any, owner: psycopg.Connection, since: int
) -> None:
    # POST deactivates and DELETE reactivates (contracts/users/openapi.yaml).
    assert api("POST", f"/v1/users/{ANALYST}/deactivation").status_code == 200
    # Deactivating an inactive account changes nothing, so it records nothing.
    assert api("POST", f"/v1/users/{ANALYST}/deactivation").status_code == 200
    assert api("DELETE", f"/v1/users/{ANALYST}/deactivation").status_code == 200

    assert [(row[5], row[7]) for row in _events(owner, since)] == [
        (ANALYST, {"change": "USER_DEACTIVATED"}),
        (ANALYST, {"change": "USER_REACTIVATED"}),
    ]


def test_an_administrator_transfer_commits_with_one_event_per_moved_role(
    api: Any, owner: psycopg.Connection, since: int
) -> None:
    reply = api(
        "POST", "/v1/administrator-transfers", json_body={"successor_user_id": ANALYST}
    )

    assert reply.status_code == 204
    events = _events(owner, since)
    assert {(row[1], row[5]) for row in events} == {(ADMIN, ADMIN), (ADMIN, ANALYST)}
    assert sorted(row[7]["to_role"] for row in events) == ["ADMIN", "ANALYST"]
    assert {row[5]: row[7] for row in events} == {
        ADMIN: {
            "change": "ADMINISTRATOR_TRANSFERRED",
            "from_role": "ADMIN",
            "to_role": "ANALYST",
        },
        ANALYST: {
            "change": "ADMINISTRATOR_TRANSFERRED",
            "from_role": "ANALYST",
            "to_role": "ADMIN",
        },
    }


def test_a_refused_change_records_nothing(
    api: Any, owner: psycopg.Connection, since: int
) -> None:
    reply = api("PATCH", f"/v1/users/{ANALYST}", json_body={"role_code": "ADMIN"})

    assert reply.status_code == 409
    assert _events(owner, since) == []


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("PATCH", f"/v1/users/{STORE_MANAGER}", {"role_code": "MARKETING"}),
        ("POST", f"/v1/users/{STORE_MANAGER}/deactivation", None),
        ("POST", "/v1/administrator-transfers", {"successor_user_id": ANALYST}),
    ],
)
def test_a_change_whose_event_cannot_be_recorded_is_rolled_back(
    api: Any,
    owner: psycopg.Connection,
    since: int,
    method: str,
    path: str,
    body: Any,
) -> None:
    before = owner.execute(
        "SELECT user_id::text, role_id, is_active FROM app_user ORDER BY user_id"
    ).fetchall()
    owner.execute(REVOKE)

    reply = api(method, path, json_body=body)

    assert reply.status_code == 503
    assert reply.get_json()["code"] == "audit_unavailable"
    after = owner.execute(
        "SELECT user_id::text, role_id, is_active FROM app_user ORDER BY user_id"
    ).fetchall()
    assert after == before
    assert _role(owner, ADMIN) == "ADMIN"
    assert _events(owner, since) == []
