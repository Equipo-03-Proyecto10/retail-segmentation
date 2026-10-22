"""One-time, timestamp-and-ID-scoped cleanup authorized by ADR-0054 (#597)."""

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

parser = argparse.ArgumentParser()
parser.add_argument("--dsn", required=True)
parser.add_argument("--seed-at", required=True)
parser.add_argument("--backup", required=True)
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
seed_at = datetime.fromisoformat(args.seed_at)
expected = {
    "categories_events": Counter({str(i): 1 for i in range(1, 31)}),
    "products_events": Counter({str(i): 1 for i in range(1, 41)}),
    "stock_events": Counter({f"{s}:{p}": 1 for s in range(1, 31) for p in range(1, 6)}),
    "stores_events": Counter({str(i): 2 for i in range(1, 31)}),
}
with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
    conn.execute("SET LOCAL lock_timeout = '5s'")
    conn.execute("SET LOCAL statement_timeout = '30s'")
    audit = conn.execute(
        "SELECT entity, entity_pk FROM audit_log "
        "WHERE executed_at = %s AND action = %s AND entity = ANY(%s)",
        (seed_at, "INSERT", ["category", "product", "store", "store_location"]),
    ).fetchall()
    for entity, n in [
        ("category", 30),
        ("product", 40),
        ("store", 30),
        ("store_location", 30),
    ]:
        actual = Counter(r["entity_pk"] for r in audit if r["entity"] == entity)
        if actual != Counter({str(i): 1 for i in range(1, n + 1)}):
            raise RuntimeError(f"{entity}: seed audit provenance mismatch")
    schemas = [
        r["schemaname"]
        for r in conn.execute(
            "SELECT schemaname FROM pg_tables WHERE tablename = %s "
            "AND right(schemaname, 7) = %s ORDER BY 1",
            ("outbox", "_events"),
        )
    ]
    candidates = {}
    retained = {}
    for schema in schemas:
        # Hold existing rows against concurrent relay updates until verification
        # and commit; new owner writes can still append different rows.
        rows = conn.execute(
            sql.SQL("SELECT * FROM {} ORDER BY event_id FOR UPDATE").format(
                sql.Identifier(schema, "outbox")
            )
        ).fetchall()
        selected = [
            r for r in rows if schema in expected and r["occurred_at"] == seed_at
        ]
        if schema in expected:
            if Counter(r["entity_id"] for r in selected) != expected[schema]:
                raise RuntimeError(f"{schema}: seed identity/count mismatch")
            if any(
                r["event_type"] != "INSERT" or r["relayed_at"] is not None
                for r in selected
            ):
                raise RuntimeError(
                    f"{schema}: unexpected event type or already relayed"
                )
        candidates[schema] = selected
        retained[schema] = [r for r in rows if r not in selected]
    if not set(expected).issubset(schemas):
        raise RuntimeError("A required outbox is absent")
    summary = {
        s: {"delete": len(candidates[s]), "retain": len(retained[s])} for s in schemas
    }
    print(json.dumps(summary, sort_keys=True))
    if not args.apply:
        conn.rollback()
        print("DRY RUN: provenance verified; no rows deleted")
    else:
        payload = json.dumps(
            {"seed_at": seed_at, "candidates": candidates, "retained": retained},
            default=str,
            indent=2,
            sort_keys=True,
        ).encode()
        # Exclusive creation prevents overwriting the only recovery artifact.
        fd = os.open(args.backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as backup:
            backup.write(payload)
            backup.flush()
            os.fsync(backup.fileno())
        print("Archive SHA256:", hashlib.sha256(payload).hexdigest())
        for schema, rows in candidates.items():
            if not rows:
                continue
            ids = [r["event_id"] for r in rows]
            deleted = conn.execute(
                sql.SQL(
                    "DELETE FROM {} WHERE event_id = ANY(%s) AND occurred_at = %s "
                    "AND relayed_at IS NULL RETURNING event_id"
                ).format(sql.Identifier(schema, "outbox")),
                (ids, seed_at),
            ).fetchall()
            if {r["event_id"] for r in deleted} != set(ids):
                raise RuntimeError(f"{schema}: deletion mismatch; rollback")
        for schema, rows in retained.items():
            if not rows:
                continue
            after = conn.execute(
                sql.SQL(
                    "SELECT * FROM {} WHERE event_id = ANY(%s) ORDER BY event_id"
                ).format(sql.Identifier(schema, "outbox")),
                ([r["event_id"] for r in rows],),
            ).fetchall()
            if after != rows:
                raise RuntimeError(f"{schema}: retained events changed; rollback")
        conn.commit()
        print("COMMITTED: 280 seed events deleted; all other captured rows preserved")
