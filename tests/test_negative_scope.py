"""Which services tests/negative calls (#459).

A contract and a gateway route land before a service's implementation
(AGENTS.md, ADR-0035), so the suite tests only the services that exist. That
set must be the one the local stack runs, or a service would be either
skipped while it runs or called while nothing answers for it.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.negative.stack import (
    implemented,
    implemented_names,
    not_implemented_reason,
    registry_names,
)

ROOT = Path(__file__).resolve().parents[1]


def _stack_services() -> set[str]:
    text = (ROOT / "compose.distributed.yaml").read_text()
    return set(re.findall(r"path: \[services/([a-z0-9-]+)/compose\.yaml", text))


def test_the_suite_calls_exactly_the_services_the_local_stack_runs() -> None:
    assert set(implemented_names()) == _stack_services()


def test_every_service_the_stack_runs_is_registered() -> None:
    assert _stack_services() <= set(registry_names())


def test_a_registered_service_without_an_implementation_is_skipped_with_a_reason() -> (
    None
):
    pending = [name for name in registry_names() if not implemented(name)]
    for name in pending:
        reason = not_implemented_reason(name)
        assert name in reason and f"services/{name}/compose.yaml" in reason
