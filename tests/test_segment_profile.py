"""The F23-05 profiles: raw units, and each label put to its claim (#817)."""

import dataclasses
from pathlib import Path

import pytest

from analysis import segment_profile as study
from analysis.dataset import generate
from analysis.normalization_study import Features
from web.services.cluster_labels import rank_clusters

FOUND, RESULT = study.profiles()
FEATURES = Features(generate(), study.WINDOW_DAYS)
OVERALL_PRIOR = 6.198793666750440


def test_the_chosen_model_gives_the_labels_and_sizes_of_the_document():
    assert [p.label for p in FOUND] == ["CHAMPION", "POTENTIAL", "AT_RISK", "LOST"]
    assert [p.customers for p in FOUND] == [529, 1471, 748, 1231]
    assert [round(p.revenue_share, 3) for p in FOUND] == [0.493, 0.292, 0.155, 0.061]


def test_sizes_and_revenue_cover_everything_once():
    assert sum(p.customers for p in FOUND) == len(FEATURES.ids)
    assert sum(p.revenue_share for p in FOUND) == pytest.approx(1.0)
    assert sum(p.customer_share for p in FOUND) == pytest.approx(1.0)
    assert sum(p.revenue for p in FOUND) == pytest.approx(sum(FEATURES.monetary))


def test_the_raw_centroid_is_the_normalized_centroid_in_raw_units():
    # Min-max is a straight line, so turning the fit's centroid back into raw units
    # must give the members' mean, with R reversed as production reverses it.
    r, f, m = FEATURES.recency_days, FEATURES.frequency, FEATURES.monetary
    for rank, cluster in enumerate(rank_clusters(RESULT)):
        nr, nf, nm = RESULT.centroids[cluster]
        p = FOUND[rank]
        assert p.recency_days == pytest.approx(max(r) - nr * (max(r) - min(r)))
        assert p.frequency == pytest.approx(min(f) + nf * (max(f) - min(f)))
        assert p.monetary == pytest.approx(min(m) + nm * (max(m) - min(m)))


def test_only_the_newcomer_claim_of_potential_fails():
    rows = study.checks(FOUND, OVERALL_PRIOR)
    failed = [(c.label, c.claim) for c in rows if not c.holds]
    assert failed == [("POTENTIAL", "a customer who is recent (new)")]
    assert len(rows) == 8


def test_a_claim_that_does_not_hold_is_reported_not_assumed():
    # AT_RISK that never bought more often than anyone, and a CHAMPION that is not
    # the best on one measure: both checks must turn to "no".
    weakened = [
        dataclasses.replace(p, prior_frequency=1.0) if p.label == "AT_RISK" else p
        for p in FOUND
    ]
    weakened[0] = dataclasses.replace(weakened[0], frequency=weakened[1].frequency - 1)
    failed = {
        (c.label, c.claim) for c in study.checks(weakened, OVERALL_PRIOR) if not c.holds
    }
    assert ("AT_RISK", "used to buy often") in failed
    assert ("CHAMPION", "highest recency, frequency and monetary value") in failed


def test_the_planted_groups_account_for_every_customer():
    for p in FOUND:
        assert sum(p.groups.values()) == p.customers
        assert 0 <= p.extreme <= p.customers
        assert 0.0 <= p.extreme_revenue_share <= 1.0


def test_every_label_has_the_description_of_the_seed():
    assert set(study.DESCRIPTION) == set(study.VOCABULARY)
    seed = (
        Path(__file__).resolve().parents[1] / "sql/02_seed_30_per_table.sql"
    ).read_text()
    for code, text in study.DESCRIPTION.items():
        assert f"'{code}'" in seed
        assert text in seed
