"""Generate the reviewable M1 physical ERD from a migrated PostgreSQL schema.

The database is the source of truth for columns, types, nullability, keys,
foreign keys, and PostgreSQL-specific objects.  Human-authored table purposes
live here because PostgreSQL reflection cannot invent business intent.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs/architecture/postgresql-physical-model.md"

TABLE_PURPOSES = {
    "audit_log": "Append-only audit trail for every state-changing operation.",
    "category": "Hierarchical product category, including recursive parentage.",
    "consent_purpose": "Stable lookup of purposes for which consent is collected.",
    "consent_record": "Versioned customer consent intervals by purpose and notice.",
    "customer": "Customer master data; segmentation membership is deliberately absent.",
    "customer_rfm_snapshot": "Immutable customer features computed by one RFM run.",
    "customer_segment_assignment": "Bi-temporal, append-oriented segment assignment history.",
    "file_object": "Metadata registry for externally stored files and evidence.",
    "ingestion_run": "Auditable status and counts for one source-file ingestion.",
    "inventory_availability": "Product availability by optional store and channel.",
    "password_reset_token": "Single-use, expiring password-reset credential metadata.",
    "permission": "Stable permission catalogue used by role-based authorization.",
    "privacy_notice_version": "Immutable version of a privacy notice shown to customers.",
    "product": "Product master data and extensible JSON attributes.",
    "rfm_run": "One reproducible feature-computation window and execution.",
    "role": "Authorization role with global or store scope semantics.",
    "role_permission": "Many-to-many assignment of permissions to roles.",
    "sales_channel": "Stable purchase-channel dimension, separate from store.",
    "sales_transaction": "Idempotently ingested sale or return header.",
    "sales_transaction_line": "Transaction item with category captured at sale time.",
    "segment": "Run-scoped cluster mapped to a stable segment label.",
    "segment_label": "Stable business identity for comparing segments across runs.",
    "segmentation_model_run": "Reproducible clustering execution over one RFM run.",
    "store": "Retail store master data.",
    "system_setting": "Audited runtime setting with structured JSON value.",
    "user_account": "Staff or customer login identity and account state.",
    "user_role": "Role assignment with optional store scope and assigning actor.",
}

CRITICAL_CONSTRAINTS = (
    "ex_customer_segment_assignment_no_overlap",
    "ck_customer_segment_assignment_validity",
    "ck_customer_segment_assignment_supersede",
    "ex_consent_record_no_overlap",
)
CRITICAL_INDEXES = (
    "ix_customer_segment_assignment_customer_valid",
    "ix_customer_segment_assignment_open",
    "ix_customer_segment_assignment_run",
    "ix_segmentation_model_run_completed",
)


@dataclass(frozen=True)
class Column:
    name: str
    postgres_type: str
    nullable: bool
    keys: tuple[str, ...]


@dataclass(frozen=True)
class ForeignKey:
    name: str
    child_table: str
    child_columns: tuple[str, ...]
    parent_table: str
    parent_columns: tuple[str, ...]
    nullable: bool
    unique: bool


@dataclass(frozen=True)
class Table:
    name: str
    purpose: str
    columns: tuple[Column, ...]


@dataclass(frozen=True)
class Schema:
    tables: tuple[Table, ...]
    foreign_keys: tuple[ForeignKey, ...]
    constraints: tuple[tuple[str, str, str], ...]
    indexes: tuple[tuple[str, str, str], ...]
    triggers: tuple[tuple[str, str, str], ...]


def _load_dotenv() -> None:
    env_file = REPOSITORY_ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _column_types(connection) -> dict[tuple[str, str], str]:
    rows = connection.execute(
        text(
            """
            SELECT c.relname AS table_name,
                   a.attname AS column_name,
                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p')
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        )
    )
    return {(row.table_name, row.column_name): row.data_type for row in rows}


def _named_definitions(connection, query: str) -> tuple[tuple[str, str, str], ...]:
    return tuple(tuple(row) for row in connection.execute(text(query)))


