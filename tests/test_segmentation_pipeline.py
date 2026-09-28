"""The method-agnostic segmentation pipeline (F9-01, ADR-0018).

Each method is an *adapter* that turns the sales into one labelled assignment per
customer. The pipeline writes the run and those assignments and knows nothing
about which method produced them. These tests cover the boundary itself: the
method domain, what an assignment is allowed to be, the order and atomicity of
the writes, and that everything downstream of the boundary is the same whichever
method sat on the other side of it.

The three cases ADR-0018 names in its Compliance section are here by name, so
`pytest -q tests/test_segmentation_pipeline.py -k 'method_domain or
cluster_id_permutation or downstream_method_independence'` selects them.
`cluster_id_permutation` belongs to F9-03, which owns the mapping from clusters
to labels; there is nothing to permute before it exists.
"""

from __future__ import annotations

import inspect
import logging
import re
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from web.db import segments as segments_db
from web.services import segmentation
from web.services.segment_migration import (
    build_migration_matrix,
    classify_migration,
    explain_migration,
)
from web.services.segmentation import (
    METHODS,
    AdapterMismatch,
    Assignment,
    InvalidAssignment,
    MethodAdapter,
    MethodOutput,
    MethodUnavailable,
    UnknownMethod,
    run,
    run_method,
    summarise,
)

_ADA = "00000000-0000-0000-0000-000000000001"
_BOB = "00000000-0000-0000-0000-000000000002"
_CAL = "00000000-0000-0000-0000-000000000003"
_SALE = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _scored(customer_id: str, label: str, segment_id: int = 1) -> Assignment:
    """A customer a method scored: labelled, with raw values and scores."""
    return Assignment(
        customer_id=customer_id,
        label_code=label,
        segment_id=segment_id,
        last_purchase_at=_SALE,
        frequency=4,
        monetary=Decimal("400.00"),
        r_score=5,
        f_score=4,
        m_score=3,
    )


def _unassigned(customer_id: str) -> Assignment:
    """A customer with no sales in the window: no label, no values (RN-21)."""
    return Assignment(customer_id=customer_id, label_code=None)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prior: dict | None = None,
    run_id: int = 7,
) -> Mock:
    """Replace every web.db call the pipeline makes with one recording manager,
    so a test can assert on the order and arguments of the whole sequence."""
    manager = Mock()
    manager.read_open_assignments.return_value = prior or {}
    manager.create_run.return_value = run_id
    for name in (
        "read_open_assignments",
        "create_run",
        "close_open_assignments",
        "insert_assignments",
    ):
        monkeypatch.setattr(segmentation, name, getattr(manager, name))
    return manager


def _adapter(
    *assignments: Assignment,
    parameters: dict | None = None,
    method: str = "RFM_RULES",
) -> MethodAdapter:
    def adapt(_connection, window_days: int) -> MethodOutput:
        return MethodOutput(
            assignments=tuple(assignments),
            parameters=parameters or {"window_days": window_days},
        )

    return MethodAdapter(method, adapt)


# ---------- the method domain ----------


def test_method_domain_is_exactly_rfm_rules_and_kmeans() -> None:
    assert METHODS == ("RFM_RULES", "KMEANS")


@pytest.mark.parametrize("method", ["", "rfm_rules", "kmeans", "HDBSCAN", "RFM_RULES "])
def test_method_domain_refuses_any_other_value_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    manager = _wire(monkeypatch)

    with pytest.raises(UnknownMethod):
        run_method(MagicMock(), method, 180, adapter=_adapter(_scored(_ADA, "LOYAL")))

    manager.create_run.assert_not_called()
    manager.insert_assignments.assert_not_called()


