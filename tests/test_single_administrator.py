"""RN-01: exactly one administrator, refused twice (#70, F4-02).

The application half is tested here against a scripted connection; the schema
half cannot be, because a unique index only exists inside PostgreSQL. What
these tests can do about the schema half is make sure it has not been deleted
and that it still agrees with the seed about which role id `ADMIN` is —
`sql/verify_integrity.sql` cases N17 and P4 exercise the index itself, and
`docs/evidence/f4-02-single-administrator.md` records a run of both.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from psycopg.errors import UniqueViolation

from web.db.users import ADMINISTRATOR_ROLE_CODE, AppUser
from web.services.users import (
    LAST_ADMINISTRATOR,
    SECOND_ADMINISTRATOR,
    DuplicateEmailError,
    SingleAdministratorError,
    UnknownRoleError,
    UnknownUserError,
    change_role,
    create_user,
    set_active,
    transfer_administrator,
)

_ROLE_IDS = {
    "ADMIN": 1,
    "ANALYST": 2,
    "STORE_MANAGER": 3,
    "MARKETING": 4,
    "INVENTORY_PLANNER": 5,
    "AUDITOR": 6,
    "CUSTOMER": 7,
}

_ADMIN_ID = UUID("11111111-1111-1111-1111-000000000001")
_ANALYST_ID = UUID("11111111-1111-1111-1111-000000000002")


def _user(user_id: UUID, role_code: str) -> AppUser:
    return AppUser(
        user_id=user_id,
        role_id=_ROLE_IDS[role_code],
        role_code=role_code,
        role_description=role_code.replace("_", " ").title(),
        name=f"{role_code.title()} user",
        email=f"{role_code.lower()}@mosaiq-demo.com",
        password_hash="argon2id-hash",
        is_active=True,
    )


class _Connection:
    """A connection that answers the three questions the service asks.

    Deliberately not a `Mock` with a canned `fetchone`: the service asks for a
    user, a role id and a count in an order that is its own business, so the
    fake answers by looking at the SQL rather than by call order.
    """

    def __init__(
        self,
        *,
        users: dict[UUID, AppUser] | None = None,
        administrators: int = 1,
        raises: Exception | None = None,
    ) -> None:
        self.users = users or {
            _ADMIN_ID: _user(_ADMIN_ID, "ADMIN"),
            _ANALYST_ID: _user(_ANALYST_ID, "ANALYST"),
        }
        self.administrators = administrators
        self.raises = raises
        self.writes: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def cursor(self):
        return _Cursor(self)


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.row: tuple | None = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exception) -> None:
        return None

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        text = " ".join(statement.split())

        if text.startswith(("INSERT", "UPDATE")):
            self.connection.writes.append((text, parameters))
            if self.connection.raises is not None:
                raise self.connection.raises
            self.row = (uuid4(),)
        elif "count(*)" in text:
            self.row = (self.connection.administrators,)
        elif "FROM role" in text:
            self.row = (_ROLE_IDS.get(parameters[0]),)
            if self.row[0] is None:
                self.row = None
        elif "WHERE u.user_id" in text:
            user = self.connection.users.get(UUID(str(parameters[0])))
            self.row = None if user is None else _as_row(user)
        else:  # pragma: no cover - the service asks nothing else
            raise AssertionError(f"unexpected statement: {text}")

    def fetchone(self) -> tuple | None:
        return self.row


def _as_row(user: AppUser) -> tuple:
    """The column order `get_user_by_id` selects."""
    return (
        user.user_id,
        user.role_id,
        user.role_code,
        user.role_description,
        user.name,
        user.email,
        user.password_hash,
        user.is_active,
    )


class _IndexViolation(UniqueViolation):
    """A `UniqueViolation` naming a constraint.

    psycopg builds `diag` from the server's error fields and exposes
    `constraint_name` read-only, so a violation cannot be assembled by hand.
    Shadowing `diag` in a subclass is the smallest honest stand-in; that the
    real error carries the same field is checked against PostgreSQL in
    docs/evidence/f4-02-single-administrator.md, not here.
    """

    def __init__(self, constraint_name: str) -> None:
        super().__init__("duplicate key value violates unique constraint")
        self._constraint_name = constraint_name

    @property
    def diag(self) -> SimpleNamespace:  # type: ignore[override]
        return SimpleNamespace(constraint_name=self._constraint_name)


# ---------- never two ----------


def test_creating_a_second_administrator_is_refused() -> None:
    connection = _Connection(administrators=1)

    with pytest.raises(SingleAdministratorError) as refusal:
        create_user(
            connection,
            name="Second admin",
            email="second@mosaiq-demo.com",
            password="Password123!",
            role_code="ADMIN",
        )

    assert str(refusal.value) == SECOND_ADMINISTRATOR
    assert connection.writes == [], "the row was written before being refused"


def test_promoting_a_second_administrator_is_refused() -> None:
    connection = _Connection(administrators=1)

    with pytest.raises(SingleAdministratorError) as refusal:
        change_role(connection, _ANALYST_ID, "ADMIN")

    assert str(refusal.value) == SECOND_ADMINISTRATOR
    assert connection.writes == []


def test_the_refusal_explains_the_rule_rather_than_the_constraint() -> None:
    """The index alone would say `ux_app_user_single_administrator`."""
    assert "exactly one administrator" in SECOND_ADMINISTRATOR
    assert "may not be left without" in LAST_ADMINISTRATOR
    for message in (SECOND_ADMINISTRATOR, LAST_ADMINISTRATOR):
        assert "ux_" not in message
        assert "role_id" not in message


def test_a_unique_violation_underneath_reads_the_same_as_the_check() -> None:
    """Two requests can both pass the count; the loser still reads a sentence."""
    connection = _Connection(
        administrators=0,
        raises=_IndexViolation("ux_app_user_single_administrator"),
    )

    with pytest.raises(SingleAdministratorError) as refusal:
        create_user(
            connection,
            name="Racing admin",
            email="racer@mosaiq-demo.com",
            password="Password123!",
            role_code="ADMIN",
        )

    assert str(refusal.value) == SECOND_ADMINISTRATOR


def test_another_unique_violation_is_not_disguised_as_the_admin_rule() -> None:
    """A duplicate email is a different problem and must keep its own error."""
    connection = _Connection(
        administrators=0, raises=_IndexViolation("app_user_email_key")
    )

    with pytest.raises(DuplicateEmailError):
        create_user(
            connection,
            name="Taken",
            email="admin@mosaiq-demo.com",
            password="Password123!",
            role_code="ANALYST",
        )


# ---------- never zero ----------


def test_demoting_the_only_administrator_is_refused() -> None:
    connection = _Connection(administrators=1)

    with pytest.raises(SingleAdministratorError) as refusal:
        change_role(connection, _ADMIN_ID, "ANALYST")

    assert str(refusal.value) == LAST_ADMINISTRATOR
    assert connection.writes == []


def test_deactivating_the_only_administrator_is_refused() -> None:
    connection = _Connection(administrators=1)

    with pytest.raises(SingleAdministratorError) as refusal:
        set_active(connection, _ADMIN_ID, False)

    assert str(refusal.value) == LAST_ADMINISTRATOR
    assert connection.writes == []


def test_deactivating_anybody_else_is_ordinary_work() -> None:
    connection = _Connection(administrators=1)

    set_active(connection, _ANALYST_ID, False)

    assert len(connection.writes) == 1
    assert connection.writes[0][1] == (False, str(_ANALYST_ID))


def test_reactivating_the_administrator_is_not_refused() -> None:
    connection = _Connection(administrators=1)

    set_active(connection, _ADMIN_ID, True)

    assert len(connection.writes) == 1


# ---------- the seat can still change hands ----------


def test_promoting_is_allowed_when_the_seat_is_empty() -> None:
    """F4-06 (#107) depends on this: a system with no administrator gets one."""
    connection = _Connection(administrators=0)

    change_role(connection, _ANALYST_ID, "ADMIN")

    assert connection.writes[0][1] == (_ROLE_IDS["ADMIN"], str(_ANALYST_ID))


def test_the_administrator_role_transfers_in_one_operation() -> None:
    """Neither half of the swap is legal alone; together they are."""
    connection = _Connection(administrators=1)

    transfer_administrator(connection, from_user_id=_ADMIN_ID, to_user_id=_ANALYST_ID)

    demote, promote = connection.writes
    # Demote first: the index may never be asked to hold two at once.
    assert demote[1] == (_ROLE_IDS["ANALYST"], str(_ADMIN_ID))
    assert promote[1] == (_ROLE_IDS["ADMIN"], str(_ANALYST_ID))


def test_transferring_from_somebody_who_is_not_the_administrator_is_refused() -> None:
    connection = _Connection(administrators=1)

    with pytest.raises(SingleAdministratorError, match="not the administrator"):
        transfer_administrator(
            connection, from_user_id=_ANALYST_ID, to_user_id=_ADMIN_ID
        )

    assert connection.writes == []


def test_transferring_to_the_incumbent_changes_nothing() -> None:
    connection = _Connection(administrators=1)

    transfer_administrator(connection, from_user_id=_ADMIN_ID, to_user_id=_ADMIN_ID)

    assert connection.writes == []


def test_a_role_change_to_the_role_already_held_writes_nothing() -> None:
    connection = _Connection(administrators=1)

    change_role(connection, _ADMIN_ID, "ADMIN")

    assert connection.writes == []


# ---------- ordinary work is unaffected ----------


def test_creating_any_other_role_is_ordinary_work() -> None:
    connection = _Connection(administrators=1)

    create_user(
        connection,
        name="An analyst",
        email="analyst@mosaiq-demo.com",
        password="Password123!",
        role_code="ANALYST",
    )

    statement, parameters = connection.writes[0]
    assert statement.startswith("INSERT INTO app_user")
    assert parameters[0] == _ROLE_IDS["ANALYST"]


def test_the_password_reaches_the_database_only_as_an_argon2id_hash() -> None:
    """RN-03: the database never sees a plaintext password."""
    connection = _Connection(administrators=1)

    create_user(
        connection,
        name="An analyst",
        email="analyst@mosaiq-demo.com",
        password="Password123!",
        role_code="ANALYST",
    )

    _statement, parameters = connection.writes[0]
    assert "Password123!" not in parameters
    assert parameters[3].startswith("$argon2id$")


def test_an_unknown_role_and_an_unknown_user_are_named_as_such() -> None:
    connection = _Connection(administrators=1)

    with pytest.raises(UnknownRoleError):
        create_user(
            connection,
            name="x",
            email="x@mosaiq-demo.com",
            password="Password123!",
            role_code="SUPERUSER",
        )
    with pytest.raises(UnknownUserError):
        set_active(connection, uuid4(), False)


# ---------- the schema half is still there ----------

_SCHEMA = Path("sql/01_schema.sql").read_text(encoding="utf-8")
_SEED = Path("sql/02_seed_30_per_table.sql").read_text(encoding="utf-8")


def test_the_schema_still_carries_the_partial_unique_index() -> None:
    """AGENTS.md: the application check alone does not count."""
    index = re.search(
        r"CREATE UNIQUE INDEX\s+ux_app_user_single_administrator\s+"
        r"ON app_user \(role_id\)\s+WHERE role_id = (\d+);",
        _SCHEMA,
    )

    assert index is not None, (
        "The partial unique index is gone from sql/01_schema.sql. Without it "
        "a direct INSERT creates a second administrator."
    )


def test_the_index_and_the_seed_agree_on_which_role_id_is_the_administrator() -> None:
    """The predicate cannot look ADMIN up by code, so the two must be checked."""
    index = re.search(
        r"ux_app_user_single_administrator\s+ON app_user \(role_id\)\s+"
        r"WHERE role_id = (\d+);",
        _SCHEMA,
    )
    seeded = re.search(r"\((\d+),'ADMIN'", _SEED)

    assert index is not None and seeded is not None
    assert index.group(1) == seeded.group(1), (
        "sql/01_schema.sql indexes a different role id than the one "
        "sql/02_seed_30_per_table.sql gives ADMIN, so the rule is unenforced."
    )


def test_the_administrator_role_code_matches_the_seed() -> None:
    assert f"({{}},'{ADMINISTRATOR_ROLE_CODE}'".format(1) in _SEED
