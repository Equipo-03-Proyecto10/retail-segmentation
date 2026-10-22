"""Id allocation and conditional moves under contention (F14-28, E-14, RN-31).

The allocation lock is the web's own key, 221001. A test that holds it on
another connection stands in for the web's create mid-transaction.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import psycopg
import pytest

from tests.conftest import ADMIN

pytestmark = pytest.mark.postgres

LOCK_KEY = 221001
BODY = {
    "name": "Concurrent",
    "label_code": "LOYAL",
    "starts_on": "2099-01-01",
    "ends_on": "2099-12-31",
}


def _waiting(owner: psycopg.Connection, expected: int, seconds: float = 10) -> int:
    """Wait until `expected` backends of this database wait on a lock."""
    deadline = time.monotonic() + seconds
    waiting = 0
    while time.monotonic() < deadline:
        waiting = owner.execute(
            "SELECT count(*) FROM pg_stat_activity"
            " WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ).fetchone()[0]
        if waiting >= expected:
            break
        time.sleep(0.05)
    return waiting


def test_a_create_waits_for_the_web_s_allocation_lock(
    service: Any, token: Any, owner: psycopg.Connection
) -> None:
    holder = psycopg.connect(owner.info.dsn, autocommit=True)
    holder.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    replies: list[Any] = []
    client = service.test_client()
    headers = {"Authorization": "Bearer " + token(ADMIN, "ADMIN")}
    racer = threading.Thread(
        target=lambda: replies.append(
            client.post("/v1/campaigns", json=BODY, headers=headers)
        )
    )
    try:
        racer.start()
        assert _waiting(owner, 1) >= 1, "the create never waited on the lock"
        time.sleep(0.3)
        assert racer.is_alive() and not replies
        holder.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        racer.join(timeout=10)
    finally:
        holder.close()
    assert not racer.is_alive()
    assert replies[0].status_code == 201
    assert replies[0].get_json()["campaign_id"] == 31


def test_ten_concurrent_creates_get_ten_distinct_ids(
    service: Any, token: Any, owner: psycopg.Connection
) -> None:
    headers = {"Authorization": "Bearer " + token(ADMIN, "ADMIN")}
    replies: list[Any] = [None] * 10

    def create(index: int) -> None:
        replies[index] = service.test_client().post(
            "/v1/campaigns", json=BODY | {"name": f"Racer {index}"}, headers=headers
        )

    threads = [threading.Thread(target=create, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert [reply.status_code for reply in replies] == [201] * 10
    ids = [reply.get_json()["campaign_id"] for reply in replies]
    assert len(set(ids)) == 10
    assert sorted(ids) == list(range(31, 41))
    assert owner.execute("SELECT count(*) FROM campaign").fetchone()[0] == 40


def _collide(connection: Any) -> None:
    """Another writer that skipped the lock already holds the id: a 23505."""
    connection.execute(
        "INSERT INTO campaign (campaign_id, name, label_code, starts_on, ends_on,"
        " status) SELECT max(campaign_id), 'Twin', 'LOYAL', DATE '2099-01-01',"
        " DATE '2099-01-02', 'DRAFT' FROM campaign"
    )


def test_an_id_collision_is_retried_at_most_three_times(
    service: Any, token: Any, monkeypatch: Any
) -> None:
    from app import rules

    attempts: list[int] = []
    real = rules.create_campaign_once
    headers = {"Authorization": "Bearer " + token(ADMIN, "ADMIN")}
    client = service.test_client()

    def collide_twice(connection: Any, **values: Any) -> Any:
        attempts.append(1)
        if len(attempts) <= 2:
            _collide(connection)
        return real(connection, **values)

    monkeypatch.setattr(rules, "create_campaign_once", collide_twice)
    created = client.post("/v1/campaigns", json=BODY, headers=headers)
    assert created.status_code == 201
    assert len(attempts) == 3

    attempts.clear()

    def always_collide(connection: Any, **values: Any) -> Any:
        attempts.append(1)
        _collide(connection)

    monkeypatch.setattr(rules, "create_campaign_once", always_collide)
    exhausted = client.post("/v1/campaigns", json=BODY, headers=headers)
    assert len(attempts) == 3
    assert (exhausted.status_code, exhausted.get_json()["code"]) == (
        503,
        "database_unavailable",
    )


def test_two_concurrent_moves_with_one_expected_status_give_one_winner(
    service: Any, token: Any, owner: psycopg.Connection
) -> None:
    campaign_id = owner.execute(
        "INSERT INTO campaign (campaign_id, name, label_code, starts_on, ends_on,"
        " status) SELECT max(campaign_id) + 1, 'Raced', 'LOYAL', CURRENT_DATE,"
        " CURRENT_DATE + 30, 'DRAFT' FROM campaign RETURNING campaign_id"
    ).fetchone()[0]
    headers = {"Authorization": "Bearer " + token(ADMIN, "ADMIN")}
    replies: list[Any] = [None, None]
    targets = ["ACTIVE", "CANCELLED"]

    def move(index: int) -> None:
        replies[index] = service.test_client().post(
            f"/v1/campaigns/{campaign_id}/transitions",
            json={"to": targets[index], "expected_status": "DRAFT"},
            headers=headers,
        )

    # Hold the row so both moves read DRAFT and queue on their UPDATE.
    blocker = psycopg.connect(owner.info.dsn)
    blocker.execute(
        "SELECT 1 FROM campaign WHERE campaign_id = %s FOR UPDATE", (campaign_id,)
    )
    threads = [threading.Thread(target=move, args=(i,)) for i in range(2)]
    try:
        for thread in threads:
            thread.start()
        assert _waiting(owner, 2) >= 2, "the moves never queued on the row"
        blocker.rollback()
    finally:
        blocker.close()
    for thread in threads:
        thread.join(timeout=10)

    answers = sorted(
        (reply.status_code, (reply.get_json() or {}).get("code")) for reply in replies
    )
    assert answers == [(200, None), (409, "status_changed")]
    winner = targets[[reply.status_code for reply in replies].index(200)]
    assert (
        owner.execute(
            "SELECT status FROM campaign WHERE campaign_id = %s", (campaign_id,)
        ).fetchone()[0]
        == winner
    )
    assert (
        owner.execute(
            "SELECT count(*) FROM audit_log WHERE entity = 'campaign'"
            " AND entity_pk = %s AND action = 'UPDATE'",
            (str(campaign_id),),
        ).fetchone()[0]
        == 1
    )