def test_method_domain_is_also_enforced_by_the_database_itself() -> None:
    """The service refusing is not enough: a second writer, a script or a later
    story could skip it. The column's CHECK is the guard that cannot be skipped,
    and it lists exactly the domain the service does."""
    schema = Path("sql/01_schema.sql").read_text(encoding="utf-8")

    match = re.search(
        r"CREATE TABLE segmentation_run \(.*?method\s+VARCHAR\(20\) NOT NULL CHECK "
        r"\(method IN \(([^)]*)\)\)",
        schema,
        re.DOTALL,
    )

    assert match is not None
    listed = tuple(part.strip().strip("'") for part in match.group(1).split(","))
    assert listed == METHODS


def test_kmeans_without_an_adapter_is_unavailable_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K-means labels come from F9-03's mapping. Until an adapter that carries
    it is supplied, the method is in the domain but cannot run."""
    manager = _wire(monkeypatch)

    with pytest.raises(MethodUnavailable):
        run_method(MagicMock(), "KMEANS", 180)

    manager.create_run.assert_not_called()


def test_an_adapter_for_another_method_is_refused_before_it_reads_or_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)
    decide = Mock(return_value=MethodOutput((_scored(_ADA, "LOYAL"),)))

    with pytest.raises(AdapterMismatch, match="KMEANS adapter.*RFM_RULES"):
        run_method(
            MagicMock(),
            "RFM_RULES",
            180,
            adapter=MethodAdapter("KMEANS", decide),
        )

    decide.assert_not_called()
    manager.read_open_assignments.assert_not_called()
    manager.create_run.assert_not_called()


# ---------- what a completed RFM_RULES run records ----------


def test_a_completed_rfm_rules_run_records_its_method_window_and_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)
    monkeypatch.setattr(
        segmentation,
        "score_rfm_rules",
        Mock(
            return_value=[
                segments_db.ScoredCustomer(
                    _ADA, _SALE, 4, Decimal("400"), 5, 4, 3, 1, "LOYAL"
                )
            ]
        ),
    )

    run(MagicMock(), 90)

    (call,) = manager.create_run.call_args_list
    assert call.args[1] == "RFM_RULES"
    assert call.args[2] == 90
    assert call.args[3] == {"window_days": 90, "quintiles": 5}
    assert call.args[4] == 1  # customer_count


def test_every_assigned_customer_carries_a_label_raw_values_and_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)
    monkeypatch.setattr(
        segmentation,
        "score_rfm_rules",
        Mock(
            return_value=[
                segments_db.ScoredCustomer(
                    _ADA, _SALE, 4, Decimal("400.00"), 5, 4, 3, 1, "LOYAL"
                ),
                segments_db.ScoredCustomer(
                    _BOB, None, None, None, None, None, None, None, None
                ),
            ]
        ),
    )

    run(MagicMock(), 180)

    ((_, run_id, rows),) = [c.args for c in manager.insert_assignments.call_args_list]
    assert run_id == 7
    scored, unassigned = sorted(rows, key=lambda row: row[0])
    # (customer, segment, label, last purchase, frequency, monetary, r, f, m)
    assert scored == (_ADA, 1, "LOYAL", _SALE, 4, Decimal("400.00"), 5, 4, 3)
    assert unassigned == (_BOB, None, None, None, None, None, None, None, None)


# ---------- what an assignment may be ----------


def test_an_assignment_without_a_label_must_carry_no_measurement() -> None:
    """A customer the run actually scored is always labelled (ADR-0018); a null
    label is valid only for the unassigned result, which has nothing measured."""
    with pytest.raises(InvalidAssignment, match="labelled"):
        Assignment(customer_id=_ADA, label_code=None, frequency=3).validate()


def test_an_unassigned_customer_is_a_valid_assignment() -> None:
    _unassigned(_ADA).validate()


@pytest.mark.parametrize("missing", ["last_purchase_at", "frequency", "monetary"])
def test_a_labelled_assignment_must_carry_every_raw_rfm_value(missing: str) -> None:
    values = {
        "last_purchase_at": _SALE,
        "frequency": 3,
        "monetary": Decimal("25.00"),
    }
    values[missing] = None

    with pytest.raises(InvalidAssignment, match="all raw recency"):
        Assignment(customer_id=_ADA, label_code="LOYAL", **values).validate()


def test_a_labelled_assignment_may_omit_quintile_scores() -> None:
    Assignment(
        customer_id=_ADA,
        label_code="LOYAL",
        last_purchase_at=_SALE,
        frequency=3,
        monetary=Decimal("25.00"),
    ).validate()


def test_the_pipeline_refuses_a_scored_customer_left_unlabelled_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)
    bad = Assignment(customer_id=_ADA, label_code=None, monetary=Decimal("5.00"))

    with pytest.raises(InvalidAssignment):
        run_method(MagicMock(), "RFM_RULES", 180, adapter=_adapter(bad))

    manager.create_run.assert_not_called()


def test_the_pipeline_refuses_the_same_customer_twice_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)
    twice = _adapter(_scored(_ADA, "LOYAL"), _scored(_ADA, "CHAMPION"))

    with pytest.raises(InvalidAssignment, match="twice"):
        run_method(MagicMock(), "RFM_RULES", 180, adapter=twice)

    manager.create_run.assert_not_called()


# ---------- the writes: order, atomicity ----------


def test_the_run_reads_what_was_open_then_records_the_run_then_closes_then_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)
    adapter = _adapter(_scored(_ADA, "LOYAL"), _unassigned(_BOB))

    run_method(MagicMock(), "RFM_RULES", 180, adapter=adapter)

    assert [call[0] for call in manager.mock_calls] == [
        "read_open_assignments",
        "create_run",
        "close_open_assignments",
        "insert_assignments",
    ]


def test_every_customer_in_the_run_has_their_open_row_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Including customers whose result did not change: ADR-0017 records one
    row per customer per run."""
    manager = _wire(monkeypatch, prior={_ADA: (1, "LOYAL")})
    adapter = _adapter(_scored(_ADA, "LOYAL"), _unassigned(_BOB))

    run_method(MagicMock(), "RFM_RULES", 180, adapter=adapter)

    ((_, closed),) = [c.args for c in manager.close_open_assignments.call_args_list]
    assert sorted(closed) == [_ADA, _BOB]


