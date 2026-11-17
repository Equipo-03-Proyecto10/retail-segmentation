"""analytics-kpis reconciles with the web's /reports/ page (F26-02, ADR-0062).

The service's own test, services/analytics-kpis/tests/web_reconciliation.rs,
runs the web's revenue statements against the same database as its net-sales
report and compares. A service never imports web/ (ADR-0045), so it holds a
copy of the two statements; this test fails when either stops being the web's,
so the copy cannot drift from the figure the page shows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from web.db import revenue_summary

RECONCILIATION = (
    Path(__file__).resolve().parents[1]
    / "services/analytics-kpis/tests/web_reconciliation.rs"
)


def _pinned(name: str) -> str:
    source = RECONCILIATION.read_text(encoding="utf-8")
    found = re.search(rf'pub const {name}: &str = r#"(.*?)"#;', source, flags=re.DOTALL)
    assert found, f"{name} is not pinned in {RECONCILIATION.name}"
    return found.group(1)


@pytest.mark.parametrize(
    ("name", "statement"),
    [
        ("WEB_TOTALS", revenue_summary._TOTALS),
        ("WEB_BY_STORE", revenue_summary._BY_STORE),
    ],
)
def test_the_service_runs_the_statement_the_web_runs(name: str, statement: str) -> None:
    assert _pinned(name) == statement


def test_the_web_reads_net_sales_through_net_sale() -> None:
    # The definition both sides share: the web's net_sale view and the
    # published api_sales.net_purchase_line_v1 are one rule (ADR-0062).
    assert "FROM net_sale AS t" in revenue_summary._TOTALS
    assert "FROM net_sale AS t" in revenue_summary._BY_STORE
