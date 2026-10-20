"""Fixtures: the service against the seeded PostgreSQL of TEST_DATABASE_URL.

Tokens are signed here with a throwaway RSA key whose public half is the JWKS
the kit reads, and the liveness check is answered by a stand-in for redis-state
(CI's service job runs no Redis; the ACL itself is exercised in test_acl.py).
"""

from __future__ import annotations

import json
import os
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

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://auth.test/"
KID = "customers-test-key"
SESSION = "5f0e9c2a-6a43-4c5e-9f86-0d6d5c6c9a11"
ADMIN_SUB_QUERY = (
    "SELECT u.user_id FROM app_user AS u JOIN role AS r ON r.role_id = u.role_id "
    "WHERE r.code = %s AND u.is_active ORDER BY u.user_id LIMIT 1"
)
PREFIX = "svc-test-"


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
    """The same database, connected as svc_customers (password = role name)."""

    parts = urlsplit(owner_url)
    host = parts.hostname or "127.0.0.1"
    netloc = "svc_customers:svc_customers@" + host
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

    def build(database_url: str | None = None) -> Flask:
        monkeypatch.setenv("AUTH_ISSUER", ISSUER)
        monkeypatch.setenv("AUTH_JWKS_PATH", str(jwks_path))
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("DATABASE_URL", database_url or service_url)
        monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "customers")
        monkeypatch.setenv("REDIS_STATE_URL", "redis://127.0.0.1:1/0")
        from app import create_app

        application = create_app()
        application.testing = True
        application.extensions["mosaiq_kit"].auth.redis = state
        created.append(application)
        return application

    yield build
    for application in created:
        application.extensions["mosaiq_kit"].db.close()


@pytest.fixture
def app(make_app) -> Flask:
    return make_app()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def token(key: rsa.RSAPrivateKey, actor: str):
    def sign(role: str = "STORE_MANAGER", sub: str | None = None) -> dict[str, str]:
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
        encoded = jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})
        return {"Authorization": "Bearer " + encoded}

    return sign


@pytest.fixture
def manager(token) -> dict[str, str]:
    return token("STORE_MANAGER")


@pytest.fixture
def unique() -> str:
    return PREFIX + uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def cleanup(request: pytest.FixtureRequest) -> Iterator[None]:
    """Remove the customers a test registered, whatever it did."""

    yield
    if "owner" in request.fixturenames:
        owner = request.getfixturevalue("owner")
        owner.execute(
            "DELETE FROM customer WHERE name LIKE %s OR email LIKE %s",
            (PREFIX + "%", PREFIX + "%"),
        )
