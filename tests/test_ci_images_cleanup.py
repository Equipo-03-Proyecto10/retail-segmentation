"""The test images are cleaned up after 14 days, never a service's newest (ADR-0080)."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ci_images_cleanup", ROOT / ".github/scripts/ci_images_cleanup.py"
)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def version(number: int, created_at: str) -> dict:
    return {"id": number, "created_at": created_at}


def test_versions_past_fourteen_days_go_and_recent_ones_stay() -> None:
    versions = [
        version(1, "2026-10-01T00:00:00Z"),
        version(2, "2026-11-05T11:59:59Z"),
        version(3, "2026-11-06T12:00:00Z"),
        version(4, "2026-11-19T00:00:00Z"),
    ]
    assert [v["id"] for v in cleanup.to_delete(versions, NOW)] == [2, 1]


def test_the_newest_version_stays_however_old_it_is() -> None:
    versions = [version(7, "2026-01-01T00:00:00Z"), version(8, "2026-02-01T00:00:00Z")]
    assert [v["id"] for v in cleanup.to_delete(versions, NOW)] == [7]
    assert cleanup.to_delete([version(9, "2025-01-01T00:00:00Z")], NOW) == []
    assert cleanup.to_delete([], NOW) == []


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for name in ("alpha", "beta"):
        (tmp_path / "services" / name).mkdir(parents=True)
        (tmp_path / "services" / name / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "services" / "draft").mkdir()
    return tmp_path


def fake_gh(packages: dict[str, list[dict]]) -> tuple[list[list[str]], object]:
    issued: list[list[str]] = []

    def run(arguments: list[str]) -> str:
        issued.append(arguments)
        if arguments[:2] == ["api", "--paginate"]:
            package = arguments[-1].split("/")[4]
            if package not in packages:
                raise cleanup.NotFound("gh: Package not found. (HTTP 404)")
            return "".join(json.dumps(v) + "\n" for v in packages[package])
        return ""

    return issued, run


def test_clean_deletes_only_in_test_image_packages_and_skips_missing_ones(
    root: Path,
) -> None:
    issued, run = fake_gh(
        {
            "mosaiq-ci-alpha": [
                version(10, "2026-11-19T00:00:00Z"),
                version(11, "2026-10-01T00:00:00Z"),
            ]
        }
    )
    report = cleanup.clean(root, "Equipo-03-Proyecto10", NOW, run=run)
    deletes = [a for a in issued if a[:3] == ["api", "-X", "DELETE"]]
    assert deletes == [
        [
            "api",
            "-X",
            "DELETE",
            "orgs/Equipo-03-Proyecto10/packages/container/mosaiq-ci-alpha/versions/11",
        ]
    ]
    listed = [a[-1] for a in issued if a[:2] == ["api", "--paginate"]]
    assert all("/packages/container/mosaiq-ci-" in path for path in listed)
    assert "mosaiq-ci-beta: no package yet" in report
    assert "mosaiq-ci-alpha: kept 1 of 2" in report


def test_a_dry_run_deletes_nothing(root: Path) -> None:
    issued, run = fake_gh({"mosaiq-ci-alpha": [version(1, "2026-01-01T00:00:00Z")] * 2})
    report = cleanup.clean(root, "o", NOW, run=run, dry_run=True)
    assert not [a for a in issued if "DELETE" in a]
    assert any("would delete version 1" in line for line in report)