def test_the_run_commits_once_after_every_write() -> None:
    connection = MagicMock()
    order: list[str] = []
    connection.commit.side_effect = lambda: order.append("commit")

    monkey = pytest.MonkeyPatch()
    try:
        manager = _wire(monkey)
        for name in ("create_run", "close_open_assignments", "insert_assignments"):
            getattr(manager, name).side_effect = (
                lambda *a, _n=name, **k: order.append(_n) or 7
            )
        run_method(
            connection, "RFM_RULES", 180, adapter=_adapter(_scored(_ADA, "LOYAL"))
        )
    finally:
        monkey.undo()

    assert order == [
        "create_run",
        "close_open_assignments",
        "insert_assignments",
        "commit",
    ]
    connection.commit.assert_called_once_with()


def test_a_failed_write_rolls_back_the_run_with_its_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = MagicMock()
    manager = _wire(monkeypatch)
    manager.insert_assignments.side_effect = RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        run_method(
            connection, "RFM_RULES", 180, adapter=_adapter(_scored(_ADA, "LOYAL"))
        )

    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


def test_the_completion_is_logged_only_after_the_commit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    connection = MagicMock()
    _wire(monkeypatch, prior={_ADA: (1, "CHAMPION")})

    def commit() -> None:
        assert "segment_run_started" in caplog.text
        assert "segment_run_succeeded" not in caplog.text

    connection.commit.side_effect = commit
    with caplog.at_level(logging.INFO):
        run_method(
            connection,
            "RFM_RULES",
            180,
            adapter=_adapter(_scored(_ADA, "LOYAL"), _unassigned(_BOB)),
        )

    assert "processed=2 assigned=1 unmatched=1 reassigned=1 cleared=1" in caplog.text
    assert "method=RFM_RULES" in caplog.text


