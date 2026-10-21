"""The eleven operations against PostgreSQL, as svc_users (F14-04)."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from argon2 import PasswordHasher
from lxml import etree

from tests.conftest import ADMIN, ANALYST, STORE_MANAGER, USER_2

pytestmark = pytest.mark.postgres
NEW_USER = {
    "name": "New Analyst",
    "email": "New.Analyst@Example.com",
    "role_code": "ANALYST",
    "password": "a long enough password",
}


def _code(reply: Any) -> tuple[int, str | None]:
    body = reply.get_json(silent=True) or {}
    return reply.status_code, body.get("code")


def _scalar(owner: psycopg.Connection, query: str, *params: Any) -> Any:
    return owner.execute(query, params).fetchone()[0]


def _administrators(owner: psycopg.Connection) -> int:
    return _scalar(owner, "SELECT count(*) FROM app_user WHERE role_id = 1")


# ---------- reads ----------


def test_list_users_pages_under_the_contracts_page_member(api: Any) -> None:
    reply = api("GET", "/v1/users?size=5&page=2")
    assert reply.status_code == 200
    body = reply.get_json()
    assert set(body) == {"users", "page"}
    assert body["page"]["page"] == 2 and body["page"]["size"] == 5
    assert body["page"]["total"] >= 30 and len(body["users"]) == 5
    assert "password_hash" not in body["users"][0]


def test_list_users_filters_by_role_activity_and_text(api: Any) -> None:
    body = api("GET", "/v1/users?role=ADMIN&active=true").get_json()
    assert [user["user_id"] for user in body["users"]] == [ADMIN]
    body = api("GET", "/v1/users?q=USER14@").get_json()
    assert [user["user_id"] for user in body["users"]] == [ANALYST]
    assert api("GET", "/v1/users?q=%25").get_json()["page"]["total"] == 0


@pytest.mark.parametrize("query", ["role=ROOT", "active=yes", "size=101"])
def test_list_users_refuses_bad_query_values(api: Any, query: str) -> None:
    assert _code(api("GET", "/v1/users?" + query)) == (422, "invalid_pagination")


def test_an_auditor_reads_and_an_analyst_does_not(api: Any) -> None:
    assert api("GET", "/v1/users", as_user=(USER_2, "AUDITOR")).status_code == 200
    reply = api("GET", "/v1/users", as_user=(ANALYST, "ANALYST"))
    assert _code(reply) == (403, "forbidden_role")


def test_the_list_answers_xml_valid_against_the_schema(api: Any, service: Any) -> None:
    # A created account: the seed's fixed ids are not RFC 4122 UUIDs, which
    # users.xsd's Uuid pattern requires (reported apart from this story).
    api("POST", "/v1/users", json_body=NEW_USER)
    reply = api(
        "GET",
        "/v1/users?q=new.analyst",
        headers={"Accept": "application/xml"},
    )
    assert reply.status_code == 200
    schema = service.extensions["mosaiq_kit"].contract.xml_schema
    document = etree.fromstring(reply.data)
    assert schema.validate(document), schema.error_log
    assert etree.QName(document).localname == "Users"
    assert len(document.findall("{*}User")) == 1


def test_every_role_including_customer_reads_itself(api: Any) -> None:
    for user_id, role in ((ANALYST, "ANALYST"), (ANALYST, "CUSTOMER")):
        reply = api("GET", "/v1/users/me", as_user=(user_id, role))
        assert reply.status_code == 200
        assert reply.get_json()["user_id"] == ANALYST


def test_a_token_for_an_unknown_user_is_user_not_found(api: Any) -> None:
    stranger = "99999999-9999-9999-9999-999999999999"
    reply = api("GET", "/v1/users/me", as_user=(stranger, "ANALYST"))
    assert _code(reply) == (404, "user_not_found")
    assert _code(api("GET", f"/v1/users/{stranger}")) == (404, "user_not_found")


def test_roles_are_the_seven_codes_and_one_can_be_read(api: Any) -> None:
    body = api("GET", "/v1/roles").get_json()
    assert [role["role_code"] for role in body["roles"]][0] == "ADMIN"
    assert body["page"]["total"] == 7 == len(body["roles"])
    assert api("GET", "/v1/roles/AUDITOR").get_json()["role_code"] == "AUDITOR"
    assert _code(api("GET", "/v1/roles/ROOT")) == (404, "role_not_found")


def test_accounts_are_never_deleted(api: Any) -> None:
    assert api("DELETE", f"/v1/users/{ANALYST}").status_code == 405


# ---------- create and edit ----------


def test_create_lowercases_the_email_and_writes_an_argon2id_hash(
    api: Any, owner: psycopg.Connection
) -> None:
    reply = api("POST", "/v1/users", json_body=NEW_USER)
    assert reply.status_code == 201
    user = reply.get_json()
    assert user["email"] == "new.analyst@example.com" and user["is_active"]
    assert reply.headers["Location"] == "/v1/users/" + user["user_id"]
    stored = _scalar(
        owner, "SELECT password_hash FROM app_user WHERE user_id = %s", user["user_id"]
    )
    # The web signs in with PasswordHasher().verify (web/services/auth.py).
    assert PasswordHasher().verify(stored, NEW_USER["password"])


def test_create_refuses_a_second_administrator(
    api: Any, owner: psycopg.Connection
) -> None:
    reply = api("POST", "/v1/users", json_body=NEW_USER | {"role_code": "ADMIN"})
    assert _code(reply) == (409, "single_administrator")
    assert _administrators(owner) == 1


def test_create_refuses_a_case_variant_email(api: Any) -> None:
    reply = api(
        "POST", "/v1/users", json_body=NEW_USER | {"email": "USER14@mosaiq-demo.com"}
    )
    assert _code(reply) == (409, "email_taken")


def test_create_refuses_an_unknown_role_and_a_short_password(api: Any) -> None:
    reply = api("POST", "/v1/users", json_body=NEW_USER | {"role_code": "ROOT"})
    assert _code(reply) == (422, "unknown_role")
    reply = api("POST", "/v1/users", json_body=NEW_USER | {"password": "short"})
    assert _code(reply) == (422, "invalid_body")


def test_only_the_administrator_creates(api: Any) -> None:
    reply = api("POST", "/v1/users", as_user=(USER_2, "AUDITOR"), json_body=NEW_USER)
    assert _code(reply) == (403, "forbidden_role")


def test_patch_edits_name_email_and_a_non_admin_role(api: Any) -> None:
    reply = api(
        "PATCH",
        f"/v1/users/{ANALYST}",
        json_body={"name": " Renamed ", "email": "Renamed@Example.com"},
    )
    assert reply.status_code == 200
    assert reply.get_json()["name"] == "Renamed"
    assert reply.get_json()["email"] == "renamed@example.com"
    reply = api("PATCH", f"/v1/users/{ANALYST}", json_body={"role_code": "AUDITOR"})
    assert reply.get_json()["role_code"] == "AUDITOR"


@pytest.mark.parametrize(
    ("user_id", "role_code"), [(ANALYST, "ADMIN"), (ADMIN, "ANALYST")]
)
def test_patch_never_moves_the_administrator_role(
    api: Any, owner: psycopg.Connection, user_id: str, role_code: str
) -> None:
    reply = api("PATCH", f"/v1/users/{user_id}", json_body={"role_code": role_code})
    assert _code(reply) == (409, "use_administrator_transfer")
    assert (
        _scalar(owner, "SELECT user_id::text FROM app_user WHERE role_id = 1") == ADMIN
    )


def test_patch_refusals(api: Any) -> None:
    missing = "99999999-9999-9999-9999-999999999999"
    assert _code(api("PATCH", f"/v1/users/{missing}", json_body={"name": "X"})) == (
        404,
        "user_not_found",
    )
    assert _code(
        api("PATCH", f"/v1/users/{ANALYST}", json_body={"role_code": "ROOT"})
    ) == (422, "unknown_role")
    assert _code(
        api(
            "PATCH",
            f"/v1/users/{STORE_MANAGER}",
            json_body={"email": "USER14@mosaiq-demo.com"},
        )
    ) == (409, "email_taken")
    assert _code(api("PATCH", f"/v1/users/{ANALYST}", json_body={}))[0] == 422


def test_writes_name_the_caller_to_the_audit_and_never_the_hash(
    api: Any, owner: psycopg.Connection
) -> None:
    api("PATCH", f"/v1/users/{ANALYST}", json_body={"name": "Audited"})
    actor = _scalar(
        owner,
        "SELECT user_id::text FROM audit_log WHERE entity = 'app_user'"
        " AND entity_pk = %s ORDER BY audit_id DESC LIMIT 1",
        ANALYST,
    )
    assert actor == ADMIN
    assert (
        _scalar(
            owner,
            "SELECT count(*) FROM audit_log WHERE entity = 'app_user'"
            " AND (data_before ? 'password_hash' OR data_after ? 'password_hash')",
        )
        == 0
    )


# ---------- activation ----------


def test_deactivate_and_reactivate(api: Any, owner: psycopg.Connection) -> None:
    reply = api("POST", f"/v1/users/{ANALYST}/deactivation")
    assert reply.status_code == 200 and reply.get_json()["is_active"] is False
    reply = api("DELETE", f"/v1/users/{ANALYST}/deactivation")
    assert reply.status_code == 200 and reply.get_json()["is_active"] is True
    assert (
        _scalar(
            owner,
            "SELECT count(*) FROM users_events.outbox WHERE entity_id = %s",
            ANALYST,
        )
        == 2
    )


def test_the_only_administrator_cannot_be_deactivated(
    api: Any, owner: psycopg.Connection
) -> None:
    reply = api("POST", f"/v1/users/{ADMIN}/deactivation")
    assert _code(reply) == (409, "administrator_required")
    assert _scalar(owner, "SELECT is_active FROM app_user WHERE user_id = %s", ADMIN)


# ---------- passwords ----------


def _set_password(owner: psycopg.Connection, user_id: str, password: str) -> None:
    owner.execute(
        "UPDATE app_user SET password_hash = %s WHERE user_id = %s",
        (PasswordHasher().hash(password), user_id),
    )


def test_a_user_changes_their_own_password(api: Any, owner: psycopg.Connection) -> None:
    _set_password(owner, ANALYST, "the old password")
    before = _scalar(
        owner, "SELECT count(*) FROM users_events.outbox WHERE entity_id = %s", ANALYST
    )
    reply = api(
        "PUT",
        f"/v1/users/{ANALYST}/password",
        as_user=(ANALYST, "ANALYST"),
        json_body={
            "current_password": "the old password",
            "new_password": "a new one!!!",
        },
    )
    assert reply.status_code == 204
    stored = _scalar(
        owner, "SELECT password_hash FROM app_user WHERE user_id = %s", ANALYST
    )
    assert PasswordHasher().verify(stored, "a new one!!!")
    # The captured change is what ends the user's sessions in auth-service.
    after = _scalar(
        owner, "SELECT count(*) FROM users_events.outbox WHERE entity_id = %s", ANALYST
    )
    assert after == before + 1


def test_a_self_change_needs_the_current_password(api: Any) -> None:
    reply = api(
        "PUT",
        f"/v1/users/{ANALYST}/password",
        as_user=(ANALYST, "ANALYST"),
        json_body={"new_password": "a new one!!!"},
    )
    assert _code(reply) == (422, "invalid_body")


def test_another_users_password_is_the_administrators_alone(api: Any) -> None:
    reply = api(
        "PUT",
        f"/v1/users/{STORE_MANAGER}/password",
        as_user=(ANALYST, "ANALYST"),
        json_body={
            "current_password": "whatever it is",
            "new_password": "a new one!!!",
        },
    )
    assert _code(reply) == (403, "forbidden_role")
    reply = api(
        "PUT",
        f"/v1/users/{STORE_MANAGER}/password",
        json_body={"new_password": "an admin reset"},
    )
    assert reply.status_code == 204


def test_the_sixth_failed_check_in_fifteen_minutes_is_rate_limited(
    api: Any, owner: psycopg.Connection
) -> None:
    _set_password(owner, ANALYST, "the real password")
    attempt = {"current_password": "not the password", "new_password": "a new one!!!"}
    for _ in range(5):
        reply = api(
            "PUT",
            f"/v1/users/{ANALYST}/password",
            as_user=(ANALYST, "ANALYST"),
            json_body=attempt,
        )
        assert _code(reply) == (422, "invalid_current_password")
    reply = api(
        "PUT",
        f"/v1/users/{ANALYST}/password",
        as_user=(ANALYST, "ANALYST"),
        json_body=attempt | {"current_password": "the real password"},
    )
    assert _code(reply) == (429, "rate_limited")
    assert 0 < int(reply.headers["Retry-After"]) <= 900


def test_a_revoked_session_is_refused(api: Any, sessions: Any) -> None:
    sessions.revoked.add("revoked-session")
    reply = api.client.get(
        "/v1/users/me",
        headers={
            "Authorization": "Bearer " + api.sign(ANALYST, "ANALYST", "revoked-session")
        },
    )
    assert _code(reply) == (401, "session_revoked")


# ---------- transfer ----------


def test_the_administrator_transfers_the_seat(
    api: Any, owner: psycopg.Connection
) -> None:
    reply = api(
        "POST", "/v1/administrator-transfers", json_body={"successor_user_id": ANALYST}
    )
    assert reply.status_code == 204
    roles = dict(
        owner.execute(
            "SELECT u.user_id::text, r.code FROM app_user u JOIN role r USING (role_id)"
            " WHERE u.user_id IN (%s, %s)",
            (ADMIN, ANALYST),
        ).fetchall()
    )
    assert roles == {ADMIN: "ANALYST", ANALYST: "ADMIN"}
    assert _administrators(owner) == 1


def test_transfer_refusals(api: Any, owner: psycopg.Connection) -> None:
    def transfer(successor: str) -> tuple[int, str | None]:
        return _code(
            api(
                "POST",
                "/v1/administrator-transfers",
                json_body={"successor_user_id": successor},
            )
        )

    assert transfer(ADMIN) == (422, "invalid_successor")
    assert transfer("99999999-9999-9999-9999-999999999999") == (
        422,
        "invalid_successor",
    )
    owner.execute(
        "UPDATE app_user SET is_active = FALSE WHERE user_id = %s", (ANALYST,)
    )
    assert transfer(ANALYST) == (409, "successor_inactive")
    assert _administrators(owner) == 1


def test_a_stale_administrator_token_cannot_transfer(
    api: Any, owner: psycopg.Connection
) -> None:
    # ANALYST's token still says ADMIN after the seat moved away from them.
    reply = api(
        "POST",
        "/v1/administrator-transfers",
        as_user=(ANALYST, "ADMIN"),
        json_body={"successor_user_id": STORE_MANAGER},
    )
    assert _code(reply) == (409, "single_administrator")
    assert (
        _scalar(owner, "SELECT user_id::text FROM app_user WHERE role_id = 1") == ADMIN
    )


@pytest.mark.parametrize("field", ["email", "role_code"])
def test_a_final_newline_is_refused_on_create(api: Any, field: str) -> None:
    """#594: Python's `$` matches before a final newline; the contract's
    patterns mean the whole value."""
    reply = api(
        "POST", "/v1/users", json_body=NEW_USER | {field: NEW_USER[field] + "\n"}
    )
    assert _code(reply) == (422, "invalid_body")
    assert reply.get_json()["details"] == [{"field": field, "code": "invalid_format"}]


def test_a_final_newline_in_an_edited_email_is_refused(
    api: Any, owner: psycopg.Connection
) -> None:
    reply = api(
        "PATCH", f"/v1/users/{ANALYST}", json_body={"email": "new@example.com\n"}
    )
    assert _code(reply) == (422, "invalid_body")
    assert (
        _scalar(owner, "SELECT email FROM app_user WHERE user_id = %s", ANALYST)
        == "user14@mosaiq-demo.com"
    )
