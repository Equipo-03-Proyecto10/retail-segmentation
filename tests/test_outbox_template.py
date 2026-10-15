"""The outbox template in sql/01_schema.sql works as written (F13-07, #434).

The template is the only copy, so it is rendered from the schema file itself
for a throwaway boundary, inside one transaction that is always rolled back.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sql/01_schema.sql"
CHECK_GRANTS = ROOT / "contracts/db/check_grants.sh"

# A throwaway boundary: its roles and captured table exist only in the test.
SETUP = """
CREATE ROLE definer_zz_probe NOLOGIN;
CREATE ROLE svc_zz_probe NOLOGIN;
CREATE TABLE public.zz_probe_item (item_id integer PRIMARY KEY, label text);
"""


def _template() -> str:
    text = SCHEMA.read_text()
    block = text.split("-- BEGIN OUTBOX TEMPLATE\n", 1)[1]
    block = block.split("-- END OUTBOX TEMPLATE", 1)[0]
    lines = block.splitlines()
    # The prose above the first statement explains the template; the SQL
    # starts at CREATE SCHEMA.
    start = next(
        i for i, line in enumerate(lines) if line.startswith("-- CREATE SCHEMA")
    )
    return "\n".join(
        line[3:] if line.startswith("-- ") else "" for line in lines[start:]
    )


def _rendered() -> str:
    return (
        _template()
        .replace("<suffix>", "zz_probe")
        .replace("<table>", "public.zz_probe_item")
        .replace("<entity_column>", "item_id")
        .replace("<event>", "changed")
    )


def test_the_template_has_no_placeholder_left_once_rendered() -> None:
    assert "<" not in _rendered().replace("<>", "")


def test_a_web_write_adds_exactly_one_outbox_row(owner_url: str) -> None:
    with psycopg.connect(owner_url) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(SETUP)
                cur.execute(_rendered())
                cur.execute(_rendered())  # a boundary section is re-runnable
                cur.execute("SET ROLE retail_app")
                cur.execute("INSERT INTO public.zz_probe_item VALUES (7, 'probe')")
                cur.execute("RESET ROLE")
                cur.execute(
                    "SELECT stream, event_type, entity_id FROM zz_probe_events.outbox"
                )
                assert cur.fetchall() == [("stream:zz_probe.v1.changed", "INSERT", "7")]
                cur.execute(
                    "SELECT has_schema_privilege("
                    "'retail_app', 'zz_probe_events', 'USAGE')"
                )
                assert cur.fetchone() == (False,)
        finally:
            conn.rollback()


def test_the_capture_function_follows_the_definer_rule(
    owner_url: str, tmp_path: Path
) -> None:
    prelude = tmp_path / "template.sql"
    prelude.write_text(SETUP + _rendered())
    result = subprocess.run(
        ["bash", str(CHECK_GRANTS), "--prelude", str(prelude), "--only", "6"],
        env=dict(os.environ, PGDATABASE=owner_url),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
