"""CI's per-service matrix and its required summary (F13-09, ADR-0040).

The matrix is derived from the changed paths by .github/scripts/service-matrix.sh,
never from a hand-written list. These tests feed it file lists against a
scratch repository layout.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/service-matrix.sh"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # stock and products are built; users has a contract but no service yet.
    for name in ("stock", "products"):
        (tmp_path / "services" / name).mkdir(parents=True)
        (tmp_path / "services" / name / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "services" / "draft").mkdir()
    (tmp_path / "contracts" / "users").mkdir(parents=True)
    return tmp_path


def matrix(repo: Path, *paths: str) -> list[str]:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="".join(path + "\n" for path in paths),
        capture_output=True,
        text=True,
        env={"ROOT": str(repo), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    return json.loads(result.stdout)


def summary(selected: str, result: str) -> int:
    return subprocess.run(
        ["bash", str(SCRIPT), "summary", selected, result],
        capture_output=True,
        text=True,
    ).returncode


def test_a_change_inside_one_service_selects_only_it(repo: Path) -> None:
    assert matrix(repo, "services/stock/src/main.rs") == ["stock"]


def test_a_change_to_a_built_services_contract_selects_it(repo: Path) -> None:
    assert matrix(repo, "contracts/products/openapi.yaml") == ["products"]


def test_a_contract_only_change_for_an_unbuilt_service_selects_nothing(
    repo: Path,
) -> None:
    assert matrix(repo, "contracts/users/openapi.yaml") == []


def test_a_service_directory_without_a_dockerfile_is_not_built(repo: Path) -> None:
    assert matrix(repo, "services/draft/README.md") == []


def test_the_common_schema_and_the_suite_select_every_built_service(
    repo: Path,
) -> None:
    assert matrix(repo, "contracts/common/v1/common.xsd") == ["products", "stock"]
    assert matrix(repo, "tests/conformance/test_conformance.py") == [
        "products",
        "stock",
    ]
    assert matrix(repo, ".github/workflows/services.yml") == ["products", "stock"]


def test_each_service_is_selected_once_and_in_order(repo: Path) -> None:
    assert matrix(
        repo,
        "services/stock/Cargo.toml",
        "contracts/stock/stock.xsd",
        "services/products/app/__init__.py",
    ) == ["products", "stock"]


def test_files_outside_services_and_contracts_select_nothing(repo: Path) -> None:
    assert matrix(repo, "contracts/registry.yaml", "contracts/db/grants.csv") == []
    assert matrix(repo, "web/app.py", "sql/01_schema.sql") == []


def test_a_docs_only_change_is_an_empty_matrix_and_a_green_summary(
    repo: Path,
) -> None:
    selected = matrix(repo, "docs/process.md", "README.md")
    assert selected == []
    # The matrix job is skipped when there is nothing to build.
    assert summary(json.dumps(selected), "skipped") == 0


@pytest.mark.parametrize(
    ("selected", "result", "status"),
    [
        ('["stock"]', "success", 0),
        ('["stock"]', "failure", 1),
        ('["stock"]', "cancelled", 1),
        ('["stock"]', "skipped", 1),
        ("[]", "skipped", 0),
    ],
)
def test_the_summary_passes_only_on_an_empty_or_green_matrix(
    selected: str, result: str, status: int
) -> None:
    assert summary(selected, result) == status
