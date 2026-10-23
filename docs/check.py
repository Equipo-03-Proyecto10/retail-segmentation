"""Check documentation facts that must stay synchronized with repository sources."""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUSINESS_RULES = Path("docs/business-rules.md")
DATA_MODEL = Path("docs/data-model.md")
SCHEMA = Path("sql/01_schema.sql")
TABLE_COUNT_DOCUMENTS = (
    Path("docs/user-stories.md"),
    Path("docs/infra.md"),
    Path("docs/demo-system-guide.md"),
    Path("docs/architecture/application.d2"),
)

README = Path("README.md")
ARCHITECTURE_DIR = Path("docs/architecture")

# [text](target) and ![alt](target); the target ends at whitespace or ")".
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)[^)]*\)")
_CODE_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.IGNORECASE)

_RULE_HEADING = re.compile(r"^### (?P<rule>RN-\d+[a-z]?)\s+—", re.MULTILINE)
_TABLE_COUNT = re.compile(r"(?<!\d)(?P<count>\d+)[ \t]+tables\b", re.IGNORECASE)
_ENTITY_SECTION = re.compile(
    r"^### Entities[ \t]*\n(?P<body>.*?)(?=^### )", re.MULTILINE | re.DOTALL
)
_ENTITY_TABLE = re.compile(
    r"^\|[ \t]*`(?P<table>[A-Za-z_][A-Za-z0-9_]*)`[ \t]*\|", re.MULTILINE
)
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


def _data_model_tables(data_model: str, errors: list[str]) -> tuple[str, ...]:
    match = _ENTITY_SECTION.search(data_model)
    if not match:
        errors.append(f"{DATA_MODEL} has no '### Entities' section")
        return ()
    tables = [
        table.group("table") for table in _ENTITY_TABLE.finditer(match.group("body"))
    ]
    duplicates = sorted({table for table in tables if tables.count(table) > 1})
    if duplicates:
        errors.append(
            f"{DATA_MODEL} entity list repeats tables: {', '.join(duplicates)}"
        )
    return tuple(dict.fromkeys(tables))


def _relative_links(text: str) -> list[tuple[int, str]]:
    """Every relative link target outside code fences, with its line number."""
    links = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _CODE_FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _MARKDOWN_LINK.finditer(line):
            target = match.group("target").strip("<>")
            if target and not _EXTERNAL.match(target):
                links.append((number, target))
    return links


def _check_links(root: Path, errors: list[str]) -> int:
    """Every relative link in README.md and docs/architecture/*.md must resolve."""
    documents = [README] if (root / README).is_file() else []
    architecture = root / ARCHITECTURE_DIR
    if architecture.is_dir():
        documents += sorted(
            path.relative_to(root) for path in architecture.glob("*.md")
        )
    checked = 0
    for document in documents:
        text = _read(root, document, errors)
        for line, target in _relative_links(text):
            checked += 1
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue
            resolved = (root / document).parent / path
            if not resolved.exists():
                errors.append(
                    f"{document}:{line} links to {target}, which does not exist"
                )
    return checked


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

    data_model_text = _read(root, DATA_MODEL, errors)
    model_tables = _data_model_tables(data_model_text, errors)
    missing_tables = sorted(set(tables) - set(model_tables))
    extra_tables = sorted(set(model_tables) - set(tables))
    if missing_tables:
        errors.append(
            f"{DATA_MODEL} entity list is missing public schema tables: "
            f"{', '.join(missing_tables)}"
        )
    if extra_tables:
        errors.append(
            f"{DATA_MODEL} entity list names tables not created in public: "
            f"{', '.join(extra_tables)}"
        )
    report.append(f"Data-model entity tables: {len(model_tables)}")

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

    report.append(f"Relative links checked: {_check_links(root, errors)}")
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