def test_the_result_carries_the_run_it_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, run_id=42)

    result = run_method(
        MagicMock(), "RFM_RULES", 180, adapter=_adapter(_scored(_ADA, "LOYAL"))
    )

    assert (result.run_id, result.method, result.window_days) == (42, "RFM_RULES", 180)


# ---------- the counts ----------


def test_a_customer_with_no_open_row_counts_as_reassigned_when_labelled() -> None:
    counts = summarise([_scored(_ADA, "LOYAL")], prior={})

    assert (counts.processed, counts.assigned, counts.unmatched) == (1, 1, 0)
    assert (counts.reassigned, counts.cleared) == (1, 0)


def test_a_first_unassigned_customer_counts_as_cleared_like_the_old_statement() -> None:
    """Parity with the single statement this replaced, which counted any customer
    with no open row as changed. Kept, not improved, so a refactor cannot move
    the numbers the run page reports."""
    counts = summarise([_unassigned(_ADA)], prior={})

    assert (counts.reassigned, counts.cleared) == (0, 1)


def test_an_unchanged_result_is_not_counted_as_a_change() -> None:
    counts = summarise([_scored(_ADA, "LOYAL")], prior={_ADA: (1, "LOYAL")})

    assert (counts.reassigned, counts.cleared) == (0, 0)


def test_a_customer_who_lost_their_label_is_cleared() -> None:
    counts = summarise([_unassigned(_ADA)], prior={_ADA: (1, "LOYAL")})

    assert (counts.assigned, counts.unmatched) == (0, 1)
    assert (counts.reassigned, counts.cleared) == (0, 1)


def test_a_customer_who_stays_unassigned_is_not_a_change() -> None:
    counts = summarise([_unassigned(_ADA)], prior={_ADA: (None, None)})

    assert (counts.reassigned, counts.cleared) == (0, 0)


def test_a_change_of_label_with_no_segment_is_still_a_change() -> None:
    """Whatever produced the label, a customer whose label moved changed."""
    after = Assignment(customer_id=_ADA, label_code="LOYAL", frequency=2)
    counts = summarise([after], prior={_ADA: (None, "CHAMPION")})

    assert counts.reassigned == 1


# ---------- downstream method independence (ADR-0018) ----------


def test_downstream_method_independence_an_assignment_has_no_method_or_cluster() -> (
    None
):
    for field in fields(Assignment):
        assert "method" not in field.name and "cluster" not in field.name, field.name


def test_downstream_method_independence_no_consumer_is_given_a_method() -> None:
    for consumer in (classify_migration, build_migration_matrix, explain_migration):
        names = " ".join(inspect.signature(consumer).parameters)
        assert "method" not in names and "cluster" not in names, consumer.__name__


@pytest.mark.parametrize(
    "reader",
    [
        segments_db.list_run_labels,
        segments_db.list_run_assignments,
        segments_db.get_customer_assignment_for_run,
    ],
)
def test_downstream_method_independence_the_readers_never_select_the_method(
    reader,
) -> None:
    source = inspect.getsource(reader).lower()

    assert "method" not in source.replace("methods", "")
    assert "cluster" not in source


