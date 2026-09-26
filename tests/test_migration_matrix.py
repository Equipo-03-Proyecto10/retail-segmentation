"""The migration matrix (F7-05): a cross-tabulation of every customer's
earlier label (row) against their later label (column).

build_migration_matrix is pure (web/services/segment_migration.py,
ADR-0003), so these tests drive it directly with plain CustomerMigration
lists -- no database.
"""

from __future__ import annotations

from web.services.segment_migration import (
    CustomerMigration,
    MigrationCategory,
    build_migration_matrix,
)

_ORDINALS = {
    "CHAMPION": 1,
    "LOYAL": 2,
    "AT_RISK": 3,
}


def _m(label_before, label_after, category) -> CustomerMigration:
    return CustomerMigration("c", label_before, label_after, category)


# ---------- AC 1: rows earlier, columns later, cells count ----------


def test_a_single_unchanged_customer_lands_on_the_diagonal() -> None:
    migrations = [_m("CHAMPION", "CHAMPION", MigrationCategory.UNCHANGED)]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert matrix.cells["CHAMPION"]["CHAMPION"] == 1
    assert matrix.cells["CHAMPION"]["LOYAL"] == 0


def test_a_moved_customer_lands_at_before_row_after_column() -> None:
    migrations = [_m("CHAMPION", "AT_RISK", MigrationCategory.MOVED)]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert matrix.cells["CHAMPION"]["AT_RISK"] == 1


def test_row_and_column_labels_follow_ordinal_position() -> None:
    """Best-to-worst (ADR-0018), Unassigned last."""
    matrix = build_migration_matrix([], _ORDINALS)

    assert matrix.row_labels == ["CHAMPION", "LOYAL", "AT_RISK", "Unassigned"]
    assert matrix.column_labels == ["CHAMPION", "LOYAL", "AT_RISK", "Unassigned"]


def test_multiple_customers_in_the_same_cell_are_counted_together() -> None:
    migrations = [
        _m("CHAMPION", "LOYAL", MigrationCategory.MOVED),
        _m("CHAMPION", "LOYAL", MigrationCategory.MOVED),
        _m("CHAMPION", "LOYAL", MigrationCategory.MOVED),
    ]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert matrix.cells["CHAMPION"]["LOYAL"] == 3


# ---------- AC 2: row and column totals reconcile with assignment counts ----------


def test_row_totals_equal_the_sum_of_that_rows_cells() -> None:
    migrations = [
        _m("CHAMPION", "CHAMPION", MigrationCategory.UNCHANGED),
        _m("CHAMPION", "LOYAL", MigrationCategory.MOVED),
        _m("CHAMPION", "AT_RISK", MigrationCategory.MOVED),
    ]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert matrix.row_totals["CHAMPION"] == 3
    assert matrix.row_totals["CHAMPION"] == sum(matrix.cells["CHAMPION"].values())


def test_column_totals_equal_the_sum_of_that_columns_cells() -> None:
    migrations = [
        _m("CHAMPION", "LOYAL", MigrationCategory.MOVED),
        _m("AT_RISK", "LOYAL", MigrationCategory.MOVED),
    ]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert matrix.column_totals["LOYAL"] == 2


def test_grand_total_equals_the_number_of_migrations_placed() -> None:
    """Every row total summed equals every column total summed, and both
    equal the count of migrations that actually had a before/after label
    pair -- the reconciliation AC 2 asks for."""
    migrations = [
        _m("CHAMPION", "CHAMPION", MigrationCategory.UNCHANGED),
        _m("CHAMPION", "LOYAL", MigrationCategory.MOVED),
        _m(None, "LOYAL", MigrationCategory.NEWLY_ASSIGNED),
        _m("AT_RISK", None, MigrationCategory.NEWLY_UNASSIGNED),
    ]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert sum(matrix.row_totals.values()) == sum(matrix.column_totals.values()) == 4


# ---------- AC 3: unassigned has its own row and column ----------


def test_newly_assigned_lands_in_the_unassigned_row() -> None:
    migrations = [_m(None, "CHAMPION", MigrationCategory.NEWLY_ASSIGNED)]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert matrix.cells["Unassigned"]["CHAMPION"] == 1


def test_newly_unassigned_lands_in_the_unassigned_column() -> None:
    migrations = [_m("CHAMPION", None, MigrationCategory.NEWLY_UNASSIGNED)]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert matrix.cells["CHAMPION"]["Unassigned"] == 1


def test_staying_unassigned_lands_on_the_unassigned_diagonal() -> None:
    migrations = [_m(None, None, MigrationCategory.UNCHANGED)]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert matrix.cells["Unassigned"]["Unassigned"] == 1


# ---------- customers absent from one run don't distort the matrix ----------


def test_a_customer_absent_from_one_run_is_excluded_from_the_grid() -> None:
    """absent_from_earlier/absent_from_later have no before/after pair to
    place -- they're covered by F7-04's own categories, not this matrix."""
    migrations = [
        _m(None, "CHAMPION", MigrationCategory.ABSENT_FROM_EARLIER),
        _m("LOYAL", None, MigrationCategory.ABSENT_FROM_LATER),
        _m("CHAMPION", "CHAMPION", MigrationCategory.UNCHANGED),
    ]
    matrix = build_migration_matrix(migrations, _ORDINALS)

    assert sum(matrix.row_totals.values()) == 1
    assert matrix.cells["CHAMPION"]["CHAMPION"] == 1


def test_an_empty_migration_list_gives_an_all_zero_matrix() -> None:
    matrix = build_migration_matrix([], _ORDINALS)

    assert all(count == 0 for row in matrix.cells.values() for count in row.values())
    assert all(total == 0 for total in matrix.row_totals.values())