def reflect_schema(connection) -> Schema:
    """Reflect the public schema and the PostgreSQL objects Mermaid cannot show."""
    inspector = inspect(connection)
    table_names = tuple(
        sorted(
            name
            for name in inspector.get_table_names(schema="public")
            if name != "alembic_version"
        )
    )
    missing_purposes = sorted(set(table_names) - TABLE_PURPOSES.keys())
    stale_purposes = sorted(TABLE_PURPOSES.keys() - set(table_names))
    if missing_purposes or stale_purposes:
        raise RuntimeError(
            "TABLE_PURPOSES must match the live schema exactly; "
            f"missing={missing_purposes}, stale={stale_purposes}"
        )

    pg_types = _column_types(connection)
    tables: list[Table] = []
    foreign_keys: list[ForeignKey] = []

    for table_name in table_names:
        raw_columns = inspector.get_columns(table_name, schema="public")
        pk_columns = set(
            inspector.get_pk_constraint(table_name, schema="public").get(
                "constrained_columns", ()
            )
        )
        explicit_unique_sets = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints(table_name, schema="public")
            if item.get("column_names")
        }
        unique_sets = set(explicit_unique_sets)
        if pk_columns:
            unique_sets.add(tuple(sorted(pk_columns)))

        raw_fks = inspector.get_foreign_keys(table_name, schema="public")
        fk_columns = {
            column
            for foreign_key in raw_fks
            for column in foreign_key["constrained_columns"]
        }

        columns = []
        nullable_by_name = {}
        for raw_column in raw_columns:
            name = raw_column["name"]
            nullable = bool(raw_column["nullable"])
            nullable_by_name[name] = nullable
            keys = []
            if name in pk_columns:
                keys.append("PK")
            if name in fk_columns:
                keys.append("FK")
            if any(len(item) == 1 and item[0] == name for item in explicit_unique_sets):
                keys.append("UK")
            columns.append(
                Column(
                    name=name,
                    postgres_type=pg_types[(table_name, name)],
                    nullable=nullable,
                    keys=tuple(keys),
                )
            )

        tables.append(Table(table_name, TABLE_PURPOSES[table_name], tuple(columns)))
        for raw_fk in raw_fks:
            child_columns = tuple(raw_fk["constrained_columns"])
            foreign_keys.append(
                ForeignKey(
                    name=raw_fk.get("name") or "unnamed_fk",
                    child_table=table_name,
                    child_columns=child_columns,
                    parent_table=raw_fk["referred_table"],
                    parent_columns=tuple(raw_fk["referred_columns"]),
                    nullable=any(nullable_by_name[name] for name in child_columns),
                    unique=child_columns in unique_sets,
                )
            )

    constraints = _critical_constraints(connection)
    indexes = _critical_indexes(connection)
    triggers = _named_definitions(
        connection,
        """
        SELECT tg.tgname, tbl.relname, pg_get_triggerdef(tg.oid, true)
        FROM pg_trigger tg
        JOIN pg_class tbl ON tbl.oid = tg.tgrelid
        JOIN pg_namespace n ON n.oid = tbl.relnamespace
        WHERE n.nspname = 'public' AND NOT tg.tgisinternal
        ORDER BY tbl.relname, tg.tgname
        """,
    )
    return Schema(
        tables=tuple(tables),
        foreign_keys=tuple(
            sorted(
                foreign_keys,
                key=lambda fk: (fk.parent_table, fk.child_table, fk.child_columns),
            )
        ),
        constraints=constraints,
        indexes=indexes,
        triggers=triggers,
    )


def _critical_constraints(connection) -> tuple[tuple[str, str, str], ...]:
    rows = connection.execute(
        text(
            """
            SELECT c.conname,
                   t.relname,
                   pg_get_constraintdef(c.oid, true)
                   || CASE
                        WHEN c.condeferrable AND c.condeferred
                            THEN ' DEFERRABLE INITIALLY DEFERRED'
                        WHEN c.condeferrable
                            THEN ' INITIALLY IMMEDIATE'
                        ELSE ''
                      END
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public' AND c.conname = ANY(:names)
            ORDER BY c.conname
            """
        ),
        {"names": list(CRITICAL_CONSTRAINTS)},
    )
    found = tuple(tuple(row) for row in rows)
    _require_names("critical constraints", CRITICAL_CONSTRAINTS, found)
    return found


def _critical_indexes(connection) -> tuple[tuple[str, str, str], ...]:
    rows = connection.execute(
        text(
            """
            SELECT indexname, tablename, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = ANY(:names)
            ORDER BY indexname
            """
        ),
        {"names": list(CRITICAL_INDEXES)},
    )
    found = tuple(tuple(row) for row in rows)
    _require_names("critical indexes", CRITICAL_INDEXES, found)
    return found


def _require_names(
    label: str, expected: tuple[str, ...], rows: tuple[tuple[str, str, str], ...]
) -> None:
    missing = sorted(set(expected) - {row[0] for row in rows})
    if missing:
        raise RuntimeError(f"Live schema is missing {label}: {', '.join(missing)}")


def _mermaid_type(postgres_type: str, nullable: bool) -> str:
    """Return a legal Mermaid token while retaining the exact type in a comment."""
    aliases = {
        "timestamp with time zone": "timestamptz",
        "timestamp without time zone": "timestamp",
        "character varying": "varchar",
        "double precision": "float8",
    }
    token = postgres_type.lower()
    for source, replacement in aliases.items():
        token = token.replace(source, replacement)
    token = token.replace(",", "_").replace(" ", "_")
    token = re.sub(r"[^a-z0-9_()\[\]-]", "_", token)
    if nullable:
        token += "?"
    return token


