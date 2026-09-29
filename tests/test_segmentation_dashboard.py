"""The segmentation dashboard's four charts, assembled from plain rows (F12-01).

Each chart is a pure function of rows a test controls directly, so what a chart
shows for given data is stated here rather than read off a screenshot. The
orchestration (`build_dashboard`) is exercised with the reads replaced by fakes.

ADR-0018 runs through every piece: a chart is keyed on the stable label and never
reads `segmentation_run.method` or a raw cluster id, and the same charts come out
whichever method produced the run — a test builds one of each and requires the
same charts.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from web.db.segmentation_dashboard import RunRfmRow
from web.db.segments import SegmentationRun
from web.services import segmentation_dashboard as service
from web.services.segment_migration import CustomerMigration, MigrationCategory
from web.services.segmentation_dashboard import (
    NoRuns,
    build_dashboard,
    build_migration_flow,
    build_revenue_by_label,
    build_rfm_heatmap,
    build_segment_sizes,
    quintile_bins,
)

_ORDINALS = {"CHAMPION": 1, "LOYAL": 2, "LOST": 3}
_NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _rfm(
    cid: str,
    label: str | None,
    days_ago: int = 10,
    freq: int = 5,
    spend: str = "100.00",
) -> RunRfmRow:
    return RunRfmRow(
        cid,
        label,
        None if label is None else _NOW - timedelta(days=days_ago),
        None if label is None else freq,
        None if label is None else Decimal(spend),
    )


def _run(run_id: int, method: str = "RFM_RULES", **overrides) -> SegmentationRun:
    defaults = dict(
        run_id=run_id,
        method=method,
        window_days=180,
        parameters={},
        customer_count=3,
        executed_by=None,
        executed_by_name=None,
        run_at=_NOW,
    )
    return SegmentationRun(**{**defaults, **overrides})


# ---------- quintile binning: a chart-only concern, deterministic and ranked ----------


def test_five_equal_groups_are_split_evenly_best_first() -> None:
    ids = [f"c{n}" for n in range(10)]
    values = list(range(10))  # c0..c9, ascending

    bins = quintile_bins(list(zip(ids, values, strict=True)))

    # highest value -> bin 5, the two highest share bin 5, etc.
    assert bins["c9"] == 5 and bins["c8"] == 5
    assert bins["c1"] == 1 and bins["c0"] == 1


def test_ties_are_broken_by_the_lower_id_so_binning_is_deterministic() -> None:
    pairs = [("b", 1.0), ("a", 1.0), ("c", 1.0)]

    bins = quintile_bins(pairs)

    # all tied on value; split by id order, so "a" ranks first (best bin)
    assert bins["a"] >= bins["b"] >= bins["c"]


def test_the_result_does_not_depend_on_input_order() -> None:
    pairs = [(f"c{n}", float(n % 7)) for n in range(23)]
    expected = quintile_bins(pairs)

    for _ in range(5):
        shuffled = pairs[:]
        import random

        random.shuffle(shuffled)
        assert quintile_bins(shuffled) == expected


def test_fewer_than_five_values_still_binned_without_error() -> None:
    bins = quintile_bins([("a", 3.0), ("b", 1.0)])

    assert bins["a"] > bins["b"]


def test_no_values_is_an_empty_mapping() -> None:
    assert quintile_bins([]) == {}


# ---------- segment sizes ----------


def test_sizes_are_counted_per_label_best_to_worst_then_unassigned() -> None:
    rows = [_rfm("a", "LOYAL"), _rfm("b", "LOYAL"), _rfm("c", "LOST"), _rfm("d", None)]

    sizes = build_segment_sizes(rows, _ORDINALS)

    assert [(s.label, s.count) for s in sizes] == [
        ("CHAMPION", 0),
        ("LOYAL", 2),
        ("LOST", 1),
        (None, 1),
    ]


def test_a_label_nobody_holds_is_listed_with_zero_not_omitted() -> None:
    sizes = build_segment_sizes([_rfm("a", "LOST")], _ORDINALS)

    assert any(s.label == "CHAMPION" and s.count == 0 for s in sizes)


def test_sizes_sum_to_the_number_of_rows() -> None:
    rows = [_rfm("a", "LOYAL"), _rfm("b", None), _rfm("c", "LOST")]

    assert sum(s.count for s in build_segment_sizes(rows, _ORDINALS)) == 3


# ---------- revenue by label ----------


def test_revenue_is_listed_in_the_same_order_as_sizes() -> None:
    revenue = build_revenue_by_label(
        {"LOYAL": Decimal("900.00"), "LOST": Decimal("10.00")}, _ORDINALS
    )

    assert [r.label for r in revenue] == ["CHAMPION", "LOYAL", "LOST"]


def test_a_label_with_no_revenue_shows_zero_not_omitted() -> None:
    revenue = build_revenue_by_label({"LOYAL": Decimal("900.00")}, _ORDINALS)

    assert next(r for r in revenue if r.label == "CHAMPION").total == Decimal("0")


def test_unassigned_never_appears_in_revenue() -> None:
    """An unassigned customer had no sale in the run's own window (RN-21), so
    there is never a figure to show for them; the chart has no such bar."""
    revenue = build_revenue_by_label({"LOYAL": Decimal("5.00")}, _ORDINALS)

    assert all(r.label is not None for r in revenue)


# ---------- the RFM heatmap ----------


def test_the_heatmap_has_all_25_cells_even_when_most_are_empty() -> None:
    rows = [_rfm("a", "LOYAL", days_ago=1, freq=9)]

    heatmap = build_rfm_heatmap(rows)

    assert len(heatmap) == 25
    assert sum(cell.count for cell in heatmap) == 1


def test_unassigned_customers_are_excluded_from_the_heatmap() -> None:
    rows = [_rfm("a", "LOYAL"), _rfm("b", None)]

    heatmap = build_rfm_heatmap(rows)

    assert sum(cell.count for cell in heatmap) == 1


def test_the_most_recent_and_most_frequent_customer_lands_in_the_best_cell() -> None:
    """Five customers so both extremes are actually reachable (quintile_bins'
    own tests cover what happens with fewer)."""
    rows = [
        _rfm("a", "LOYAL", days_ago=1, freq=100),
        _rfm("b", "LOYAL", days_ago=100, freq=80),
        _rfm("c", "LOYAL", days_ago=200, freq=50),
        _rfm("d", "LOYAL", days_ago=300, freq=20),
        _rfm("e", "LOYAL", days_ago=400, freq=1),
    ]

    heatmap = build_rfm_heatmap(rows)

    best = next(
        cell for cell in heatmap if cell.recency_bin == 5 and cell.frequency_bin == 5
    )
    worst = next(
        cell for cell in heatmap if cell.recency_bin == 1 and cell.frequency_bin == 1
    )
    assert best.count == 1 and worst.count == 1


def test_with_fewer_scored_customers_than_bins_the_extremes_are_not_both_reached() -> (
    None
):
    """PostgreSQL's own ntile(5) behaves the same way: with 3 rows the first
    three buckets each take one and the last two stay empty, so the worst of
    3 lands in bucket 3, not bucket 1. The heatmap must read the same as the
    pipeline's own quintile scoring does for the same count of customers."""
    rows = [
        _rfm("a", "LOYAL", days_ago=1, freq=1),
        _rfm("b", "LOYAL", days_ago=2, freq=1),
        _rfm("c", "LOYAL", days_ago=3, freq=1),
    ]

    heatmap = build_rfm_heatmap(rows)

    assert sum(cell.count for cell in heatmap if cell.recency_bin == 1) == 0
    assert sum(cell.count for cell in heatmap if cell.recency_bin == 5) == 1


