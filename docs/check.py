"""Check documentation facts that must stay synchronized with repository sources."""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUSINESS_RULES = Path("docs/business-rules.md")
SCHEMA = Path("sql/01_schema.sql")
TABLE_COUNT_DOCUMENTS = (
    Path("docs/user-stories.md"),
    Path("docs/infra.md"),
    Path("docs/demo-system-guide.md"),
    Path("docs/architecture/application.d2"),
)

_RULE_HEADING = re.compile(r"^### (?P<rule>RN-\d+[a-z]?)\s+—", re.MULTILINE)
_TABLE_COUNT = re.compile(r"(?<!\d)(?P<count>\d+)[ \t]+tables\b", re.IGNORECASE)
_SQL_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_IDENTIFIER = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
_CREATE_TABLE = re.compile(
    rf"^[ \t]*CREATE[ \t]+TABLE[ \t]+"
    rf"(?:IF[ \t]+NOT[ \t]+EXISTS[ \t]+)?"
    rf"(?:(?P<schema>{_IDENTIFIER})[ \t]*\.[ \t]*)?"
    rf"(?P<table>{_IDENTIFIER})[ \t]*\(",
    re.IGNORECASE | re.MULTILINE,
)


def _read(root: Path, path: Path, errors: list[str]) -> str:
    try:
        return (root / path).read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"cannot read {path}: {error}")
        return ""


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _unquote(identifier: str) -> str:
    if identifier.startswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier.lower()


def _schema_tables(schema: str) -> tuple[str, ...]:
    """The web's own tables. A service's tables live in its own schema
    (ADR-0046) and are not what the documents count."""
    uncommented = _SQL_COMMENTS.sub("", schema)
    tables = []
    for match in _CREATE_TABLE.finditer(uncommented):
        if match.group("schema") and _unquote(match.group("schema")) != "public":
            continue
        tables.append(_unquote(match.group("table")))
    return tuple(dict.fromkeys(tables))


def check(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    report: list[str] = []

    rules_text = _read(root, BUSINESS_RULES, errors)
    occurrences: dict[str, list[int]] = defaultdict(list)
    for match in _RULE_HEADING.finditer(rules_text):
        occurrences[match.group("rule")].append(_line_number(rules_text, match.start()))
    for rule, lines in sorted(occurrences.items()):
        if len(lines) > 1:
            locations = ", ".join(f"{BUSINESS_RULES}:{line}" for line in lines)
            errors.append(f"duplicate business-rule id {rule}: {locations}")
    report.append(
        f"Business-rule ids: {sum(map(len, occurrences.values()))} headings, "
        f"{len(occurrences)} unique"
    )

    schema_text = _read(root, SCHEMA, errors)
    tables = _schema_tables(schema_text)
    schema_count = len(tables)
    report.append(f"Schema tables: {schema_count}")

    stated_counts = []
    for path in TABLE_COUNT_DOCUMENTS:
        text = _read(root, path, errors)
        matches = list(_TABLE_COUNT.finditer(text))
        if not matches:
            errors.append(f"{path} does not state a table count")
            continue
        for match in matches:
            count = int(match.group("count"))
            line = _line_number(text, match.start())
            stated_counts.append(f"{path}:{line}={count}")
            if count != schema_count:
                errors.append(
                    f"{path}:{line} states {count} tables; "
                    f"{SCHEMA} creates {schema_count}"
                )
    report.append(f"Documented table counts: {', '.join(stated_counts)}")
    return errors, report


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) > 1:
        print("usage: python docs/check.py [repository-root]", file=sys.stderr)
        return 2
    root = Path(arguments[0]).resolve() if arguments else ROOT
    errors, report = check(root)
    for line in report:
        print(line)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
