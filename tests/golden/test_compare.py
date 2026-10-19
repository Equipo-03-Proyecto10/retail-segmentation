"""The drift check names what moved, and skips a port not yet captured (#470)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.golden import compare

FIXTURE = {
    "header": {"port": "users", "web_commit": "abc123", "captured_on": "2026-10-16"},
    "cases": [
        {"id": "a", "input": {"x": 1}, "outcome": {"outcome": "accepted"}},
        {
            "id": "b",
            "input": {"x": 2},
            "outcome": {"outcome": "refused", "reason": "no"},
        },
    ],
}


def _write(path: Path, fixture: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture))


def _setup(tmp_path: Path, fresh: dict) -> tuple[Path, Path]:
    services = tmp_path / "services"
    captured = tmp_path / "captured"
    _write(services / "users" / "tests" / "golden" / "users.json", FIXTURE)
    _write(captured / "users.json", fresh)
    return captured, services


def test_a_port_not_yet_captured_is_skipped(tmp_path: Path) -> None:
    lines = compare.compare_port("sales", tmp_path, tmp_path / "services")
    golden = tmp_path / "services" / "sales" / "tests" / "golden"
    assert lines == [f"sales: not yet captured ({golden} does not exist); skipped"]


def test_only_the_header_may_differ(tmp_path: Path) -> None:
    fresh = json.loads(json.dumps(FIXTURE))
    fresh["header"] = {
        "port": "users",
        "web_commit": "def456",
        "captured_on": "2027-01-01",
    }
    captured, services = _setup(tmp_path, fresh)
    assert compare.compare_port("users", captured, services) == []


def test_a_changed_outcome_names_the_fixture_the_case_and_the_commit(
    tmp_path: Path,
) -> None:
    fresh = json.loads(json.dumps(FIXTURE))
    fresh["cases"][1]["outcome"] = {"outcome": "accepted"}
    captured, services = _setup(tmp_path, fresh)
    [line] = compare.compare_port("users", captured, services)
    assert line.startswith("DRIFT ")
    assert "users/tests/golden/users.json" in line
    assert "case b" in line
    assert "captured from web abc123" in line


def test_a_missing_or_new_case_is_drift(tmp_path: Path) -> None:
    fresh = json.loads(json.dumps(FIXTURE))
    fresh["cases"] = [fresh["cases"][0], {"id": "c", "input": {}, "outcome": {}}]
    captured, services = _setup(tmp_path, fresh)
    lines = compare.compare_port("users", captured, services)
    assert any("case b" in line and "no longer captured" in line for line in lines)
    assert any("case c" in line and "absent from the fixture" in line for line in lines)


def test_main_fails_on_drift_and_passes_otherwise(tmp_path: Path, capsys) -> None:
    captured, services = _setup(tmp_path, FIXTURE)
    assert compare.main([str(captured), "--services-dir", str(services)]) == 0
    fresh = json.loads(json.dumps(FIXTURE))
    fresh["cases"][0]["input"] = {"x": 9}
    _write(captured / "users.json", fresh)
    assert compare.main([str(captured), "--services-dir", str(services)]) == 1
    assert "DRIFT" in capsys.readouterr().out
