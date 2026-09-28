"""RN-33 (#253): the category hierarchy is a tree.

The service refuses a move under the category itself or one of its own
subcategories before writing, and translates the database's own refusal
(category_not_own_parent, category_no_cycle) the same way, so neither path
reaches the visitor as a 500.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from psycopg.errors import CheckViolation

from web.db import categories
from web.services import catalog
from web.services.catalog import CATEGORY_CYCLE, CatalogConflict


def test_a_move_into_the_categorys_own_subtree_is_refused_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(categories, "is_in_subtree", lambda *_a: True)
    write = MagicMock()
    monkeypatch.setattr(categories, "update_category", write)

    with pytest.raises(CatalogConflict) as refusal:
        catalog.update_category(MagicMock(), 1, name="Dairy", parent_category_id=11)

    assert refusal.value.field == "parent_category_id"
    assert str(refusal.value) == CATEGORY_CYCLE
    write.assert_not_called()


def test_a_move_under_an_unrelated_category_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(categories, "is_in_subtree", lambda *_a: False)
    write = MagicMock()
    monkeypatch.setattr(categories, "update_category", write)

    catalog.update_category(MagicMock(), 11, name="Milk", parent_category_id=2)

    write.assert_called_once()


def test_moving_to_the_top_level_skips_the_subtree_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = MagicMock()
    monkeypatch.setattr(categories, "is_in_subtree", check)
    monkeypatch.setattr(categories, "update_category", MagicMock())

    catalog.update_category(MagicMock(), 11, name="Milk", parent_category_id=None)

    check.assert_not_called()


@pytest.mark.parametrize("constraint", ["category_not_own_parent", "category_no_cycle"])
def test_the_databases_refusal_lands_on_the_parent_field(
    monkeypatch: pytest.MonkeyPatch, constraint: str
) -> None:
    """A concurrent move can pass the service's check and still close a cycle;
    the trigger refuses it, and the visitor sees the same message."""
    violation = CheckViolation()
    monkeypatch.setattr(
        type(violation),
        "diag",
        property(lambda _self: MagicMock(constraint_name=constraint)),
    )
    monkeypatch.setattr(categories, "is_in_subtree", lambda *_a: False)
    monkeypatch.setattr(categories, "update_category", MagicMock(side_effect=violation))

    with pytest.raises(CatalogConflict) as refusal:
        catalog.update_category(MagicMock(), 1, name="Dairy", parent_category_id=11)

    assert refusal.value.field == "parent_category_id"
    assert str(refusal.value) == CATEGORY_CYCLE


def test_the_subtree_query_walks_down_from_the_moved_category() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (True,)

    assert categories.is_in_subtree(connection, 1, 11) is True
    sql, params = cursor.execute.call_args.args
    assert "WITH RECURSIVE" in sql
    assert params == (1, 11)
