"""Every table with the `fn_audit` trigger is named by audit-trail (#1023, ADR-0082).

`fn_audit` records a table's name in `audit_log.entity`, and audit-trail lists
and reads every entry. So its OpenAPI `AuditedEntity`, its XSD `AuditedEntity`,
the service's `ENTITIES` and the desktop client's list must name exactly the
tables that carry the trigger. The set comes from the database loaded from
`sql/01_schema.sql` (`pg_trigger` joined to `pg_proc`), so a trigger attached
by a plain `CREATE TRIGGER`, a `DO` loop or a helper counts the same.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.postgres

AUDITED = """
    SELECT n.nspname, c.relname
      FROM pg_trigger t
      JOIN pg_proc p ON p.oid = t.tgfoid
      JOIN pg_class c ON c.oid = t.tgrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE p.proname = 'fn_audit' AND NOT t.tgisinternal
     ORDER BY 1, 2
"""


@pytest.fixture(scope="module")
def audited_tables(owner_url: str) -> list[tuple[str, str]]:
    with psycopg.connect(owner_url) as connection:
        return [(row[0], row[1]) for row in connection.execute(AUDITED).fetchall()]


def _openapi_enum() -> set[str]:
    # Read without PyYAML: CI's database job installs web/requirements-dev.txt
    # only, and this guard must run there, where the database is. The schema is
    # a block under components.schemas whose `enum:` is a plain list of names.
    text = (ROOT / "contracts/audit-trail/openapi.yaml").read_text()
    block = re.search(
        r"^    AuditedEntity:\n(?P<body>(?:      .*\n|\n)+)", text, flags=re.MULTILINE
    )
    assert block, "AuditedEntity is not a schema under components.schemas"
    listed = re.search(
        r"^      enum:\n(?P<items>(?:        - .+\n)+)",
        block["body"],
        flags=re.MULTILINE,
    )
    assert listed, "AuditedEntity has no enum list"
    return {
        line.strip()[2:].strip()
        for line in listed["items"].splitlines()
        if line.strip()
    }


def _xsd_enum() -> set[str]:
    xs = "{http://www.w3.org/2001/XMLSchema}"
    tree = ET.parse(ROOT / "contracts/audit-trail/audit-trail.xsd")
    for simple in tree.getroot().iter(f"{xs}simpleType"):
        if simple.get("name") == "AuditedEntity":
            return {e.get("value") for e in simple.iter(f"{xs}enumeration")}
    raise AssertionError("audit-trail.xsd has no AuditedEntity")


def _service_entities() -> set[str]:
    source = (ROOT / "services/audit-trail/src/lib.rs").read_text()
    block = re.search(r"pub const ENTITIES: \[&str; (\d+)\] = \[(.*?)\];", source, re.S)
    assert block, "services/audit-trail/src/lib.rs has no ENTITIES"
    names = re.findall(r'"([a-z_]+)"', block.group(2))
    assert int(block.group(1)) == len(names), "the declared length of ENTITIES is wrong"
    return set(names)


def _desktop_entities() -> set[str]:
    source = (ROOT / "clients/desktop/src/shared/api.ts").read_text()
    block = re.search(r"AUDITED_ENTITIES = \[(.*?)\] as const", source, re.S)
    assert block, "clients/desktop/src/shared/api.ts has no AUDITED_ENTITIES"
    return set(re.findall(r"'([a-z_]+)'", block.group(1)))


def test_a_table_name_is_audited_in_one_schema_only(
    audited_tables: list[tuple[str, str]],
) -> None:
    """`entity` is the bare name: two audited tables sharing one would be ambiguous."""
    names = [name for _, name in audited_tables]
    assert len(names) == len(set(names)), audited_tables
    assert len(names) >= 25


@pytest.mark.parametrize(
    ("where", "read"),
    [
        ("contracts/audit-trail/openapi.yaml AuditedEntity", _openapi_enum),
        ("contracts/audit-trail/audit-trail.xsd AuditedEntity", _xsd_enum),
        ("services/audit-trail/src/lib.rs ENTITIES", _service_entities),
        ("clients/desktop/src/shared/api.ts AUDITED_ENTITIES", _desktop_entities),
    ],
)
def test_the_contract_and_its_lists_name_exactly_the_audited_tables(
    audited_tables: list[tuple[str, str]], where: str, read
) -> None:
    audited = {name for _, name in audited_tables}
    named = read()
    assert audited - named == set(), (
        f"{where} does not name audited tables {sorted(audited - named)}: "
        "add them to the contract, the XSD, ENTITIES and the desktop list (ADR-0082)"
    )
    assert (
        named - audited == set()
    ), f"{where} names {sorted(named - audited)}, which carry no fn_audit trigger"
