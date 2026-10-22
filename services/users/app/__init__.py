"""users-service: accounts, passwords, the role vocabulary and RN-01 (F14-04).

The eleven operations of contracts/users/openapi.yaml on mosaiq-kit. Every
write runs in `kit.db.transaction()`, which names the caller to fn_audit. Any
change to an account's access (role, active flag, password) is captured by the
schema's trigger into users_events.outbox, and the relay started here carries
it to `stream:users.v1.access_changed`, where auth-service ends that user's
sessions (ADR-0044).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import redis
from flask import Flask, request
from mosaiq_kit import (
    ApiError,
    Kit,
    NoContent,
    OutboxRelay,
    Page,
    Representation,
    configure_logging,
)

from app import rules
from app.passwords import hash_password, verify_password

LOGGER = logging.getLogger(__name__)
STREAM = "stream:users.v1.access_changed"
ROLE_CODES = (
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
)
# More than five failed current-password checks for one user in 15 minutes.
PASSWORD_FAILURES = 5
PASSWORD_WINDOW_SECONDS = 900


def relay_redis(url: str) -> redis.Redis:
    """svc_users on redis-state, with time for WAITAOF's two seconds.

    The kit's request client gives up after 0.2 s, which suits a liveness
    lookup but would fail every durable stream write.
    """
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = "[" + hostname + "]"
    netloc = hostname + ((":" + str(parsed.port)) if parsed.port else "")
    return redis.Redis.from_url(
        urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, "")),
        username="svc_users",
        password=os.environ.get("REDIS_USERS_PASSWORD"),
        socket_connect_timeout=1,
        socket_timeout=3,
        decode_responses=True,
    )


def _user(user: dict[str, Any], status: int = 200) -> Representation:
    return Representation("User", rules.representation(user), status=status)


def _filters() -> tuple[str | None, bool | None, str | None]:
    """listUsers' own query parameters; the kit validates page and size."""
    details = []
    role = request.args.get("role")
    if role is not None and role not in ROLE_CODES:
        details.append({"field": "role", "code": "invalid_value"})
    active = request.args.get("active")
    if active is not None and active not in ("true", "false"):
        details.append({"field": "active", "code": "invalid_type"})
    query = request.args.get("q")
    if query is not None and not 1 <= len(query) <= 160:
        details.append({"field": "q", "code": "out_of_range"})
    elif query is not None and "\x00" in query:
        # PostgreSQL text cannot hold U+0000, so no name or email matches it.
        details.append({"field": "q", "code": "invalid_format"})
    if details:
        raise ApiError(
            422,
            "invalid_pagination",
            "Query values are outside their allowed ranges.",
            details,
        )
    return role, None if active is None else active == "true", query


