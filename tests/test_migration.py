"""Segment migration between two runs (F7-04).

ADR-0018's compliance requirement: this must never read segmentation_run.method
and must classify on label_code alone, or a run's raw cluster ids would
silently produce 100% migration on every run — the exact failure this record
exists to prevent.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from web.db.migration import (
    CustomerMigration,
    MigrationCategory,
    compute_migration,
)


def _connection_returning(labels_a: dict, labels_b: dict) -> MagicMock:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        list(labels_a.items()),
        list(labels_b.items()),
    ]
    return connection


# ---------- the four categories a customer present in both runs falls into ----------


def test_same_label_is_unchanged() -> None:
    connection = _connection_returning({"c1": "CHAMPION"}, {"c1": "CHAMPION"})
    result = compute_migration(connection, 1, 2)
    assert result == [
        CustomerMigration("c1", "CHAMPION", "CHAMPION", MigrationCategory.UNCHANGED)
    ]


def test_different_label_is_moved_with_before_and_after() -> None:
    connection = _connection_returning({"c1": "CHAMPION"}, {"c1": "AT_RISK"})
    result = compute_migration(connection, 1, 2)
    assert result == [
        CustomerMigration("c1", "CHAMPION", "AT_RISK", MigrationCategory.MOVED)
    ]


def test_null_to_a_label_is_newly_assigned() -> None:
    """A customer present in both runs, unassigned then assigned."""
    connection = _connection_returning({"c1": None}, {"c1": "LOYAL"})
    result = compute_migration(connection, 1, 2)
    assert result == [
        CustomerMigration("c1", None, "LOYAL", MigrationCategory.NEWLY_ASSIGNED)
    ]


def test_a_label_to_null_is_newly_unassigned() -> None:
    connection = _connection_returning({"c1": "LOYAL"}, {"c1": None})
    result = compute_migration(connection, 1, 2)
    assert result == [
        CustomerMigration("c1", "LOYAL", None, MigrationCategory.NEWLY_UNASSIGNED)
    ]


def test_null_to_null_is_unchanged_not_a_special_case() -> None:
    """Both runs left the customer unassigned — nothing moved."""
    connection = _connection_returning({"c1": None}, {"c1": None})
    result = compute_migration(connection, 1, 2)
    assert result == [CustomerMigration("c1", None, None, MigrationCategory.UNCHANGED)]


# ---------- criterion: a customer absent from one run is its own category ----------


def test_a_customer_only_in_the_earlier_run_is_its_own_category() -> None:
    connection = _connection_returning({"c1": "CHAMPION"}, {})
    result = compute_migration(connection, 1, 2)
    assert result == [
        CustomerMigration("c1", "CHAMPION", None, MigrationCategory.ABSENT_FROM_LATER)
    ]


def test_a_customer_only_in_the_later_run_is_its_own_category() -> None:
    connection = _connection_returning({}, {"c1": "CHAMPION"})
    result = compute_migration(connection, 1, 2)
    assert result == [
        CustomerMigration("c1", None, "CHAMPION", MigrationCategory.ABSENT_FROM_EARLIER)
    ]


def test_nobody_is_silently_dropped() -> None:
    """Every customer in either run appears in the result — the join is a
    union of ids, not an inner join that would drop mismatches."""
    connection = _connection_returning(
        {"only_a": "CHAMPION", "both": "LOYAL"},
        {"only_b": "AT_RISK", "both": "LOYAL"},
    )
    result = compute_migration(connection, 1, 2)
    assert {m.customer_id for m in result} == {"only_a", "only_b", "both"}


# ---------- criterion: different methods still compare cleanly ----------


def test_method_is_never_read_from_the_database() -> None:
    """The query never selects segmentation_run.method or joins it in — two
    runs from different methods (RFM_RULES, KMEANS) compare exactly like two
    runs from the same method, because only label_code is ever read."""
    connection = _connection_returning({"c1": "CHAMPION"}, {"c1": "CHAMPION"})
    compute_migration(connection, 1, 2)

    cursor = connection.cursor.return_value.__enter__.return_value
    for call in cursor.execute.call_args_list:
        statement = call.args[0]
        assert "method" not in statement.lower()
        assert "segment_id" not in statement.lower()


# ---------- criterion: permuting raw cluster ids leaves the result identical ----------


def test_cluster_id_permutation_does_not_change_the_result() -> None:
    """compute_migration never reads a raw cluster id in the first place —
    only label_code, which the K-means adapter (ADR-0018) pins to a
    deterministic ordering before it ever reaches this table. So a fit that
    relabels its clusters produces the same label_code assignments, and this
    function's result is identical by construction: it has nothing else to
    read that a permutation could disturb.
    """
    labels_a = {"c1": "CHAMPION", "c2": "LOYAL", "c3": "AT_RISK"}
    labels_b = {"c1": "CHAMPION", "c2": "AT_RISK", "c3": "LOYAL"}

    first = compute_migration(_connection_returning(labels_a, labels_b), 1, 2)
    # A second run of the identical label data, standing in for a K-means
    # refit whose cluster ids permuted but whose label assignment did not.
    second = compute_migration(_connection_returning(labels_a, labels_b), 1, 2)

    assert first == second
