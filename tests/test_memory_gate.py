"""ADR-0040's memory gate over every compose project (F13-09)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# The SQL job installs only the web's requirements; the gate needs PyYAML.
pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / ".github/scripts/memory_gate.py"


def compose(path: Path, **limits: object) -> Path:
    lines = ["services:"]
    for name, limit in limits.items():
        lines.append(f"  {name}:")
        lines.append("    image: example")
        if limit is not None:
            lines.append(f"    mem_limit: {limit}")
    path.write_text("\n".join(lines) + "\n")
    return path


def gate(*files: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), *map(str, files)],
        capture_output=True,
        text=True,
    )


def test_the_repository_is_within_the_budget() -> None:
    result = subprocess.run(
        [sys.executable, str(GATE)], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


def test_exactly_3000_mb_passes(tmp_path: Path) -> None:
    infra = compose(tmp_path / "infra.yaml", state="96m", cache="160m", mongo="512m")
    service = compose(tmp_path / "svc.yaml", auth="2232m")
    result = gate(infra, service)
    assert result.returncode == 0, result.stdout
    assert "3000 MB" in result.stdout


def test_a_fixture_totalling_3001_mb_fails(tmp_path: Path) -> None:
    infra = compose(tmp_path / "infra.yaml", state="96m", cache="160m", mongo="512m")
    service = compose(tmp_path / "svc.yaml", auth="2233m")
    result = gate(infra, service)
    assert result.returncode == 1
    assert "3001 MB, above 3,000 MB" in result.stdout


def test_gigabytes_count_as_1024_mb(tmp_path: Path) -> None:
    assert gate(compose(tmp_path / "a.yaml", big="2g", small="952m")).returncode == 0
    assert gate(compose(tmp_path / "b.yaml", big="2g", small="953m")).returncode == 1


@pytest.mark.parametrize("limit", ["512M", "0.5g", "512mb", "512", None])
def test_a_limit_it_cannot_parse_fails(tmp_path: Path, limit: object) -> None:
    result = gate(compose(tmp_path / "svc.yaml", svc=limit))
    assert result.returncode == 1
    assert "svc has mem_limit" in result.stdout
