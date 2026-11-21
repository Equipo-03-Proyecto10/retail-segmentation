"""The port scores a customer as the web did (ADR-0045, RN-65).

The seed holds a run of the web's rule-based segmentation: for each customer, the
frequency, the monetary total, the three quintile scores and the label that run
recorded. The seeded rule set freezes the boundaries those scores imply. Scored
under it at the instant of that run, from the same customer's net purchases, every
customer takes the web's counts, scores and label. The service imports nothing from
`web/`: the web's answers are rows of the seed.
"""

from __future__ import annotations

import psycopg
import pytest

from app import profile, store

pytestmark = pytest.mark.postgres


def test_every_customer_the_web_scored_is_scored_the_same_under_the_seeded_rule_set(
    owner: psycopg.Connection,
) -> None:
    run = owner.execute(
        "SELECT run_id, run_at, window_days FROM public.segmentation_run"
        " WHERE method = 'RFM_RULES' AND scope = 'FULL' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    assert run is not None
    run_id, run_at, window_days = run
    rules = store.rule_set(owner, 1)
    assert rules is not None and rules.window_days == window_days
    web = owner.execute(
        "SELECT customer_id, frequency_count, monetary_total, r_score, f_score,"
        " m_score, label_code FROM public.customer_segment_history"
        " WHERE run_id = %s AND frequency_count IS NOT NULL ORDER BY customer_id",
        (run_id,),
    ).fetchall()
    assert len(web) >= 30

    different = []
    for customer_id, *expected in web:
        ported = profile.build(
            rules, store.measures(owner, customer_id, window_days, run_at), run_at
        )
        got = [
            ported.f_count,
            ported.m_total,
            ported.r_score,
            ported.f_score,
            ported.m_score,
            ported.label_code,
        ]
        if got != expected:
            different.append((customer_id, got, expected))
    assert different == []
