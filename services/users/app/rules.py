"""The users rules, ported from web/services/users.py (ADR-0045, E-17).

RN-01: exactly one ADMIN. "Never two" is enforced here and by
`ux_app_user_single_administrator`; a 23505 from it is mapped by constraint
name to 409 `single_administrator`, never 500. "Never zero" is enforced here
only, as in the web: no index can require a row to exist. The seat moves only
through `transfer_administrator`.
RN-02: one account per mailbox, whatever the case (`ux_app_user_email_lower`).
RN-04: accounts are deactivated, never deleted; svc_users holds no DELETE.

Every function takes the request's connection and raises `ApiError`. The
outcomes are pinned to the web's by tests/golden/users.json; where this port
deliberately differs (ADR-0036), tests/test_golden.py says so.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mosaiq_kit import ApiError
from psycopg import Connection, sql
from psycopg.errors import UniqueViolation

ADMIN = "ADMIN"
ADMIN_ROLE_ID = 1

# Fixed fragments, composed with psycopg.sql; values are only ever bound.
USER_COLUMNS = sql.SQL(
    "u.user_id, u.name, u.email, r.code AS role_code, u.is_active, u.created_at"
)
USER_FROM = sql.SQL("app_user AS u JOIN role AS r ON r.role_id = u.role_id")


def normalize_email(email: str) -> str:
    """One spelling per mailbox, as the web stores it (#288)."""
    return email.strip().lower()


def single_administrator() -> ApiError:
    return ApiError(
        409,
        "single_administrator",
        "This system has exactly one administrator, and the role is already taken.",
    )


def clean_name(name: str) -> str:
    """The name as stored: trimmed, and refused when nothing is left.

    The schema's minLength counts the untrimmed value, so "   " passes it and
    would be stored as "" (#587). The web refuses the same name.
    """
    cleaned = name.strip()
    if not cleaned:
        raise ApiError(
            422,
            "invalid_body",
            "The request body violates its schema.",
            [{"field": "name", "code": "too_short"}],
        )
    return cleaned


def user_not_found() -> ApiError:
    return ApiError(404, "user_not_found", "The user does not exist.")


def invalid_successor(message: str) -> ApiError:
    return ApiError(
        422,
        "invalid_successor",
        message,
        [{"field": "successor_user_id", "code": "invalid"}],
    )


def translate(error: UniqueViolation) -> ApiError:
    """The index's refusal in the rule's words, by constraint name."""
    constraint = getattr(getattr(error, "diag", None), "constraint_name", None)
    if constraint == "ux_app_user_single_administrator":
        return single_administrator()
    if constraint == "ux_app_user_email_lower":
        return ApiError(
            409,
            "email_taken",
            "A user with that email already exists.",
            [{"field": "email", "code": "not_unique"}],
        )
    raise error


def _row(cursor: Any) -> dict[str, Any] | None:
    row = cursor.fetchone()
    if row is None:
        return None
    names = [column.name for column in cursor.description]
    return dict(zip(names, row, strict=True))


def representation(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(user["user_id"]),
        "name": user["name"],
        "email": user["email"],
        "role_code": user["role_code"],
        "is_active": user["is_active"],
        "created_at": user["created_at"],
    }


def get_user(
    connection: Connection, user_id: UUID | str, *, lock: bool = False
) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT {columns}, u.role_id, u.password_hash FROM {source}"
                " WHERE u.user_id = %s{lock}"
            ).format(
                columns=USER_COLUMNS,
                source=USER_FROM,
                lock=sql.SQL(" FOR UPDATE OF u" if lock else ""),
            ),
            (str(user_id),),
        )
        return _row(cursor)


def require_user(connection: Connection, user_id: UUID | str) -> dict[str, Any]:
    user = get_user(connection, user_id)
    if user is None:
        raise user_not_found()
    return user


def role_id(connection: Connection, role_code: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT role_id FROM role WHERE code = %s", (role_code,))
        row = cursor.fetchone()
    if row is None:
        raise ApiError(
            422,
            "unknown_role",
            "No role has that code.",
            [{"field": "role_code", "code": "unknown"}],
        )
    return row[0]


def administrator_count(connection: Connection) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM app_user WHERE role_id = %s", (ADMIN_ROLE_ID,)
        )
        return cursor.fetchone()[0]


def create_user(
    connection: Connection,
    *,
    name: str,
    email: str,
    password_hash: str,
    role_code: str,
) -> dict[str, Any]:
    """Create an account; an ADMIN is never created here, only transferred."""
    name = clean_name(name)
    if role_code == ADMIN:
        raise single_administrator()
    new_role = role_id(connection, role_code)
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO app_user (role_id, name, email, password_hash)"
                " VALUES (%s, %s, %s, %s) RETURNING user_id",
                (new_role, name, normalize_email(email), password_hash),
            )
            user_id = cursor.fetchone()[0]
    except UniqueViolation as error:
        raise translate(error) from None
    return require_user(connection, user_id)


