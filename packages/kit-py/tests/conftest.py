from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import redis
from flask import Flask

from mosaiq_kit import Kit, Page, Representation, money

KIT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = KIT_ROOT.parents[1]
VECTORS = REPOSITORY / "packages/kit-vectors"
EXAMPLE_CONTRACT = KIT_ROOT / "example/contracts/kit-example"
COMMON_CONTRACT = REPOSITORY / "contracts/common/v1"
LIVE_SESSION = "087357a4-7d4b-4bdd-936d-8e403790303e"
TEST_SUFFIX = os.environ.get("KIT_TEST_SUFFIX", "kit_test")
TEST_SOURCE_SUFFIX = os.environ.get("KIT_TEST_SOURCE_SUFFIX", "kit_source")
TEST_OWN_STREAM = TEST_SUFFIX + ".v1.changed"
TEST_CONSUMED_STREAM = TEST_SOURCE_SUFFIX + ".v1.changed"
if re.fullmatch(r"[a-z][a-z0-9_]*", TEST_SUFFIX) is None:
    raise ValueError("KIT_TEST_SUFFIX must be a lowercase SQL identifier.")


def _redis_acl(password: str) -> tuple[str, list[str]]:
    design = (REPOSITORY / "docs/datastores/redis-design.md").read_text()
    marker = (
        "Each service commits `services/<name>/redis/<name>.acl`, one line, built "
        "from\nthese rules:"
    )
    rules = design.split(marker, 1)[1].split("```text", 1)[1].split("```", 1)[0]
    rendered = " ".join(
        line.strip()
        for line in rules.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    rendered = (
        rendered.replace("<suffix>", TEST_SUFFIX)
        .replace("<own streams>", TEST_OWN_STREAM)
        .replace("<consumed streams>", TEST_CONSUMED_STREAM)
        .replace("{{password}}", ">" + password)
    )
    arguments = re.findall(r"\([^)]*\)|\S+", rendered)
    if arguments[:2] != ["user", "svc_" + TEST_SUFFIX]:
        raise ValueError("The Redis ACL template did not render a service user.")
    return arguments[1], arguments[2:]


def _service_missing(message: str) -> None:
    if os.environ.get("KIT_REQUIRE_SERVICES") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def tokens() -> dict[str, str]:
    return json.loads((VECTORS / "tokens.json").read_text())


@pytest.fixture(scope="session")
def expected() -> dict[str, dict[str, object]]:
    return json.loads((VECTORS / "expected.json").read_text())


@pytest.fixture(scope="session")
def contracts_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("contracts")
    shutil.copytree(EXAMPLE_CONTRACT, target / "kit-example")
    shutil.copytree(COMMON_CONTRACT, target / "common/v1")
    return target


@pytest.fixture(scope="session")
def redis_admin() -> Iterator[redis.Redis]:
    url = os.environ.get("KIT_TEST_REDIS_URL")
    if not url:
        _service_missing("KIT_TEST_REDIS_URL is not set")
    client = redis.Redis.from_url(url, decode_responses=True)
    try:
        client.ping()
    except redis.RedisError:
        _service_missing("The kit Redis service is unreachable")
    yield client
    client.close()


@pytest.fixture(scope="session")
def service_redis(redis_admin: redis.Redis) -> Iterator[redis.Redis]:
    password = secrets.token_urlsafe(24)
    user, rules = _redis_acl(password)
    redis_admin.execute_command("ACL", "DELUSER", user)
    redis_admin.execute_command("ACL", "SETUSER", user, *rules)
    password_variable = "REDIS_" + TEST_SUFFIX.upper() + "_PASSWORD"
    os.environ[password_variable] = password
    os.environ["REDIS_STATE_URL"] = os.environ["KIT_TEST_REDIS_URL"]
    redis_admin.set("pub:auth:v1:live:" + LIVE_SESSION, "1", ex=3600)
    parsed_url = urlsplit(os.environ["KIT_TEST_REDIS_URL"])
    client = redis.Redis(
        host=parsed_url.hostname,
        port=parsed_url.port,
        db=int(parsed_url.path.removeprefix("/") or 0),
        username=user,
        password=password,
        socket_timeout=3,
        decode_responses=True,
    )
    client.ping()
    yield client
    for pattern in (
        TEST_SUFFIX + ":rl:*",
        "stream:" + TEST_OWN_STREAM,
        "stream:" + TEST_CONSUMED_STREAM,
    ):
        keys = list(redis_admin.scan_iter(match=pattern))
        if keys:
            redis_admin.delete(*keys)
    redis_admin.delete("pub:auth:v1:live:" + LIVE_SESSION)
    redis_admin.execute_command("ACL", "DELUSER", user)
    client.close()


@pytest.fixture
def configured_environment(
    monkeypatch: pytest.MonkeyPatch,
    service_redis: redis.Redis,
) -> None:
    monkeypatch.setenv("AUTH_ISSUER", "https://kit-vectors.invalid/auth")
    monkeypatch.setenv("AUTH_JWKS_PATH", str(VECTORS / "jwks.json"))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    database_url = os.environ.get("KIT_TEST_DATABASE_URL")
    if database_url:
        monkeypatch.setenv("DATABASE_URL", database_url)


@pytest.fixture
def app(configured_environment: None, contracts_dir: Path) -> Flask:
    application = Flask(__name__)
    application.testing = True
    kit = Kit(
        application,
        service="kit-example",
        suffix=TEST_SUFFIX,
        contracts_dir=contracts_dir,
        ready_checks=[lambda: True],
    )

    @kit.operation("listNotes")
    def list_notes() -> Page:
        items = []
        if kit.page == 1:
            items.append(
                {
                    "note_id": 1,
                    "title": "One",
                    "amount": money(Decimal("12.5")),
                    "created_by": None,
                    "created_at": datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
                }
            )
        return Page(
            "Notes",
            "Note",
            "notes",
            items,
            page=kit.page,
            total=1,
        )

    @kit.operation("getNote")
    def get_note(note_id: int) -> Representation:
        return Representation(
            "Note",
            {
                "note_id": note_id,
                "title": "One",
                "amount": money(Decimal("12.5")),
                "created_by": None,
                "created_at": datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
            },
        )

    @kit.operation("createNote")
    def create_note() -> Representation:
        return Representation(
            "Note",
            {
                "note_id": 46,
                "title": kit.body["title"],
                "amount": kit.body["amount"],
                "created_by": kit.principal["sub"],
                "created_at": datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
            },
            status=201,
            headers={"Location": "/v1/notes/46"},
        )

    application.extensions["mosaiq_kit"] = kit
    yield application
    kit.db.close()


@pytest.fixture
def client(app: Flask):
    return app.test_client()


@pytest.fixture
def reader_headers(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": "Bearer " + tokens["valid_reader"]}


@pytest.fixture
def writer_headers(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": "Bearer " + tokens["valid_writer"]}
