"""The rules' refusals that need no database (RN-01, RN-02)."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from mosaiq_kit import ApiError
from psycopg.errors import UniqueViolation

from app import passwords, rules


class _Diag:
    def __init__(self, constraint: str | None) -> None:
        self.constraint_name = constraint


class _Violation(UniqueViolation):
    """A 23505 naming a constraint, as PostgreSQL raises it."""

    def __init__(self, constraint: str | None) -> None:
        super().__init__("duplicate key value violates unique constraint")
        self._constraint = _Diag(constraint)

    @property
    def diag(self) -> _Diag:  # type: ignore[override]
        return self._constraint


def test_the_single_administrator_index_is_a_409_not_a_500() -> None:
    error = rules.translate(_Violation("ux_app_user_single_administrator"))
    assert (error.status, error.code) == (409, "single_administrator")


def test_the_case_insensitive_email_index_is_email_taken() -> None:
    error = rules.translate(_Violation("ux_app_user_email_lower"))
    assert (error.status, error.code) == (409, "email_taken")
    assert error.details == [{"field": "email", "code": "not_unique"}]


def test_any_other_unique_violation_is_not_disguised() -> None:
    violation = _Violation("some_other_index")
    with pytest.raises(UniqueViolation):
        rules.translate(violation)


def test_creating_an_administrator_is_refused_before_the_database() -> None:
    with pytest.raises(ApiError) as refused:
        rules.create_user(
            None,  # type: ignore[arg-type]
            name="Second",
            email="second@example.com",
            password_hash="unused",
            role_code="ADMIN",
        )
    assert refused.value.code == "single_administrator"


@pytest.mark.parametrize(
    ("given", "stored"),
    [("User14@Mosaiq-Demo.COM", "user14@mosaiq-demo.com"), (" a@b.co ", "a@b.co")],
)
def test_emails_are_stored_as_the_web_stores_them(given: str, stored: str) -> None:
    assert rules.normalize_email(given) == stored


def test_a_service_hash_verifies_with_the_webs_hasher() -> None:
    # web/services/auth.py verifies with PasswordHasher() and its defaults.
    password_hash = passwords.hash_password("correct horse battery")
    assert password_hash.startswith("$argon2id$")
    assert PasswordHasher().verify(password_hash, "correct horse battery")


def test_a_web_hash_verifies_here_and_a_wrong_password_does_not() -> None:
    web_hash = PasswordHasher().hash("correct horse battery")
    assert passwords.verify_password(web_hash, "correct horse battery")
    assert not passwords.verify_password(web_hash, "wrong horse battery")
    assert not passwords.verify_password("not-a-hash", "correct horse battery")