def _escape_comment(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")


def _relationship(fk: ForeignKey) -> str:
    parent_end = "o|" if fk.nullable else "||"
    child_end = "o|" if fk.unique else "o{"
    role = ", ".join(fk.child_columns)
    return (
        f"    {fk.parent_table} {parent_end}--{child_end} {fk.child_table} "
        f': "{role}"'
    )


def render(schema: Schema) -> str:
    lines = [
        "# PostgreSQL physical model — Milestone 1",
        "",
        "<!-- Generated by web/scripts/generate_postgresql_erd.py. Do not edit. -->",
        "",
        "This is the complete physical view of the 27-table M1 PostgreSQL schema.",
        "It is generated from a database migrated to Alembic `head`; the diagram",
        "therefore cannot silently diverge from the executable DDL. The conceptual",
        "and logical models remain in [`postgresql-model.md`](../data/postgresql-model.md#4-conceptual-model).",
        "",
        "Every relationship label is the child-side FK role. Crow's-foot markers",
        "are derived from FK nullability and uniqueness (`||` exactly one, `o|` zero",
        "or one, `o{` zero or many). Exact PostgreSQL types appear in each attribute",
        "comment because Mermaid type tokens cannot contain commas or spaces.",
        "",
        "```mermaid",
        "erDiagram",
        "    direction LR",
    ]
    lines.extend(_relationship(fk) for fk in schema.foreign_keys)
    for table in schema.tables:
        lines.append(f"    {table.name} {{")
        for column in table.columns:
            key_text = f" {','.join(column.keys)}" if column.keys else ""
            nullability = "NULL" if column.nullable else "NOT NULL"
            comment = _escape_comment(
                f"PostgreSQL: {column.postgres_type}; {nullability}"
            )
            lines.append(
                f"        {_mermaid_type(column.postgres_type, column.nullable)} "
                f'{column.name}{key_text} "{comment}"'
            )
        lines.append("    }")
    lines.extend(["```", "", "## Table purposes", ""])
    lines.extend(f"- `{table.name}` — {table.purpose}" for table in schema.tables)
    lines.extend(
        [
            "",
            "## Load-bearing traceability rules",
            "",
            "Mermaid cannot encode exclusion predicates, deferrability, triggers,",
            "partial-index predicates, or transaction-level invariants. These rules",
            "are part of the model, not optional implementation notes:",
            "",
            "1. Segment assignments are never updated in place. A production run closes",
            "   the prior valid-time interval and inserts the next assignment.",
            "2. The closing `valid_to` and opening `valid_from` are the same instant:",
            "   `segmentation_model_run.completed_at`. This makes the half-open intervals",
            "   contiguous without overlap.",
            "3. `is_authoritative = false` permits champion/challenger assignments to",
            "   coexist. Only authoritative, non-superseded intervals are excluded from",
            "   overlapping.",
            "4. `valid_from`/`valid_to` are valid time; `recorded_at`/`superseded_at` are",
            "   decision time. Routine runs change the former; corrections change the latter.",
            "5. Migration compares `segment_label.code`, never run-scoped `segment.id`.",
            "",
            "See [`postgresql-model.md` §5.1](../data/postgresql-model.md#51-customer_segment_assignment--the-four-timestamps)",
            "for the temporal semantics and §3 D-01 through D-04 for their rationale.",
            "",
            "### Reflected critical constraints",
            "",
        ]
    )
    for name, table, definition in schema.constraints:
        lines.append(f"- `{table}.{name}`: `{definition}`")
    lines.extend(["", "### Reflected critical indexes", ""])
    for name, table, definition in schema.indexes:
        lines.append(f"- `{table}.{name}`: `{definition}`")
    lines.extend(["", "### Reflected business triggers", ""])
    for name, table, definition in schema.triggers:
        lines.append(f"- `{table}.{name}`: `{definition}`")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        help="Schema-owner SQLAlchemy URL (default: DATABASE_MIGRATION_URL)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when the committed artifact differs from reflection.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    _load_dotenv()
    database_url = args.database_url or os.environ.get("DATABASE_MIGRATION_URL")
    if not database_url:
        raise SystemExit(
            "DATABASE_MIGRATION_URL is required; migrate PostgreSQL to Alembic head first"
        )
    engine = create_engine(database_url)
    with engine.connect() as connection:
        generated = render(reflect_schema(connection))
    output = args.output.resolve()
    if args.check:
        current = output.read_text(encoding="utf-8") if output.exists() else ""
        if current != generated:
            print(
                f"{output} is stale; run web/scripts/generate_postgresql_erd.py",
                file=sys.stderr,
            )
            return 1
        print(f"{output} matches the migrated schema")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generated, encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
