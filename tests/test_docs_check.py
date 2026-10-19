"""Regression tests for the dependency-free documentation check (#460)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "docs/check.py"
COUNT_DOCUMENTS = (
    Path("docs/user-stories.md"),
    Path("docs/infra.md"),
    Path("docs/demo-system-guide.md"),
    Path("docs/architecture/application.d2"),
)


def _run(root: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), str(root)],
        capture_output=True,
        text=True,
    )


def _fixture(
    root: Path,
    *,
    rules: str = "### RN-01 — One\n### RN-02 — Two\n",
    documented_count: int = 2,
    model_tables: tuple[str, ...] = ("first_table", "second_table"),
) -> None:
    (root / "docs/architecture").mkdir(parents=True)
    (root / "sql").mkdir()
    (root / "docs/business-rules.md").write_text(rules, encoding="utf-8")
    (root / "docs/data-model.md").write_text(
        "### Entities\n\n"
        "| Entity | What it is |\n"
        "|---|---|\n"
        + "".join(f"| `{table}` | Fixture table |\n" for table in model_tables)
        + "\n### Relationships\n",
        encoding="utf-8",
    )
    (root / "sql/01_schema.sql").write_text(
        "CREATE TABLE first_table (id integer);\n"
        "-- CREATE TABLE example_only (id integer);\n"
        "CREATE TABLE IF NOT EXISTS svc_events.outbox (id integer);\n"
        "CREATE TABLE second_table (id integer);\n",
        encoding="utf-8",
    )
    for path in COUNT_DOCUMENTS:
        (root / path).write_text(
            f"The schema has {documented_count} tables.\n", encoding="utf-8"
        )


def test_repository_documentation_is_consistent() -> None:
    result = _run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Business-rule ids:" in result.stdout
    assert "Schema tables:" in result.stdout
    assert "Data-model entity tables:" in result.stdout
    assert "Documented table counts:" in result.stdout


def test_duplicate_ids_and_stale_table_counts_are_both_reported(
    tmp_path: Path,
) -> None:
    _fixture(
        tmp_path,
        rules="### RN-01 — One\n### RN-02 — Two\n### RN-01 — Other\n",
        documented_count=3,
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "duplicate business-rule id RN-01" in result.stderr
    assert "docs/business-rules.md:1" in result.stderr
    assert "docs/business-rules.md:3" in result.stderr
    assert (
        "docs/user-stories.md:1 states 3 tables; sql/01_schema.sql creates 2"
        in result.stderr
    )


def test_missing_and_extra_data_model_tables_are_both_reported(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path, model_tables=("first_table", "extra_table"))

    result = _run(tmp_path)

    assert result.returncode == 1
    assert (
        "docs/data-model.md entity list is missing public schema tables: second_table"
        in result.stderr
    )
    assert (
        "docs/data-model.md entity list names tables not created in public: extra_table"
        in result.stderr
    )
