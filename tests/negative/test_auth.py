"""Sessions and stateful authentication limits at the deployed edge (#459)."""

from __future__ import annotations

import secrets

import pytest

from tests.conformance.harness import ROLE_CODES
from tests.negative.stack import USERS, Evidence, Stack, require, run_last


@pytest.mark.parametrize("role", ROLE_CODES)
def test_each_role_has_a_live_real_session(deployed_stack: Stack, role: str) -> None:
    session = deployed_stack.sessions[role]

    reply = session.current()

    require(reply, 200)
    assert reply.json()["sub"] == deployed_stack.accounts[role].user_id
    assert reply.json()["role"] == role


def test_refresh_reuse_revokes_the_whole_session(
    deployed_stack: Stack, evidence: Evidence
) -> None:
    # Its own session: reuse ends the session, and the role cases still need
    # the shared one.
    session = deployed_stack.fresh_session("CUSTOMER")

    old_refresh, rotated = session.rotate()
    require(rotated, 200)
    reused = session.reuse(old_refresh)
    after = session.current()

    held = evidence.check(
        "auth", "a rotated refresh token reused", reused, 401, "refresh_reused"
    )
    held &= evidence.check(
        "auth", "that session's access token, next call", after, 401, "session_revoked"
    )
    assert held, (reused.outcome(), after.outcome())


def test_sign_out_revokes_the_access_token(deployed_stack: Stack) -> None:
    session = deployed_stack.fresh_session("INVENTORY_PLANNER")

    require(session.sign_out(), 204)

    require(session.current(), 401, "session_revoked")


def test_five_failures_lock_the_email_and_ip_pair(
    deployed_stack: Stack, evidence: Evidence
) -> None:
    # A run-unique unknown address cannot collide with a real account or a
    # fifteen-minute failure counter left by a different suite invocation.
    email = f"f18-04-failure-{deployed_stack.run_id}@example.test"
    for _ in range(5):
        require(
            deployed_stack.sign_in_reply(email, "definitely-wrong-password"),
            401,
            "invalid_credentials",
        )

    locked = deployed_stack.sign_in_reply(email, "definitely-wrong-password")
    retry = locked.headers.get("Retry-After", "")
    held = evidence.check(
        "auth",
        "sixth sign-in after 5 failures, same email and IP",
        locked,
        429,
        "rate_limited",
    )
    # NGINX's per-IP reply is about ten seconds. This proves that the
    # independent fifteen-minute email/IP failure lock answered instead.
    held &= evidence.record(
        "auth",
        "that refusal's Retry-After",
        "800 to 900 s",
        f"{retry} s",
        retry.isdigit() and 800 <= int(retry) <= 900,
    )
    assert held, (locked.outcome(), retry)


def test_sixth_wrong_current_password_is_rate_limited(
    deployed_stack: Stack, evidence: Evidence
) -> None:
    # The limit is per account and lasts fifteen minutes, so a shared account
    # would refuse the next run's first attempt: this case has one of its own.
    payload = {
        "current_password": "not-the-current-password",
        "new_password": "Unused-" + secrets.token_urlsafe(18),
    }
    with deployed_stack.throwaway_account("ANALYST") as account:
        token = deployed_stack.sign_in(account).token()
        path = f"{USERS}/{account.user_id}/password"
        for _ in range(5):
            require(
                deployed_stack.edge.json("PUT", path, payload, token=token),
                422,
                "invalid_current_password",
            )
        locked = deployed_stack.edge.json("PUT", path, payload, token=token)

    held = evidence.check(
        "users", "sixth wrong current password", locked, 429, "rate_limited"
    )
    assert held, locked.outcome()
    assert 0 < int(locked.headers["Retry-After"]) <= 900


@run_last
def test_sign_in_is_rate_limited_by_ip(
    deployed_stack: Stack, evidence: Evidence
) -> None:
    """Vary email so the email/IP lock cannot mask the IP-only limit."""
    refused = None
    accepted = 0
    for attempt in range(35):
        email = f"f18-04-ip-{deployed_stack.run_id}-{attempt}@example.test"
        reply = deployed_stack.sign_in_reply(
            email, "definitely-wrong-password", paced=False
        )
        if reply.status == 429:
            refused = reply
            break
        require(reply, 401, "invalid_credentials")
        accepted += 1

    assert accepted > 0
    assert refused is not None, "The per-IP sign-in limit accepted 35 attempts."
    held = evidence.check(
        "auth",
        f"sign-in attempt {accepted + 1} from one IP, varied emails",
        refused,
        429,
        "rate_limited",
    )
    assert held, refused.outcome()
    assert 0 < int(refused.headers["Retry-After"]) <= 60
