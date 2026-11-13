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


def test_stock_is_restored_only_through_the_stock_contract() -> None:
    # A reversal gives stock back through api_stock.restock_v1 (ADR-0061), once
    # per resellable line and only from the store module, never by a write.
    callers = [
        name for name, text in SOURCES.items() if "api_stock.restock_v1(" in text
    ]
    assert callers == ["store.py"]
    assert _matching(r"stock\.stock_movement", re.IGNORECASE) == []


def test_only_the_service_writes_a_lines_running_total() -> None:
    # transaction_line.returned_quantity is written by this service alone, and
    # nothing else of a sale line is ever updated (ADR-0062).
    updates = [
        line
        for text in SOURCES.values()
        for line in re.findall(
            r"update\s+public\.transaction_line[\"'\s]+set\s+(\w+)", text, re.IGNORECASE
        )
    ]
    assert updates == ["returned_quantity"]


def test_one_rule_serves_a_phone_sale_and_a_row_of_a_file() -> None:
    # ADR-0070 section 4: the five ported checks live in one module, called by
    # POST /v1/sales and by the import worker; neither keeps a copy.
    callers = sorted(
        name for name, text in SOURCES.items() if "validate_sale_rules(" in text
    )
    assert callers == ["import_worker.py", "salerules.py", "service.py"]
    references = sorted(
        name for name, text in SOURCES.items() if "validate_sale_references(" in text
    )
    assert references == ["import_worker.py", "salerules.py", "service.py"]
    for name in ("import_worker.py", "rows.py", "imports.py"):
        text = SOURCES[name]
        assert "datetime.now(" not in text and "MAX_PRICE" not in text
        assert "registered_on" not in text and "is_active" not in text


def test_the_import_keeps_no_state_in_redis_and_the_worker_runs_no_relay() -> None:
    for name in ("import_worker.py", "rows.py", "imports.py"):
        assert not re.search(r"redis|xadd|OutboxRelay", SOURCES[name], re.IGNORECASE)
    assert "OutboxRelay" not in SOURCES["import_worker.py"]


def test_a_files_rows_take_stock_through_the_one_function_a_sale_does() -> None:
    assert "store.consume_stock(" in SOURCES["import_worker.py"]
    assert "consume_v1" not in SOURCES["import_worker.py"]
