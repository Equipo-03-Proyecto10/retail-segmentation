"""Capture the web's outcomes for one ported rule (F13-11, ADR-0045).

    python -m tests.golden.capture --service NAME --out DIR

NAME is sales, recommendations, users or campaigns.

Runs against a database built by the three scripts, as the web's own role,
from DATABASE_URL or --database-url, and writes DIR/<service>.json.

Every case runs inside `_rolled_back`, an `atomic` owner from
web/db/transactions.py. The web's own `@atomic` functions join that owner
instead of committing, and the owner rolls the case back once its outcome is
recorded. psycopg's `connection.transaction()` could not do this: the web
commits through the connection, and inside a transaction block that raises
ProgrammingError.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import psycopg

from tests.golden.cases import Case, Context, campaigns, recommendations, sales, users
from web.db.transactions import atomic

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "sql/02_seed_30_per_table.sql"
PORTS: dict[str, ModuleType] = {
    "sales": sales,
    "recommendations": recommendations,
    "users": users,
    "campaigns": campaigns,
}


class _Recorded(Exception):
    """Carries a case's outcome out of the transaction it then rolls back."""

    def __init__(self, outcome: dict[str, Any]):
        super().__init__("recorded")
        self.outcome = outcome


@atomic
def _rolled_back(connection: psycopg.Connection, case: Case) -> None:
    raise _Recorded(case.run(connection))


def run_case(connection: psycopg.Connection, case: Case) -> dict[str, Any]:
    try:
        _rolled_back(connection, case)
    except _Recorded as recorded:
        return recorded.outcome
    raise AssertionError(f"{case.id}: returned without rolling back")


def read_context(connection: psycopg.Connection) -> Context:
    with connection.cursor() as cursor:
        cursor.execute("SELECT max(run_at) FROM segmentation_run")
        (anchor,) = cursor.fetchone()
    connection.rollback()
    if anchor is None:
        raise SystemExit("no segmentation run: load sql/02_seed_30_per_table.sql first")
    return Context(anchor=anchor)


def web_commit() -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        changed = subprocess.run(
            ["git", "status", "--porcelain", "--", "web", "sql", "tests/golden"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit}-dirty" if changed else commit


def capture(connection: psycopg.Connection, service: str) -> dict[str, Any]:
    port = PORTS[service]
    context = read_context(connection)
    cases = port.build(connection, context)
    connection.rollback()
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{service}: duplicate case ids")
    recorded = []
    for case in cases:
        entry: dict[str, Any] = {
            "id": case.id,
            "input": dict(case.input),
            "outcome": run_case(connection, case),
        }
        if case.service_differs:
            entry["service_differs"] = case.service_differs
        recorded.append(entry)
    return {
        "header": {
            "port": service,
            "web_commit": web_commit(),
            "captured_on": datetime.now(UTC).date().isoformat(),
            "seed_sha256": hashlib.sha256(SEED.read_bytes()).hexdigest(),
            "as_of": port.as_of(context),
        },
        "cases": recorded,
    }


def _plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"{type(value).__name__} is not JSON")


def dumps(fixture: dict[str, Any]) -> str:
    return (
        json.dumps(
            fixture, indent=2, sort_keys=True, default=_plain, ensure_ascii=False
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--service", required=True, choices=sorted(PORTS))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("set DATABASE_URL or pass --database-url")

    with psycopg.connect(args.database_url) as connection:
        fixture = capture(connection, args.service)
        connection.rollback()
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"{args.service}.json"
    target.write_text(dumps(fixture))
    print(f"{target}: {len(fixture['cases'])} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
