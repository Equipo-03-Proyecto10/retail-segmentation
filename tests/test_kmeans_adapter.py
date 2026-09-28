"""The KMEANS adapter behind the pipeline (F9-02), and ADR-0021's compliance.

The fit is tested in tests/test_kmeans.py. What is covered here is the adapter
around it: that it fits only customers who have sales, leaves the rest unassigned,
takes every label from a mapping it is handed and never from a cluster number,
records the parameters and quality measures on the run, and says so when a fit did
not converge. The mapping from clusters to labels is F9-03's, so these tests hand
the adapter a stand-in mapper and say so; no production code here invents labels.
"""

from __future__ import annotations

import ast
import logging
import re
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from web.db.segments import CustomerSales
from web.services import segmentation
from web.services.kmeans import KMeansFit, KMeansParams, TooFewCustomers
from web.services.segmentation import (
    InvalidAssignment,
    LabelMappingIncomplete,
    kmeans_adapter,
    run_method,
)

_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _cid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


def _sales() -> list[CustomerSales]:
    """Nine customers in three obvious groups, then three with no sales."""
    groups = [
        (1, 1, Decimal("20.00")),  # lapsed, rare, small
        (30, 8, Decimal("900.00")),  # recent, frequent, big
        (10, 4, Decimal("300.00")),  # in between
    ]
    rows = []
    for g, (days, freq, spend) in enumerate(groups):
        for i in range(3):
            rows.append(
                CustomerSales(
                    _cid(g * 3 + i + 1),
                    _NOW - timedelta(days=days + i),
                    freq + i,
                    spend + i,
                )
            )
    rows += [CustomerSales(_cid(n), None, None, None) for n in (10, 11, 12)]
    return rows


def _mapper(labels=("BEST", "MIDDLE", "WORST")):
    """A stand-in for F9-03's mapping: label a cluster by how many customers
    it holds. Not the ADR's rule; it only has to be a function of the fit."""

    def mapping(fit: KMeansFit) -> dict[int, str]:
        order = sorted(range(fit.params.k), key=lambda c: (-len(fit.members(c)), c))
        return {cluster: labels[rank] for rank, cluster in enumerate(order)}

    return mapping


def _wire(monkeypatch: pytest.MonkeyPatch, rows=None) -> Mock:
    manager = Mock()
    manager.read_open_assignments.return_value = {}
    manager.create_run.return_value = 3
    manager.read_rfm_inputs.return_value = _sales() if rows is None else rows
    for name in (
        "read_rfm_inputs",
        "read_open_assignments",
        "create_run",
        "close_open_assignments",
        "insert_assignments",
    ):
        monkeypatch.setattr(segmentation, name, getattr(manager, name))
    return manager


def _run(monkeypatch, *, params=None, mapper=None, rows=None):
    manager = _wire(monkeypatch, rows)
    adapter = kmeans_adapter(params or KMeansParams(k=3, seed=1), mapper or _mapper())
    result = run_method(MagicMock(), "KMEANS", 180, adapter=adapter)
    return manager, result


def _written(manager: Mock) -> dict[str, tuple]:
    ((_, _run_id, rows),) = [c.args for c in manager.insert_assignments.call_args_list]
    return {row[0]: row for row in rows}


# ---------- what the adapter reads and whom it fits ----------


def test_the_adapter_reads_the_raw_inputs_over_the_window(monkeypatch) -> None:
    manager, _ = _run(monkeypatch)

    assert manager.read_rfm_inputs.call_args.args[1] == 180


def test_only_customers_with_sales_are_clustered_and_the_rest_are_unassigned(
    monkeypatch,
) -> None:
    manager, result = _run(monkeypatch)
    written = _written(manager)

    assert result.processed == 12 and result.assigned == 9 and result.unmatched == 3
    for n in (10, 11, 12):  # no sales in the window: RN-21's unassigned result
        assert written[_cid(n)] == (_cid(n),) + (None,) * 8
    for n in range(1, 10):
        assert written[_cid(n)][2] in {"BEST", "MIDDLE", "WORST"}


