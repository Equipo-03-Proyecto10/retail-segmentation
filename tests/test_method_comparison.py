"""The F23-06 comparison: the RFM replica, the three criteria and the verdict (#818)."""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import Connection

from analysis import dataset as evaluation
from analysis import method_comparison as study
from analysis.k_study import VOCABULARY
from analysis.load import load
from analysis.segment_profile import checks as profile_checks
from analysis.segment_profile import profiles as profile_clusters
from web.db.segments import score_rfm_rules

DATASET = evaluation.generate()
SMALL = evaluation.generate(customers=300)


# ---------- the RFM replica ----------


def test_the_quintile_split_is_over_distinct_values_with_the_larger_groups_first():
    # Seven distinct values split 2, 2, 1, 1, 1, best group first (ntile's rule),
    # so the best two share the top score.
    cut = study._cut([1, 2, 3, 4, 5, 6, 7])
    assert [study._score(cut, v) for v in range(1, 8)] == [1, 2, 3, 4, 4, 5, 5]


def test_equal_values_share_a_score_and_few_values_take_the_top_scores():
    assert study._cut([4, 4, 4, 4]) == [(5, 4)]
    three = study._cut([10, 20, 30, 30, 20])
    assert [study._score(three, v) for v in (10, 20, 30)] == [3, 4, 5]


def test_a_value_the_cut_was_not_built_from_takes_the_group_it_reaches():
    cut = study._cut([1, 2, 3, 4, 5, 6, 7])
    assert study._score(cut, 4.5) == 4
    assert study._score(cut, 100) == 5
    assert study._score(cut, -100) == 1


def test_the_bands_are_read_from_the_seed_and_partition_every_triple():
    rules = study.rules()
    assert [number for number, *_ in rules] == list(range(1, 31))
    for triple in itertools.product(range(1, 6), repeat=3):
        holding = [
            n
            for n, (r0, r1, f0, f1, m0, m1), _ in rules
            if r0 <= triple[0] <= r1 and f0 <= triple[1] <= f1 and m0 <= triple[2] <= m1
        ]
        assert len(holding) == 1, triple
    assert {label for *_, label in rules} == set(VOCABULARY)


def test_each_rule_has_the_label_the_seed_comments_on_it():
    text = (
        Path(study.__file__).resolve().parents[1] / "sql" / "02_seed_30_per_table.sql"
    ).read_text()
    commented = dict(
        (int(n), label)
        for n, label in re.findall(
            r"\(\s*(\d+), 'RULE_\d+',[^)]*\)[,;]\s*-- ([A-Z_]+)", text
        )
    )
    assert len(commented) == 30
    assert commented == {n: label for n, *_, label in study.rules()}


def test_the_best_triple_is_a_champion_and_the_worst_is_lost():
    assert study.band_label(5, 5, 5) == "CHAMPION"
    assert study.band_label(1, 1, 1) == "LOST"


def test_the_replica_labels_everyone_with_a_sale_and_no_one_else():
    raw = study.window_raw(SMALL, 0, 180)
    labels = study.rfm_labels(raw)
    assert set(labels) == set(raw)
    assert set(labels.values()) <= set(VOCABULARY)
    in_window = {s.customer_id for s in SMALL.sales if s.seconds_ago <= 180 * 86400}
    assert set(raw) == in_window


def test_the_default_dataset_gives_the_labels_of_the_document():
    counts = {}
    for label in study.rfm_labels(study.window_raw(DATASET, 0, 180)).values():
        counts[label] = counts.get(label, 0) + 1
    assert [counts.get(label, 0) for label in VOCABULARY] == [
        201,
        336,
        1852,
        4,
        419,
        1167,
    ]


def test_window_raw_adds_up_to_the_window_s_sales():
    raw = study.window_raw(SMALL, 90, 180)
    window = [s for s in SMALL.sales if 90 * 86400 < s.seconds_ago <= 270 * 86400]
    assert sum(count for _, count, _ in raw.values()) == len(window)
    assert sum((total for *_, total in raw.values()), Decimal(0)) == sum(
        (s.amount for s in window), Decimal(0)
    )


@pytest.fixture
def owner_connection(owner_url: str) -> Iterator[Connection[Any]]:
    connection = psycopg.connect(owner_url)
    yield connection
    connection.rollback()
    connection.close()


@pytest.mark.postgres
def test_the_replica_agrees_with_the_production_query(
    owner_connection: Connection[Any],
) -> None:
    """The Python scoring and the bands give every customer the label, and the three
    scores, that `score_rfm_rules` does. The seed's own sales are moved out of every
    window and the dataset's are loaded at the database's own `now()`, so the query
    sees exactly the dataset. Nothing is committed."""
    data = evaluation.generate(seed=99, customers=300)
    with owner_connection.cursor() as cursor:
        cursor.execute("SELECT now()")
        moment = cursor.fetchone()[0]
        cursor.execute(
            "UPDATE transaction SET occurred_at = occurred_at - interval '4000 days'"
        )
    load(owner_connection, data, as_of=moment)
    raw = study.window_raw(data, 0, 180)
    cuts = study.fit_cuts(raw)
    mine = study.rfm_labels(raw, cuts)
    ids = {c.customer_id for c in data.customers}
    scored = {
        str(row.customer_id): row
        for row in score_rfm_rules(owner_connection, 180)
        if str(row.customer_id) in ids
    }
    assert set(scored) == ids
    for who, row in scored.items():
        label = row.label_code
        label = label.decode() if isinstance(label, bytes) else label
        if who not in raw:
            assert label is None and row.r_score is None
            continue
        newest, count, total = raw[who]
        assert (row.r_score, row.f_score, row.m_score) == (
            study._score(cuts.r, -newest),
            study._score(cuts.f, count),
            study._score(cuts.m, total),
        )
        assert label == mine[who]


