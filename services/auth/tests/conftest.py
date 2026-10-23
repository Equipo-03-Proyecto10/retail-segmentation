"""auth-service fixtures for the documented PostgreSQL and Redis instances."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest
import redis
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SERVICE = Path(__file__).resolve().parents[1]
ISSUER = "https://auth.test/"
KID = "auth-test"
EMAIL = "user2@mosaiq-demo.com"
PASSWORD = "Password123!"
USER_ID = "11111111-1111-1111-1111-000000000002"


def _b64url(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _required() -> bool:
    return os.environ.get("AUTH_REQUIRE_SERVICES") == "1"


def _missing(message: str) -> None:
    if _required():
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session", autouse=True)
def key_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    directory = tmp_path_factory.mktemp("auth-keys")
    private_path = directory / "private.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    numbers = private_key.public_key().public_numbers()
    jwks_path = directory / "jwks.json"
    jwks_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "kty": "RSA",
                        "kid": KID,
                        "alg": "RS256",
                        "use": "sig",
                        "n": _b64url(numbers.n),
                        "e": _b64url(numbers.e),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    old = dict(os.environ)
    os.environ.update(
        {
            "AUTH_ISSUER": ISSUER,
            "AUTH_KID": KID,
            "AUTH_PRIVATE_KEY_PATH": str(private_path),
            "AUTH_JWKS_PATH": str(jwks_path),
            "AUTH_ACCESS_TOKEN_TTL_SECONDS": "600",
            "AUTH_SESSION_ABSOLUTE_TTL_SECONDS": "43200",
            "MOSAIQ_SERVICE_NAME": "auth",
        }
    )
    yield
    os.environ.clear()
    os.environ.update(old)


@pytest.fixture
def redis_admin() -> Iterator[redis.Redis]:
    url = os.environ.get("AUTH_TEST_REDIS_URL", "")
    if not url:
        _missing("AUTH_TEST_REDIS_URL is not set")
    client = redis.Redis.from_url(url, decode_responses=True)
    try:
        client.ping()
    except redis.RedisError:
        client.close()
        _missing("AUTH_TEST_REDIS_URL is unavailable")

    def clean() -> None:
        keys = list(client.scan_iter(match="auth:*"))
        keys += list(client.scan_iter(match="pub:auth:v1:live:*"))
        if keys:
            client.delete(*keys)
        client.delete("stream:users.v1.access_changed")

    clean()
    yield client
    clean()
    client.close()


def _service_database_url(owner_url: str) -> str:
    parts = urlsplit(owner_url)
    query = "options=" + quote("-c role=svc_auth")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


@pytest.fixture
def database_url() -> str:
    owner_url = os.environ.get("TEST_DATABASE_URL", "")
    if not owner_url:
        _missing("TEST_DATABASE_URL is not set")
    try:
        with psycopg.connect(owner_url) as connection:
            assert connection.execute("SELECT 1").fetchone() == (1,)
    except psycopg.Error:
        _missing("TEST_DATABASE_URL is unavailable")
    return _service_database_url(owner_url)


@pytest.fixture
def app(
    database_url: str,
    redis_admin: redis.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[object]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    from app import create_app

    application = create_app(start_background=False, state_redis=redis_admin)
    try:
        yield application
    finally:
        application.extensions["mosaiq_kit"].db.close()


@pytest.fixture
def client(app: object) -> object:
    return app.test_client()


def sign_in(client: object, *, ip: str = "192.0.2.10") -> object:
    return client.post(
        "/v1/sessions",
        json={"email": EMAIL, "password": PASSWORD, "device_label": "pytest"},
        headers={"X-Real-IP": ip},
    )


@pytest.fixture
def session(client: object) -> dict[str, str]:
    response = sign_in(client)
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()
