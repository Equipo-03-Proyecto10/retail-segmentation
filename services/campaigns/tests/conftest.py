"""Fixtures for campaigns-service: tokens, a stand-in session store, a database.

Tokens are signed with a key generated per run; only its public half reaches
the service, as the JWKS file auth-service would publish (ADR-0036).

`postgres` tests need TEST_DATABASE_URL, the owner's URL to a database built
by the three scripts (CI's services job sets it). Each test gets its own copy
of that database, made from it as a template, and the service connects to the
copy as svc_campaigns, so every privilege it lacks is lacking here too.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask.testing import FlaskClient
from psycopg import sql

SERVICE = Path(__file__).resolve().parents[1]
ADMIN = "11111111-1111-1111-1111-000000000001"
ANALYST = "11111111-1111-1111-1111-000000000014"
MARKETING = "11111111-1111-1111-1111-000000000004"
AUDITOR = "11111111-1111-1111-1111-000000000006"
ISSUER = "https://auth.test/"
KID = "campaigns-test"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_copies = itertools.count()


def _b64url_bytes(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url(number: int) -> str:
    return _b64url_bytes(number.to_bytes((number.bit_length() + 7) // 8, "big"))


def _sign_rs256(claims: dict[str, Any]) -> str:
    """A compact RS256 JWS by hand, as tests/conformance/tokens.py makes them.

    Only auth-service signs with a JWT library (ADR-0036); this throwaway key
    exists for these tests alone.
    """
    header = {"alg": "RS256", "typ": "JWT", "kid": KID}
    signing_input = (
        _b64url_bytes(json.dumps(header).encode())
        + "."
        + _b64url_bytes(json.dumps(claims).encode())
    )
    signature = _PRIVATE_KEY.sign(
        signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )
    return signing_input + "." + _b64url_bytes(signature)


def _missing(variable: str) -> None:
    if os.environ.get("CAMPAIGNS_REQUIRE_SERVICES") == "1":
        pytest.fail(variable + " is not set")
    pytest.skip(variable + " is not set")


@pytest.fixture(scope="session", autouse=True)
def _environment(tmp_path_factory: pytest.TempPathFactory) -> None:
    public = _PRIVATE_KEY.public_key().public_numbers()
    jwks = tmp_path_factory.mktemp("auth") / "jwks.json"
    jwks.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "kty": "RSA",
                        "use": "sig",
                        "alg": "RS256",
                        "kid": KID,
                        "n": _b64url(public.n),
                        "e": _b64url(public.e),
                    }
                ]
            }
        )
    )
    os.environ["AUTH_JWKS_PATH"] = str(jwks)
    os.environ["AUTH_ISSUER"] = ISSUER
    os.environ.setdefault("REDIS_STATE_URL", "redis://127.0.0.1:1/0")
    if not (SERVICE / "contracts/campaigns/openapi.yaml").is_file():
        pytest.exit(
            "Stage the contract first: .github/scripts/stage-contracts.sh campaigns",
            returncode=2,
        )


class FakeSessionStore:
    """The one redis-state command the request path uses, in memory.

    Every session is live unless revoked. The rate limit is off in these tests.
    """

    def __init__(self) -> None:
        self.revoked: set[str] = set()

    def exists(self, key: str) -> int:
        return int(key.removeprefix("pub:auth:v1:live:") not in self.revoked)


@pytest.fixture
def token() -> Callable[..., str]:
    def sign(user_id: str, role: str, sid: str | None = None) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": "mosaiq-api",
            "sub": user_id,
            "sid": sid or str(uuid.uuid4()),
            "role": role,
            "iat": now,
            "exp": now + 300,
        }
        return _sign_rs256(claims)

    return sign


def _with_database(url: str, database: str, *, role: str | None = None) -> str:
    """The URL of another database, signed in as `role` when one is named.

    A role signs in under its own name, with its own name as the password, as
    CI's database does. It must be a real session user, not SET ROLE: the
    activation guard looks at session_user.
    """
    parts = urlsplit(url)
    netloc = parts.netloc
    if role:
        netloc = f"{role}:{role}@{parts.hostname or '127.0.0.1'}"
        if parts.port:
            netloc += ":" + str(parts.port)
    return urlunsplit((parts.scheme, netloc, "/" + database, "", ""))


@pytest.fixture
def database() -> Iterator[dict[str, str]]:
    """A private copy of the seeded database, as its owner, the service and the web."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        _missing("TEST_DATABASE_URL")
    template = urlsplit(url).path.lstrip("/") or "retail"
    name = f"campaigns_test_{os.getpid()}_{next(_copies)}"
    maintenance = _with_database(url, "postgres")
    with psycopg.connect(maintenance, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(template)
            )
        )
    try:
        yield {
            "owner": _with_database(url, name),
            "service": _with_database(url, name, role="svc_campaigns"),
            "web": _with_database(url, name, role="retail_app"),
        }
    finally:
        with psycopg.connect(maintenance, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def sessions() -> FakeSessionStore:
    return FakeSessionStore()


@pytest.fixture
def service(
    database: dict[str, str],
    sessions: FakeSessionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Any]:
    monkeypatch.setenv("DATABASE_URL", database["service"])
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    # Ten creates queue on one advisory lock behind a pool of three connections.
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT_SECONDS", "20")
    from app import create_app

    app = create_app()
    kit = app.extensions["mosaiq_kit"]
    kit.auth.redis = sessions
    try:
        yield app
    finally:
        kit.db.close()


class Api:
    """A test client that signs in as one user per call."""

    def __init__(self, client: FlaskClient, sign: Callable[..., str]) -> None:
        self.client = client
        self.sign = sign

    def __call__(
        self,
        method: str,
        path: str,
        *,
        as_user: tuple[str, str] = (ADMIN, "ADMIN"),
        json_body: Any = None,
        **kwargs: Any,
    ) -> Any:
        headers = {"Authorization": "Bearer " + self.sign(*as_user)}
        headers.update(kwargs.pop("headers", {}))
        return self.client.open(
            path, method=method, json=json_body, headers=headers, **kwargs
        )


@pytest.fixture
def api(service: Any, token: Callable[..., str]) -> Api:
    return Api(service.test_client(), token)


@pytest.fixture
def owner(database: dict[str, str]) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database["owner"], autocommit=True) as connection:
        yield connection
