"""User administration, and the rule that there is exactly one administrator.

RN-01 in docs/business-rules.md, and a hard rule in AGENTS.md. It has two
directions, and only one of them can be a constraint:

* **Never two.** Enforced here *and* by `ux_app_user_single_administrator` in
  sql/01_schema.sql. AGENTS.md is explicit that one half alone does not count —
  this check alone is bypassed by a direct INSERT, and the index alone reaches
  the user as an unexplained database error.
* **Never zero.** Enforced only here. No unique index can require a row to
  exist, and a trigger that refused every write leaving the table without an
  administrator would also refuse the legitimate rotation F4-06 (#107) has to
  perform. So the application refuses to demote or deactivate the last one, and
  says why.

Every write to `app_user` goes through this module. The screens that call it —
listing, forms, validation messages — are F3-06 (#66); what lives here is the
rule those screens must not be able to talk their way around.
"""

from __future__ import annotations

from uuid import UUID

from psycopg import Connection
from psycopg.errors import UniqueViolation

from web.db.transactions import atomic
from web.db.users import (
    ADMINISTRATOR_ROLE_CODE,
    AppUser,
    count_administrators,
    deactivate_demonstration_accounts,
    get_role_id_by_code,
    get_sole_administrator,
    get_user_by_id,
    insert_user,
    update_active,
    update_password_hash,
    update_role,
)
from web.services.auth import hash_password

SECOND_ADMINISTRATOR = (
    "This system has exactly one administrator, and the role is already taken. "
    "Move the current administrator to another role first, or choose a "
    "different role for this user."
)
# What a successor is created as for the moment before the role moves onto it,
# and what the outgoing administrator is left holding afterwards. ANALYST reads
# and writes nothing outside reports, so an account parked here briefly can do
# no harm if the transfer fails and the transaction rolls back anyway.
PLACEHOLDER_ROLE_CODE = "ANALYST"

LAST_ADMINISTRATOR = (
    "This is the only administrator, and the system may not be left without "
    "one. Appoint another administrator first."
)


class SingleAdministratorError(Exception):
    """A write refused because it would leave two administrators, or none."""


class DuplicateEmailError(Exception):
    """The email is already registered."""


class UnknownRoleError(Exception):
    """A role code that does not exist in `role`."""


class UnknownUserError(Exception):
    """A user id that does not exist in `app_user`."""


def _role_id(connection: Connection, role_code: str) -> int:
    role_id = get_role_id_by_code(connection, role_code)
    if role_id is None:
        raise UnknownRoleError(f"No role with code {role_code!r} exists.")
    return role_id


def _user(connection: Connection, user_id: UUID | str) -> AppUser:
    user = get_user_by_id(connection, user_id)
    if user is None:
        raise UnknownUserError(f"No user with id {user_id!r} exists.")
    return user


def _refuse_a_second_administrator(connection: Connection) -> None:
    if count_administrators(connection) > 0:
        raise SingleAdministratorError(SECOND_ADMINISTRATOR)


def _refuse_leaving_nobody(connection: Connection) -> None:
    """Refuse a write that would take the last administrator away.

    Asks the question the rule actually asks — would this leave nobody? — so
    the check still reads correctly if the database ever holds more than one,
    which is precisely the state the index exists to prevent and the state a
    dropped index would produce.
    """
    if count_administrators(connection) <= 1:
        raise SingleAdministratorError(LAST_ADMINISTRATOR)


def _translate_unique_violation(error: UniqueViolation) -> Exception:
    """Give the index's refusal the same words the application's check uses.

    The count and the write are two statements, so two requests can both pass
    the check and one will lose the insert. The database is the authority that
    settles it; the person who lost should still read a sentence about the
    rule rather than a constraint name.
    """
    constraint = getattr(getattr(error, "diag", None), "constraint_name", None)
    if constraint == "ux_app_user_single_administrator":
        return SingleAdministratorError(SECOND_ADMINISTRATOR)
    return DuplicateEmailError("A user with that email already exists.")


@atomic
def create_user(
    connection: Connection,
    *,
    name: str,
    email: str,
    password: str,
    role_code: str,
) -> UUID:
    """Create a user, refusing a second administrator. Returns the new id."""
    role_id = _role_id(connection, role_code)

    if role_code == ADMINISTRATOR_ROLE_CODE:
        _refuse_a_second_administrator(connection)

    try:
        return insert_user(
            connection,
            role_id=role_id,
            name=name,
            email=email,
            password_hash=hash_password(password),
        )
    except UniqueViolation as error:
        raise _translate_unique_violation(error) from error


@atomic
def change_role(connection: Connection, user_id: UUID | str, role_code: str) -> None:
    """Move a user to another role, in either direction of RN-01."""
    user = _user(connection, user_id)
    role_id = _role_id(connection, role_code)

    if role_code == user.role_code:
        return

    if role_code == ADMINISTRATOR_ROLE_CODE:
        _refuse_a_second_administrator(connection)
    elif user.role_code == ADMINISTRATOR_ROLE_CODE:
        # Demoting the last administrator would leave nobody able to appoint
        # the next one, which is the failure the rule exists to prevent.
        # `transfer_administrator` is how the seat legitimately changes hands.
        _refuse_leaving_nobody(connection)

    try:
        update_role(connection, user.user_id, role_id)
    except UniqueViolation as error:
        raise _translate_unique_violation(error) from error