def test_no_scored_customers_is_25_empty_cells_not_an_error() -> None:
    heatmap = build_rfm_heatmap([_rfm("a", None)])

    assert len(heatmap) == 25
    assert all(cell.count == 0 for cell in heatmap)


# ---------- migration flow ----------


def _migration(cid, before, after, category, direction=None) -> CustomerMigration:
    return CustomerMigration(cid, before, after, category, direction)


def test_a_link_is_reported_for_each_earlier_to_later_label_pair() -> None:
    migrations = [
        _migration("a", "LOYAL", "CHAMPION", MigrationCategory.MOVED),
        _migration("b", "LOYAL", "CHAMPION", MigrationCategory.MOVED),
        _migration("c", "LOST", "LOST", MigrationCategory.UNCHANGED),
    ]

    flow = build_migration_flow(migrations, _ORDINALS)

    weights = {(link.source, link.target): link.weight for link in flow.links}
    assert weights[("LOYAL", "CHAMPION")] == 2
    assert weights[("LOST", "LOST")] == 1


def test_unchanged_customers_are_a_self_loop_not_omitted() -> None:
    migrations = [_migration("a", "LOYAL", "LOYAL", MigrationCategory.UNCHANGED)]

    flow = build_migration_flow(migrations, _ORDINALS)

    assert any(link.source == link.target == "LOYAL" for link in flow.links)


