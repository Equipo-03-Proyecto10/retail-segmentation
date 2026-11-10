"""Fixtures and the results file of the parity harness (F20-07, F20-08).

Each path is skipped, with its reason, when its configuration or its service
is missing; nothing is faked. At the end of the session the cases are written
to PARITY_RESULTS (default test-results/parity.json), the file the
consolidated report (F20-03) reads, next to the JUnit XML pytest writes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.parity import stack
from tests.parity.compare import (
    AuditDifference,
    CaseResult,
    Difference,
    write_results,
)

ROOT = Path(__file__).resolve().parents[2]
_results: list[CaseResult] = []
_differences: dict[str, list[Difference | AuditDifference]] = {}
# The files whose cases are parity cases; test_compare.py is not one.
_CASE_FILES = {"test_parity.py", "test_audit_parity.py"}


def _missing(*names: str) -> list[str]:
    return [n for n in names if not stack.env(n)]


@pytest.fixture(scope="session")
def db():
    """Parameterized queries on the throwaway stack's PostgreSQL."""

    if missing := _missing("PARITY_DATABASE_URL"):
        pytest.skip("not set: " + ", ".join(missing))
    import psycopg

    connection = psycopg.connect(stack.env("PARITY_DATABASE_URL"), autocommit=True)

    def run(sql: str, params: tuple = ()) -> list[tuple]:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall() if cursor.description else []

    yield run
    connection.close()


@pytest.fixture(scope="session")
def web(db) -> stack.WebPath:
    if missing := _missing("PARITY_WEB_URL", "PARITY_WEB_EMAIL", "PARITY_WEB_PASSWORD"):
        pytest.skip("web path: not set: " + ", ".join(missing))
    return stack.WebPath(
        stack.env("PARITY_WEB_URL"),
        stack.env("PARITY_WEB_EMAIL"),
        stack.env("PARITY_WEB_PASSWORD"),
        db,
    )


@pytest.fixture(scope="session")
def service() -> stack.ServicePath:
    absent = [
        f"services/{n}/"
        for n in ("auth", "sales", "stock")
        if not (ROOT / "services" / n).is_dir()
    ]
    if absent:
        pytest.skip("service path: waiting for " + ", ".join(absent))
    if missing := _missing(
        "PARITY_GATEWAY_URL", "PARITY_SERVICE_EMAIL", "PARITY_SERVICE_PASSWORD"
    ):
        pytest.skip("service path: not set: " + ", ".join(missing))
    return stack.ServicePath(
        stack.env("PARITY_GATEWAY_URL"),
        stack.env("PARITY_SERVICE_EMAIL"),
        stack.env("PARITY_SERVICE_PASSWORD"),
    )


@pytest.fixture(scope="session")
def audit() -> stack.AuditPath:
    absent = [
        f"services/{n}/"
        for n in ("auth", "audit-trail", *stack.AuditPath.WRITERS)
        if not (ROOT / "services" / n).is_dir()
    ]
    if absent:
        pytest.skip("audit path: waiting for " + ", ".join(absent))
    if missing := _missing(
        "PARITY_GATEWAY_URL", "PARITY_WEB_EMAIL", "PARITY_WEB_PASSWORD"
    ):
        pytest.skip("audit path: not set: " + ", ".join(missing))
    return stack.AuditPath(
        stack.env("PARITY_GATEWAY_URL"),
        stack.env("PARITY_WEB_EMAIL"),
        stack.env("PARITY_WEB_PASSWORD"),
    )


@pytest.fixture
def differences(request) -> list[Difference]:
    """Where a case collects what differs, so it is printed and recorded."""

    found: list[Difference | AuditDifference] = []
    _differences[request.node.name] = found
    return found


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if Path(str(item.fspath)).parent != Path(__file__).parent:
        return
    if item.fspath.basename not in _CASE_FILES:
        return
    if report.when == "call" or (report.when == "setup" and report.skipped):
        status = "skipped" if report.skipped else report.outcome
        detail = ""
        if report.skipped and isinstance(report.longrepr, tuple):
            detail = report.longrepr[2].removeprefix("Skipped: ")
        elif report.failed:
            detail = str(call.excinfo.value) if call.excinfo else ""
        _results.append(
            CaseResult(item.name, status, detail, list(_differences.get(item.name, [])))
        )


def pytest_sessionfinish(session, exitstatus) -> None:
    if _results:
        write_results(
            _results, Path(os.environ.get("PARITY_RESULTS", "test-results/parity.json"))
        )
