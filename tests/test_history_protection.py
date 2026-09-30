"""#350 (ADR-0029): what the application role may do to segment history, runs and
experiments, read from the schema text.

There is no PostgreSQL in the unit suite, so these check that the statements are
present and say what the decision says. That they do what they say is the job of
the opt-in application-role self-test in `sql/01_schema.sql`, cases N33-N36 of
`sql/verify_integrity.sql`, and CI's clean load of the three scripts.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "sql/01_schema.sql").read_text(encoding="utf-8")
CREATE = (ROOT / "sql/00_create_database.sql").read_text(encoding="utf-8")
VERIFY = (ROOT / "sql/verify_integrity.sql").read_text(encoding="utf-8")


def _table(name: str) -> str:
    start = SCHEMA.index(f"CREATE TABLE {name} (")
    return SCHEMA[start : SCHEMA.index("\n);", start)]


def test_the_application_role_cannot_delete_an_experiment_or_its_groups() -> None:
    """Cascades run as the owner, so revoking DELETE on the assignments alone left
    `DELETE FROM experiment` free to remove them."""
    assert "REVOKE DELETE ON experiment FROM retail_app;" in SCHEMA
    assert "REVOKE DELETE ON experiment_group FROM retail_app;" in SCHEMA


def test_an_experiment_can_still_be_edited() -> None:
    """The edit form updates the row before the first assignment."""
    assert not re.search(r"REVOKE[^;]*UPDATE[^;]*ON experiment FROM", SCHEMA)


def test_a_run_is_insert_only_for_the_application_role() -> None:
    assert "REVOKE UPDATE, DELETE ON segmentation_run FROM retail_app;" in SCHEMA


def test_history_grants_the_application_role_valid_to_and_nothing_else() -> None:
    assert (
        "REVOKE UPDATE, DELETE ON customer_segment_history FROM retail_app;" in SCHEMA
    )
    grants = re.findall(
        r"GRANT UPDATE \(([^)]*)\) ON customer_segment_history TO retail_app;", SCHEMA
    )
    assert grants == ["valid_to"]


def test_the_revoke_comes_before_the_column_grant() -> None:
    """Revoking a table privilege also revokes its column privileges, so the
    other order would leave the role with nothing."""
    revoke = SCHEMA.index("REVOKE UPDATE, DELETE ON customer_segment_history")
    grant = SCHEMA.index("GRANT UPDATE (valid_to) ON customer_segment_history")

    assert revoke < grant


def test_the_pipeline_still_only_sets_valid_to() -> None:
    """The one statement the column grant has to cover."""
    source = (ROOT / "web/db/segments.py").read_text(encoding="utf-8")
    statement = re.search(
        r"UPDATE customer_segment_history\s+SET (.*?)\s+WHERE", source, re.S
    )

    assert statement is not None
    assert statement.group(1).strip() == "valid_to = %s"
    assert len(re.findall(r"UPDATE customer_segment_history", source)) == 1


def test_no_application_code_deletes_what_the_role_can_no_longer_delete() -> None:
    forbidden = re.compile(
        r"DELETE\s+FROM\s+(experiment|experiment_group|segmentation_run|"
        r"customer_segment_history)\b|UPDATE\s+(segmentation_run|experiment_group)\b",
        re.I,
    )
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "web").rglob("*.py")
        if forbidden.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_a_history_row_can_only_be_closed_once() -> None:
    assert "CREATE TRIGGER trg_customer_segment_history_close_only" in SCHEMA
    assert "BEFORE UPDATE ON customer_segment_history" in SCHEMA
    body = SCHEMA[
        SCHEMA.index("CREATE FUNCTION fn_customer_segment_history_close_only") :
    ]
    body = body[: body.index("$$ LANGUAGE plpgsql;")]
    # Only valid_to (and the foreign key's own SET NULL on segment_id) may differ.
    assert "- 'valid_to' - 'segment_id'" in body
    assert "OLD.valid_to IS NOT NULL" in body
    assert "23514" in body


def test_scores_and_measures_are_bounded() -> None:
    table = _table("customer_segment_history")

    for check in (
        "CHECK (r_score BETWEEN 1 AND 5)",
        "CHECK (f_score BETWEEN 1 AND 5)",
        "CHECK (m_score BETWEEN 1 AND 5)",
        "CHECK (frequency_count >= 0)",
        "CHECK (monetary_total >= 0)",
    ):
        assert check in table


def test_one_customers_intervals_cannot_overlap() -> None:
    table = _table("customer_segment_history")

    assert "EXCLUDE USING gist" in table
    assert "customer_id WITH =" in table
    assert "tstzrange(valid_from, valid_to) WITH &&" in table


def test_the_extension_the_exclusion_needs_is_created_first() -> None:
    assert "CREATE EXTENSION IF NOT EXISTS btree_gist;" in CREATE


def test_the_application_role_self_test_expects_the_narrower_privileges() -> None:
    """Otherwise its ordinary-table loop would fail on the four tables."""
    loop = SCHEMA[SCHEMA.index("c.relname NOT IN (") :][:400]

    for table in (
        "experiment",
        "experiment_group",
        "segmentation_run",
        "customer_segment_history",
    ):
        assert f"'{table}'" in loop
    assert (
        "has_column_privilege('public.customer_segment_history', 'valid_to'" in SCHEMA
    )


def test_the_integrity_script_exercises_each_new_guard_as_the_owner() -> None:
    for case, sqlstate in (
        ("N33", "23514"),
        ("N34", "23P01"),
        ("N35", "23514"),
        ("N36", "23514"),
    ):
        assert re.search(rf"-- {case}: .*\[expect: {sqlstate} ", VERIFY), case
