"""Suite-wide fixtures.

Server-side sessions (ADR-0022, #251): every request re-reads its session
from `app_session`. Most route tests sign in by writing `user_id` and
`role_code` straight into the cookie through `session_transaction()`, against
a mocked connection, so by default the lookup is stubbed to trust what the
test wrote. Tests of the lookup itself opt out with `@pytest.mark.real_sessions`.
"""

from __future__ import annotations

import pytest
from flask import session

from web.db.sessions import SessionPrincipal


def _trust_the_cookie(_session_id):
    if not session.get("role_code"):
        return None
    return SessionPrincipal(
        user_id=session["user_id"],
        role_id=session.get("role_id", 0),
        role_code=session["role_code"],
        name=session.get("name", ""),
    )


@pytest.fixture(autouse=True)
def _stub_session_lookup(request: pytest.FixtureRequest, monkeypatch) -> None:
    if request.node.get_closest_marker("real_sessions"):
        return
    monkeypatch.setattr("web.middleware.authz._principal_for", _trust_the_cookie)
