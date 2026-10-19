"""Users outcomes, through web/services/users.py.

The seed's single administrator is user 1, `admin@mosaiq-demo.com`. Users 2
and 14 are ANALYSTs and user 3 a STORE_MANAGER, all active.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from tests.golden.cases import Case, Context, accepted, invalid, one, refused
from web.services.users import (
    DuplicateEmailError,
    SingleAdministratorError,
    change_role,
    create_user,
    set_active,
    transfer_administrator,
    update_user,
    validate_user,
)

ADMIN = "11111111-1111-1111-1111-000000000001"
ANALYST = "11111111-1111-1111-1111-000000000014"
STORE_MANAGER = "11111111-1111-1111-1111-000000000003"
# Never a real credential: the cases only need one long enough to pass the form.
PASSWORD = "golden-fixture-password"
REFUSALS = (SingleAdministratorError, DuplicateEmailError, psycopg.Error)


def _role(connection: psycopg.Connection, user_id: str) -> str:
    return one(
        connection,
        "SELECT r.code FROM app_user u JOIN role r USING (role_id)"
        " WHERE u.user_id = %s",
        (user_id,),
    )


def _attempt(
    call: Callable[[psycopg.Connection], Any],
    observe: Callable[[psycopg.Connection], dict[str, Any]] | None = None,
) -> Callable[[psycopg.Connection], dict[str, Any]]:
    def run(connection: psycopg.Connection) -> dict[str, Any]:
        try:
            call(connection)
        except REFUSALS as error:
            return refused(error)
        return accepted(**(observe(connection) if observe else {}))

    return run


def build(connection: psycopg.Connection, context: Context) -> list[Case]:
    def new_user(email: str, role_code: str) -> Callable[[psycopg.Connection], Any]:
        return lambda c: create_user(
            c, name="Golden user", email=email, password=PASSWORD, role_code=role_code
        )

    return [
        Case(
            "create-second-administrator",
            {
                "call": "create_user",
                "email": "second-admin@example.com",
                "role_code": "ADMIN",
            },
            _attempt(new_user("second-admin@example.com", "ADMIN")),
        ),
        Case(
            "promote-to-second-administrator",
            {"call": "change_role", "user_id": ANALYST, "role_code": "ADMIN"},
            _attempt(lambda c: change_role(c, ANALYST, "ADMIN")),
        ),
        Case(
            "edit-into-second-administrator",
            {
                "call": "update_user",
                "user_id": ANALYST,
                "name": "Demo User 14",
                "email": "user14@mosaiq-demo.com",
                "role_code": "ADMIN",
            },
            _attempt(
                lambda c: update_user(
                    c,
                    ANALYST,
                    name="Demo User 14",
                    email="user14@mosaiq-demo.com",
                    role_code="ADMIN",
                )
            ),
        ),
        Case(
            "demote-only-administrator",
            {"call": "change_role", "user_id": ADMIN, "role_code": "ANALYST"},
            _attempt(lambda c: change_role(c, ADMIN, "ANALYST")),
        ),
        Case(
            "deactivate-only-administrator",
            {"call": "set_active", "user_id": ADMIN, "is_active": False},
            _attempt(lambda c: set_active(c, ADMIN, False)),
        ),
        Case(
            "transfer-administrator",
            {
                "call": "transfer_administrator",
                "from_user_id": ADMIN,
                "to_user_id": ANALYST,
            },
            _attempt(
                lambda c: transfer_administrator(
                    c, from_user_id=ADMIN, to_user_id=ANALYST
                ),
                lambda c: {"from_role": _role(c, ADMIN), "to_role": _role(c, ANALYST)},
            ),
        ),
        Case(
            "transfer-from-non-administrator",
            {
                "call": "transfer_administrator",
                "from_user_id": ANALYST,
                "to_user_id": STORE_MANAGER,
            },
            _attempt(
                lambda c: transfer_administrator(
                    c, from_user_id=ANALYST, to_user_id=STORE_MANAGER
                )
            ),
        ),
        Case(
            "create-case-variant-duplicate-email",
            {
                "call": "create_user",
                "email": "User14@Mosaiq-Demo.COM",
                "role_code": "ANALYST",
            },
            _attempt(new_user("User14@Mosaiq-Demo.COM", "ANALYST")),
        ),
        Case(
            "edit-into-case-variant-duplicate-email",
            {
                "call": "update_user",
                "user_id": STORE_MANAGER,
                "name": "Demo User 3",
                "email": "USER14@mosaiq-demo.com",
                "role_code": "STORE_MANAGER",
            },
            _attempt(
                lambda c: update_user(
                    c,
                    STORE_MANAGER,
                    name="Demo User 3",
                    email="USER14@mosaiq-demo.com",
                    role_code="STORE_MANAGER",
                )
            ),
        ),
        Case(
            "validate-malformed-email",
            {"call": "validate_user", "email": "not-an-email", "role_code": "ANALYST"},
            lambda c: invalid(
                validate_user(
                    name="Golden user",
                    email="not-an-email",
                    password=PASSWORD,
                    role_code="ANALYST",
                )
            ),
        ),
        Case(
            "create-malformed-email",
            {"call": "create_user", "email": "no-domain@", "role_code": "ANALYST"},
            _attempt(new_user("no-domain@", "ANALYST")),
        ),
        Case(
            "create-analyst",
            {
                "call": "create_user",
                "email": "golden-analyst@example.com",
                "role_code": "ANALYST",
            },
            _attempt(
                new_user("golden-analyst@example.com", "ANALYST"),
                lambda c: {
                    "role": one(
                        c,
                        "SELECT r.code FROM app_user u JOIN role r USING (role_id)"
                        " WHERE u.email = %s",
                        ("golden-analyst@example.com",),
                    )
                },
            ),
        ),
        Case(
            "deactivate-analyst",
            {"call": "set_active", "user_id": ANALYST, "is_active": False},
            _attempt(
                lambda c: set_active(c, ANALYST, False),
                lambda c: {
                    "is_active": one(
                        c,
                        "SELECT is_active FROM app_user WHERE user_id = %s",
                        (ANALYST,),
                    )
                },
            ),
        ),
    ]


def as_of(context: Context) -> None:
    return None
