"""The files the repository rules ask a service to hold."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_semver_and_the_kit_is_pinned_by_exact_tag():
    assert re.fullmatch(r"\d+\.\d+\.\d+", (ROOT / "VERSION").read_text().strip())
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert re.search(r"@kit-py/v\d+\.\d+\.\d+#subdirectory=packages/kit-py", pyproject)
    assert 'packages/kit-py"' in pyproject
    assert "file://" not in pyproject


def test_compose_publishes_on_loopback_with_the_contracted_limits():
    compose = (ROOT / "compose.yaml").read_text()
    assert '"127.0.0.1:5007:5007"' in compose
    assert "mem_limit: 160m" in compose
    assert "depends_on" not in re.sub(r"#.*", "", compose)
