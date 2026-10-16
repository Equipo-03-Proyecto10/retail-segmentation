from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from conftest import TEST_SUFFIX
from flask import Flask

from mosaiq_kit import Kit, Representation


def _database_url() -> str:
    value = os.environ.get("KIT_TEST_DATABASE_URL")
    if value:
        return value
    if os.environ.get("KIT_REQUIRE_SERVICES") == "1":
        pytest.fail("KIT_TEST_DATABASE_URL is not set")
    pytest.skip("KIT_TEST_DATABASE_URL is not set")


def _headers(tokens: dict[str, str], role: str = "valid_writer") -> dict[str, str]:
    return {"Authorization": "Bearer " + tokens[role]}


def test_pool_is_capped_at_three(app) -> None:
    kit = app.extensions["mosaiq_kit"]
    assert kit.db.pool.max_size == 3
    assert kit.db.pool.min_size == 0


def test_transaction_sets_the_bound_audit_actor(
    monkeypatch: pytest.MonkeyPatch,
    configured_environment,
    contracts_dir: Path,
    tokens: dict[str, str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", _database_url())
    app = Flask("database-actor")
    kit = Kit(
        app,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=contracts_dir,
    )

    @kit.operation("createNote")
    def create_note() -> Representation:
        with kit.db.transaction() as connection:
            actor = connection.execute(
                "SELECT current_setting(%s, true)", ("mosaiq.user_id",)
            ).fetchone()[0]
        return Representation("Note", {"actor": actor})

    response = app.test_client().post(
        "/v1/notes",
        headers=_headers(tokens),
        json={"title": "Audit", "amount": "1.00"},
    )
    assert response.status_code == 200
    assert response.json["actor"] == "3b6a2b8b-3e24-4ba6-9ef9-fb3e6b4c9ad1"
    kit.db.close()


def test_unreachable_database_uses_the_public_vector_expectation(
    monkeypatch: pytest.MonkeyPatch,
    configured_environment,
    contracts_dir: Path,
    tokens: dict[str, str],
    expected: dict[str, dict[str, object]],
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://service@127.0.0.1:1/retail?connect_timeout=1"
    )
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT_SECONDS", "0.3")
    app = Flask("database-down")
    kit = Kit(
        app,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=contracts_dir,
    )

    @kit.operation("getNote")
    def get_note(note_id: int) -> Representation:
        with kit.db.connection() as connection:
            connection.execute("SELECT %s", (note_id,)).fetchone()
        return Representation("Note", {"note_id": note_id})

    response = app.test_client().get(
        "/v1/notes/1", headers=_headers(tokens, "valid_reader")
    )
    wanted = expected["database_unavailable"]
    assert response.status_code == wanted["status"]
    assert response.json["code"] == wanted["code"]
    kit.db.close()


def test_mid_request_connection_failure_is_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
    configured_environment,
    contracts_dir: Path,
    tokens: dict[str, str],
) -> None:
    database_url = _database_url()
    monkeypatch.setenv("DATABASE_URL", database_url)
    app = Flask("database-terminated")
    kit = Kit(
        app,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=contracts_dir,
    )

    @kit.operation("getNote")
    def get_note(note_id: int) -> Representation:
        with kit.db.connection() as connection:
            backend = connection.execute(
                "SELECT pg_backend_pid(), %s", (note_id,)
            ).fetchone()[0]
            with psycopg.connect(database_url, autocommit=True) as killer:
                killer.execute("SELECT pg_terminate_backend(%s)", (backend,))
            connection.execute("SELECT %s", (note_id,)).fetchone()
        return Representation("Note", {"note_id": note_id})

    response = app.test_client().get(
        "/v1/notes/1", headers=_headers(tokens, "valid_reader")
    )
    assert response.status_code == 503
    assert response.json["code"] == "database_unavailable"
    kit.db.close()


def test_readiness_keeps_schema_not_ready_when_database_check_fails(
    monkeypatch: pytest.MonkeyPatch,
    configured_environment,
    contracts_dir: Path,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://service@127.0.0.1:1/retail?connect_timeout=1"
    )
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT_SECONDS", "0.3")
    app = Flask("database-readiness")
    holder: dict[str, Kit] = {}

    def check() -> bool:
        with holder["kit"].db.connection() as connection:
            connection.execute("SELECT %s", (1,)).fetchone()
        return True

    kit = Kit(
        app,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=contracts_dir,
        ready_checks=[check],
    )
    holder["kit"] = kit
    response = app.test_client().get("/health/ready")
    assert response.status_code == 503
    assert response.json["code"] == "schema_not_ready"
    kit.db.close()
