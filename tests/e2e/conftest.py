"""The nightly demonstration chain's harness (F13-09).

It runs against the local distributed stack, `compose.distributed.yaml`, when
these are set, and every scenario is skipped otherwise:

    E2E_BASE_URL   the stack's gateway, http://127.0.0.1:8080
    E2E_COMPOSE    the compose file, to reach PostgreSQL and stop redis-state

Each scenario names the services it needs with `@pytest.mark.services(...)`.
While one of them has no services/<name>/ directory the scenario is skipped,
and the nightly job's summary lists it with what it waits for.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conformance.harness import Client, load_contract

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("E2E_BASE_URL", "")
_waiting: list[tuple[str, list[str]]] = []


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "services(*names): the services an end-to-end scenario needs "
        "(tests/e2e/conftest.py)",
    )


def pytest_collection_modifyitems(config, items) -> None:
    for item in items:
        if Path(str(item.fspath)).parent != Path(__file__).parent:
            continue
        if not BASE_URL:
            item.add_marker(pytest.mark.skip(reason="E2E_BASE_URL is not set"))
            continue
        marker = item.get_closest_marker("services")
        needed = marker.args if marker else ()
        missing = [name for name in needed if not (ROOT / "services" / name).is_dir()]
        if missing:
            _waiting.append((item.name, missing))
            reason = "waiting for " + ", ".join("services/" + n for n in missing)
            item.add_marker(pytest.mark.skip(reason=reason))


def pytest_sessionfinish(session, exitstatus) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary or not _waiting:
        return
    with open(summary, "a", encoding="utf-8") as out:
        out.write("### Scenarios skipped until their services exist\n\n")
        out.write("| Scenario | Waiting for |\n|---|---|\n")
        for name, missing in _waiting:
            out.write(f"| `{name}` | {', '.join(missing)} |\n")


@pytest.fixture(scope="session")
def auth() -> Client:
    return Client(BASE_URL, load_contract(ROOT / "contracts/auth"))


@pytest.fixture(scope="session")
def sales() -> Client:
    return Client(BASE_URL, load_contract(ROOT / "contracts/sales"))
