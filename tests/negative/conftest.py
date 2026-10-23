"""Fixtures for negative checks against a running distributed stack.

Without MOSAIQ_BASE_URL every case is skipped, so the repository's plain
`pytest` stays green; the outside-in port probe needs MOSAIQ_PROBE_HOST
instead. Each run writes its section of docs/evidence/f18-04-negative-tests.md
(MOSAIQ_EVIDENCE_FILE elsewhere).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.negative.stack import EVIDENCE_FILE, Evidence, Service, Stack, registry


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # Cases marked run_last go after every other case, in collection order.
    last = [i for i in items if getattr(i, "function", None) and _last(i)]
    if last:
        items[:] = [i for i in items if i not in last] + last


def _last(item: pytest.Item) -> bool:
    return getattr(item.function, "negative_run_last", False)


def _evidence_file() -> Path:
    return Path(os.environ.get("MOSAIQ_EVIDENCE_FILE") or EVIDENCE_FILE)


@pytest.fixture(scope="session")
def deployed_stack() -> Iterator[Stack]:
    if not os.environ.get("MOSAIQ_BASE_URL"):
        pytest.skip("MOSAIQ_BASE_URL is not set; deployed-stack checks are disabled.")

    stack = Stack.from_environment()
    try:
        stack.provision()
        yield stack
    finally:
        stack.cleanup()


@pytest.fixture(scope="session")
def evidence(deployed_stack: Stack) -> Iterator[Evidence]:
    label = "Instance" if deployed_stack.is_instance else "Local stack"
    record = Evidence(label)
    yield record
    record.write(os.environ["MOSAIQ_BASE_URL"], _evidence_file())


@pytest.fixture(scope="session")
def services(deployed_stack: Stack) -> dict[str, Service]:
    return {service.name: service for service in registry()}


@pytest.fixture(scope="session")
def outside_evidence() -> Iterator[Evidence]:
    host = os.environ.get("MOSAIQ_PROBE_HOST")
    if not host:
        pytest.skip("MOSAIQ_PROBE_HOST is not set; the outside port probe is disabled.")
    record = Evidence("Instance, probed from outside")
    yield record
    record.write(host, _evidence_file())
