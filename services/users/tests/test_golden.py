"""The port held to the web's captured outcomes (ADR-0045, E-17).

tests/golden/users.json is what web/services/users.py did on each case, as
captured by `python -m tests.golden.capture --service users`. Each case is
replayed here through the service's HTTP surface on a fresh copy of the seed.
An accepted case must be accepted and leave the same state behind; a refused
case must be refused. Where the service answers differently on purpose, the
case is in DIFFERS with the reason, and the service's own answer is asserted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from tests.conftest import ADMIN, ANALYST

pytestmark = pytest.mark.postgres
FIXTURE = json.loads((Path(__file__).parent / "golden/users.json").read_text())
CASES = {case["id"]: case for case in FIXTURE["cases"]}
PASSWORD = "golden-fixture-password"

# Where the port deliberately does not do what the web does. ADR-0036 decides
# each one: its "own check" and "transfer" rules for users-service.
DIFFERS = {
    "promote-to-second-administrator": (
        "The web refuses with the second-administrator message; the contract "
        "moves the ADMIN role only through the transfer, so any PATCH to or "
        "from ADMIN is use_administrator_transfer.",
        (409, "use_administrator_transfer"),
    ),
    "edit-into-second-administrator": (
        "As above: an edit naming ADMIN is use_administrator_transfer.",
        (409, "use_administrator_transfer"),
    ),
    "demote-only-administrator": (
        "The web refuses to leave nobody; the service refuses any PATCH away "
        "from ADMIN before that question arises.",
        (409, "use_administrator_transfer"),
    ),
    "transfer-from-non-administrator": (
        "The web names both parties; over HTTP the transferring party is the "
        "caller, so a caller who does not hold the seat is single_administrator.",
        (409, "single_administrator"),
    ),
    "validate-malformed-email": (
        "The web's form validation is the contract's schema here: invalid_body.",
        (422, "invalid_body"),
    ),
    "create-malformed-email": (
        "The web lets the database's CHECK refuse; the contract's Email schema "
        "refuses first, with invalid_body, so no 23514 reaches the client.",
        (422, "invalid_body"),
    ),
}


# Decided by ADR-0036 on inputs the fixture has no case for, so they are
# asserted against the service alone (tests/test_api.py::test_transfer_refusals).
SERVICE_ONLY = {
    "self-transfer": (
        "The web returns silently when the successor is the incumbent; ADR-0036 "
        "makes it 422 invalid_successor.",
        (422, "invalid_successor"),
    ),
    "inactive-successor": (
        "The web transfers to an inactive account; ADR-0036 refuses it with "
        "409 successor_inactive.",
        (409, "successor_inactive"),
    ),
}


def _request(case: dict[str, Any]) -> tuple[str, str, dict[str, Any], tuple]:
    given = case["input"]
    call = given["call"]
    if call in ("create_user", "validate_user"):
        body = {
            "name": "Golden user",
            "email": given["email"],
            "role_code": given["role_code"],
            "password": PASSWORD,
        }
        return "POST", "/v1/users", body, (ADMIN, "ADMIN")
    if call == "change_role":
        body = {"role_code": given["role_code"]}
        return "PATCH", f"/v1/users/{given['user_id']}", body, (ADMIN, "ADMIN")
    if call == "update_user":
        body = {k: given[k] for k in ("name", "email", "role_code")}
        return "PATCH", f"/v1/users/{given['user_id']}", body, (ADMIN, "ADMIN")
    if call == "set_active":
        method = "DELETE" if given["is_active"] else "POST"
        return (
            method,
            f"/v1/users/{given['user_id']}/deactivation",
            {},
            (
                ADMIN,
                "ADMIN",
            ),
        )
    if call == "transfer_administrator":
        body = {"successor_user_id": given["to_user_id"]}
        return (
            "POST",
            "/v1/administrator-transfers",
            body,
            (
                given["from_user_id"],
                "ADMIN",
            ),
        )
    raise AssertionError("no HTTP mapping for " + call)


def _observed(case_id: str, owner: psycopg.Connection) -> dict[str, Any]:
    def role(user_id: str) -> str:
        return owner.execute(
            "SELECT r.code FROM app_user u JOIN role r USING (role_id)"
            " WHERE u.user_id = %s",
            (user_id,),
        ).fetchone()[0]

    given = CASES[case_id]["input"]
    if case_id == "transfer-administrator":
        return {
            "from_role": role(given["from_user_id"]),
            "to_role": role(given["to_user_id"]),
        }
    if case_id == "create-analyst":
        row = owner.execute(
            "SELECT r.code FROM app_user u JOIN role r USING (role_id)"
            " WHERE u.email = %s",
            (given["email"],),
        ).fetchone()
        return {"role": row[0]}
    if case_id == "deactivate-analyst":
        row = owner.execute(
            "SELECT is_active FROM app_user WHERE user_id = %s", (given["user_id"],)
        ).fetchone()
        return {"is_active": row[0]}
    return {}


def test_the_fixture_is_the_users_port() -> None:
    assert FIXTURE["header"]["port"] == "users"
    assert set(DIFFERS) <= set(CASES)
    assert not set(SERVICE_ONLY) & set(CASES)


@pytest.mark.parametrize("difference", sorted(SERVICE_ONLY))
def test_the_service_only_differences_hold(
    difference: str, api: Any, owner: psycopg.Connection
) -> None:
    successor = ADMIN
    if difference == "inactive-successor":
        successor = ANALYST
        owner.execute(
            "UPDATE app_user SET is_active = FALSE WHERE user_id = %s", (successor,)
        )
    reply = api(
        "POST",
        "/v1/administrator-transfers",
        json_body={"successor_user_id": successor},
    )
    answer = (reply.status_code, reply.get_json()["code"])
    assert answer == SERVICE_ONLY[difference][1]


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_service_matches_the_web(
    case_id: str, api: Any, owner: psycopg.Connection
) -> None:
    case = CASES[case_id]
    method, path, body, caller = _request(case)
    reply = api(method, path, as_user=caller, json_body=body or None)
    answer = (reply.status_code, (reply.get_json(silent=True) or {}).get("code"))
    web = case["outcome"]

    if case_id in DIFFERS:
        assert answer == DIFFERS[case_id][1]
        return
    if web["outcome"] == "accepted":
        assert 200 <= reply.status_code < 300, answer
        assert _observed(case_id, owner) == web.get("observed", {})
    else:
        assert answer[0] == 409, answer
        expected = {
            "SingleAdministratorError": {
                "single_administrator",
                "administrator_required",
            },
            "DuplicateEmailError": {"email_taken"},
        }[web["error"]]
        assert answer[1] in expected
    assert (
        owner.execute("SELECT count(*) FROM app_user WHERE role_id = 1").fetchone()[0]
        == 1
    )