def test_newly_assigned_and_newly_unassigned_involve_the_unassigned_node() -> None:
    migrations = [
        _migration("a", None, "LOYAL", MigrationCategory.NEWLY_ASSIGNED),
        _migration("b", "LOYAL", None, MigrationCategory.NEWLY_UNASSIGNED),
    ]

    flow = build_migration_flow(migrations, _ORDINALS)

    sources = {link.source for link in flow.links}
    targets = {link.target for link in flow.links}
    assert None in sources or "Unassigned" in {s for s in sources}
    assert flow.unassigned_label in sources or flow.unassigned_label in targets


def test_a_customer_only_one_run_scored_is_counted_not_charted_as_a_flow() -> None:
    """New-to-the-population and dropped-from-the-population are population
    changes, not a move between segments, so they are not sankey links."""
    migrations = [
        _migration("a", None, "LOYAL", MigrationCategory.ABSENT_FROM_EARLIER),
        _migration("b", "LOYAL", None, MigrationCategory.ABSENT_FROM_LATER),
        _migration("c", "LOYAL", "LOYAL", MigrationCategory.UNCHANGED),
    ]

    flow = build_migration_flow(migrations, _ORDINALS)

    assert len(flow.links) == 1
    assert flow.new_to_population == 1
    assert flow.left_the_population == 1


def test_zero_weight_pairs_are_left_out_of_the_links() -> None:
    migrations = [_migration("a", "LOYAL", "LOYAL", MigrationCategory.UNCHANGED)]

    flow = build_migration_flow(migrations, _ORDINALS)

    assert all(link.weight > 0 for link in flow.links)
    assert len(flow.links) == 1


def test_the_flow_never_reads_a_method_or_a_cluster() -> None:
    names = " ".join(inspect.signature(build_migration_flow).parameters)
    assert "method" not in names and "cluster" not in names


# ---------- assembling the dashboard ----------


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current=None,
    previous=None,
    rfm=None,
    revenue=None,
) -> Mock:
    manager = Mock()
    manager.get_run = Mock(return_value=current if current is not None else _run(31))
    manager.get_previous_run = Mock(return_value=previous)
    manager.list_run_rfm_rows = Mock(return_value=rfm or [_rfm("a", "LOYAL")])
    manager.list_run_revenue_by_label = Mock(
        return_value=revenue or {"LOYAL": Decimal("5.00")}
    )
    manager.get_label_ordinals = Mock(return_value=dict(_ORDINALS))
    manager.compute_migration = Mock(return_value=[])
    for name in (
        "get_run",
        "get_previous_run",
        "list_run_rfm_rows",
        "list_run_revenue_by_label",
        "get_label_ordinals",
        "compute_migration",
    ):
        monkeypatch.setattr(service, name, getattr(manager, name))
    return manager


