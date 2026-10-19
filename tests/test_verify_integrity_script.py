"""``sql/verify_integrity.sql`` has the declared outcome for every case.

A case that inserts a transaction without `source_transaction_id` fails on its
NOT NULL (#209) before the constraint the case is about is ever evaluated, so
it looks like a pass while checking nothing (#262). The PostgreSQL test below
also rejects positive-case errors, negative-case successes and wrong SQLSTATEs.
"""

from __future__ import annotations

import bisect
import os
import re
import subprocess
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "sql/verify_integrity.sql"
SCRIPT = SCRIPT_PATH.read_text()

_CASE = re.compile(r"^\\echo '-- ([PN]\d+):", re.MULTILINE)
_EXPECTATION = re.compile(r"\[expect: ([0-9A-Z]{5}) [^]]+\]'$")
_PSQL_ERROR = re.compile(
    r"^psql:.*verify_integrity\.sql:(\d+): ERROR:\s+([0-9A-Z]{5}):",
    re.MULTILINE,
)
_PSQL_ENV = {
    "dbname": "PGDATABASE",
    "host": "PGHOST",
    "password": "PGPASSWORD",
    "port": "PGPORT",
    "user": "PGUSER",
}


def test_every_transaction_insert_supplies_a_source_transaction_id() -> None:
    columns = re.findall(r"INSERT INTO transaction \(([^)]*)\)", SCRIPT)

    assert columns
    assert all("source_transaction_id" in listed for listed in columns), columns


@pytest.mark.postgres
def test_every_integrity_case_has_its_declared_outcome(
    owner_url: str, postgres_binaries: dict[str, str]
) -> None:
    """Run the hand-runnable script and turn its expected errors into assertions."""
    env = dict(os.environ)
    for variable in _PSQL_ENV.values():
        env.pop(variable, None)
    for parameter, value in conninfo_to_dict(owner_url).items():
        if parameter in _PSQL_ENV:
            env[_PSQL_ENV[parameter]] = value

    result = subprocess.run(
        [
            postgres_binaries["psql"],
            "-X",
            "-v",
            "VERBOSITY=verbose",
            "-f",
            str(SCRIPT_PATH),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    cases = []
    for match in _CASE.finditer(SCRIPT):
        line = SCRIPT.count("\n", 0, match.start()) + 1
        declaration = SCRIPT.splitlines()[line - 1]
        expectation = _EXPECTATION.search(declaration)
        cases.append(
            (line, match.group(1), expectation.group(1) if expectation else None)
        )

    case_lines = [line for line, _, _ in cases]
    observed = {name: [] for _, name, _ in cases}
    errors_before_first_case = []
    for line_text, state in _PSQL_ERROR.findall(result.stderr):
        line = int(line_text)
        case_index = bisect.bisect_right(case_lines, line) - 1
        if case_index < 0:
            errors_before_first_case.append((line, state))
        else:
            observed[cases[case_index][1]].append(state)

    assert not errors_before_first_case, errors_before_first_case
    # Every case is judged before failing, so one run names every hole.
    # N32 catches its own refusals and reports them below.
    mismatches = []
    for _, name, expected_state in cases:
        expected = [] if name.startswith("P") or name == "N32" else [expected_state]
        if observed[name] != expected:
            mismatches.append(f"{name}: expected {expected}, got {observed[name]}")
    assert not mismatches, "\n".join(mismatches)

    for field in ("kind", "name", "description"):
        assert f"PASS: assigned arm {field} refused (SQLSTATE 23514)" in result.stderr
