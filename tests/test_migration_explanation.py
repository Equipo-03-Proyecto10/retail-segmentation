"""Per-customer migration explanation (F7-06): why a customer moved (or
didn't) between two runs, from the stored R/F/M values and scores alone.

explain_migration is pure (web/services/segment_migration.py, ADR-0003), so
these tests build simple stand-ins for the two RunAssignment rows instead of
touching a database.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from web.services.segment_migration import explain_migration


def _assignment(
    *,
    label_code,
    r_score,
    f_score,
    m_score,
    recency_last_purchase_at=None,
    frequency_count=None,
    monetary_total=None,
):
    return SimpleNamespace(
        label_code=label_code,
        r_score=r_score,
        f_score=f_score,
        m_score=m_score,
        recency_last_purchase_at=recency_last_purchase_at,
        frequency_count=frequency_count,
        monetary_total=monetary_total,
    )


# ---------- AC 1: raw values, scores, and deltas from both runs ----------


def test_shows_raw_values_and_scores_from_both_runs() -> None:
    before = _assignment(
        label_code="CHAMPION",
        r_score=3,
        f_score=3,
        m_score=3,
        recency_last_purchase_at=datetime(2026, 1, 1),
        frequency_count=5,
        monetary_total=Decimal("100.00"),
    )
    after = _assignment(
        label_code="LOYAL",
        r_score=1,
        f_score=3,
        m_score=3,
        recency_last_purchase_at=datetime(2026, 3, 1),
        frequency_count=5,
        monetary_total=Decimal("100.00"),
    )
    explanation = explain_migration(before, after)

    assert explanation.recency.raw_before == datetime(2026, 1, 1)
    assert explanation.recency.raw_after == datetime(2026, 3, 1)
    assert explanation.recency.score_before == 3
    assert explanation.recency.score_after == 1
    assert explanation.recency.score_delta == -2


def test_a_component_that_did_not_change_has_a_zero_delta() -> None:
    before = _assignment(label_code="LOYAL", r_score=2, f_score=4, m_score=4)
    after = _assignment(label_code="LOYAL", r_score=2, f_score=4, m_score=4)
    explanation = explain_migration(before, after)

    assert explanation.recency.score_delta == 0
    assert explanation.frequency.score_delta == 0
    assert explanation.monetary.score_delta == 0


# ---------- AC 2: names the component that moved most, from stored scores ----------


def test_names_the_component_with_the_largest_score_delta() -> None:
    before = _assignment(label_code="CHAMPION", r_score=5, f_score=3, m_score=3)
    after = _assignment(label_code="AT_RISK", r_score=1, f_score=2, m_score=3)
    explanation = explain_migration(before, after)

    assert explanation.most_changed == (explanation.recency,)
    assert explanation.recency.score_delta == -4
    assert (
        explanation.most_changed_caption == "Recency moved the most (score delta -4)."
    )


def test_a_tie_names_every_component_that_shares_it() -> None:
    """#297: two (or three) components at the same absolute delta must all
    be named, not silently reduced to whichever came first in the list."""
    before = _assignment(label_code="LOYAL", r_score=3, f_score=3, m_score=3)
    after = _assignment(label_code="AT_RISK", r_score=1, f_score=1, m_score=3)
    explanation = explain_migration(before, after)

    assert explanation.most_changed == (explanation.recency, explanation.frequency)
    assert (
        explanation.most_changed_caption
        == "Recency and Frequency moved equally (score deltas -2, -2)."
    )


def test_a_three_way_tie_names_all_three() -> None:
    before = _assignment(label_code="LOYAL", r_score=3, f_score=3, m_score=3)
    after = _assignment(label_code="AT_RISK", r_score=2, f_score=4, m_score=2)
    explanation = explain_migration(before, after)

    assert explanation.most_changed == (
        explanation.recency,
        explanation.frequency,
        explanation.monetary,
    )
    assert (
        explanation.most_changed_caption
        == "Recency, Frequency and Monetary moved equally (score deltas -1, +1, -1)."
    )


# ---------- AC 3: an unchanged label still shows deltas, and says so ----------


def test_an_unchanged_label_still_reports_deltas() -> None:
    before = _assignment(label_code="LOYAL", r_score=3, f_score=3, m_score=2)
    after = _assignment(label_code="LOYAL", r_score=4, f_score=3, m_score=2)
    explanation = explain_migration(before, after)

    assert explanation.label_changed is False
    assert explanation.label_before == explanation.label_after == "LOYAL"
    assert explanation.recency.score_delta == 1


def test_a_changed_label_is_reported_as_changed() -> None:
    before = _assignment(label_code="LOYAL", r_score=3, f_score=3, m_score=2)
    after = _assignment(label_code="AT_RISK", r_score=1, f_score=3, m_score=2)
    explanation = explain_migration(before, after)

    assert explanation.label_changed is True


# ---------- AC 4: unassigned in one run means no scores, not zeros ----------


def test_unassigned_in_the_earlier_run_has_no_before_scores() -> None:
    after = _assignment(label_code="LOYAL", r_score=3, f_score=3, m_score=2)
    explanation = explain_migration(None, after)

    assert explanation.recency.score_before is None
    assert explanation.recency.raw_before is None
    assert explanation.recency.score_delta is None
    assert explanation.label_before is None
    assert explanation.label_changed is True


def test_unassigned_in_the_later_run_has_no_after_scores() -> None:
    before = _assignment(label_code="LOYAL", r_score=3, f_score=3, m_score=2)
    explanation = explain_migration(before, None)

    assert explanation.recency.score_after is None
    assert explanation.recency.raw_after is None
    assert explanation.recency.score_delta is None
    assert explanation.label_after is None


def test_most_changed_is_none_when_either_run_has_no_scores() -> None:
    """A None score_delta on any component can't be compared to the other
    two, so most_changed itself is undefined rather than guessed."""
    after = _assignment(label_code="LOYAL", r_score=3, f_score=3, m_score=2)
    explanation = explain_migration(None, after)

    assert explanation.most_changed is None


def test_most_changed_is_none_when_nothing_moved() -> None:
    """All three deltas at zero means no component actually moved -- naming
    one as "moved the most" would be misleading."""
    before = _assignment(label_code="LOYAL", r_score=2, f_score=1, m_score=4)
    after = _assignment(label_code="LOYAL", r_score=2, f_score=1, m_score=4)
    explanation = explain_migration(before, after)

    assert explanation.most_changed is None
