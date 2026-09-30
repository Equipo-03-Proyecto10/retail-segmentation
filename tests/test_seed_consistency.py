"""Regression guards for the cross-table relationships in the demonstration seed.

The database CI job executes the seed against PostgreSQL.  These source-level
guards keep the important ordering and derivation contract visible to the unit
suite too: assignment history must be built from reconciled sales, and no sale
may predate its customer's registration.
"""

from __future__ import annotations

from pathlib import Path

_SEED = (Path(__file__).parents[1] / "sql/02_seed_30_per_table.sql").read_text(
    encoding="utf-8"
)


def test_customer_registration_predates_the_seeded_sales_window() -> None:
    """The oldest possible sale is 180 days old, so registration is earlier."""

    assert "CURRENT_DATE - (180 + n)" in _SEED
    assert "CURRENT_DATE - (n*7 || ' days')::interval" not in _SEED


def test_history_is_derived_after_reconciled_transaction_lines() -> None:
    """A profile and its run assignment must consume the same sales facts."""

    line_reconciliation = _SEED.index("UPDATE transaction AS t")
    history = _SEED.index("INSERT INTO customer_segment_history")
    assert history > line_reconciliation

    history_sql = _SEED[history:]
    assert "JOIN transaction AS t" in history_sql
    assert "t.occurred_at >= r.run_at - make_interval" in history_sql
    assert "t.occurred_at <= r.run_at" in history_sql
    assert "sum(t.total) AS monetary" in history_sql
