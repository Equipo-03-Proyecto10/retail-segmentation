"""Atomic service operations and the layer boundaries behind them."""

import ast
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    NotNullViolation,
    RestrictViolation,
)

from web.db.transactions import atomic
from web.services import catalog, users


def test_nested_services_commit_once_after_all_work():
    connection = MagicMock()
    steps = []

    @atomic
    def child(connection):
        steps.append("child")
        connection.commit.assert_not_called()

    @atomic
    def parent(connection):
        child(connection)
        child(connection)
        steps.append("parent")
        connection.commit.assert_not_called()

    parent(connection)
    assert steps == ["child", "child", "parent"]
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


@pytest.mark.parametrize("fail_at_commit", [False, True])
def test_failed_unit_rolls_back_and_releases_transaction_owner(fail_at_commit):
    connection = MagicMock()

    @atomic
    def child(connection):
        if not fail_at_commit:
            raise RuntimeError("partial work")

    @atomic
    def parent(connection):
        child(connection)

    if fail_at_commit:
        connection.commit.side_effect = RuntimeError("commit failed")
    with pytest.raises(RuntimeError):
        parent(connection)
    connection.rollback.assert_called_once()
    connection.commit.reset_mock(side_effect=True)

    @atomic
    def next_operation(connection):
        return "done"

    assert next_operation(connection) == "done"
    connection.commit.assert_called_once()


def test_failed_demo_deactivation_rolls_back_administrator_installation(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr(users, "install_administrator", MagicMock())
    monkeypatch.setattr(
        users,
        "deactivate_demonstration_accounts",
        MagicMock(side_effect=RuntimeError("failure")),
    )
    with pytest.raises(RuntimeError):
        users.provision_administrator(
            connection,
            name="Admin",
            email="admin@example.com",
            password="private password",
            deactivate_demo_accounts=True,
        )
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()


@pytest.mark.parametrize("entity", ["store", "category", "channel", "product", "role"])
@pytest.mark.parametrize("violation", [ForeignKeyViolation, RestrictViolation])
def test_delete_constraints_use_one_typed_failure(entity, violation):
    """A protected row refuses the same way whichever code the server sends.

    `ON DELETE RESTRICT` in sql/01_schema.sql arrives as RestrictViolation
    (23001), not ForeignKeyViolation (23503); the two are siblings, so a
    translation that named only the second let the refusal escape as a 500.
    """
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.execute.side_effect = (
        violation()
    )
    with pytest.raises(catalog.CatalogConflict, match="still reference"):
        getattr(catalog, f"delete_{entity}")(connection, 1)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("violation", [CheckViolation, NotNullViolation])
def test_any_other_constraint_still_reaches_the_form(violation):
    """A CHECK or NOT NULL refusal is a refused value, not a server fault."""
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.execute.side_effect = (
        violation()
    )
    with pytest.raises(catalog.CatalogConflict, match="refused by a database"):
        catalog.create_product(
            connection,
            product_id=1,
            sku="SKU",
            name="Product",
            category_id=1,
            list_price=Decimal("10.00"),
        )
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("password,valid", [("x" * 11, False), ("x" * 12, True)])
def test_user_password_policy_is_independent_of_http(password, valid):
    errors = users.validate_user(
        name="User", email="user@example.com", password=password, role_code="ANALYST"
    )
    assert ("password" not in errors) == valid


def test_layer_boundaries_keep_sql_and_transactions_out_of_entry_points():
    for path in Path("web").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr in {"cursor", "execute", "executemany"}:
                assert path.is_relative_to("web/db"), path
            if node.func.attr in {"commit", "rollback"}:
                assert path == Path("web/db/transactions.py"), path
            if node.func.attr in {"execute", "executemany"} and node.args:
                assert not isinstance(node.args[0], ast.JoinedStr | ast.BinOp), path


@pytest.mark.parametrize(
    "email", ["", "@example.com", "user@", "a@b@c", "a b@example.com", "!" * 100_000]
)
def test_invalid_email_is_refused_without_a_backtracking_pattern(email):
    errors = users.validate_user(
        name="User", email=email, password="TestPassword!123", role_code="ANALYST"
    )
    assert "email" in errors
