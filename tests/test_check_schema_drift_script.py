"""deploy/check-schema-drift.sh (#346).

`compare` is plain diffing, so it runs here. `dump` needs PostgreSQL and is not
run by the unit suite; what is checked about it is that it stays read-only and
that the repository schema still carries what the instance was missing.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/check-schema-drift.sh"


def _side(directory: Path, schema: str, privileges: str) -> Path:
    directory.mkdir()
    (directory / "schema.sql").write_text(schema)
    (directory / "privileges.txt").write_text(privileges)
    return directory


def _compare(reference: Path, live: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "compare", str(reference), str(live)],
        capture_output=True,
        text=True,
    )


_SCHEMA = "CREATE TRIGGER trg_experiment_exposure_treatment_only;\n"
_LOCKED = "experiment_exposure INSERT\nexperiment_exposure SELECT\n"
_OPEN = _LOCKED + "experiment_exposure DELETE\nexperiment_exposure UPDATE\n"


def test_identical_sides_report_no_drift(tmp_path: Path) -> None:
    reference = _side(tmp_path / "ref", _SCHEMA, _LOCKED)
    live = _side(tmp_path / "live", _SCHEMA, _LOCKED)

    result = _compare(reference, live)

    assert result.returncode == 0
    assert "no drift" in result.stdout


def test_a_missing_trigger_is_reported_as_drift(tmp_path: Path) -> None:
    reference = _side(tmp_path / "ref", _SCHEMA, _LOCKED)
    live = _side(tmp_path / "live", "", _LOCKED)

    result = _compare(reference, live)

    assert result.returncode == 1
    assert "trg_experiment_exposure_treatment_only" in result.stdout


def test_privileges_the_application_should_not_hold_are_reported(
    tmp_path: Path,
) -> None:
    """The second half of #346: UPDATE and DELETE still granted."""
    reference = _side(tmp_path / "ref", _SCHEMA, _LOCKED)
    live = _side(tmp_path / "live", _SCHEMA, _OPEN)

    result = _compare(reference, live)

    assert result.returncode == 1
    assert "+experiment_exposure DELETE" in result.stdout
    assert "+experiment_exposure UPDATE" in result.stdout


def test_a_missing_dump_is_an_error_not_a_pass(tmp_path: Path) -> None:
    reference = _side(tmp_path / "ref", _SCHEMA, _LOCKED)

    assert _compare(reference, tmp_path / "absent").returncode == 2


def test_the_script_reads_and_never_writes_the_database() -> None:
    text = SCRIPT.read_text()
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    ).upper()

    assert "--schema-only" in text
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "GRANT"):
        assert not re.search(
            rf"\b{verb}\b", code
        ), f"{verb} in a script that must be read-only"


def test_privilege_dump_checks_effective_role_privileges() -> None:
    """PUBLIC and inherited grants must not hide application-role drift."""
    text = SCRIPT.read_text()

    assert "has_table_privilege" in text
    assert "grantee = :'role'" not in text


def test_the_repository_schema_carries_what_the_instance_lacked() -> None:
    """#346's diagnosis, so a reference build is the right thing to compare to."""
    schema = (ROOT / "sql/01_schema.sql").read_text()

    assert "CREATE FUNCTION fn_experiment_exposure_treatment_only" in schema
    assert "trg_experiment_exposure_treatment_only" in schema
    assert "REVOKE UPDATE, DELETE ON experiment_assignment FROM retail_app;" in schema
    assert "REVOKE UPDATE, DELETE ON experiment_exposure FROM retail_app;" in schema
