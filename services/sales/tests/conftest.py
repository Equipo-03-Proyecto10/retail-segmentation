"""Real integration fixtures for sales-service.

Public fixtures used by this suite and the companion modules are ``app``,
``client``, ``build_app``, ``token``, ``tokens``, ``owner``,
``owner_connection``, ``redis_connection``, ``service_url``, ``retail_url``,
and ``sale_body``. Every token subject exists in ``app_user``. PostgreSQL and
Redis are required when ``MOSAIQ_REQUIRE_POSTGRES=1`` and
``MOSAIQ_REQUIRE_REDIS=1`` respectively; no database or Redis fake is used.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
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
KID = "sales-test-key"
ROLES = (
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
)


def _required(variable: str, kind: str) -> str:
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
    return _required("TEST_DATABASE_URL", "POSTGRES")


@pytest.fixture(scope="session")
def service_url(owner_url: str) -> str:
    return _role_url(owner_url, "svc_sales")


@pytest.fixture(scope="session")
def retail_url(owner_url: str) -> str:
    return _role_url(owner_url, "retail_app")


@pytest.fixture(scope="session")
def owner_connection(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session", autouse=True)
def auth_environment(
    signing_key: rsa.RSAPrivateKey, tmp_path_factory: pytest.TempPathFactory
) -> None:
    if not (ROOT / "contracts/sales/openapi.yaml").is_file():
        pytest.exit(
            "Stage contracts first: bash .github/scripts/stage-contracts.sh sales",
            returncode=2,
        )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    path = tmp_path_factory.mktemp("sales-auth") / "jwks.json"
    path.write_text(json.dumps({"keys": [jwk]}))
    os.environ["AUTH_ISSUER"] = ISSUER
    os.environ["AUTH_JWKS_PATH"] = str(path)


@pytest.fixture(scope="session")
def redis_connection() -> Iterator[redis.Redis]:
    url = _required("TEST_REDIS_URL", "REDIS")
    client = redis.Redis.from_url(url, decode_responses=True)
    password = "sales-test-" + uuid.uuid4().hex
    line = (ROOT / "redis/sales.acl").read_text().strip()
    rendered = line.replace("{{password}}", ">" + password)
    arguments = re.findall(r"\([^)]*\)|\S+", rendered)
    client.execute_command("ACL", "SETUSER", "svc_sales", *arguments[2:])
    parts = urlsplit(url)
    os.environ["REDIS_STATE_URL"] = urlunsplit(
        (parts.scheme, f"{parts.hostname}:{parts.port}", "/0", "", "")
    )
    os.environ["REDIS_SALES_PASSWORD"] = password
    try:
        yield client
    finally:
        client.execute_command("ACL", "DELUSER", "svc_sales")
        client.close()


@pytest.fixture(scope="session")
def role_subjects(owner_connection: psycopg.Connection) -> dict[str, str]:
    rows = owner_connection.execute(
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
    def sign(
        role: str = "ADMIN", *, sub: str | None = None, sid: str | None = None
    ) -> dict[str, str]:
        session = sid or str(uuid.uuid4())
        redis_connection.set("pub:auth:v1:live:" + session, "1", ex=600)
        now = int(time.time())
        encoded = jwt.encode(
            {
                "iss": ISSUER,
                "aud": "mosaiq-api",
                "sub": sub or role_subjects[role],
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
def tokens(token: Callable[..., dict[str, str]]) -> dict[str, dict[str, str]]:
    return {role: token(role) for role in ROLES}


@pytest.fixture
def build_app(
    monkeypatch: pytest.MonkeyPatch,
    service_url: str,
    redis_connection: redis.Redis,
) -> Iterator[Callable[..., Flask]]:
    applications: list[Flask] = []

    def build(*, relay: bool = False, database_url: str | None = None) -> Flask:
        monkeypatch.setenv("DATABASE_URL", database_url or service_url)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("OUTBOX_RELAY_ENABLED", "true" if relay else "false")
        from app import create_app

        application = create_app(start_relay=relay)
        application.config["TESTING"] = True
        applications.append(application)
        return application

    yield build
    for application in applications:
        relay = application.extensions.get("sales_relay")
        if relay is not None:
            relay.stop()
            thread = relay._thread
            if thread is not None:
                thread.join(timeout=6)
        application.extensions["mosaiq_kit"].db.close()


@pytest.fixture
def app(build_app: Callable[..., Flask]) -> Flask:
    return build_app()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def sale_body(owner: psycopg.Connection) -> dict:
    row = owner.execute(
        "SELECT i.store_id, i.product_id, c.customer_id, ch.channel_id"
        " FROM public.inventory AS i JOIN public.product AS p USING (product_id)"
        " CROSS JOIN LATERAL (SELECT customer_id FROM public.customer"
        " ORDER BY registered_on, customer_id LIMIT %s) AS c"
        " CROSS JOIN LATERAL (SELECT channel_id FROM public.channel"
        " ORDER BY channel_id LIMIT %s) AS ch"
        " WHERE p.is_active AND i.quantity_on_hand >= %s"
        " ORDER BY i.store_id, i.product_id LIMIT %s",
        (1, 1, 10, 1),
    ).fetchone()
    assert row is not None
    return {
        "customer_id": str(row[2]),
        "store_id": row[0],
        "channel_id": row[3],
        "occurred_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "lines": [{"product_id": row[1], "quantity": 2, "unit_price": "12.50"}],
    }


@pytest.fixture(autouse=True)
def cleanup(owner_url: str) -> Iterator[None]:
    with psycopg.connect(owner_url) as connection:
        quantities = connection.execute(
            "SELECT store_id, product_id, quantity_on_hand FROM public.inventory"
        ).fetchall()
    yield
    with psycopg.connect(owner_url, autocommit=True) as connection:
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT transaction_id FROM public.transaction"
                " WHERE source_transaction_id LIKE %s",
                ("svc-sales:%",),
            )
        ]
        if ids:
            connection.execute(
                "DELETE FROM sales_events.outbox WHERE entity_id = ANY(%s)",
                ([str(value) for value in ids],),
            )
            connection.execute(
                "DELETE FROM public.transaction WHERE transaction_id = ANY(%s)",
                (ids,),
            )
        with connection.cursor() as cursor:
            cursor.executemany(
                "UPDATE public.inventory SET quantity_on_hand = %s"
                " WHERE store_id = %s AND product_id = %s",
                [
                    (quantity, store_id, product_id)
                    for store_id, product_id, quantity in quantities
                ],
            )


def idempotency_headers(auth: dict[str, str], key: str | None = None) -> dict[str, str]:
    return {**auth, "Idempotency-Key": key or str(uuid.uuid4())}


def sale_xml(body: dict) -> bytes:
    lines = "".join(
        "<lines>"
        f"<product_id>{line['product_id']}</product_id>"
        f"<quantity>{line['quantity']}</quantity>"
        f"<unit_price>{line['unit_price']}</unit_price>"
        "</lines>"
        for line in body.get("lines", [])
    )
    return (
        '<SaleRequest xmlns="https://mosaiq.maxthecoder.online/xml/sales/v1">'
        f"<customer_id>{body['customer_id']}</customer_id>"
        f"<store_id>{body['store_id']}</store_id>"
        f"<channel_id>{body['channel_id']}</channel_id>"
        f"<occurred_at>{body['occurred_at']}</occurred_at>"
        f"{lines}</SaleRequest>"
    ).encode()
