"""Real integration fixtures for model-registry-service.

PostgreSQL is the seeded database of the three scripts (TEST_DATABASE_URL, as
the owner) and Redis a real server (TEST_REDIS_URL). Each is required when
``MOSAIQ_REQUIRE_POSTGRES=1`` or ``MOSAIQ_REQUIRE_REDIS=1``; nothing is faked.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
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
KID = "model-registry-test-key"
ROLES = (
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
)


def required(variable: str, kind: str) -> str:
    value = os.environ.get(variable)
    if value:
        return value
    if os.environ.get(f"MOSAIQ_REQUIRE_{kind}") == "1":
        pytest.fail(f"{variable} is required; integration tests may not skip")
    pytest.skip(f"{variable} is not set")


def _role_url(owner_url: str, role: str) -> str:
    parts = urlsplit(owner_url)
    host = parts.hostname or "127.0.0.1"
    netloc = f"{role}:{role}@{host}"
    if parts.port:
        netloc += ":" + str(parts.port)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


@pytest.fixture(scope="session")
def owner_url() -> str:
    return required("TEST_DATABASE_URL", "POSTGRES")


@pytest.fixture(scope="session")
def service_url(owner_url: str) -> str:
    return _role_url(owner_url, "svc_models")


@pytest.fixture
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def service_db(service_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(service_url, autocommit=True) as connection:
        yield connection


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session", autouse=True)
def auth_environment(
    signing_key: rsa.RSAPrivateKey, tmp_path_factory: pytest.TempPathFactory
) -> None:
    if not (ROOT / "contracts/model-registry/openapi.yaml").is_file():
        pytest.exit(
            "Stage contracts first: "
            "bash .github/scripts/stage-contracts.sh model-registry",
            returncode=2,
        )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    path = tmp_path_factory.mktemp("model-registry-auth") / "jwks.json"
    path.write_text(json.dumps({"keys": [jwk]}))
    os.environ["AUTH_ISSUER"] = ISSUER
    os.environ["AUTH_JWKS_PATH"] = str(path)
    os.environ["MOSAIQ_SERVICE_NAME"] = "model-registry"


@pytest.fixture(scope="session")
def redis_connection() -> Iterator[redis.Redis]:
    url = required("TEST_REDIS_URL", "REDIS")
    client = redis.Redis.from_url(url, decode_responses=True)
    password = "model-registry-test-" + uuid.uuid4().hex
    line = (ROOT / "redis/model-registry.acl").read_text().strip()
    rendered = line.replace("{{password}}", ">" + password)
    arguments = re.findall(r"\([^)]*\)|\S+", rendered)
    client.execute_command("ACL", "SETUSER", "svc_models", *arguments[2:])
    parts = urlsplit(url)
    plain = urlunsplit((parts.scheme, f"{parts.hostname}:{parts.port}", "/0", "", ""))
    os.environ["REDIS_STATE_URL"] = plain
    os.environ["REDIS_MODELS_PASSWORD"] = password
    try:
        yield client
    finally:
        client.execute_command("ACL", "DELUSER", "svc_models")
        client.close()


@pytest.fixture(scope="session")
def role_subjects(owner_url: str) -> dict[str, str]:
    with psycopg.connect(owner_url) as connection:
        rows = connection.execute(
            "SELECT r.code, min(u.user_id::text) FROM public.app_user AS u"
            " JOIN public.role AS r USING (role_id) WHERE u.is_active"
            " GROUP BY r.code",
        ).fetchall()
    subjects = dict(rows)
    assert all(role in subjects for role in ROLES)
    return subjects


@pytest.fixture
def token(
    signing_key: rsa.RSAPrivateKey,
    role_subjects: dict[str, str],
    redis_connection: redis.Redis,
) -> Callable[..., dict[str, str]]:
    def sign(role: str = "ADMIN") -> dict[str, str]:
        session = str(uuid.uuid4())
        redis_connection.set("pub:auth:v1:live:" + session, "1", ex=600)
        now = int(time.time())
        encoded = jwt.encode(
            {
                "iss": ISSUER,
                "aud": "mosaiq-api",
                "sub": role_subjects[role],
                "sid": session,
                "role": role,
                "iat": now,
                "exp": now + 600,
            },
            signing_key,
            algorithm="RS256",
            headers={"kid": KID},
        )
        return {"Authorization": "Bearer " + encoded}

    return sign


@pytest.fixture
def app(
    monkeypatch: pytest.MonkeyPatch,
    service_url: str,
    redis_connection: redis.Redis,
) -> Iterator[Flask]:
    monkeypatch.setenv("DATABASE_URL", service_url)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app import create_app

    application = create_app()
    application.config["TESTING"] = True
    yield application
    application.extensions["mosaiq_kit"].db.close()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
