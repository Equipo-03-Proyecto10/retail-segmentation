"""Segment migration between two runs (F7-04).

ADR-0018's compliance requirement: this must never compare on
segmentation_run.method or on segment_id — only on label_code — or a run's
raw cluster ids would silently produce 100% migration on every run, the
exact failure this record exists to prevent.

classify_migration is pure (web/services/segment_migration.py, ADR-0003), so
most tests drive it directly with plain dicts. compute_migration's database
wiring (run lookup, ordering, refusing unknown ids, and -- critically --
which columns the SQL actually reads) is exercised separately below, with a
mocked connection.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from web.db.segments import get_run_at, list_run_labels
from web.services.segment_migration import (
    CustomerMigration,
    Direction,
    MigrationCategory,
    UnknownRun,
    classify_migration,
    compute_migration,
)

_ORDINALS = {
    "CHAMPION": 1,
    "LOYAL": 2,
    "POTENTIAL": 3,
    "AT_RISK": 4,
    "HIBERNATING": 5,
    "LOST": 6,
}


# ---------- the four categories a customer present in both runs falls into ----------


def test_same_label_is_unchanged() -> None:
    result = classify_migration({"c1": "CHAMPION"}, {"c1": "CHAMPION"}, _ORDINALS)
    assert result == [
        CustomerMigration("c1", "CHAMPION", "CHAMPION", MigrationCategory.UNCHANGED)
    ]


def test_different_label_is_moved_with_before_and_after() -> None:
    result = classify_migration({"c1": "CHAMPION"}, {"c1": "AT_RISK"}, _ORDINALS)
    assert result == [
        CustomerMigration(
            "c1", "CHAMPION", "AT_RISK", MigrationCategory.MOVED, Direction.DECLINED
        )
    ]


def test_null_to_a_label_is_newly_assigned() -> None:
    """A customer present in both runs, unassigned then assigned."""
    result = classify_migration({"c1": None}, {"c1": "LOYAL"}, _ORDINALS)
    assert result == [
        CustomerMigration("c1", None, "LOYAL", MigrationCategory.NEWLY_ASSIGNED)
    ]


def test_a_label_to_null_is_newly_unassigned() -> None:
    result = classify_migration({"c1": "LOYAL"}, {"c1": None}, _ORDINALS)
    assert result == [
        CustomerMigration("c1", "LOYAL", None, MigrationCategory.NEWLY_UNASSIGNED)
    ]


def test_null_to_null_is_unchanged_not_a_special_case() -> None:
    """Both runs left the customer unassigned — nothing moved."""
    result = classify_migration({"c1": None}, {"c1": None}, _ORDINALS)
    assert result == [CustomerMigration("c1", None, None, MigrationCategory.UNCHANGED)]


# ---------- direction, by ordinal_position (ADR-0018's best-to-worst order) ----------


def test_moving_to_a_lower_ordinal_is_improved() -> None:
    """CHAMPION (1) is better than LOST (6): LOST -> CHAMPION improves."""
    result = classify_migration({"c1": "LOST"}, {"c1": "CHAMPION"}, _ORDINALS)
    assert result[0].category == MigrationCategory.MOVED
    assert result[0].direction == Direction.IMPROVED


def test_moving_to_a_higher_ordinal_is_declined() -> None:
    result = classify_migration({"c1": "CHAMPION"}, {"c1": "LOST"}, _ORDINALS)
    assert result[0].direction == Direction.DECLINED


def test_non_moved_categories_carry_no_direction() -> None:
    unchanged = classify_migration({"c1": "LOYAL"}, {"c1": "LOYAL"}, _ORDINALS)
    assigned = classify_migration({"c1": None}, {"c1": "LOYAL"}, _ORDINALS)
    assert unchanged[0].direction is None
    assert assigned[0].direction is None


def test_a_label_missing_from_the_vocabulary_raises_rather_than_guessing() -> None:
    """A label not in ordinals means something other than a label code
    reached the comparison -- e.g. a raw segment_id. Fail loudly."""
    with pytest.raises(ValueError, match="not in the vocabulary"):
        classify_migration({"c1": "CHAMPION"}, {"c1": "2"}, _ORDINALS)


# ---------- criterion: a customer absent from one run is its own category ----------


def test_a_customer_only_in_the_earlier_run_is_its_own_category() -> None:
    result = classify_migration({"c1": "CHAMPION"}, {}, _ORDINALS)
    assert result == [
        CustomerMigration("c1", "CHAMPION", None, MigrationCategory.ABSENT_FROM_LATER)
    ]


def test_a_customer_only_in_the_later_run_is_its_own_category() -> None:
    result = classify_migration({}, {"c1": "CHAMPION"}, _ORDINALS)
    assert result == [
        CustomerMigration("c1", None, "CHAMPION", MigrationCategory.ABSENT_FROM_EARLIER)
    ]


def test_nobody_is_silently_dropped() -> None:
    """Every customer in either input appears in the result — a union of
    keys, never an inner join that could drop a mismatch."""
    result = classify_migration(
        {"only_a": "CHAMPION", "both": "LOYAL"},
        {"only_b": "AT_RISK", "both": "LOYAL"},
        _ORDINALS,
    )
    assert {m.customer_id for m in result} == {"only_a", "only_b", "both"}


# ---------- criterion: different methods still compare cleanly ----------


def test_signature_never_admits_method_or_segment_id() -> None:
    """classify_migration's own parameters admit no method or segment_id --
    but this only proves the pure function can't branch on them. The SQL
    that fills labels_a/labels_b could still read the wrong column; the
    next test guards that."""
    import inspect

    parameters = inspect.signature(classify_migration).parameters
    assert set(parameters) == {"labels_a", "labels_b", "ordinals"}


def test_run_queries_read_label_code_never_segment_id_or_method() -> None:
    """AC 2/3 can only actually break in the SQL: classify_migration never
    sees a column, so a query that reads segment_id or method instead of
    label_code would still pass every classify_migration test above while
    reporting the exact "plausible, no error" migration ADR-0018 warns
    about. This asserts on the statements the cursor received."""
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None

    list_run_labels(connection, 1)
    get_run_at(connection, 1)

    statements = [call.args[0].lower() for call in cursor.execute.call_args_list]
    assert "label_code" in statements[0]
    for statement in statements:
        assert "segment_id" not in statement
        assert "method" not in statement


# ---------- compute_migration: database wiring ----------


def test_an_unknown_run_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "web.services.segment_migration.get_run_at",
        lambda _c, run_id: None if run_id == 999 else object(),
    )
    with pytest.raises(UnknownRun):
        compute_migration(MagicMock(), 999, 1)


def test_reversed_arguments_give_the_same_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing (later, earlier) must not silently swap newly_assigned and
    newly_unassigned — compute_migration orders the pair by run_at itself."""
    run_ats = {1: datetime(2026, 1, 1), 2: datetime(2026, 2, 1)}
    labels = {1: {"c1": None}, 2: {"c1": "LOYAL"}}

    monkeypatch.setattr(
        "web.services.segment_migration.get_run_at",
        lambda _c, run_id: run_ats[run_id],
    )
    monkeypatch.setattr(
        "web.services.segment_migration.list_run_labels",
        lambda _c, run_id: labels[run_id],
    )
    monkeypatch.setattr(
        "web.services.segment_migration.get_label_ordinals", lambda _c: _ORDINALS
    )

    forward = compute_migration(MagicMock(), 1, 2)
    backward = compute_migration(MagicMock(), 2, 1)

    assert forward == backward
    assert forward[0].category == MigrationCategory.NEWLY_ASSIGNED
