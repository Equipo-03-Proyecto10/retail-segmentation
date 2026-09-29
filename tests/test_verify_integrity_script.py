"""sql/verify_integrity.sql's negative cases fail for the reason they name.

A case that inserts a transaction without `source_transaction_id` fails on its
NOT NULL (#209) before the constraint the case is about is ever evaluated, so
it looks like a pass while checking nothing (#262).
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "sql/verify_integrity.sql").read_text()


def test_every_transaction_insert_supplies_a_source_transaction_id() -> None:
    columns = re.findall(r"INSERT INTO transaction \(([^)]*)\)", SCRIPT)

    assert columns
    assert all("source_transaction_id" in listed for listed in columns), columns
