"""Fixtures: the service against the seeded PostgreSQL, a MongoDB prepared by
mongo/init.js and a Redis, each named by an environment variable.

Tokens are signed here with a throwaway RSA key whose public half is the JWKS
the kit reads, and the liveness check is answered by a stand-in for redis-state
(the ACL itself is exercised in test_acl.py). A test that needs PostgreSQL,
MongoDB or Redis skips without it, unless MOSAIQ_REQUIRE_POSTGRES,
MOSAIQ_REQUIRE_MONGO or MOSAIQ_REQUIRE_REDIS is 1, as CI sets them.

MongoDB is shared: a test works on days of its own, far in the past, and on
events recorded by a subject of its own, and removes both when it is done.
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from pymongo import MongoClient

import redis

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://auth.test/"
KID = "engagement-test-key"
SESSION = "5f0e9c2a-6a43-4c5e-9f86-0d6d5c6c9a11"
DEAD_MONGO = "mongodb://127.0.0.1:1/engagement?serverSelectionTimeoutMS=300"
DEAD_POSTGRES = "postgresql://svc_engagement:x@127.0.0.1:1/retail"
DEAD_REDIS = "redis://127.0.0.1:1/0"


def _require(what: str, flag: str) -> None:
    if os.environ.get(flag) == "1":
        pytest.fail(f"{what} is missing, and {flag} forbids skipping")
    pytest.skip(f"{what} is missing")


# ---- PostgreSQL ---------------------------------------------------------


@pytest.fixture(scope="session")
def owner_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        _require("TEST_DATABASE_URL", "MOSAIQ_REQUIRE_POSTGRES")
    return url


@pytest.fixture(scope="session")
def service_url(owner_url: str) -> str:
    """The same database, connected as svc_engagement (password = role name)."""

    parts = urlsplit(owner_url)
    host = parts.hostname or "127.0.0.1"
    netloc = "svc_engagement:svc_engagement@" + host
    if parts.port:
        netloc += ":" + str(parts.port)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


@pytest.fixture(scope="session")
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    try:
        connection = psycopg.connect(owner_url, autocommit=True)
    except psycopg.OperationalError:
        _require("a reachable PostgreSQL", "MOSAIQ_REQUIRE_POSTGRES")
    yield connection
    connection.close()


# ---- MongoDB and Redis --------------------------------------------------


@pytest.fixture(scope="session")
def mongo_uri() -> str:
    uri = os.environ.get("TEST_MONGO_URI")
    if not uri:
        _require("TEST_MONGO_URI", "MOSAIQ_REQUIRE_MONGO")
    return uri


@pytest.fixture(scope="session")
def mongo(mongo_uri: str) -> Iterator[MongoClient]:
    client: MongoClient = MongoClient(
        mongo_uri, serverSelectionTimeoutMS=2000, tz_aware=True
    )
    try:
        client.admin.command("ping")
    except Exception:
        _require("a reachable MongoDB", "MOSAIQ_REQUIRE_MONGO")
    yield client
    client.close()


@pytest.fixture(scope="session")
def redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        _require("TEST_REDIS_URL", "MOSAIQ_REQUIRE_REDIS")
    return url


@pytest.fixture
def cache(redis_url: str) -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    try:
        client.ping()
    except redis.RedisError:
        _require("a reachable Redis", "MOSAIQ_REQUIRE_REDIS")
    yield client
    client.close()


# ---- tokens and the app ---------------------------------------------------


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
def make_app(monkeypatch: pytest.MonkeyPatch, jwks_path: Path, state: FakeState):
    """build(mongo_uri=..., database_url=..., redis_url=...) -> the Flask app.

    Any argument left out is a dead address, so a test names exactly the
    dependencies it uses and the rest answer "unavailable".
    """

    created: list[Flask] = []

    def build(
        *,
        mongo_uri: str = DEAD_MONGO,
        database_url: str = DEAD_POSTGRES,
        redis_url: str = DEAD_REDIS,
    ) -> Flask:
        monkeypatch.setenv("AUTH_ISSUER", ISSUER)
        monkeypatch.setenv("AUTH_JWKS_PATH", str(jwks_path))
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("DATABASE_URL", database_url)
        monkeypatch.setenv("MOSAIQ_SERVICE_NAME", "engagement")
        monkeypatch.setenv("REDIS_STATE_URL", "redis://127.0.0.1:1/0")
        monkeypatch.setenv("MONGO_URI", mongo_uri)
        monkeypatch.setenv("REDIS_CACHE_URL", redis_url)
        monkeypatch.setenv("REDIS_ENGAGEMENT_PASSWORD", "")
        from app import create_app
        from app.live import LiveCounters

        application = create_app(
            live=(
                LiveCounters(redis.Redis.from_url(redis_url, decode_responses=True))
                if redis_url != DEAD_REDIS
                else None
            )
        )
        application.testing = True
        application.extensions["mosaiq_kit"].auth.redis = state
        created.append(application)
        return application

    yield build
    for application in created:
        application.extensions["mosaiq_kit"].db.close()
        store = application.extensions["engagement_store"]
        if store.client is not None:
            store.client.close()


@pytest.fixture
def token(key: rsa.RSAPrivateKey):
    def sign(role: str = "STORE_MANAGER", sub: str | None = None) -> dict[str, str]:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": "mosaiq-api",
            "sub": sub or str(uuid.uuid4()),
            "sid": SESSION,
            "role": role,
            "iat": now,
            "exp": now + 600,
        }
        encoded = jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})
        return {"Authorization": "Bearer " + encoded}

    return sign


# ---- a test's own corner of MongoDB ----------------------------------------


class Workspace:
    """A recorder, a UTC day in the past and helpers to post events on it."""

    def __init__(self, client: FlaskClient, token: Callable[..., dict[str, str]]):
        self.client = client
        self.sub = str(uuid.uuid4())
        self.day = date(1990, 1, 1) + timedelta(days=random.randrange(0, 11000))
        self.headers = token("STORE_MANAGER", self.sub)
        self.admin = token("ADMIN", str(uuid.uuid4()))

    def at(self, hour: int = 12, minute: int = 0) -> str:
        return f"{self.day.isoformat()}T{hour:02d}:{minute:02d}:00Z"

    def scan(self, **overrides):
        body = {
            "client_event_id": str(uuid.uuid4()),
            "type": "QR_SCAN",
            "store_id": 3,
            "product_id": 12,
            "occurred_at": self.at(),
        } | overrides
        return body

    def click(self, **overrides):
        return self.scan(type="RECO_CLICK", label_code="LOYAL", rank=2, **overrides)

    def post(self, body: dict, headers: dict | None = None):
        return self.client.post(
            "/v1/engagement-events", json=body, headers=headers or self.headers
        )


@pytest.fixture
def workspace(
    make_app,
    mongo_uri: str,
    service_url: str,
    redis_url: str,
    token,
    mongo: MongoClient,
    cache,
) -> Iterator[Workspace]:
    """The app against real PostgreSQL, MongoDB and Redis, and a day of its own."""

    app = make_app(mongo_uri=mongo_uri, database_url=service_url, redis_url=redis_url)
    space = Workspace(app.test_client(), token)
    yield space
    database = mongo.get_default_database(default="engagement")
    database.engagement_event.delete_many({"recorded_by": space.sub})
    database.engagement_daily.delete_many({"day": space.day.isoformat()})
    for found in cache.scan_iter(match=f"engagement:live:*:{space.day:%Y%m%d}:*"):
        cache.delete(found)