def test_a_clustered_customer_carries_their_raw_values_and_no_quintile_scores(
    monkeypatch,
) -> None:
    """Scores are RFM_RULES' quintiles. Inventing them for K-means would make two
    different measures look like one, so they stay empty; the raw values, which
    the migration explanation and the consumption profile read, are recorded."""
    manager, _ = _run(monkeypatch)
    row = _written(manager)[_cid(4)]

    # (customer, segment, label, last purchase, frequency, monetary, r, f, m)
    assert row[1] is None  # no business segment resolved
    assert row[3] == _NOW - timedelta(days=30)
    assert row[4] == 8 and row[5] == Decimal("900.00")
    assert row[6:] == (None, None, None)


def test_every_clustered_customer_is_labelled(monkeypatch) -> None:
    """ADR-0018: a customer the run actually scored is always labelled."""
    manager, _ = _run(monkeypatch)

    assert all(_written(manager)[_cid(n)][2] is not None for n in range(1, 10))


# ---------- labels come from the mapping and nowhere else ----------


def test_labels_come_only_through_the_mapping_the_adapter_is_given(monkeypatch) -> None:
    manager, _ = _run(
        monkeypatch, mapper=lambda fit: dict.fromkeys(range(3), "ONLY_ONE")
    )

    labels = {row[2] for cid, row in _written(manager).items() if row[2] is not None}
    assert labels == {"ONLY_ONE"}


def test_the_mapping_is_handed_the_finished_fit(monkeypatch) -> None:
    seen = []

    def mapper(fit: KMeansFit) -> dict[int, str]:
        seen.append(fit)
        return dict.fromkeys(range(fit.params.k), "X")

    _run(monkeypatch, mapper=mapper)

    (fit,) = seen
    assert isinstance(fit, KMeansFit)
    assert len(fit.customer_ids) == 9  # only the customers with sales
    assert fit.params == KMeansParams(k=3, seed=1)


def test_a_mapping_that_leaves_a_cluster_unlabelled_is_refused_before_any_write(
    monkeypatch,
) -> None:
    manager = _wire(monkeypatch)
    adapter = kmeans_adapter(KMeansParams(k=3, seed=1), lambda fit: {0: "A", 1: "B"})

    with pytest.raises(LabelMappingIncomplete):
        run_method(MagicMock(), "KMEANS", 180, adapter=adapter)

    manager.create_run.assert_not_called()
    manager.insert_assignments.assert_not_called()


def test_a_mapping_that_labels_a_cluster_with_nothing_is_refused_before_any_write(
    monkeypatch,
) -> None:
    manager = _wire(monkeypatch)
    adapter = kmeans_adapter(
        KMeansParams(k=3, seed=1), lambda fit: {0: "A", 1: "B", 2: None}
    )

    with pytest.raises(InvalidAssignment):
        run_method(MagicMock(), "KMEANS", 180, adapter=adapter)

    manager.create_run.assert_not_called()


def test_no_cluster_number_reaches_what_the_pipeline_writes(monkeypatch) -> None:
    manager, _ = _run(monkeypatch)

    for row in _written(manager).values():
        # every value is a customer, a label, a raw measurement or None
        assert all(not isinstance(value, KMeansFit) for value in row)
        assert row[2] is None or isinstance(row[2], str)


# ---------- what the run records ----------


def test_the_run_records_method_window_and_every_parameter_of_the_fit(
    monkeypatch,
) -> None:
    manager, result = _run(
        monkeypatch,
        params=KMeansParams(k=3, seed=17, max_iterations=40, tolerance=0.001),
    )

    call = manager.create_run.call_args
    assert call.args[1] == "KMEANS"
    assert call.args[2] == 180
    recorded = call.args[3]
    assert recorded["k"] == 3
    assert recorded["seed"] == 17
    assert recorded["max_iterations"] == 40
    assert recorded["tolerance"] == 0.001
    assert recorded["window_days"] == 180
    assert (result.method, result.run_id) == ("KMEANS", 3)


def test_the_quality_measures_are_recorded_alongside_the_parameters(
    monkeypatch,
) -> None:
    manager, _ = _run(monkeypatch)

    quality = manager.create_run.call_args.args[3]["quality"]

    assert quality["customers_clustered"] == 9
    assert quality["customers_unassigned"] == 3
    assert quality["converged"] is True
    assert quality["silhouette"] > 0.5  # three well-separated groups
    assert sum(quality["cluster_sizes"]) == 9


def test_the_customer_count_on_the_run_includes_the_unassigned(monkeypatch) -> None:
    manager, _ = _run(monkeypatch)

    assert manager.create_run.call_args.args[4] == 12