# ---------- the criteria ----------


def test_adjusted_r_squared_is_one_for_perfect_groups_and_not_positive_for_none():
    perfect = {"a": [1.0, 1.0, 1.0], "b": [5.0, 5.0, 5.0]}
    assert study.adjusted_r2(perfect) == pytest.approx(1.0)
    shuffled = {"a": [1.0, 5.0, 1.0], "b": [5.0, 1.0, 5.0]}
    assert study.adjusted_r2(shuffled) <= 0.0
    assert study.adjusted_r2({"a": [2.0, 2.0], "b": [2.0, 2.0]}) == 0.0


def test_the_same_resamples_are_given_to_every_method():
    first, second = study.draws(50, 3), study.draws(50, 3)
    assert first == second
    assert len(first) == 3 and all(len(d) == 50 for d in first)


def test_the_interpretability_of_the_chosen_model_is_f23_05_s():
    method = study.KMeans(DATASET, 4)
    found = study.interpretability(DATASET, method)
    assert (found["held"], len(found["claims"])) == (7, 8)
    # The same claims, the same outcomes, as the document F23-05 wrote.
    clusters, _ = profile_clusters(DATASET)
    earlier = profile_checks(
        clusters, study.profiles_of(DATASET, method.window(0))[1].prior_frequency
    )
    failed = {(c.label, c.claim) for c in found["claims"] if not c.holds}
    assert failed == {(c.label, c.claim) for c in earlier if not c.holds}


def test_rfm_rules_hold_nine_of_twelve_claims_and_the_hibernating_claim_is_the_seed_s():
    found = study.interpretability(DATASET, study.RfmRules(DATASET))
    assert (found["held"], len(found["claims"])) == (9, 12)
    failed = {(c.label, c.claim) for c in found["claims"] if not c.holds}
    assert ("HIBERNATING", "low frequency and monetary value") in failed
    assert found["small"] == ["AT_RISK"]


def test_both_methods_are_scored_on_the_same_customers_by_the_same_code():
    rfm, kmeans = study.RfmRules(SMALL), study.KMeans(SMALL, 4)
    drawn = study.draws(len(rfm.window(0)), 2)
    for method in (rfm, kmeans):
        assert set(method.window(0)) == set(rfm.window(0))
        stable = study.stability(method, drawn)
        assert -1.0 <= stable["bootstrap_lowest"] <= stable["bootstrap_mean"] <= 1.0
        assert 0.0 <= stable["window_kept"] <= 1.0
        useful = study.usefulness(SMALL, method)
        assert [o["customers"] for o in useful["origins"]] == [
            len(study.window_raw(SMALL, offset, 180)) for offset, _ in study.ORIGINS
        ]


def test_usefulness_of_the_chosen_pair_does_not_move():
    scores = {
        method.name: study.usefulness(DATASET, method)["score"]
        for method in (study.RfmRules(DATASET), study.KMeans(DATASET, 4))
    }
    assert round(scores["RFM rules"], 3) == 0.420
    assert round(scores["K-means, k = 4"], 3) == 0.400


# ---------- the verdict ----------


def test_a_lead_counts_only_at_the_margin_and_the_margin_itself_counts():
    assert study.result(0.50, 0.30, 0.20) == "first"  # exactly the margin leads
    assert study.result(0.30, 0.50, 0.20) == "second"
    assert study.result(0.49, 0.30, 0.20) == "tie"
    assert study.result(0.30, 0.30, 0.20) == "tie"


def _scores(i: float, s: float, u: float) -> dict[str, float]:
    return {"interpretability": i, "stability": s, "usefulness": u}


def test_a_method_is_favoured_only_with_two_wins_and_no_loss():
    low = _scores(0.1, 0.1, 0.1)
    assert study.verdict(_scores(0.9, 0.9, 0.1), low)[1] == "first"
    assert study.verdict(low, _scores(0.9, 0.9, 0.1))[1] == "second"
    # two wins and a loss, one win and two ties, and all ties favour neither
    assert study.verdict(_scores(0.9, 0.9, 0.0), _scores(0.1, 0.1, 0.9))[1] == "neither"
    assert study.verdict(_scores(0.9, 0.1, 0.1), low)[1] == "neither"
    assert study.verdict(low, low)[1] == "neither"


def test_the_verdict_can_be_read_at_other_margins():
    first, second = _scores(0.5, 0.5, 0.5), _scores(0.4, 0.4, 0.45)
    assert study.verdict(first, second)[1] == "neither"
    assert study.verdict(first, second, 0.5)[1] == "first"


def test_agreement_is_reported_and_reconciles():
    rfm, kmeans = study.RfmRules(SMALL), study.KMeans(SMALL, 4)
    found = study.agreement(SMALL, rfm, kmeans)["comparison"]
    assert found.agree + found.disagree == found.compared == len(rfm.window(0))
    assert found.only_first == found.only_second == 0
