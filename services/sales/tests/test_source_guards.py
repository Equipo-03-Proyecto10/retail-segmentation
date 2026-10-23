"""ADR-0039's static checks, held over the service's own code (app/).

The tests themselves restock inventory as the database owner between cases
(conftest.py's cleanup, and the "stock arrives" case), so the checks are about
what the service runs, which is what ADR-0039 constrains.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
SOURCES = {path.name: path.read_text() for path in APP.rglob("*.py")}


def _matching(pattern: str, flags: int = 0) -> list[str]:
    compiled = re.compile(pattern, flags)
    return [
        f"{name}:{number}"
        for name, text in SOURCES.items()
        for number, line in enumerate(text.splitlines(), start=1)
        if compiled.search(line)
    ]


def test_the_service_never_imports_the_web() -> None:
    assert _matching(r"^\s*(from|import)\s+web(\.|\s|$)") == []


def test_no_idempotency_state_is_kept_in_redis() -> None:
    idempotency = re.compile(r"idempot", re.IGNORECASE)
    redis_write = re.compile(r"redis|setnx|\bnx\s*=", re.IGNORECASE)
    assert [
        f"{name}:{number}"
        for name, text in SOURCES.items()
        for number, line in enumerate(text.splitlines(), start=1)
        if idempotency.search(line) and redis_write.search(line)
    ] == []


def test_inventory_is_written_only_through_the_stock_contract() -> None:
    assert (
        _matching(
            r"(update|insert\s+into|delete\s+from)\s+(public\.)?inventory\b",
            re.IGNORECASE,
        )
        == []
    )
    callers = [
        name for name, text in SOURCES.items() if "api_stock.consume_v1(" in text
    ]
    assert callers == ["store.py"]
