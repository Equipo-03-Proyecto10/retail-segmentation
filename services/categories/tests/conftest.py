"""Fixtures: the service against the seeded PostgreSQL of TEST_DATABASE_URL.

Redis-backed tests (cache, relay, consumer, ACL) also need TEST_REDIS_URL.

Tokens are signed here with a throwaway RSA key whose public half is the JWKS
the kit reads, and the liveness check is answered by a stand-in for redis-state
(CI's service job runs no Redis; the ACL itself is exercised in test_acl.py).
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient

import redis

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://auth.test/"
KID = "customers-test-key"
SESSION = "5f0e9c2a-6a43-4c5e-9f86-0d6d5c6c9a11"
ADMIN_SUB_QUERY = (
    "SELECT u.user_id FROM app_user AS u JOIN role AS r ON r.role_id = u.role_id "
    "WHERE r.code = %s AND u.is_active ORDER BY u.user_id LIMIT 1"
)
PREFIX = "svc-test-"
TEST_ID_FLOOR = 30000


def _require(what: str) -> None:
    if os.environ.get("MOSAIQ_REQUIRE_POSTGRES") == "1":
        pytest.fail(f"{what} is missing, and MOSAIQ_REQUIRE_POSTGRES forbids skipping")
    pytest.skip(f"{what} is missing; these tests need the seeded PostgreSQL")


@pytest.fixture(scope="session")
def owner_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        _require("TEST_DATABASE_URL")
    return url


@pytest.fixture(scope="session")
def service_url(owner_url: str) -> str:
    """The same database, connected as svc_categories (password = role name)."""

    parts = urlsplit(owner_url)
    host = parts.hostname or "127.0.0.1"
    netloc = "svc_categories:svc_categories@" + host
    if parts.port:
        netloc += ":" + str(parts.port)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


@pytest.fixture(scope="session")
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    try:
        connection = psycopg.connect(owner_url, autocommit=True)
    except psycopg.OperationalError:
        _require("a reachable PostgreSQL")
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def actor(owner: psycopg.Connection) -> str:
    """An active seeded administrator, so audit_log.user_id has a parent."""

    row = owner.execute(ADMIN_SUB_QUERY, ("ADMIN",)).fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture(scope="session")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwks_path(key: rsa.RSAPrivateKey, tmp_path_factory: pytest.TempPathFactory) -> Path:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    path = tmp_path_factory.mktemp("jwks") / "jwks.json"
    path.write_text(json.dumps({"keys": [jwk]}))
    return path


class FakeState:
    """The part of redis-state a request touches: the liveness key."""

    def __init__(self) -> None:
        self.live: set[str] = {"pub:auth:v1:live:" + SESSION}

    def exists(self, name: str) -> int:
        return int(name in self.live)


@pytest.fixture
def state() -> FakeState:
    return FakeState()


@pytest.fixture
def make_app(
    monkeypatch: pytest.MonkeyPatch,
    jwks_path: Path,
    service_url: str,
    state: FakeState,
):
    created: list[Flask] = []

    def build(
        database_url: str | None = None,
        redis_url: str | None = None,
        background: bool = False,
    ) -> Flask:
        monkeypatch.setenv("AUTH_ISSUER", ISSUER)
        monkeypatch.setenv("AUTH_JWKS_PATH", str(jwks_path))
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("DATABASE_URL", database_url or service_url)
        monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "categories")
        monkeypatch.setenv(
            "CATEGORIES_BACKGROUND_WORKERS", "true" if background else "false"
        )
        monkeypatch.setenv(
            "REDIS_STATE_URL",
            redis_url if background and redis_url else "redis://127.0.0.1:1/0",
        )
        if redis_url:
            monkeypatch.setenv("REDIS_CACHE_URL", redis_url)
        else:
            monkeypatch.delenv("REDIS_CACHE_URL", raising=False)
        from app import create_app

        application = create_app()
        application.testing = True
        application.extensions["mosaiq_kit"].auth.redis = state
        created.append(application)
        return application

    yield build
    for application in created:
        for worker in application.extensions.get("categories_workers", ()):
            worker.stop()
        application.extensions["mosaiq_kit"].db.close()


@pytest.fixture
def app(make_app) -> Flask:
    return make_app()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def token(key: rsa.RSAPrivateKey, actor: str):
    def sign(role: str = "ADMIN", sub: str | None = None) -> dict[str, str]:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": "mosaiq-api",
            "sub": sub or actor,
            "sid": SESSION,
            "role": role,
            "iat": now,
            "exp": now + 600,
        }
        # A throwaway key the test's own JWKS publishes; guards.sh lets a
        # service's tests sign, and refuses signing in its application code.
        encoded = jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})
        return {"Authorization": "Bearer " + encoded}

    return sign


@pytest.fixture
def admin_headers(token) -> dict[str, str]:
    return token("ADMIN")


@pytest.fixture
def unique() -> str:
    return PREFIX + uuid.uuid4().hex[:12]


@pytest.fixture
def new_id(owner: psycopg.Connection):
    """Allocate category ids in the test range, unused at the time of the call.

    The cleanup deletes a test's categories but not their outbox rows, so an id
    an earlier test used is still taken: drawing it again would let that test's
    events leak into this one's outbox assertions (#600).
    """

    taken = {
        row[0]
        for row in owner.execute(
            "SELECT category_id FROM category WHERE category_id >= %s",
            (TEST_ID_FLOOR,),
        )
    }
    # entity_id is text and other tests write non-numeric ones, so convert here
    # rather than cast in SQL.
    taken.update(
        int(row[0])
        for row in owner.execute(
            "SELECT DISTINCT entity_id FROM categories_events.outbox"
        )
        if row[0].isdigit() and int(row[0]) >= TEST_ID_FLOOR
    )

    def allocate() -> int:
        while True:
            candidate = random.randint(TEST_ID_FLOOR, 32000)
            if candidate not in taken:
                taken.add(candidate)
                return candidate

    return allocate


@pytest.fixture(autouse=True)
def cleanup(request: pytest.FixtureRequest) -> Iterator[None]:
    """Remove what a test created, whatever it did."""

    yield
    if "owner" in request.fixturenames:
        owner = request.getfixturevalue("owner")
        owner.execute("DELETE FROM product WHERE sku LIKE %s", (PREFIX + "%",))
        owner.execute("DELETE FROM category WHERE category_id >= %s", (TEST_ID_FLOOR,))


REDIS_URL = os.environ.get("TEST_REDIS_URL")
_parts = urlsplit(REDIS_URL or "")
# What the service is given: the URL carries no credentials (ADR-0037).
SERVICE_REDIS_URL = urlunsplit(
    (_parts.scheme, f"{_parts.hostname}:{_parts.port}", _parts.path, "", "")
)
ACL = ROOT / "redis/categories.acl"
STREAM = "stream:categories.v1.changed"


def acl_rules(password: str) -> list[str]:
    text = ACL.read_text().strip().replace("{{password}}", ">" + password)
    return re.findall(r"\([^)]*\)|\S+", text)[2:]


@pytest.fixture
def redis_admin() -> Iterator[redis.Redis]:
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL is not set")
    admin = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield admin
    for pattern in ("categories:cache:*", "categories:rl:*"):
        keys = list(admin.scan_iter(match=pattern))
        if keys:
            admin.delete(*keys)
    admin.delete(STREAM)
    admin.close()


@pytest.fixture
def service_redis_url(redis_admin: redis.Redis, monkeypatch: pytest.MonkeyPatch) -> str:
    """Create the svc_categories ACL user from the committed line."""

    password = uuid.uuid4().hex
    redis_admin.execute_command("ACL", "DELUSER", "svc_categories")
    redis_admin.execute_command(
        "ACL", "SETUSER", "svc_categories", "on", *acl_rules(password)
    )
    monkeypatch.setenv("REDIS_CATEGORIES_PASSWORD", password)
    yield SERVICE_REDIS_URL
    redis_admin.execute_command("ACL", "DELUSER", "svc_categories")


def service_client(password: str) -> redis.Redis:
    return redis.Redis.from_url(
        SERVICE_REDIS_URL,
        username="svc_categories",
        password=password,
        decode_responses=True,
        socket_timeout=10,
    )