@pytest.mark.parametrize("method", ["RFM_RULES", "KMEANS"])
def test_downstream_method_independence_writes_are_the_same_for_each_method(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Identical labelled assignments give identical writes, apart from the one
    field that says which method the run was."""
    manager = _wire(monkeypatch)
    adapter = _adapter(
        _scored(_ADA, "LOYAL"),
        _scored(_BOB, "CHAMPION"),
        _unassigned(_CAL),
        method=method,
    )

    run_method(
        MagicMock(),
        method,
        180,
        adapter=adapter,
    )

    ((_, _run, rows),) = [c.args for c in manager.insert_assignments.call_args_list]
    assert sorted(rows) == sorted(
        [
            (_ADA, 1, "LOYAL", _SALE, 4, Decimal("400.00"), 5, 4, 3),
            (_BOB, 1, "CHAMPION", _SALE, 4, Decimal("400.00"), 5, 4, 3),
            (_CAL, None, None, None, None, None, None, None, None),
        ]
    )
    assert manager.create_run.call_args.args[1] == method


def test_downstream_method_independence_migration_reads_labels_and_nothing_else() -> (
    None
):
    """Two runs, one whose labels a rule-based method wrote and one whose labels
    K-means wrote, are compared by label code alone. The same labels give the
    same migration, and it cannot tell them apart because it is never told."""
    ordinals = {"CHAMPION": 1, "LOYAL": 2, "LOST": 3}
    rules_run = {_ADA: "LOYAL", _BOB: "CHAMPION", _CAL: None}
    kmeans_run = {_ADA: "LOYAL", _BOB: "CHAMPION", _CAL: None}

    same_method = classify_migration(rules_run, rules_run, ordinals)
    across_methods = classify_migration(rules_run, kmeans_run, ordinals)

    assert across_methods == same_method
    assert build_migration_matrix(across_methods, ordinals) == build_migration_matrix(
        same_method, ordinals
    )


# ---------- F9-03: the two ADR-0018 cases K-means adds ----------


def _kmeans_reads(monkeypatch: pytest.MonkeyPatch, *, labels: int = 3) -> Mock:
    """The reads run_kmeans makes, over nine customers in three tight groups."""
    manager = _wire(monkeypatch)
    manager.get_label_ordinals = Mock(
        return_value={f"L{n}": n for n in range(1, labels + 1)}
    )
    monkeypatch.setattr(segmentation, "get_label_ordinals", manager.get_label_ordinals)
    rows = [
        segments_db.CustomerSales(
            f"00000000-0000-0000-0000-{g * 3 + i + 1:012d}",
            _SALE.replace(day=1 + g * 9),
            2 + g * 7 + i,
            Decimal(100 + g * 700 + i),
        )
        for g in range(3)
        for i in range(3)
    ]
    manager.read_rfm_inputs = Mock(return_value=rows)
    monkeypatch.setattr(segmentation, "read_rfm_inputs", manager.read_rfm_inputs)
    return manager


def test_method_domain_refuses_a_kmeans_run_whose_k_differs_from_the_label_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from web.services.cluster_labels import VocabularySizeMismatch
    from web.services.kmeans import KMeansParams
    from web.services.segmentation import run_kmeans

    manager = _kmeans_reads(monkeypatch, labels=3)

    with pytest.raises(VocabularySizeMismatch):
        run_kmeans(MagicMock(), 180, KMeansParams(k=4, seed=1))

    manager.read_rfm_inputs.assert_not_called()
    manager.create_run.assert_not_called()
    manager.insert_assignments.assert_not_called()


def test_cluster_id_permutation_writes_identical_customer_label_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rename every cluster of a fixed partition and expect identical
    (customer_id, label_code) rows, as the ADR's compliance case reads."""
    import itertools

    from web.services import kmeans
    from web.services.kmeans import KMeansParams
    from web.services.segmentation import run_kmeans

    def written(permutation: tuple[int, ...] | None) -> list[tuple]:
        manager = _kmeans_reads(monkeypatch, labels=3)
        if permutation is not None:
            real_fit = kmeans.fit_customers

            def renamed(rows, params):
                result = real_fit(rows, params)
                centroids = [None] * len(result.centroids)
                for old, new in enumerate(permutation):
                    centroids[new] = result.centroids[old]
                return kmeans.KMeansFit(
                    **{
                        **result.__dict__,
                        "assignments": tuple(
                            permutation[a] for a in result.assignments
                        ),
                        "centroids": tuple(centroids),
                    }
                )

            monkeypatch.setattr(segmentation, "fit_customers", renamed)
        run_kmeans(MagicMock(), 180, KMeansParams(k=3, seed=2))
        ((_, _run, rows),) = [c.args for c in manager.insert_assignments.call_args_list]
        return sorted((row[0], row[2]) for row in rows)

    baseline = written(None)
    for permutation in itertools.permutations(range(3)):
        monkeypatch.undo()
        assert written(permutation) == baseline, permutation