def update_user(
    connection: Connection,
    user_id: UUID | str,
    *,
    name: str | None = None,
    email: str | None = None,
    role_code: str | None = None,
) -> dict[str, Any]:
    """Edit name, email or a non-ADMIN role; the ADMIN role only transfers."""
    if name is not None:
        name = clean_name(name)
    user = get_user(connection, user_id, lock=True)
    if user is None:
        raise user_not_found()
    new_role = user["role_id"]
    if role_code is not None and role_code != user["role_code"]:
        if role_code == ADMIN or user["role_code"] == ADMIN:
            raise ApiError(
                409,
                "use_administrator_transfer",
                "The administrator role moves only through an administrator transfer.",
            )
        new_role = role_id(connection, role_code)
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE app_user SET name = %s, email = %s WHERE user_id = %s",
                (
                    user["name"] if name is None else name,
                    user["email"] if email is None else normalize_email(email),
                    str(user["user_id"]),
                ),
            )
            if new_role != user["role_id"]:
                cursor.execute(
                    "UPDATE app_user SET role_id = %s WHERE user_id = %s",
                    (new_role, str(user["user_id"])),
                )
    except UniqueViolation as error:
        raise translate(error) from None
    return require_user(connection, user_id)


def set_active(
    connection: Connection, user_id: UUID | str, is_active: bool
) -> dict[str, Any]:
    """Deactivate or reactivate; never deactivate the only ADMIN (RN-01)."""
    user = get_user(connection, user_id, lock=True)
    if user is None:
        raise user_not_found()
    if (
        not is_active
        and user["role_code"] == ADMIN
        and administrator_count(connection) <= 1
    ):
        raise ApiError(
            409,
            "administrator_required",
            "This is the only administrator, and the system may not be left "
            "without one.",
        )
    if user["is_active"] != is_active:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE app_user SET is_active = %s WHERE user_id = %s",
                (is_active, str(user["user_id"])),
            )
    return require_user(connection, user_id)


def set_password_hash(
    connection: Connection, user_id: UUID | str, password_hash: str
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE app_user SET password_hash = %s WHERE user_id = %s",
            (password_hash, str(user_id)),
        )


def transfer_administrator(
    connection: Connection, *, caller: str, successor_id: UUID | str
) -> None:
    """Move the ADMIN seat to a successor, under row locks, in one transaction.

    The current ADMIN row is locked first, then the successor's, always in
    that order. The incumbent takes the successor's previous role before the
    successor is promoted, so the index never sees two administrators. If the
    seat moved while this waited for the lock (the web's own transfer), the
    re-check refuses with `single_administrator` rather than act on a stale
    caller; the index refuses anything the re-check missed.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT user_id FROM app_user WHERE role_id = %s FOR UPDATE",
            (ADMIN_ROLE_ID,),
        )
        locked = cursor.fetchone()
    if locked is None or str(locked[0]) != str(caller):
        raise single_administrator()
    incumbent = locked[0]
    if str(successor_id) == str(incumbent):
        raise invalid_successor("The successor is already the administrator.")
    successor = get_user(connection, successor_id, lock=True)
    if successor is None:
        raise invalid_successor("The successor does not exist.")
    if not successor["is_active"]:
        raise ApiError(
            409,
            "successor_inactive",
            "The successor is inactive.",
            [{"field": "successor_user_id", "code": "inactive"}],
        )
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE app_user SET role_id = %s WHERE user_id = %s",
                (successor["role_id"], str(incumbent)),
            )
            cursor.execute(
                "UPDATE app_user SET role_id = %s WHERE user_id = %s",
                (ADMIN_ROLE_ID, str(successor["user_id"])),
            )
    except UniqueViolation as error:
        raise translate(error) from None
    if administrator_count(connection) != 1:
        raise single_administrator()


def list_users(
    connection: Connection,
    *,
    role_code: str | None,
    active: bool | None,
    query: str | None,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    conditions: list[sql.Composable] = []
    params: list[Any] = []
    if role_code is not None:
        conditions.append(sql.SQL("r.code = %s"))
        params.append(role_code)
    if active is not None:
        conditions.append(sql.SQL("u.is_active = %s"))
        params.append(active)
    if query is not None:
        # % and _ match literally.
        pattern = (
            "%"
            + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        conditions.append(sql.SQL("(u.name ILIKE %s OR u.email ILIKE %s)"))
        params.extend([pattern, pattern])
    where = (
        sql.SQL(" WHERE {}").format(sql.SQL(" AND ").join(conditions))
        if conditions
        else sql.SQL("")
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT count(*) FROM {source}{where}").format(
                source=USER_FROM, where=where
            ),
            params,
        )
        total = cursor.fetchone()[0]
        cursor.execute(
            sql.SQL(
                "SELECT {columns} FROM {source}{where}"
                " ORDER BY u.created_at, u.user_id LIMIT %s OFFSET %s"
            ).format(columns=USER_COLUMNS, source=USER_FROM, where=where),
            [*params, size, (page - 1) * size],
        )
        names = [column.name for column in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    return rows, total


def list_roles(connection: Connection) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT code, description FROM role ORDER BY role_id")
        return [
            {"role_code": code, "description": description}
            for code, description in cursor.fetchall()
        ]
