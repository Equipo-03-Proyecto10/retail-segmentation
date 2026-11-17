"""Real integration fixtures for rfm-engine.

PostgreSQL is the seeded database of the three scripts (TEST_DATABASE_URL, as the
owner) and Redis a real server (TEST_REDIS_URL). Each is required when
``MOSAIQ_REQUIRE_POSTGRES=1`` or ``MOSAIQ_REQUIRE_REDIS=1``; nothing is faked.
Customers and sales a test needs are its own: it creates them as the owner and
removes them after.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import jwt
import psycopg
import pytest
import redis
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://auth.test/"
KID = "rfm-engine-test-key"


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
    return _role_url(owner_url, "svc_rfm")


@pytest.fixture
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def service(service_url: str) -> Iterator[psycopg.Connection]:
    """The service's own role, autocommit, so a test controls each transaction."""
    with psycopg.connect(service_url, autocommit=True) as connection:
        yield connection


class Shop:
    """Customers and sales a test creates as the owner, and takes away again."""

    def __init__(self, owner: psycopg.Connection, sales_url: str):
        self.owner = owner
        self.sales_url = sales_url
        self.customers: list[uuid.UUID] = []
        self.sales: list[int] = []

    def customer(self) -> uuid.UUID:
        row = self.owner.execute(
            "INSERT INTO public.customer (name, registration_channel_id)"
            " VALUES ('rfm-engine test', 1) RETURNING customer_id"
        ).fetchone()
        assert row is not None
        self.customers.append(row[0])
        return row[0]

    def sale(
        self,
        customer_id: uuid.UUID,
        occurred_at: datetime,
        *,
        quantity: int = 1,
        unit_price: str = "10.00",
        returned: int = 0,
    ) -> int:
        """One sale of one line; its total is what the line adds up to, as the
        header's check requires, and both rows commit together."""
        total = Decimal(unit_price) * quantity
        with self.owner.transaction():
            row = self.owner.execute(
                "INSERT INTO public.transaction (source_transaction_id, customer_id,"
                " store_id, channel_id, occurred_at, total, data_origin)"
                " VALUES (%s, %s, 1, 1, %s, %s, 'OBSERVED') RETURNING transaction_id",
                ("rfm-test:" + uuid.uuid4().hex, customer_id, occurred_at, total),
            ).fetchone()
            assert row is not None
            self.owner.execute(
                "INSERT INTO public.transaction_line (transaction_id, product_id,"
                " quantity, unit_price, returned_quantity) VALUES (%s, 1, %s, %s, %s)",
                (row[0], quantity, unit_price, returned),
            )
        self.sales.append(row[0])
        return row[0]

    def reverse(self, transaction_id: int) -> None:
        """Cancel the sale as svc_sales does: the reversal, its line, and the line's
        returned quantity commit together (ADR-0062)."""
        user = self.owner.execute(
            "SELECT user_id FROM public.app_user WHERE is_active LIMIT 1"
        ).fetchone()
        assert user is not None
        with psycopg.connect(self.sales_url) as sales:
            with sales.transaction():
                reversal = sales.execute(
                    "INSERT INTO sales.sale_reversal (transaction_id, kind, reason,"
                    " idempotency_key, request_sha256, recorded_by)"
                    " VALUES (%s, 'CANCELLATION', 'rfm-engine test', %s, %s, %s)"
                    " RETURNING reversal_id",
                    (transaction_id, uuid.uuid4(), uuid.uuid4().bytes * 2, user[0]),
                ).fetchone()
                assert reversal is not None
                sales.execute(
                    "INSERT INTO sales.sale_reversal_line (reversal_id,"
                    " transaction_id, product_id, quantity)"
                    " SELECT %s, transaction_id, product_id, quantity"
                    " FROM public.transaction_line WHERE transaction_id = %s",
                    (reversal[0], transaction_id),
                )
                sales.execute(
                    "UPDATE public.transaction_line SET returned_quantity = quantity"
                    " WHERE transaction_id = %s",
                    (transaction_id,),
                )

    def profile(self, customer_id: uuid.UUID) -> tuple[object, ...] | None:
        return self.owner.execute(
            "SELECT state, rule_set_id, r_days, f_count, m_total, r_score, f_score,"
            " m_score, label_code, via_fallback FROM rfm.customer_profile"
            " WHERE customer_id = %s",
            (customer_id,),
        ).fetchone()

    def events(self, customer_id: uuid.UUID) -> list[str]:
        rows = self.owner.execute(
            "SELECT event_type FROM rfm_events.outbox WHERE entity_id = %s"
            " AND stream = 'stream:rfm.v1.profile_changed' ORDER BY occurred_at",
            (str(customer_id),),
        ).fetchall()
        return [row[0] for row in rows]

    def clean(self) -> None:
        for customer_id in self.customers:
            self.owner.execute(
                "DELETE FROM rfm_events.outbox WHERE entity_id = %s",
                (str(customer_id),),
            )
            self.owner.execute(
                "DELETE FROM rfm.customer_profile WHERE customer_id = %s",
                (customer_id,),
            )
        if self.sales:
            self.owner.execute(
                "DELETE FROM sales_events.outbox WHERE split_part(entity_id, ':', 1)"
                " = ANY(%s)",
                ([str(sale) for sale in self.sales],),
            )
        for customer_id in self.customers:
            with self.owner.transaction():
                self.owner.execute(
                    "DELETE FROM public.transaction_line WHERE transaction_id IN"
                    " (SELECT transaction_id FROM public.transaction"
                    "  WHERE customer_id = %s)",
                    (customer_id,),
                )
                self.owner.execute(
                    "DELETE FROM public.transaction WHERE customer_id = %s",
                    (customer_id,),
                )
                self.owner.execute(
                    "DELETE FROM public.customer WHERE customer_id = %s", (customer_id,)
                )


