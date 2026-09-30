"""Per-customer migration explanation (F7-06): why a customer moved (or
didn't) between two runs, from the stored R/F/M values and scores alone.

explain_migration is pure (web/services/segment_migration.py, ADR-0003), so
these tests build simple stand-ins for the two RunAssignment rows instead of
touching a database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from web.services.segment_migration import (
    FREQUENCY_STABLE_PURCHASES,
    MONETARY_STABLE_RATIO,
    RECENCY_STABLE_DAYS,
    Judgement,
    describe_migration,
    explain_migration,
)


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


# ---------- #338: the explanation in plain language (RN-50) ----------

_RUN_BEFORE = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
_RUN_AFTER = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)


def _measured(
    label_code="LOYAL",
    *,
    recency_days: int,
    run_at: datetime,
    frequency: int = 5,
    monetary: str = "1000.00",
    scores=(3, 3, 3),
):
    return _assignment(
        label_code=label_code,
        r_score=scores[0],
        f_score=scores[1],
        m_score=scores[2],
        recency_last_purchase_at=run_at - timedelta(days=recency_days),
        frequency_count=frequency,
        monetary_total=Decimal(monetary),
    )


def _narrate(before, after):
    return describe_migration(
        explain_migration(before, after),
        run_at_before=_RUN_BEFORE,
        run_at_after=_RUN_AFTER,
    )


def _sentence(narrative, name):
    return next(s for s in narrative.sentences if s.name == name)


def test_the_summary_reads_like_the_issue_asks() -> None:
    narrative = _narrate(
        _measured(recency_days=18, run_at=_RUN_BEFORE, frequency=7),
        _measured(
            "AT_RISK", recency_days=72, run_at=_RUN_AFTER, frequency=3, scores=(1, 2, 3)
        ),
    )
    assert narrative.summary == (
        "Recency went from 18 to 72 days, frequency dropped from 7 to 3, "
        "monetary stable."
    )


def test_recency_is_in_days_and_never_a_timestamp() -> None:
    narrative = _narrate(
        _measured(recency_days=18, run_at=_RUN_BEFORE),
        _measured(recency_days=72, run_at=_RUN_AFTER),
    )
    recency = _sentence(narrative, "Recency")

    assert recency.text == (
        "Recency went from 18 to 72 days — changed: longer since the last purchase."
    )
    assert recency.judgement is Judgement.CHANGED
    assert (narrative.recency_days_before, narrative.recency_days_after) == (18, 72)
    assert "2026" not in recency.text and ":00" not in recency.text


def test_a_more_recent_purchase_reads_as_one() -> None:
    narrative = _narrate(
        _measured(recency_days=40, run_at=_RUN_BEFORE),
        _measured(recency_days=2, run_at=_RUN_AFTER),
    )
    assert _sentence(narrative, "Recency").text.endswith(
        "changed: a more recent purchase."
    )


@pytest.mark.parametrize(
    ("after_days", "judgement"),
    [
        (10 + RECENCY_STABLE_DAYS, Judgement.STABLE),
        (10 + RECENCY_STABLE_DAYS + 1, Judgement.CHANGED),
        (10 - RECENCY_STABLE_DAYS, Judgement.STABLE),
        (10 - RECENCY_STABLE_DAYS - 1, Judgement.CHANGED),
    ],
)
def test_recency_is_stable_within_seven_days(after_days, judgement) -> None:
    assert RECENCY_STABLE_DAYS == 7
    narrative = _narrate(
        _measured(recency_days=10, run_at=_RUN_BEFORE),
        _measured(recency_days=after_days, run_at=_RUN_AFTER),
    )
    assert _sentence(narrative, "Recency").judgement is judgement


def test_one_day_is_singular() -> None:
    narrative = _narrate(
        _measured(recency_days=1, run_at=_RUN_BEFORE, frequency=1),
        _measured(recency_days=1, run_at=_RUN_AFTER, frequency=1),
    )
    assert _sentence(narrative, "Recency").text == "Recency stayed at 1 day — stable."
    assert (
        _sentence(narrative, "Frequency").text
        == "Frequency stayed at 1 purchase — stable."
    )


@pytest.mark.parametrize(
    ("after", "judgement", "text"),
    [
        (5, Judgement.STABLE, "Frequency stayed at 5 purchases — stable."),
        (6, Judgement.CHANGED, "Frequency rose from 5 to 6 purchases — changed."),
        (4, Judgement.CHANGED, "Frequency dropped from 5 to 4 purchases — changed."),
    ],
)
def test_frequency_is_stable_only_when_the_count_is_unchanged(
    after, judgement, text
) -> None:
    assert FREQUENCY_STABLE_PURCHASES == 0
    narrative = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, frequency=5),
        _measured(recency_days=5, run_at=_RUN_AFTER, frequency=after),
    )
    sentence = _sentence(narrative, "Frequency")
    assert (sentence.judgement, sentence.text) == (judgement, text)


@pytest.mark.parametrize(
    ("after", "judgement"),
    [
        ("1100.00", Judgement.STABLE),
        ("900.00", Judgement.STABLE),
        ("1100.01", Judgement.CHANGED),
        ("899.99", Judgement.CHANGED),
    ],
)
def test_monetary_is_stable_within_ten_percent(after, judgement) -> None:
    assert MONETARY_STABLE_RATIO == Decimal("0.10")
    narrative = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, monetary="1000.00"),
        _measured(recency_days=5, run_at=_RUN_AFTER, monetary=after),
    )
    assert _sentence(narrative, "Monetary").judgement is judgement


def test_monetary_sentences_carry_both_amounts_in_mxn() -> None:
    changed = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, monetary="1234.50"),
        _measured(recency_days=5, run_at=_RUN_AFTER, monetary="480.00"),
    )
    stable = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, monetary="1000.00"),
        _measured(recency_days=5, run_at=_RUN_AFTER, monetary="1050.00"),
    )
    assert _sentence(changed, "Monetary").text == (
        "Monetary fell from 1,234.50 to 480.00 MXN — changed (more than 10%)."
    )
    assert _sentence(stable, "Monetary").text == (
        "Monetary went from 1,000.00 to 1,050.00 MXN — stable (within 10%)."
    )


def test_a_zero_spend_is_stable_only_against_another_zero() -> None:
    narrative = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, monetary="0.00"),
        _measured(recency_days=5, run_at=_RUN_AFTER, monetary="10.00"),
    )
    assert _sentence(narrative, "Monetary").judgement is Judgement.CHANGED


def test_the_page_states_the_thresholds_from_the_constants() -> None:
    narrative = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE),
        _measured(recency_days=5, run_at=_RUN_AFTER),
    )
    assert narrative.threshold_note == (
        "Changed or stable is judged on the customer's own values, not the "
        "scores: recency within 7 days, the same number of purchases, and spend "
        "within 10% are stable (RN-50)."
    )


# ---------- #338: a score moved only because the cut points did ----------


def test_an_unchanged_frequency_with_a_new_score_says_the_rank_moved() -> None:
    narrative = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, frequency=5, scores=(3, 4, 3)),
        _measured(recency_days=5, run_at=_RUN_AFTER, frequency=5, scores=(3, 3, 3)),
    )
    frequency = _sentence(narrative, "Frequency")

    assert frequency.rank_only
    assert frequency.judgement is Judgement.STABLE
    assert frequency.text == (
        "Frequency stayed at 5 purchases — stable. Its score still went from 4 to 3: "
        "other customers moved the quintile cut points, not this customer's behaviour."
    )


def test_the_same_last_purchase_with_a_new_recency_score_says_the_rank_moved() -> None:
    last_purchase = _RUN_BEFORE - timedelta(days=3)
    before = _measured(recency_days=0, run_at=_RUN_BEFORE, scores=(5, 3, 3))
    before.recency_last_purchase_at = last_purchase
    after = _measured(recency_days=0, run_at=_RUN_AFTER, scores=(4, 3, 3))
    after.recency_last_purchase_at = last_purchase
    # Runs a week apart, so the unchanged purchase drifts by 7 days: stable.
    narrative = describe_migration(
        explain_migration(before, after),
        run_at_before=_RUN_BEFORE,
        run_at_after=_RUN_BEFORE + timedelta(days=7),
    )
    recency = _sentence(narrative, "Recency")

    assert recency.rank_only
    assert "other customers moved the quintile cut points" in recency.text


def test_an_unchanged_monetary_with_a_new_score_says_the_rank_moved() -> None:
    narrative = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, scores=(3, 3, 2)),
        _measured(recency_days=5, run_at=_RUN_AFTER, scores=(3, 3, 1)),
    )
    assert _sentence(narrative, "Monetary").rank_only


def test_a_changed_value_is_never_blamed_on_the_rank() -> None:
    narrative = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, frequency=7, scores=(3, 5, 3)),
        _measured(recency_days=5, run_at=_RUN_AFTER, frequency=3, scores=(3, 2, 3)),
    )
    frequency = _sentence(narrative, "Frequency")
    assert not frequency.rank_only
    assert "cut points" not in frequency.text


def test_a_stable_but_different_value_with_a_new_score_is_not_blamed_on_the_rank() -> (
    None
):
    narrative = _narrate(
        _measured(
            recency_days=5, run_at=_RUN_BEFORE, monetary="1000", scores=(3, 3, 3)
        ),
        _measured(recency_days=5, run_at=_RUN_AFTER, monetary="1050", scores=(3, 3, 2)),
    )
    monetary = _sentence(narrative, "Monetary")
    assert not monetary.rank_only
    assert monetary.text.endswith("Its score went from 3 to 2.")


def test_a_kmeans_run_without_scores_still_reads_and_never_blames_the_rank() -> None:
    narrative = _narrate(
        _measured(recency_days=18, run_at=_RUN_BEFORE, scores=(None, None, None)),
        _measured(recency_days=72, run_at=_RUN_AFTER, scores=(None, None, None)),
    )
    assert [s.judgement for s in narrative.sentences] == [
        Judgement.CHANGED,
        Judgement.STABLE,
        Judgement.STABLE,
    ]
    assert not any(s.rank_only for s in narrative.sentences)
    assert not any("score" in s.text for s in narrative.sentences)


# ---------- #338: new customer versus unassigned ----------


def test_a_customer_absent_from_the_earlier_run_is_new_not_unassigned() -> None:
    explanation = explain_migration(None, _measured(recency_days=5, run_at=_RUN_AFTER))
    narrative = describe_migration(
        explanation, run_at_before=_RUN_BEFORE, run_at_after=_RUN_AFTER
    )

    assert not explanation.in_earlier and explanation.in_later
    assert narrative.is_new and not narrative.left
    assert all(s.judgement is Judgement.NOT_MEASURED for s in narrative.sentences)
    assert "not part of the earlier run" in narrative.sentences[0].text
    assert "no purchase" not in narrative.sentences[0].text


def test_a_customer_scored_but_unassigned_is_not_new() -> None:
    unassigned = _assignment(label_code=None, r_score=None, f_score=None, m_score=None)
    explanation = explain_migration(
        unassigned, _measured(recency_days=5, run_at=_RUN_AFTER)
    )
    narrative = describe_migration(
        explanation, run_at_before=_RUN_BEFORE, run_at_after=_RUN_AFTER
    )

    assert explanation.in_earlier
    assert not narrative.is_new
    assert "the earlier run found no purchase in its window" in (
        narrative.sentences[0].text
    )


def test_a_customer_absent_from_the_later_run_has_left() -> None:
    explanation = explain_migration(_measured(recency_days=5, run_at=_RUN_BEFORE), None)
    narrative = describe_migration(
        explanation, run_at_before=_RUN_BEFORE, run_at_after=_RUN_AFTER
    )
    assert narrative.left and not narrative.is_new
    assert "not part of the later run" in narrative.sentences[0].text


def test_a_label_change_with_every_value_stable_is_put_down_to_the_rank() -> None:
    narrative = _narrate(
        _measured("HIBERNATING", recency_days=19, run_at=_RUN_BEFORE, scores=(2, 2, 3)),
        _measured("AT_RISK", recency_days=19, run_at=_RUN_AFTER, scores=(2, 3, 3)),
    )
    assert narrative.moved_by_rank_only


def test_a_label_change_with_a_changed_value_is_not_put_down_to_the_rank() -> None:
    narrative = _narrate(
        _measured("LOYAL", recency_days=5, run_at=_RUN_BEFORE, frequency=7),
        _measured("AT_RISK", recency_days=5, run_at=_RUN_AFTER, frequency=3),
    )
    assert not narrative.moved_by_rank_only


def test_an_unchanged_label_is_never_a_rank_only_move() -> None:
    narrative = _narrate(
        _measured(recency_days=5, run_at=_RUN_BEFORE, scores=(3, 4, 3)),
        _measured(recency_days=5, run_at=_RUN_AFTER, scores=(3, 3, 3)),
    )
    assert not narrative.moved_by_rank_only