def create_app(*, start_relay: bool = True) -> Flask:
    app = Flask(__name__, static_folder=None)
    configure_logging()
    holder: dict[str, Kit] = {}

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            row = connection.execute(
                "SELECT to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL",
                ("api_users.credential_v1", "users_events.outbox"),
            ).fetchone()
        return bool(row and row[0])

    kit = Kit(
        app,
        service="users",
        suffix="users",
        contracts_dir=Path(__file__).resolve().parent.parent / "contracts",
        ready_checks=[schema_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit

    def caller() -> dict[str, Any]:
        principal = kit.principal
        assert principal is not None  # every operation here is protected
        return principal

    @kit.operation("listUsers")
    def list_users() -> Page:
        role, active, query = _filters()
        with kit.db.connection() as connection:
            users, total = rules.list_users(
                connection,
                role_code=role,
                active=active,
                query=query,
                page=kit.page,
                size=kit.size,
            )
        return Page(
            "Users",
            "User",
            "users",
            [rules.representation(user) for user in users],
            page=kit.page,
            size=kit.size,
            total=total,
            info_key="page",
        )

    @kit.operation("createUser")
    def create_user() -> Representation:
        body = kit.body
        # Refused before the 64 MiB hash: an ADMIN is only ever transferred.
        if body["role_code"] == rules.ADMIN:
            raise rules.single_administrator()
        password_hash = hash_password(body["password"])
        with kit.db.transaction() as connection:
            user = rules.create_user(
                connection,
                name=body["name"],
                email=body["email"],
                password_hash=password_hash,
                role_code=body["role_code"],
            )
        response = _user(user, status=201)
        response.headers = {"Location": f"/v1/users/{user['user_id']}"}
        return response

    @kit.operation("getCurrentUser")
    def get_current_user() -> Representation:
        # auth-service puts app_user.user_id in sub (ADR-0036); any other
        # subject, however well signed, names no user.
        try:
            user_id = UUID(str(caller()["sub"]))
        except ValueError:
            raise rules.user_not_found() from None
        with kit.db.connection() as connection:
            return _user(rules.require_user(connection, user_id))

    @kit.operation("getUser")
    def get_user(user_id: UUID) -> Representation:
        with kit.db.connection() as connection:
            return _user(rules.require_user(connection, user_id))

    @kit.operation("updateUser")
    def update_user(user_id: UUID) -> Representation:
        body = kit.body
        with kit.db.transaction() as connection:
            user = rules.update_user(
                connection,
                user_id,
                name=body.get("name"),
                email=body.get("email"),
                role_code=body.get("role_code"),
            )
        return _user(user)

    @kit.operation("changeUserPassword")
    def change_user_password(user_id: UUID) -> NoContent:
        principal = caller()
        own = str(user_id) == str(principal["sub"])
        if not own and principal.get("role") != rules.ADMIN:
            raise ApiError(
                403,
                "forbidden_role",
                "The authenticated role cannot perform this operation.",
            )
        current = kit.body.get("current_password")
        if own and current is None:
            raise ApiError(
                422,
                "invalid_body",
                "The request body violates its schema.",
                [{"field": "current_password", "code": "required"}],
            )
        with kit.db.connection() as connection:
            user = rules.get_user(connection, user_id)
        if user is None:
            raise rules.user_not_found()
        if own:
            _check_current_password(kit, user, current)
        password_hash = hash_password(kit.body["new_password"])
        with kit.db.transaction() as connection:
            if rules.get_user(connection, user_id, lock=True) is None:
                raise rules.user_not_found()
            rules.set_password_hash(connection, user_id, password_hash)
        return NoContent()

    @kit.operation("deactivateUser")
    def deactivate_user(user_id: UUID) -> Representation:
        with kit.db.transaction() as connection:
            return _user(rules.set_active(connection, user_id, False))

    @kit.operation("reactivateUser")
    def reactivate_user(user_id: UUID) -> Representation:
        with kit.db.transaction() as connection:
            return _user(rules.set_active(connection, user_id, True))

    @kit.operation("listRoles")
    def list_roles() -> Page:
        with kit.db.connection() as connection:
            roles = rules.list_roles(connection)
        return Page(
            "Roles",
            "Role",
            "roles",
            roles,
            page=1,
            size=max(len(roles), 1),
            total=len(roles),
            info_key="page",
        )

    @kit.operation("getRole")
    def get_role(role_code: str) -> Representation:
        with kit.db.connection() as connection:
            for role in rules.list_roles(connection):
                if role["role_code"] == role_code:
                    return Representation("Role", role)
        raise ApiError(404, "role_not_found", "The role does not exist.")

    @kit.operation("transferAdministrator")
    def transfer_administrator() -> NoContent:
        with kit.db.transaction() as connection:
            rules.transfer_administrator(
                connection,
                caller=str(caller()["sub"]),
                successor_id=kit.body["successor_user_id"],
            )
        return NoContent()

    dsn = os.environ.get("DATABASE_URL")
    if start_relay and dsn:
        relay = OutboxRelay(
            dsn,
            "users",
            [STREAM],
            relay_redis(
                os.environ.get("REDIS_STATE_URL", "redis://redis-state:6379/0")
            ),
        )
        relay.start()
        app.extensions["users_relay"] = relay

    return app


def _check_current_password(kit: Kit, user: dict[str, Any], current: str) -> None:
    """Verify a self-service change, counting failures per user in redis-state.

    The counter is the brake on guessing through a stolen session. Without
    Redis it cannot be honoured, so the check fails closed, as authentication
    does (ADR-0037).
    """
    key = f"users:rl:pwd:{user['user_id']}"
    store = kit.auth.redis
    try:
        failures = int(store.get(key) or 0)
        if failures >= PASSWORD_FAILURES:
            ttl = store.ttl(key)
            raise ApiError(
                429,
                "rate_limited",
                "Too many failed current-password checks.",
                headers={
                    "Retry-After": str(ttl if ttl > 0 else PASSWORD_WINDOW_SECONDS)
                },
            )
        if verify_password(user["password_hash"], current):
            return
        pipe = store.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, PASSWORD_WINDOW_SECONDS, nx=True)
        pipe.execute()
    except redis.RedisError as error:
        LOGGER.warning("Password-check counter unavailable: %r", error)
        raise ApiError(
            503, "auth_unavailable", "Session state is unavailable."
        ) from None
    raise ApiError(
        422,
        "invalid_current_password",
        "The current password is incorrect.",
        [{"field": "current_password", "code": "incorrect"}],
    )
