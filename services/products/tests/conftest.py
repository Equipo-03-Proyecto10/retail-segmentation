"""Fixtures: the app in-process against the seeded PostgreSQL, as svc_products.

CI provides TEST_DATABASE_URL, the database owner on the seeded database. The
app connects as that owner with ``role=svc_products`` so every statement runs
with the service role's privileges. Redis-backed tests need TEST_REDIS_URL, an
administrator URL for a throwaway instance, and skip without it.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from flask import Flask
from lxml import etree
from mosaiq_kit import ApiError
from psycopg.conninfo import make_conninfo

import redis

SERVICE_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = SERVICE_ROOT / "contracts"
FIRST_TEST_ID = 900_000
_CALLER = {"sub": "00000000-0000-4000-8000-000000000001"}


def _missing(message: str, kind: str) -> None:
    if os.environ.get(f"MOSAIQ_REQUIRE_{kind}") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        _missing("TEST_DATABASE_URL names no seeded PostgreSQL", "POSTGRES")
    return url


@pytest.fixture(scope="session")
def owner_url() -> str:
    return _database_url()


@pytest.fixture(scope="session")
def service_dsn(owner_url: str) -> str:
    return make_conninfo(owner_url, options="-c role=svc_products")


@pytest.fixture
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


@pytest.fixture(autouse=True)
def clean_products() -> Iterator[None]:
    """Remove the products tests create, before and after each test."""

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        yield
        return

    def purge() -> None:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(
                "DELETE FROM public.product WHERE product_id >= %s",
                (FIRST_TEST_ID,),
            )
            # Their audit and outbox rows are test residue too (ids 9xxxxx).
            connection.execute(
                "DELETE FROM public.audit_log " "WHERE entity = %s AND entity_pk ~ %s",
                ("product", "^9[0-9]{5}$"),
            )
            connection.execute(
                "DELETE FROM products_events.outbox WHERE entity_id ~ %s",
                ("^9[0-9]{5}$",),
            )

    purge()
    yield
    purge()


@pytest.fixture(scope="session")
def category_id(owner_url: str) -> int:
    with psycopg.connect(owner_url) as connection:
        row = connection.execute(
            "SELECT min(category_id) FROM public.category"
        ).fetchone()
    return int(row[0])


@pytest.fixture(scope="session")
def admin_user(owner_url: str) -> str:
    """A real app_user, so the audit row's foreign key can name the caller."""

    with psycopg.connect(owner_url) as connection:
        row = connection.execute(
            "SELECT user_id FROM public.app_user ORDER BY user_id LIMIT 1"
        ).fetchone()
    return str(row[0])


@pytest.fixture(autouse=True)
def default_caller(admin_user: str) -> None:
    _CALLER["sub"] = admin_user


def fake_verify(authorization: str | None) -> dict:
    """Stand in for the RS256 check, which has its own tests in the kit."""

    match = re.fullmatch(r"Bearer ([A-Z_]+):([0-9a-f-]{36})", authorization or "")
    if match is None:
        raise ApiError(401, "invalid_token", "The bearer token is invalid.")
    return {"role": match.group(1), "sub": match.group(2), "sid": "s"}


@pytest.fixture
def build_app(
    monkeypatch: pytest.MonkeyPatch, service_dsn: str
) -> Iterator[Callable[..., Flask]]:
    """Create apps with explicit switches; every worker defaults to off."""

    apps: list[Flask] = []

    def build(**environment: str) -> Flask:
        from app import create_app

        defaults = {
            "DATABASE_URL": service_dsn,
            "MOSAIQ_SERVICE_NAME": "products",
            "MOSAIQ_CONTRACTS_DIR": str(CONTRACTS),
            "CACHE_ENABLED": "false",
            "RATE_LIMIT_ENABLED": "false",
            "STREAM_CONSUMER_ENABLED": "false",
            "OUTBOX_RELAY_ENABLED": "false",
        }
        for name, value in {**defaults, **environment}.items():
            monkeypatch.setenv(name, value)
        flask_app = create_app()
        flask_app.extensions["mosaiq_kit"].auth.verify = fake_verify
        flask_app.config["TESTING"] = True
        apps.append(flask_app)
        return flask_app

    yield build
    for flask_app in apps:
        for name in ("products_relay", "products_consumer"):
            worker = flask_app.extensions.get(name)
            if worker is not None:
                worker.stop()
        flask_app.extensions["mosaiq_kit"].db.close()


@pytest.fixture
def app(build_app: Callable[..., Flask]) -> Flask:
    return build_app()


@pytest.fixture
def client(app: Flask):
    return app.test_client()


def headers(role: str = "ADMIN", sub: str | None = None, **extra: str) -> dict:
    return {"Authorization": f"Bearer {role}:{sub or _CALLER['sub']}", **extra}


def product_body(offset: int = 1, **changes) -> dict:
    body = {
        "product_id": FIRST_TEST_ID + offset,
        "sku": f"TST-{offset:05d}",
        "name": f"Test product {offset}",
        "category_id": 1,
        "list_price": "12.50",
    }
    return {**body, **changes}


@pytest.fixture(scope="session")
def xsd() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(CONTRACTS / "products" / "products.xsd")))


# Redis ----------------------------------------------------------------------


@pytest.fixture
def redis_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[redis.Redis]:
    """An svc_products user built from redis/products.acl on a test instance."""

    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        _missing("TEST_REDIS_URL names no throwaway Redis", "REDIS")
    admin = redis.Redis.from_url(url, decode_responses=True)
    password = secrets.token_urlsafe(16)
    line = (SERVICE_ROOT / "redis" / "products.acl").read_text().strip()
    rendered = line.replace("{{password}}", ">" + password)
    arguments = re.findall(r"\([^)]*\)|\S+", rendered)
    assert arguments[:2] == ["user", "svc_products"]
    admin.execute_command("ACL", "SETUSER", "svc_products", *arguments[2:])
    try:
        admin.config_set("appendonly", "yes")
    except redis.RedisError:
        _missing("the test Redis cannot enable the AOF that WAITAOF needs", "REDIS")
    parsed = urlsplit(url)
    plain = urlunsplit(
        (parsed.scheme, parsed.hostname + f":{parsed.port}", "/0", "", "")
    )
    monkeypatch.setenv("REDIS_STATE_URL", plain)
    monkeypatch.setenv("REDIS_CACHE_URL", plain)
    monkeypatch.setenv("REDIS_PRODUCTS_PASSWORD", password)
    admin.set("pub:auth:v1:live:s", "1", ex=600)

    def sweep() -> None:
        for pattern in ("products:cache:*", "products:rl:*"):
            for key in admin.scan_iter(match=pattern):
                admin.delete(key)
        admin.delete("stream:products.v1.changed")

    sweep()
    yield admin
    sweep()
    admin.delete("pub:auth:v1:live:s")
    admin.execute_command("ACL", "DELUSER", "svc_products")
    admin.close()


def wait_for(check: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return check()