def test_the_same_seed_over_the_same_sales_writes_the_same_assignments(
    monkeypatch,
) -> None:
    first, _ = _run(monkeypatch, params=KMeansParams(k=3, seed=5))
    second, _ = _run(monkeypatch, params=KMeansParams(k=3, seed=5))

    assert _written(first) == _written(second)
    assert first.create_run.call_args.args[3] == second.create_run.call_args.args[3]


def test_a_fit_that_did_not_converge_is_recorded_and_logged_as_such(
    monkeypatch, caplog
) -> None:
    params = KMeansParams(k=3, seed=1, max_iterations=1, tolerance=0.0)
    with caplog.at_level(logging.WARNING):
        manager, _ = _run(monkeypatch, params=params)

    quality = manager.create_run.call_args.args[3]["quality"]
    assert quality["converged"] is False
    assert quality["stopped_on"] == "iteration_limit"
    assert "kmeans_not_converged" in caplog.text


def test_a_fit_that_converged_logs_no_warning(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        _run(monkeypatch)

    assert "kmeans_not_converged" not in caplog.text


# ---------- when there is nothing to cluster ----------


def test_fewer_customers_with_sales_than_clusters_is_refused_and_writes_nothing(
    monkeypatch,
) -> None:
    rows = _sales()[:2] + [CustomerSales(_cid(10), None, None, None)]
    manager = _wire(monkeypatch, rows)
    adapter = kmeans_adapter(KMeansParams(k=3, seed=1), _mapper())

    with pytest.raises(TooFewCustomers):
        run_method(MagicMock(), "KMEANS", 180, adapter=adapter)

    manager.create_run.assert_not_called()


def test_a_run_refused_for_too_few_customers_rolls_back() -> None:
    connection = MagicMock()
    monkey = pytest.MonkeyPatch()
    try:
        _wire(monkey, [CustomerSales(_cid(1), _NOW, 1, Decimal(1))])
        with pytest.raises(TooFewCustomers):
            run_method(
                connection,
                "KMEANS",
                180,
                adapter=kmeans_adapter(KMeansParams(k=3, seed=1), _mapper()),
            )
    finally:
        monkey.undo()

    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


def test_kmeans_is_still_unavailable_without_an_adapter(monkeypatch) -> None:
    """The adapter is supplied, never registered: no run reaches the database
    until F9-03's mapping exists to hand it."""
    _wire(monkeypatch)

    with pytest.raises(segmentation.MethodUnavailable):
        run_method(MagicMock(), "KMEANS", 180)


def test_the_kmeans_adapter_cannot_be_recorded_as_another_method(
    monkeypatch,
) -> None:
    manager = _wire(monkeypatch)
    adapter = kmeans_adapter(KMeansParams(k=3, seed=1), _mapper())

    assert adapter.method == "KMEANS"
    with pytest.raises(segmentation.AdapterMismatch):
        run_method(MagicMock(), "RFM_RULES", 180, adapter=adapter)

    manager.create_run.assert_not_called()


# ---------- ADR-0021 compliance ----------


def _import_roots(path: str) -> set[str]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize(
    "path", ["web/services/segmentation.py", "web/services/kmeans.py"]
)
def test_the_clustering_code_imports_only_the_standard_library_and_the_application(
    path: str,
) -> None:
    """The acceptance criterion: nothing third-party, and in particular no
    numpy, scipy or scikit-learn."""
    allowed = set(sys.stdlib_module_names) | {"web"}

    assert _import_roots(path) <= allowed


def test_the_kmeans_module_passes_adr_0021s_grep_literally() -> None:
    """The check as the ADR writes it: any import line naming a module outside
    its list would be printed, and it must print nothing."""
    allowlist = re.compile(
        r"\b(web|typing|dataclasses|math|random|statistics|collections|itertools"
        r"|decimal|datetime)\b"
    )
    source = Path("web/services/kmeans.py").read_text(encoding="utf-8")

    offending = [
        line
        for line in source.splitlines()
        if re.match(r"^\s*(import|from)\s+", line) and not allowlist.search(line)
    ]

    assert offending == []


def test_no_scientific_computing_dependency_is_declared() -> None:
    forbidden = re.compile(r"scikit-learn|sklearn|^numpy|^scipy", re.IGNORECASE)

    for path in ("web/requirements.txt", "web/requirements-dev.txt", "pyproject.toml"):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            assert not forbidden.search(line.strip()), (path, line)