@atomic
def set_active(connection: Connection, user_id: UUID | str, is_active: bool) -> None:
    """Activate or deactivate a user, never the sole administrator.

    RN-04: deactivation, not deletion, is how access is removed, so that the
    history keeps its actor.
    """
    user = _user(connection, user_id)

    if not is_active and user.role_code == ADMINISTRATOR_ROLE_CODE:
        _refuse_leaving_nobody(connection)

    update_active(connection, user.user_id, is_active)


@atomic
def install_administrator(
    connection: Connection,
    *,
    name: str,
    email: str,
    password: str,
) -> UUID:
    """Make a new account the one administrator, whoever holds the role now.

    The procedure F4-06 (#107) runs on the instance, where the administrator
    must be somebody whose password was never in this repository. Both cases
    the deployed system can be in are handled here rather than in the command
    that calls it:

    * **The seat is empty** — the account is created as the administrator.
    * **Somebody holds it** — the account is created in a placeholder role and
      `transfer_administrator` immediately moves the role onto it. The
      placeholder exists because RN-01 leaves no other legal order: creating a
      second administrator is refused, and demoting the incumbent first is
      refused too. The incumbent inherits the placeholder role, which is what
      the caller then deactivates.

    One service-owned transaction: at no point does the system
    hold two administrators, and if anything fails it holds the one it started
    with.
    """
    if count_administrators(connection) == 0:
        return create_user(
            connection,
            name=name,
            email=email,
            password=password,
            role_code=ADMINISTRATOR_ROLE_CODE,
        )

    incumbent = _sole_administrator(connection)
    successor = create_user(
        connection,
        name=name,
        email=email,
        password=password,
        role_code=PLACEHOLDER_ROLE_CODE,
    )
    transfer_administrator(
        connection, from_user_id=incumbent.user_id, to_user_id=successor
    )
    return successor


def _sole_administrator(connection: Connection) -> AppUser:
    """The account currently holding the role. There is exactly one."""
    user = get_sole_administrator(connection)
    if user is None:
        raise SingleAdministratorError("There is no administrator to replace.")
    return user


@atomic
def transfer_administrator(
    connection: Connection,
    *,
    from_user_id: UUID | str,
    to_user_id: UUID | str,
) -> None:
    """Hand the administrator role from one user to another, atomically.

    Without this the rule has no legal way to change who the administrator is:
    promoting the successor first is refused because the seat is taken, and
    demoting the incumbent first is refused because it would leave nobody. Both
    refusals are correct in isolation, and together they would force F4-06
    (#107) to bypass this module with raw SQL on the instance — which is the
    outcome the rule exists to prevent.

    So the swap is one operation. It demotes and then promotes, in that order,
    so the partial unique index is never asked to hold two administrators at
    once; the service transaction commits both changes or rolls back both.
    """
    incumbent = _user(connection, from_user_id)
    successor = _user(connection, to_user_id)

    if incumbent.role_code != ADMINISTRATOR_ROLE_CODE:
        raise SingleAdministratorError(
            f"{incumbent.email} is not the administrator, so there is nothing "
            "to transfer."
        )
    if incumbent.user_id == successor.user_id:
        return

    administrator = _role_id(connection, ADMINISTRATOR_ROLE_CODE)
    update_role(connection, incumbent.user_id, successor.role_id)
    try:
        update_role(connection, successor.user_id, administrator)
    except UniqueViolation as error:
        raise _translate_unique_violation(error) from error


MINIMUM_PASSWORD_LENGTH = 12


def validate_user(
    *, name: str, email: str, password: str, role_code: str
) -> dict[str, str]:
    """Validate a user form without an HTTP request or database."""
    errors: dict[str, str] = {}
    if not name.strip():
        errors["name"] = "Name is required."
    elif len(name) > 120:
        errors["name"] = "Name must be 120 characters or fewer."
    local, separator, domain = email.partition("@")
    if (
        len(email) > 160
        or not local
        or not separator
        or not domain
        or "@" in domain
        or any(character.isspace() for character in email)
    ):
        errors["email"] = "A valid email is required."
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        errors["password"] = (
            f"Password must be at least {MINIMUM_PASSWORD_LENGTH} characters."
        )
    if not role_code:
        errors["role_code"] = "Role is required."
    return errors


@atomic
def provision_administrator(
    connection: Connection,
    *,
    name: str,
    email: str,
    password: str,
    deactivate_demo_accounts: bool,
) -> list[str]:
    """Install an administrator and optionally close demo access atomically."""
    install_administrator(connection, name=name, email=email, password=password)
    return (
        deactivate_demonstration_accounts(connection)
        if deactivate_demo_accounts
        else []
    )


@atomic
def rotate_password(connection: Connection, email: str, password: str) -> None:
    """Replace an existing account's password as one committed operation."""
    update_password_hash(connection, email, hash_password(password))
