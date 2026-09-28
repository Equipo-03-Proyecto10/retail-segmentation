"""Comparing what two segmentation methods produce (F9-04, ADR-0018).

The comparison is made on label codes and nothing else. Every rule is a pure
function of two runs' (customer, name, label) rows and the label vocabulary, so a
test states exactly what each run assigned. Nothing in web/services/model_comparison.py
knows or is told which method produced a run: the method is run metadata that the
page describes, and is never what an assignment is read through.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web.db import model_comparison as comparison_db
from web.services import model_comparison
from web.services.model_comparison import (
    UNASSIGNED,
    Agreement,
    compare_runs,
    filter_customers,
    flatten_parameters,
)

_ORDINALS = {"CHAMPION": 1, "LOYAL": 2, "LOST": 3}
_ADA, _BOB, _CAL, _DAN = "a1", "b2", "c3", "d4"


def _rows(*triples):
    return [(customer, name, label) for customer, name, label in triples]


def _compare(first, second):
    return compare_runs(first, second, _ORDINALS, first_run_id=10, second_run_id=20)


def _population(result, label):
    return next(p for p in result.populations if p.label == label)


# ---------- per-label population ----------


def test_each_run_reports_how_many_customers_it_put_under_each_label() -> None:
    first = _rows(
        (_ADA, "Ada", "CHAMPION"), (_BOB, "Bob", "CHAMPION"), (_CAL, "Cal", "LOST")
    )
    second = _rows(
        (_ADA, "Ada", "LOYAL"), (_BOB, "Bob", "LOYAL"), (_CAL, "Cal", "LOYAL")
    )

    result = _compare(first, second)

    assert (
        _population(result, "CHAMPION").first,
        _population(result, "CHAMPION").second,
    ) == (2, 0)
    assert (
        _population(result, "LOYAL").first,
        _population(result, "LOYAL").second,
    ) == (0, 3)
    assert (_population(result, "LOST").first, _population(result, "LOST").second) == (
        1,
        0,
    )


def test_labels_are_listed_best_to_worst_then_unassigned() -> None:
    result = _compare(_rows((_ADA, "Ada", "LOST")), _rows((_ADA, "Ada", "LOST")))

    assert [p.label for p in result.populations] == ["CHAMPION", "LOYAL", "LOST", None]


def test_a_label_no_customer_holds_is_still_listed_with_zero() -> None:
    result = _compare(_rows((_ADA, "Ada", "LOST")), _rows((_ADA, "Ada", "LOST")))

    assert _population(result, "CHAMPION").first == 0


def test_a_customer_left_unassigned_is_counted_as_unassigned_in_that_run() -> None:
    result = _compare(_rows((_ADA, "Ada", None)), _rows((_ADA, "Ada", "LOYAL")))

    assert _population(result, None).first == 1
    assert _population(result, None).second == 0


def test_the_populations_sum_to_each_runs_own_customer_count() -> None:
    first = _rows((_ADA, "Ada", "CHAMPION"), (_BOB, "Bob", None), (_CAL, "Cal", "LOST"))
    second = _rows((_ADA, "Ada", "LOYAL"), (_BOB, "Bob", "LOYAL"))

    result = _compare(first, second)

    assert sum(p.first for p in result.populations) == result.first_total == 3
    assert sum(p.second for p in result.populations) == result.second_total == 2


def test_each_populations_share_is_its_fraction_of_that_runs_customers() -> None:
    first = _rows(
        (_ADA, "Ada", "CHAMPION"),
        (_BOB, "Bob", "LOST"),
        (_CAL, "Cal", "LOST"),
        (_DAN, "Dan", "LOST"),
    )

    result = _compare(first, _rows((_ADA, "Ada", "LOYAL")))

    assert _population(result, "LOST").share_first == pytest.approx(0.75)
    assert _population(result, "CHAMPION").share_first == pytest.approx(0.25)


def test_a_run_with_no_customers_has_no_shares_rather_than_a_division_by_zero() -> None:
    result = _compare([], _rows((_ADA, "Ada", "LOYAL")))

    assert _population(result, "LOYAL").share_first is None


# ---------- where the two agree and disagree, per customer ----------


def test_a_customer_given_the_same_label_by_both_agrees() -> None:
    result = _compare(_rows((_ADA, "Ada", "LOYAL")), _rows((_ADA, "Ada", "LOYAL")))

    (customer,) = result.customers
    assert customer.status is Agreement.AGREE
    assert (customer.first, customer.second) == ("LOYAL", "LOYAL")


def test_a_customer_given_different_labels_disagrees_and_carries_both() -> None:
    result = _compare(_rows((_ADA, "Ada", "CHAMPION")), _rows((_ADA, "Ada", "LOST")))

    (customer,) = result.customers
    assert customer.status is Agreement.DISAGREE
    assert (customer.first, customer.second) == ("CHAMPION", "LOST")


def test_a_customer_both_runs_left_unassigned_is_an_agreement() -> None:
    """Both said there was nothing to label. That is a result, not an absence."""
    result = _compare(_rows((_ADA, "Ada", None)), _rows((_ADA, "Ada", None)))

    assert result.customers[0].status is Agreement.AGREE
    assert result.agree == 1


def test_a_customer_unassigned_by_only_one_run_is_a_disagreement() -> None:
    result = _compare(_rows((_ADA, "Ada", None)), _rows((_ADA, "Ada", "LOYAL")))

    assert result.customers[0].status is Agreement.DISAGREE


def test_a_customer_only_one_run_scored_is_not_a_disagreement() -> None:
    result = _compare(
        _rows((_ADA, "Ada", "LOYAL")),
        _rows((_ADA, "Ada", "LOYAL"), (_BOB, "Bob", "LOST")),
    )

    bob = next(c for c in result.customers if c.customer_id == _BOB)
    assert bob.status is Agreement.ONLY_IN_SECOND
    assert bob.first is None and bob.second == "LOST"
    assert result.only_second == 1 and result.disagree == 0


def test_the_first_run_only_case_is_reported_too() -> None:
    result = _compare(_rows((_ADA, "Ada", "LOYAL")), [])

    assert result.customers[0].status is Agreement.ONLY_IN_FIRST
    assert result.only_first == 1


def test_the_counts_reconcile() -> None:
    """Every customer either run scored is agreed, disagreed or in only one."""
    first = _rows((_ADA, "Ada", "LOYAL"), (_BOB, "Bob", "LOST"), (_CAL, "Cal", None))
    second = _rows(
        (_ADA, "Ada", "LOYAL"), (_BOB, "Bob", "CHAMPION"), (_DAN, "Dan", "LOST")
    )

    result = _compare(first, second)

    assert result.compared == result.agree + result.disagree == 2
    assert (
        result.compared + result.only_first + result.only_second
        == len(result.customers)
        == 4
    )


def test_the_agreement_rate_is_the_fraction_of_compared_customers_who_agree() -> None:
    first = _rows(
        (_ADA, "Ada", "LOYAL"),
        (_BOB, "Bob", "LOST"),
        (_CAL, "Cal", "LOST"),
        (_DAN, "Dan", "LOST"),
    )
    second = _rows(
        (_ADA, "Ada", "LOYAL"),
        (_BOB, "Bob", "LOST"),
        (_CAL, "Cal", "LOYAL"),
        (_DAN, "Dan", "LOYAL"),
    )

    assert _compare(first, second).agreement_rate == pytest.approx(0.5)


def test_with_nobody_compared_there_is_no_agreement_rate() -> None:
    assert (
        _compare(
            _rows((_ADA, "Ada", "LOYAL")), _rows((_BOB, "Bob", "LOYAL"))
        ).agreement_rate
        is None
    )


def test_customers_are_listed_by_name_then_id() -> None:
    first = _rows(
        ("z9", "Bea", "LOYAL"), ("a1", "Ann", "LOYAL"), ("b2", "Bea", "LOYAL")
    )

    result = _compare(first, first)

    assert [c.customer_id for c in result.customers] == ["a1", "b2", "z9"]


def test_a_customers_name_comes_from_whichever_run_has_them() -> None:
    result = _compare([], _rows((_BOB, "Bob", "LOST")))

    assert result.customers[0].name == "Bob"


# ---------- the cross-tabulation ----------


def test_the_cross_tabulation_counts_customers_by_the_label_each_run_gave() -> None:
    first = _rows(
        (_ADA, "Ada", "CHAMPION"), (_BOB, "Bob", "CHAMPION"), (_CAL, "Cal", "LOST")
    )
    second = _rows(
        (_ADA, "Ada", "CHAMPION"), (_BOB, "Bob", "LOYAL"), (_CAL, "Cal", "LOST")
    )

    cells = _compare(first, second).cells

    assert cells["CHAMPION"]["CHAMPION"] == 1
    assert cells["CHAMPION"]["LOYAL"] == 1
    assert cells["LOST"]["LOST"] == 1
    assert cells["LOYAL"]["LOYAL"] == 0


def test_the_cells_sum_to_the_compared_and_the_diagonal_to_the_agreements() -> None:
    first = _rows(
        (_ADA, "Ada", "CHAMPION"),
        (_BOB, "Bob", "LOST"),
        (_CAL, "Cal", None),
        (_DAN, "Dan", "LOYAL"),
    )
    second = _rows(
        (_ADA, "Ada", "CHAMPION"), (_BOB, "Bob", "LOYAL"), (_CAL, "Cal", None)
    )

    result = _compare(first, second)

    total = sum(sum(row.values()) for row in result.cells.values())
    diagonal = sum(result.cells[label][label] for label in result.cells)
    assert total == result.compared == 3
    assert diagonal == result.agree == 2


def test_unassigned_has_its_own_row_and_column() -> None:
    result = _compare(_rows((_ADA, "Ada", None)), _rows((_ADA, "Ada", None)))

    assert UNASSIGNED in result.cells and result.cells[UNASSIGNED][UNASSIGNED] == 1
    assert result.column_labels[-1] == UNASSIGNED


# ---------- what it refuses ----------


def test_a_label_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(ValueError, match="NOT_A_LABEL"):
        _compare(_rows((_ADA, "Ada", "NOT_A_LABEL")), [])


def test_the_same_customer_twice_in_one_run_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        _compare(_rows((_ADA, "Ada", "LOYAL"), (_ADA, "Ada", "LOST")), [])


# ---------- filtering ----------


def test_the_customer_list_can_be_narrowed_to_agreements_or_disagreements() -> None:
    first = _rows((_ADA, "Ada", "LOYAL"), (_BOB, "Bob", "LOST"))
    second = _rows((_ADA, "Ada", "LOYAL"), (_BOB, "Bob", "CHAMPION"))
    result = _compare(first, second)

    assert [c.customer_id for c in filter_customers(result, "agree")] == [_ADA]
    assert [c.customer_id for c in filter_customers(result, "disagree")] == [_BOB]
    assert len(filter_customers(result, "all")) == 2


def test_an_unknown_filter_is_refused() -> None:
    with pytest.raises(ValueError):
        filter_customers(_compare([], []), "everything")


# ---------- describing a run's parameters without knowing what made them ----------


def test_parameters_are_listed_as_they_were_recorded_whatever_they_hold() -> None:
    flat = dict(
        flatten_parameters(
            {"window_days": 180, "quality": {"inertia": 0.5, "cluster_sizes": [7, 3]}}
        )
    )

    assert flat["window_days"] == "180"
    assert flat["quality.inertia"] == "0.5"
    assert flat["quality.cluster_sizes"] == "7, 3"


def test_missing_parameter_values_are_shown_as_none_recorded() -> None:
    assert dict(flatten_parameters({"silhouette": None}))["silhouette"] == "none"


# ---------- method independence (ADR-0018, F9-04's third criterion) ----------


def test_building_a_comparison_is_never_told_and_never_reads_a_method() -> None:
    assert "method" not in " ".join(inspect.signature(compare_runs).parameters)
    source = Path(model_comparison.__file__).read_text(encoding="utf-8").lower()
    assert (
        "method" not in source and "kmeans" not in source and "rfm_rules" not in source
    )


def test_the_same_labelled_rows_give_the_same_comparison_whoever_produced_them() -> (
    None
):
    """There is nothing else to give it: two runs are their labelled rows."""
    rows = _rows((_ADA, "Ada", "LOYAL"), (_BOB, "Bob", None))

    assert _compare(rows, list(rows)) == _compare(list(rows), list(rows))


def test_the_assignment_reader_does_not_select_the_method() -> None:
    source = inspect.getsource(comparison_db.list_run_label_rows).lower()

    assert "method" not in source and "cluster" not in source


# ---------- the reads ----------


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def test_a_runs_label_rows_are_read_by_run_with_the_customers_name() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (_ADA, "Ada", "LOYAL"),
        (_BOB, "Bob", None),
    ]

    rows = comparison_db.list_run_label_rows(connection, 42)

    statement, parameters = _cursor(connection).execute.call_args.args
    assert parameters == (42,)
    assert "42" not in statement and "run_id = %s" in statement
    assert rows == [(_ADA, "Ada", "LOYAL"), (_BOB, "Bob", None)]


def test_a_customer_id_is_returned_as_text() -> None:
    from uuid import UUID

    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (UUID("00000000-0000-0000-0000-000000000001"), "Ada", None)
    ]

    assert (
        comparison_db.list_run_label_rows(connection, 1)[0][0]
        == "00000000-0000-0000-0000-000000000001"
    )


def test_runs_are_listed_newest_first_for_one_method_as_a_parameter() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    comparison_db.list_runs_of_method(connection, "KMEANS", limit=25)

    statement, parameters = _cursor(connection).execute.call_args.args
    assert parameters == ("KMEANS", 25)
    assert "KMEANS" not in statement
    assert re.search(r"ORDER BY r\.run_at DESC, r\.run_id DESC", statement)


def test_the_reads_never_write() -> None:
    source = Path(comparison_db.__file__).read_text(encoding="utf-8")
    for pattern in (
        r"\bINSERT\s+INTO\b",
        r"\bDELETE\s+FROM\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\.commit\(",
    ):
        assert re.search(pattern, source, re.IGNORECASE) is None, pattern
