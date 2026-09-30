"""A KMEANS run from start to finish (F9-03).

`run_kmeans` is F9-02's adapter with ADR-0018's mapping wired in: it reads the
label vocabulary, refuses a `k` that is not its size before it reads a single
sale, fits, maps every cluster to a label by the deterministic order, and hands the
labelled assignments to the same pipeline RFM_RULES uses. The fit is tested in
test_kmeans.py and the mapping in test_cluster_labels.py; what is covered here is
the run around them, and that nothing which names a cluster reaches what is
written.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from web.db.segments import CustomerSales
from web.services import segmentation
from web.services.cluster_labels import VocabularySizeMismatch
from web.services.kmeans import KMeansParams
from web.services.segmentation import MethodUnavailable, run_kmeans, run_method

_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
# Deliberately not in ordinal order: the vocabulary must be read by ordinal.
_ORDINALS = {"LOST": 5, "CHAMPION": 1, "AT_RISK": 4, "POTENTIAL": 3, "LOYAL": 2}
_BEST_TO_WORST = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "LOST"]


def _cid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


def _sales() -> list[CustomerSales]:
    """Fifteen customers in five tight groups, best group first, then two with no
    sales. Group g is more recent, more frequent and bigger-spending than g + 1."""
    rows = []
    for group in range(5):
        for i in range(3):
            rows.append(
                CustomerSales(
                    _cid(group * 3 + i + 1),
                    _NOW - timedelta(days=5 + group * 40 + i),
                    40 - group * 8 + i,
                    Decimal(4000 - group * 800 + i * 5),
                )
            )
    rows += [CustomerSales(_cid(n), None, None, None) for n in (16, 17)]
    return rows


def _wire(monkeypatch: pytest.MonkeyPatch, ordinals=None, rows=None) -> Mock:
    manager = Mock()
    manager.get_label_ordinals.return_value = dict(
        _ORDINALS if ordinals is None else ordinals
    )
    manager.read_rfm_inputs.return_value = _sales() if rows is None else rows
    manager.read_open_assignments.return_value = {}
    manager.create_run.return_value = 5
    for name in (
        "get_label_ordinals",
        "read_rfm_inputs",
        "read_open_assignments",
        "create_run",
        "close_open_assignments",
        "insert_assignments",
    ):
        monkeypatch.setattr(segmentation, name, getattr(manager, name))
    return manager


def _rows(manager: Mock) -> dict[str, tuple]:
    ((_, _run, rows),) = [c.args for c in manager.insert_assignments.call_args_list]
    return {row[0]: row for row in rows}


# ---------- refusing a k that cannot be paired (ADR-0030) ----------


def test_a_single_cluster_is_refused_before_any_sale_is_read(monkeypatch) -> None:
    manager = _wire(monkeypatch)

    with pytest.raises(VocabularySizeMismatch, match="k=1.*at least 2"):
        run_kmeans(MagicMock(), 180, KMeansParams(k=1, seed=1))

    manager.read_rfm_inputs.assert_not_called()
    manager.create_run.assert_not_called()
    manager.close_open_assignments.assert_not_called()
    manager.insert_assignments.assert_not_called()


def test_a_refused_run_rolls_back_and_commits_nothing(monkeypatch) -> None:
    connection = MagicMock()
    _wire(monkeypatch)

    with pytest.raises(VocabularySizeMismatch):
        run_kmeans(connection, 180, KMeansParams(k=1, seed=1))

    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


def test_an_empty_vocabulary_is_refused_rather_than_run(monkeypatch) -> None:
    manager = _wire(monkeypatch, ordinals={})

    with pytest.raises(VocabularySizeMismatch):
        run_kmeans(MagicMock(), 180, KMeansParams(k=1, seed=1))

    manager.read_rfm_inputs.assert_not_called()


# ---------- the run ----------


def test_a_run_whose_k_is_the_vocabulary_size_is_recorded_as_kmeans(
    monkeypatch,
) -> None:
    manager = _wire(monkeypatch)

    result = run_kmeans(MagicMock(), 180, KMeansParams(k=5, seed=1))

    call = manager.create_run.call_args
    assert call.args[1] == "KMEANS" and call.args[2] == 180
    assert call.args[3]["k"] == 5 and call.args[3]["seed"] == 1
    assert (result.method, result.run_id, result.processed) == ("KMEANS", 5, 17)


def test_the_vocabulary_is_read_best_to_worst_by_ordinal_and_not_by_key_order(
    monkeypatch,
) -> None:
    manager = _wire(monkeypatch)

    run_kmeans(MagicMock(), 180, KMeansParams(k=5, seed=1))
    written = _rows(manager)

    # the five groups, best to worst, carry the five labels, best to worst
    for group, label in enumerate(_BEST_TO_WORST):
        assert {written[_cid(group * 3 + i + 1)][2] for i in range(3)} == {label}


def test_customers_without_sales_stay_unassigned(monkeypatch) -> None:
    manager = _wire(monkeypatch)

    run_kmeans(MagicMock(), 180, KMeansParams(k=5, seed=1))
    written = _rows(manager)

    for n in (16, 17):
        assert written[_cid(n)] == (_cid(n),) + (None,) * 8


def test_the_same_seed_over_the_same_sales_writes_the_same_labels(monkeypatch) -> None:
    first, second = _wire(monkeypatch), None
    run_kmeans(MagicMock(), 180, KMeansParams(k=5, seed=3))
    second = _wire(monkeypatch)
    run_kmeans(MagicMock(), 180, KMeansParams(k=5, seed=3))

    assert _rows(first) == _rows(second)


def test_runs_that_find_the_same_partition_label_it_alike_whatever_they_number(
    monkeypatch,
) -> None:
    """Different seeds number the clusters differently, and a few fall into a worse
    local optimum, which is how K-means behaves. What is guaranteed is that runs
    which find the same partition give every group the same label."""
    by_partition: dict[frozenset, set[tuple]] = {}
    for seed in range(1, 13):
        manager = _wire(monkeypatch)
        run_kmeans(MagicMock(), 180, KMeansParams(k=5, seed=seed))
        pairs = {cid: row[2] for cid, row in _rows(manager).items() if row[2]}
        groups = {}
        for cid, label in pairs.items():
            groups.setdefault(label, set()).add(cid)
        partition = frozenset(frozenset(members) for members in groups.values())
        by_partition.setdefault(partition, set()).add(tuple(sorted(pairs.items())))

    # several seeds found the same partition, and no partition was labelled two ways
    assert len(by_partition) < 12
    assert all(len(labellings) == 1 for labellings in by_partition.values())


# ---------- nothing that names a cluster reaches what is written ----------


def test_no_cluster_number_is_written_and_no_business_segment_is_invented(
    monkeypatch,
) -> None:
    manager = _wire(monkeypatch)

    run_kmeans(MagicMock(), 180, KMeansParams(k=5, seed=1))

    for row in _rows(manager).values():
        customer, segment, label, *measurements = row
        assert isinstance(customer, str)
        assert segment is None
        assert label is None or label in _ORDINALS
        assert measurements[3:] == [None, None, None]  # no quintile scores


def test_the_recorded_parameters_name_no_cluster(monkeypatch) -> None:
    manager = _wire(monkeypatch)

    run_kmeans(MagicMock(), 180, KMeansParams(k=5, seed=1))

    text = json.dumps(manager.create_run.call_args.args[3])
    assert "cluster_id" not in text and "centroid" not in text


def test_the_history_table_has_no_column_that_could_hold_a_cluster_id() -> None:
    """A raw cluster id has nowhere to go: the table that holds every
    assignment has no such column."""
    schema = Path("sql/01_schema.sql").read_text(encoding="utf-8")
    table = re.search(
        r"CREATE TABLE customer_segment_history \((.*?)\n\);", schema, re.DOTALL
    ).group(1)

    assert "cluster" not in table.lower()


# ---------- what is still refused ----------


def test_kmeans_still_cannot_be_started_without_its_parameters(monkeypatch) -> None:
    """run_method has no default k or seed to give, so KMEANS is started through
    run_kmeans and nowhere else."""
    _wire(monkeypatch)

    with pytest.raises(MethodUnavailable):
        run_method(MagicMock(), "KMEANS", 180)


# ---------- any k over the six-label vocabulary (#336, ADR-0030) ----------

_SIX_ORDINALS = {
    "LOST": 6,
    "CHAMPION": 1,
    "HIBERNATING": 5,
    "AT_RISK": 4,
    "POTENTIAL": 3,
    "LOYAL": 2,
}
_SIX = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"]


def _groups(count: int) -> list[CustomerSales]:
    """`count` tight groups of three customers, best group first, far enough apart
    that any k equal to `count` finds exactly these groups."""
    rows = []
    for group in range(count):
        for i in range(3):
            rows.append(
                CustomerSales(
                    _cid(group * 3 + i + 1),
                    _NOW - timedelta(days=5 + group * 30 + i),
                    60 - group * 7 + i,
                    Decimal(9000 - group * 1000 + i * 5),
                )
            )
    return rows


def _labels_by_group(manager: Mock, count: int) -> list[str]:
    written = _rows(manager)
    by_group = []
    for group in range(count):
        labels = {written[_cid(group * 3 + i + 1)][2] for i in range(3)}
        assert len(labels) == 1, "a group was split across labels"
        by_group.append(labels.pop())
    return by_group


def _run(monkeypatch, k: int) -> Mock:
    manager = _wire(monkeypatch, ordinals=_SIX_ORDINALS, rows=_groups(k))
    run_kmeans(MagicMock(), 180, KMeansParams(k=k, seed=1))
    return manager


def test_k_six_pairs_the_six_groups_one_to_one_as_before(monkeypatch) -> None:
    manager = _run(monkeypatch, 6)

    assert _labels_by_group(manager, 6) == _SIX
    mapping = manager.create_run.call_args.args[3]["label_mapping"]
    assert mapping["clusters_per_label"] == dict.fromkeys(_SIX, 1)
    assert mapping["shared_labels"] == [] and mapping["labels_without_cluster"] == []


def test_k_five_is_run_rather_than_refused_and_leaves_potential_unused(
    monkeypatch,
) -> None:
    manager = _run(monkeypatch, 5)

    assert _labels_by_group(manager, 5) == [
        "CHAMPION",
        "LOYAL",
        "AT_RISK",
        "HIBERNATING",
        "LOST",
    ]
    parameters = manager.create_run.call_args.args[3]
    assert parameters["k"] == 5
    assert parameters["label_mapping"]["labels_without_cluster"] == ["POTENTIAL"]


def test_k_eight_shares_labels_and_the_run_records_which(monkeypatch) -> None:
    manager = _run(monkeypatch, 8)

    assert _labels_by_group(manager, 8) == [
        "CHAMPION",
        "LOYAL",
        "LOYAL",
        "POTENTIAL",
        "AT_RISK",
        "HIBERNATING",
        "HIBERNATING",
        "LOST",
    ]
    mapping = manager.create_run.call_args.args[3]["label_mapping"]
    assert mapping["shared_labels"] == ["LOYAL", "HIBERNATING"]
    assert mapping["clusters_per_label"]["LOYAL"] == 2


@pytest.mark.parametrize("k", [2, 5, 6, 8])
def test_whatever_k_only_vocabulary_labels_and_no_cluster_number_is_written(
    monkeypatch, k: int
) -> None:
    manager = _run(monkeypatch, k)

    labels = {row[2] for row in _rows(manager).values()}
    assert labels <= set(_SIX)
    for row in _rows(manager).values():
        assert row[1] is None  # no segment: a K-means result has no rule band
    parameters = manager.create_run.call_args.args[3]
    stored = json.dumps(parameters)
    mapping = parameters["label_mapping"]
    assert set(mapping["clusters_per_label"]) == set(_SIX)
    assert all(label in _SIX for label in mapping["labels_by_rank"])
    assert not re.search(r'"\d+"\s*:', stored)  # nothing keyed by a number
