"""Suite-wide fixtures.

Server-side sessions (ADR-0022, #251): every request re-reads its session
from `app_session`. Most route tests sign in by writing `user_id` and
`role_code` straight into the cookie through `session_transaction()`, against
a mocked connection, so by default the lookup is stubbed to trust what the
test wrote. Tests of the lookup itself opt out with `@pytest.mark.real_sessions`.

CSRF (#294, ADR-0025): every unsafe-method request is checked against a
per-session token and, when present, the `Origin` header. Route tests post
plain `data={...}` dicts with no token, so by default the check is stubbed
off the same way; tests of the check itself opt out with
`@pytest.mark.real_csrf`.

Notifications (#419, ADR-0047): every signed-in page asks the database for the
navigation's unread count. Route tests run against a mocked connection, and a
count read through it would be a `MagicMock` drawn as a badge on every page, and
would open a connection that tests of a refused request assert was never
opened. So by default the count is stubbed to none; tests of the badge itself
opt out with `@pytest.mark.real_notification_count`.

The same page read announces any experiment that has ended (ADR-0047), a write
and a commit through the same mocked connection, which tests that assert on the
statements a route ran would count. So by default that is stubbed to announce
nothing; tests of the announcer opt out with `@pytest.mark.real_experiment_announcer`.
"""

from __future__ import annotations

import os
import shutil

import pytest
from flask import session

from web.db.sessions import SessionPrincipal


def _require_postgres(what: str) -> None:
    """Skip a `postgres` test when what it needs is absent, except in CI.

    The database job sets MOSAIQ_REQUIRE_POSTGRES=1, so a missing database or
    PostgreSQL binary fails there instead of passing the test silently.
    """
    if os.environ.get("MOSAIQ_REQUIRE_POSTGRES") == "1":
        pytest.fail(f"{what} is missing, and MOSAIQ_REQUIRE_POSTGRES forbids skipping")
    pytest.skip(f"{what} is missing; this test needs a real PostgreSQL")


@pytest.fixture(scope="session")
def postgres_binaries() -> dict[str, str]:
    """initdb, pg_ctl and psql, for tests that need a scratch cluster."""
    paths = {}
    for name in ("initdb", "pg_ctl", "psql"):
        path = shutil.which(name)
        if path is None:
            _require_postgres(f"the {name} binary")
        paths[name] = path
    return paths


@pytest.fixture(scope="session")
def owner_url() -> str:
    """The database owner's URL for a database loaded with the three scripts."""
    url = os.environ.get("MOSAIQ_TEST_OWNER_URL")
    if not url:
        _require_postgres("MOSAIQ_TEST_OWNER_URL")
    return url


@pytest.fixture(scope="session")
def app_url() -> str:
    """retail_app's URL for the same database."""
    url = os.environ.get("MOSAIQ_TEST_APP_URL")
    if not url:
        _require_postgres("MOSAIQ_TEST_APP_URL")
    return url


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


@pytest.fixture(autouse=True)
def _stub_csrf(request: pytest.FixtureRequest, monkeypatch) -> None:
    if request.node.get_closest_marker("real_csrf"):
        return
    monkeypatch.setattr("web.middleware.csrf.verify_csrf", lambda: None)


@pytest.fixture(autouse=True)
def _stub_notification_count(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.get_closest_marker("real_notification_count"):
        return
    monkeypatch.setattr("web.routes.notifications._unread_for_this_page", lambda: None)


@pytest.fixture(autouse=True)
def _stub_experiment_announcer(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.get_closest_marker("real_experiment_announcer"):
        return
    monkeypatch.setattr(
        "web.routes.notifications.announce_experiment_ends", lambda connection: 0
    )