def test_no_runs_at_all_is_refused_with_a_clear_signal(monkeypatch) -> None:
    manager = _wire(monkeypatch, current=None)

    with pytest.raises(NoRuns):
        build_dashboard(MagicMock(), run_id=None)

    manager.list_run_rfm_rows.assert_not_called()


def test_with_no_run_id_given_the_newest_run_is_used(monkeypatch) -> None:
    manager = _wire(monkeypatch)
    manager.get_run.side_effect = None
    from web.services import segmentation_dashboard as svc

    monkeypatch.setattr(svc, "list_runs", Mock(return_value=([_run(31)], 1)))

    dashboard = build_dashboard(MagicMock(), run_id=None)

    assert dashboard.run.run_id == 31


def test_a_named_run_id_is_used_instead_of_the_newest(monkeypatch) -> None:
    _wire(monkeypatch, current=_run(9))

    dashboard = build_dashboard(MagicMock(), run_id=9)

    assert dashboard.run.run_id == 9


def test_the_previous_run_is_looked_up_from_the_chosen_run(monkeypatch) -> None:
    manager = _wire(monkeypatch, current=_run(31))

    build_dashboard(MagicMock(), run_id=31)

    manager.get_previous_run.assert_called_once()
    assert manager.get_previous_run.call_args.args[1] == 31


def test_with_no_previous_run_migration_is_none_and_it_is_not_computed(
    monkeypatch,
) -> None:
    manager = _wire(monkeypatch, current=_run(31), previous=None)

    dashboard = build_dashboard(MagicMock(), run_id=31)

    assert dashboard.migration is None
    assert dashboard.previous_run is None
    manager.compute_migration.assert_not_called()


def test_with_a_previous_run_migration_is_computed_between_the_two(monkeypatch) -> None:
    manager = _wire(monkeypatch, current=_run(31), previous=_run(30))

    dashboard = build_dashboard(MagicMock(), run_id=31)

    assert dashboard.previous_run.run_id == 30
    assert manager.compute_migration.call_args.args[1:] == (30, 31)
    assert dashboard.migration is not None


def test_revenue_is_read_over_the_runs_own_window(monkeypatch) -> None:
    run = _run(31, window_days=90, run_at=_NOW)
    manager = _wire(monkeypatch, current=run)

    build_dashboard(MagicMock(), run_id=31)

    since, until = manager.list_run_revenue_by_label.call_args.args[2:]
    assert until == _NOW
    assert since == _NOW - timedelta(days=90)


def test_the_dashboard_carries_all_four_charts(monkeypatch) -> None:
    _wire(monkeypatch, current=_run(31), previous=_run(30))

    dashboard = build_dashboard(MagicMock(), run_id=31)

    assert dashboard.sizes and dashboard.revenue and dashboard.heatmap
    assert dashboard.migration is not None


def test_the_module_never_reads_or_is_told_a_method(monkeypatch) -> None:
    _wire(monkeypatch, current=_run(31))
    source = Path(service.__file__).read_text(encoding="utf-8").lower()

    for word in ("kmeans", "rfm_rules", "segmentation_run.method"):
        assert word not in source


def test_the_same_rfm_rows_give_the_same_charts_whichever_method_made_the_run(
    monkeypatch,
) -> None:
    rows = [_rfm("a", "LOYAL"), _rfm("b", "LOST", days_ago=200, freq=1)]

    _wire(monkeypatch, current=_run(31, method="RFM_RULES"), rfm=rows)
    a = build_dashboard(MagicMock(), run_id=31)
    _wire(monkeypatch, current=_run(31, method="KMEANS"), rfm=rows)
    b = build_dashboard(MagicMock(), run_id=31)

    assert a.sizes == b.sizes
    assert a.heatmap == b.heatmap
