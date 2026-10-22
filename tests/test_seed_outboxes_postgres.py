"""ADR-0054: bootstrap leaves no events, but ordinary writes still capture."""

import psycopg
import pytest
from psycopg import sql

pytestmark = pytest.mark.postgres


def test_every_outbox_is_empty_after_the_seed(owner_url: str) -> None:
    """Discover outboxes so a new boundary cannot silently miss seed cleanup."""
    with psycopg.connect(owner_url) as connection:
        tables = connection.execute(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE tablename = %s AND right(schemaname, 7) = %s ORDER BY 1",
            ("outbox", "_events"),
        ).fetchall()
        assert tables, "No outboxes found: run all three database scripts first"
        for schema, table in tables:
            count = connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
            ).fetchone()[0]
            assert count == 0, f"{schema}.{table} retains {count} seed events"


def test_post_seed_web_write_captures_transactionally(owner_url: str) -> None:
    """The web role still captures, and rollback removes both write and event."""
    with psycopg.connect(owner_url, autocommit=True) as observer:
        with psycopg.connect(owner_url) as writer:
            writer.execute("SET LOCAL ROLE retail_app")
            product_id = writer.execute(
                "SELECT product_id FROM product ORDER BY product_id LIMIT 1"
            ).fetchone()[0]
            writer.execute(
                "UPDATE product SET name = name WHERE product_id = %s", (product_id,)
            )
            # The web cannot read an outbox; reset to its owner to inspect
            # this transaction's captured event without committing it.
            writer.execute("RESET ROLE")
            events = writer.execute(
                "SELECT entity_id, relayed_at FROM products_events.outbox"
            ).fetchall()
            assert events == [(str(product_id), None)]
            assert (
                observer.execute(
                    "SELECT count(*) FROM products_events.outbox"
                ).fetchone()[0]
                == 0
            )
            writer.rollback()

        assert (
            observer.execute("SELECT count(*) FROM products_events.outbox").fetchone()[
                0
            ]
            == 0
        )