@pytest.fixture
def shop(owner: psycopg.Connection, owner_url: str) -> Iterator[Shop]:
    shop = Shop(owner, _role_url(owner_url, "svc_sales"))
    try:
        yield shop
    finally:
        shop.clean()


@pytest.fixture
def now(owner: psycopg.Connection) -> datetime:
    row = owner.execute("SELECT clock_timestamp()").fetchone()
    assert row is not None
    return row[0]


def days_ago(moment: datetime, days: float) -> datetime:
    return moment - timedelta(days=days)


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session", autouse=True)
def auth_environment(
    signing_key: rsa.RSAPrivateKey, tmp_path_factory: pytest.TempPathFactory
) -> None:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    path = tmp_path_factory.mktemp("rfm-auth") / "jwks.json"
    path.write_text(json.dumps({"keys": [jwk]}))
    os.environ["AUTH_ISSUER"] = ISSUER
    os.environ["AUTH_JWKS_PATH"] = str(path)
    os.environ["MOSAIQ_SERVICE_NAME"] = "rfm-engine"


@pytest.fixture(scope="session")
def redis_connection() -> Iterator[redis.Redis]:
    url = required("TEST_REDIS_URL", "REDIS")
    client = redis.Redis.from_url(url, decode_responses=True)
    password = "rfm-test-" + uuid.uuid4().hex
    line = (ROOT / "redis/rfm-engine.acl").read_text().strip()
    rendered = line.replace("{{password}}", ">" + password)
    arguments = re.findall(r"\([^)]*\)|\S+", rendered)
    client.execute_command("ACL", "SETUSER", "svc_rfm", *arguments[2:])
    parts = urlsplit(url)
    plain = urlunsplit((parts.scheme, f"{parts.hostname}:{parts.port}", "/0", "", ""))
    os.environ["REDIS_STATE_URL"] = plain
    os.environ["REDIS_RFM_PASSWORD"] = password
    try:
        yield client
    finally:
        client.execute_command("ACL", "DELUSER", "svc_rfm")
        client.close()


@pytest.fixture
def token(
    signing_key: rsa.RSAPrivateKey,
    owner: psycopg.Connection,
    redis_connection: redis.Redis,
) -> Callable[..., dict[str, str]]:
    def sign(role: str = "ANALYST") -> dict[str, str]:
        row = owner.execute(
            "SELECT min(u.user_id::text) FROM public.app_user AS u"
            " JOIN public.role AS r USING (role_id) WHERE u.is_active AND r.code = %s",
            (role,),
        ).fetchone()
        assert row is not None and row[0]
        session = str(uuid.uuid4())
        redis_connection.set("pub:auth:v1:live:" + session, "1", ex=600)
        now = int(time.time())
        encoded = jwt.encode(
            {
                "iss": ISSUER,
                "aud": "mosaiq-api",
                "sub": row[0],
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
def build_app(
    monkeypatch: pytest.MonkeyPatch, service_url: str, redis_connection: redis.Redis
) -> Iterator[Callable[..., Flask]]:
    """The application, with the consumer and the relay off unless a test asks:
    the tests drive each one pass at a time."""
    applications: list[Flask] = []

    def build() -> Flask:
        if not (ROOT / "contracts/rfm-engine/openapi.yaml").is_file():
            pytest.exit(
                "Stage contracts first: "
                "bash .github/scripts/stage-contracts.sh rfm-engine",
                returncode=2,
            )
        monkeypatch.setenv("DATABASE_URL", service_url)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("STREAM_CONSUMER_ENABLED", "false")
        monkeypatch.setenv("OUTBOX_RELAY_ENABLED", "false")
        from app import create_app

        application = create_app()
        application.config["TESTING"] = True
        applications.append(application)
        return application

    yield build
    for application in applications:
        application.extensions["mosaiq_kit"].db.close()
