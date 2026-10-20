"""Two writers race for the administrator seat (RN-01, F14-04).

The web and the service both write `app_user`. While the web's transfer holds
its transaction open, the service's transfer waits on the row lock; once the
web commits, the seat has moved and the service refuses with 409, leaving
exactly one administrator.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import psycopg
import pytest

from tests.conftest import ADMIN, ANALYST, STORE_MANAGER

pytestmark = pytest.mark.postgres


def test_a_transfer_racing_the_webs_waits_then_refuses(
    api: Any, database: dict[str, str], owner: psycopg.Connection
) -> None:
    replies: list[Any] = []
    web = psycopg.connect(database["owner"])
    try:
        with web.cursor() as cursor:
            cursor.execute("SET ROLE retail_app")
            # The web's transfer (web/services/users.py), mid-transaction:
            # the incumbent demoted to the successor's role, then promoted.
            cursor.execute(
                "UPDATE app_user SET role_id = (SELECT role_id FROM app_user"
                " WHERE user_id = %s) WHERE user_id = %s",
                (ANALYST, ADMIN),
            )
            cursor.execute(
                "UPDATE app_user SET role_id = 1 WHERE user_id = %s", (ANALYST,)
            )

        racer = threading.Thread(
            target=lambda: replies.append(
                api(
                    "POST",
                    "/v1/administrator-transfers",
                    json_body={"successor_user_id": STORE_MANAGER},
                )
            )
        )
        racer.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            waiting = owner.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ).fetchone()[0]
            if waiting:
                break
            time.sleep(0.05)
        assert waiting, "the service's transfer never waited on the web's lock"
        assert racer.is_alive()
        web.commit()
        racer.join(timeout=10)
    finally:
        web.close()

    assert len(replies) == 1
    reply = replies[0]
    assert (reply.status_code, reply.get_json()["code"]) == (
        409,
        "single_administrator",
    )
    assert owner.execute(
        "SELECT array_agg(user_id::text) FROM app_user WHERE role_id = 1"
    ).fetchone()[0] == [ANALYST]
