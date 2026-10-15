"""contracts/db/check_grants.sh catches a grant in either direction (#434).

Every change runs inside the script's own transaction, which it rolls back,
so the shared test database keeps its grants.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "contracts/db/check_grants.sh"
GRANTS = ROOT / "contracts/db/grants.csv"


def _check(owner_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=dict(os.environ, PGDATABASE=owner_url),
        capture_output=True,
        text=True,
    )


def test_the_database_matches_grants_csv(owner_url: str) -> None:
    result = _check(owner_url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_a_grant_missing_from_the_csv_is_reported(
    owner_url: str, tmp_path: Path
) -> None:
    prelude = tmp_path / "extra.sql"
    prelude.write_text("GRANT SELECT ON public.customer TO svc_customers;\n")
    result = _check(owner_url, "--prelude", str(prelude))
    assert result.returncode != 0
    assert "> svc_customers,public.customer,SELECT" in result.stdout.splitlines()


def test_a_csv_row_with_no_grant_is_reported(owner_url: str, tmp_path: Path) -> None:
    grants = tmp_path / "grants.csv"
    grants.write_text(GRANTS.read_text() + "svc_stock,public.inventory,SELECT\n")
    result = _check(owner_url, "--grants", str(grants))
    assert result.returncode != 0
    assert "< svc_stock,public.inventory,SELECT" in result.stdout.splitlines()


def test_the_extra_grant_does_not_outlive_the_check(
    owner_url: str, tmp_path: Path
) -> None:
    prelude = tmp_path / "extra.sql"
    prelude.write_text("GRANT SELECT ON public.customer TO svc_customers;\n")
    _check(owner_url, "--prelude", str(prelude))
    assert _check(owner_url).stdout == ""
