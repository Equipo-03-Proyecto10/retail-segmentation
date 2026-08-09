"""Unit tests for deterministic ERD rendering rules."""

from web.scripts.generate_postgresql_erd import (
    Column,
    ForeignKey,
    Schema,
    Table,
    _mermaid_type,
    render,
)


def test_mermaid_type_preserves_postgresql_name_and_marks_nullable() -> None:
    assert _mermaid_type("numeric(14,2)", False) == "numeric(14_2)"
    assert _mermaid_type("timestamp with time zone", True) == "timestamptz?"


def test_render_states_fk_roles_cardinality_and_exact_type() -> None:
    schema = Schema(
        tables=(
            Table(
                "parent",
                "Parent rows.",
                (Column("id", "bigint", False, ("PK",)),),
            ),
            Table(
                "child",
                "Child rows.",
                (
                    Column("id", "bigint", False, ("PK",)),
                    Column("parent_id", "numeric(14,2)", True, ("FK",)),
                ),
            ),
        ),
        foreign_keys=(
            ForeignKey(
                "fk_child_parent_id_parent",
                "child",
                ("parent_id",),
                "parent",
                ("id",),
                nullable=True,
                unique=False,
            ),
        ),
        constraints=(),
        indexes=(),
        triggers=(),
    )

    document = render(schema)

    assert 'parent o|--o{ child : "parent_id"' in document
    assert 'numeric(14_2)? parent_id FK "PostgreSQL: numeric(14,2); NULL"' in document
    assert "`child` — Child rows." in document
