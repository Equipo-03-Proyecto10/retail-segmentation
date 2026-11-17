"""The recorded explanation of each recommendation (F24-05, #828, ADR-0067).

What is tested: a served answer stores its decision with its items, their
order, reasons and quantities, the run and the time; a stored decision read back
equals what the client received, even after stock or rules changed; a cached
answer is still attributable to a stored decision; the tables hold ids, figures
and codes only and are kept 90 days; and the schema refuses an item that
contradicts its kind. Real PostgreSQL (the seeded database) and real Redis.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import jsonschema
import psycopg
import psycopg.sql
import pytest
import yaml
from flask import Flask

import redis
from app import decision_store, retention, rule
from app.cache import result_key
from app.decisions import Decision, Item, Reason
from app.service import ViewReads
from app.store import current_label

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/golden/recommendations.json").read_text())
CUSTOMER = next(
    case["input"]["customer_id"]
    for case in FIXTURE["cases"]
    if case["outcome"]["status"] == "recommended"
)
OPENAPI = yaml.safe_load((ROOT / "contracts/recommendations/openapi.yaml").read_text())
TABLES = (
    "recommendation_decision",
    "recommendation_exclusion",
    "recommendation_item",
    "recommendation_reason",
)
NOW = datetime(2026, 10, 15, 16, 42, 10, 123456, tzinfo=UTC)


def _valid(schema: str, document: object) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": OPENAPI["components"]}
    ).validate(document)


def _decision_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def service(service_url: str) -> Iterator[psycopg.Connection]:
    """svc_recommendations in a transaction that is rolled back. A statement runs
    first so that the store's own `transaction()` is a savepoint inside this one
    and does not commit."""
    with psycopg.connect(service_url) as connection:
        connection.execute("SELECT 1")
        yield connection
        connection.rollback()


@pytest.fixture(autouse=True, scope="module")
def leaves_the_tables_as_found(owner_url: str) -> Iterator[None]:
    """No test may leave a decision behind: a leftover would pin a run, a store
    or a product against deletion in every other suite."""

    def counts() -> list[int]:
        with psycopg.connect(owner_url) as connection:
            return _counts(connection)

    before = counts()
    yield
    assert counts() == before


@pytest.fixture
def products(owner: psycopg.Connection) -> dict[int, int]:
    return dict(
        owner.execute(
            "SELECT product_id, category_id FROM product ORDER BY 1"
        ).fetchall()
    )


@pytest.fixture
def kept(owner: psycopg.Connection) -> Iterator[list[str]]:
    """Decision ids a test committed through the service; removed afterwards."""
    ids: list[str] = []
    yield ids
    for decision_id in ids:
        owner.execute(
            "DELETE FROM recommendations.recommendation_decision"
            " WHERE decision_id = %s",
            (decision_id,),
        )


def _full(products: dict[int, int], store: int = 5) -> Decision:
    """A decision with every kind of item, every signal and two exclusions."""
    return Decision(
        decision_id=_decision_id(),
        customer_id=CUSTOMER,
        status="recommended",
        computed_at=NOW,
        run_id=1,
        model_version_id=7,
        store_id=store,
        store_source="given",
        channel_id=1,
        exclusions=(("already_bought", 2), ("out_of_stock", 2)),
        items=(
            Item(1, products[1], "primary", 1, 22, (Reason("segment", 9),)),
            Item(
                2,
                products[2],
                "primary",
                2,
                4,
                (
                    Reason("preferred_category", None, products[2]),
                    Reason("purchase_history", None, products[2], Decimal("0.4")),
                ),
            ),
            Item(
                3,
                products[3],
                "alternative",
                3,
                11,
                (Reason("relation_substitute"),),
                alternative_for=4,
                unavailable_reason="out_of_stock",
            ),
            Item(
                5,
                products[5],
                "elsewhere",
                4,
                6,
                (
                    Reason("segment", 5),
                    Reason("purchase_history", None, 3, Decimal("0.25")),
                ),
                held_at_store_id=3,
                unavailable_reason="out_of_stock",
            ),
        ),
    )


# ---------- what is stored, and what is read back ----------


def test_a_decision_is_stored_with_everything_it_holds_and_read_back_equal(
    service: psycopg.Connection, products: dict[int, int]
) -> None:
    decision = _full(products)
    assert decision_store.record(service, decision) is True
    stored = decision_store.read(service, decision.decision_id)
    assert stored == decision
    assert stored.as_dict() == decision.as_dict()
    # The contract's own schema accepts what was read back.
    _valid("RecommendationDecision", stored.as_dict())


def test_the_read_back_derives_what_is_not_stored(
    service: psycopg.Connection, products: dict[int, int]
) -> None:
    decision = _full(products, store=5)
    decision_store.record(service, decision)
    view = decision_store.read(service, decision.decision_id).as_dict()
    primary, _, alternative, elsewhere = view["items"]
    assert primary["factors"] == {
        "signals_matched": 1,
        "segment_buyers": 9,
        "category_share": None,
    }
    assert alternative["factors"] is None
    assert elsewhere["factors"] == {
        "signals_matched": 2,
        "segment_buyers": 5,
        "category_share": 0.25,
    }
    # The channel is the decision's, the store is its own only when elsewhere.
    assert [i["availability"]["store_id"] for i in view["items"]] == [5, 5, 5, 3]
    assert {i["availability"]["channel_id"] for i in view["items"]} == {1}
    # Reasons and exclusions come back in the contract's fixed order.
    assert [r["signal"] for r in view["items"][1]["reasons"]] == [
        "preferred_category",
        "purchase_history",
    ]
    assert view["exclusions"] == [
        {"reason": "already_bought", "count": 2},
        {"reason": "out_of_stock", "count": 2},
    ]


@pytest.mark.parametrize(
    "status, store, source",
    [
        ("none_match", 5, "usual_store"),
        ("no_usual_store", None, None),
        ("no_open_assignment", None, None),
        ("unassigned_result", None, None),
        ("PERSONALIZATION_WITHDRAWN", 5, "given"),
    ],
)
def test_a_decision_without_items_is_kept_with_its_status(
    service: psycopg.Connection, status: str, store: int | None, source: str | None
) -> None:
    decision = Decision(
        _decision_id(), CUSTOMER, status, NOW, store_id=store, store_source=source
    )
    decision_store.record(service, decision)
    stored = decision_store.read(service, decision.decision_id)
    assert stored == decision
    assert stored.as_dict()["items"] == []
    _valid("RecommendationDecision", stored.as_dict())


def test_recording_the_same_decision_again_writes_nothing(
    service: psycopg.Connection, products: dict[int, int]
) -> None:
    decision = _full(products)
    assert decision_store.record(service, decision) is True
    counts = _counts(service)
    assert decision_store.record(service, decision) is False
    assert _counts(service) == counts


def _counts(connection: psycopg.Connection) -> list[int]:
    counts = []
    for table in TABLES:
        row = connection.execute(
            psycopg.sql.SQL("SELECT count(*) FROM recommendations.{}").format(
                psycopg.sql.Identifier(table)
            )
        ).fetchone()
        counts.append(row[0])
    return counts


def test_an_unknown_decision_is_none(service: psycopg.Connection) -> None:
    assert decision_store.read(service, _decision_id()) is None
    assert decision_store.exists(service, _decision_id()) is False


def test_the_seeded_decisions_are_the_contracts_and_read_back_whole(
    service: psycopg.Connection,
) -> None:
    ids = [
        str(row[0])
        for row in service.execute(
            "SELECT decision_id FROM recommendations.recommendation_decision"
            " ORDER BY decision_id"
        ).fetchall()
    ]
    assert len(ids) >= 30
    kinds = set()
    for decision_id in ids:
        stored = decision_store.read(service, decision_id)
        _valid("RecommendationDecision", stored.as_dict())
        kinds |= {item.kind for item in stored.items}
        # An item of the contract has reasons, and positions run from 1 in order.
        assert [i.position for i in stored.items] == sorted(
            {i.position for i in stored.items}
        )
        assert all(item.reasons for item in stored.items)
    assert kinds == {"primary", "alternative", "elsewhere"}


# ---------- a served answer is recorded ----------


def _ask(app: Flask, token: Callable[..., dict], limit: int = 10) -> dict:
    response = app.test_client().get(
        "/v1/recommendations",
        query_string={"customer_id": CUSTOMER, "limit": str(limit)},
        headers=token(),
    )
    assert response.status_code == 200
    return response.get_json()


def _cached_decision(
    cache: redis.Redis, owner: psycopg.Connection, limit: int = 10
) -> str:
    (run_id,) = owner.execute(
        "SELECT run_id FROM api_segments.current_label_v1 WHERE customer_id = %s",
        (CUSTOMER,),
    ).fetchone()
    entry = json.loads(cache.get(result_key(CUSTOMER, run_id, limit)))
    return entry["decision_id"]


def _decisions_of(owner: psycopg.Connection) -> int:
    return owner.execute(
        "SELECT count(*) FROM recommendations.recommendation_decision"
        " WHERE customer_id = %s",
        (CUSTOMER,),
    ).fetchone()[0]


def test_a_served_answer_stores_its_decision(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
    kept: list[str],
) -> None:
    before, started = _decisions_of(owner), datetime.now(UTC)
    body = _ask(app, token)
    decision_id = _cached_decision(redis_connection, owner)
    kept.append(decision_id)
    assert _decisions_of(owner) == before + 1
    stored = decision_store.read(owner, decision_id)
    assert stored.customer_id == CUSTOMER
    assert stored.status == body["status"] == "recommended"
    assert stored.run_id == body["run_id"]
    assert stored.store_id == body["usual_store_id"]
    assert (stored.store_source, stored.channel_id) == ("usual_store", None)
    assert started - timedelta(seconds=5) <= stored.computed_at <= datetime.now(UTC)
    # The order and the stock it used are the ones sent.
    assert [(i.product_id, i.quantity_on_hand) for i in stored.items] == [
        (i["product_id"], i["quantity_on_hand"]) for i in body["items"]
    ]
    assert [i.position for i in stored.items] == list(range(1, len(body["items"]) + 1))


def test_a_stored_decision_matches_what_the_client_received(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
    kept: list[str],
) -> None:
    body = _ask(app, token)
    decision_id = _cached_decision(redis_connection, owner)
    kept.append(decision_id)
    stored = decision_store.read(owner, decision_id)
    assert stored.v1_items() == body["items"]
    assert stored.as_dict()["computed_at"] == body["computed_at"]
    _valid("RecommendationDecision", stored.as_dict())


def test_it_still_matches_after_stock_and_rules_changed(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner_url: str,
    owner: psycopg.Connection,
    kept: list[str],
) -> None:
    body = _ask(app, token)
    decision_id = _cached_decision(redis_connection, owner)
    kept.append(decision_id)
    shown = [item["product_id"] for item in body["items"]]
    assert shown
    # In a transaction that is rolled back: every shown product loses its stock
    # and is withdrawn, and the customer's interests go.
    with psycopg.connect(owner_url) as connection:
        connection.execute(
            "UPDATE inventory SET quantity_on_hand = 0 WHERE product_id = ANY(%s)",
            (shown,),
        )
        connection.execute(
            "UPDATE product SET is_active = false WHERE product_id = ANY(%s)", (shown,)
        )
        fresh = rule.recommend(
            ViewReads(connection, current_label(connection, CUSTOMER)),
            CUSTOMER,
            datetime.now(UTC),
            10,
        )
        # The rule now says something else...
        assert [i["product_id"] for i in fresh.items] != shown
        # ...and the record still says what was sent.
        stored = decision_store.read(connection, decision_id)
        assert stored.v1_items() == body["items"]
        connection.rollback()


def test_a_cached_answer_is_attributed_to_the_stored_decision(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
    kept: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = _ask(app, token)
    decision_id = _cached_decision(redis_connection, owner)
    kept.append(decision_id)
    recorded = _decisions_of(owner)
    with caplog.at_level("INFO", logger="app.service"):
        second = _ask(app, token)
    # Served from the cache: the same answer, the same decision, nothing written.
    assert second == first
    assert _cached_decision(redis_connection, owner) == decision_id
    assert _decisions_of(owner) == recorded
    assert f"Served cached decision {decision_id}" in caplog.text
    assert decision_store.read(owner, decision_id).v1_items() == second["items"]


def test_each_computation_is_its_own_decision(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
    kept: list[str],
) -> None:
    _ask(app, token, limit=3)
    kept.append(_cached_decision(redis_connection, owner, 3))
    _ask(app, token, limit=4)
    kept.append(_cached_decision(redis_connection, owner, 4))
    assert kept[-1] != kept[-2]
    assert len(decision_store.read(owner, kept[-2]).items) <= 3


def test_a_cache_entry_from_before_decisions_is_recomputed_and_recorded(
    app: Flask,
    token: Callable[..., dict],
    redis_connection: redis.Redis,
    owner: psycopg.Connection,
    kept: list[str],
) -> None:
    _ask(app, token, limit=6)
    first = _cached_decision(redis_connection, owner, 6)
    kept.append(first)
    (run_id,) = owner.execute(
        "SELECT run_id FROM api_segments.current_label_v1 WHERE customer_id = %s",
        (CUSTOMER,),
    ).fetchone()
    key = result_key(CUSTOMER, run_id, 6)
    entry = json.loads(redis_connection.get(key))
    del entry["decision_id"]
    redis_connection.set(key, json.dumps(entry), ex=60)
    before = _decisions_of(owner)
    _ask(app, token, limit=6)
    second = _cached_decision(redis_connection, owner, 6)
    kept.append(second)
    assert second != first
    assert _decisions_of(owner) == before + 1


def test_a_customer_without_a_segment_is_recorded_too(
    app: Flask,
    token: Callable[..., dict],
    owner: psycopg.Connection,
    kept: list[str],
) -> None:
    customer = next(
        case["input"]["customer_id"]
        for case in FIXTURE["cases"]
        if case["outcome"]["status"] != "recommended"
    )
    response = app.test_client().get(
        "/v1/recommendations",
        query_string={"customer_id": customer},
        headers=token(),
    )
    status = response.get_json()["status"]
    rows = owner.execute(
        "SELECT decision_id::text, status FROM recommendations.recommendation_decision"
        " WHERE customer_id = %s AND computed_at > now() - interval '1 minute'",
        (customer,),
    ).fetchall()
    kept.extend(row[0] for row in rows)
    assert len(rows) == 1 and rows[0][1] == status
    assert not decision_store.read(owner, rows[0][0]).items


# ---------- retention and anonymization ----------


def test_decisions_older_than_90_days_are_deleted_with_what_they_hold(
    service: psycopg.Connection, products: dict[int, int]
) -> None:
    now = datetime.now(UTC)
    old, edge, fresh = (_full(products), _full(products), _full(products))
    old = _at(old, now - timedelta(days=91))
    edge = _at(edge, now - timedelta(days=89, hours=23))
    fresh = _at(fresh, now)
    for decision in (old, edge, fresh):
        decision_store.record(service, decision)
    before = _counts(service)
    deleted = decision_store.purge_expired(service, now)
    assert deleted >= 1
    assert not decision_store.exists(service, old.decision_id)
    assert decision_store.exists(service, edge.decision_id)
    assert decision_store.exists(service, fresh.decision_id)
    # Its exclusions, items and reasons went with it, and only those.
    after = _counts(service)
    assert after[1] <= before[1] - 2 and after[2] <= before[2] - 4
    assert after[3] <= before[3] - 5
    leftovers = service.execute(
        "SELECT count(*) FROM recommendations.recommendation_item AS i"
        " WHERE NOT EXISTS (SELECT FROM recommendations.recommendation_decision AS d"
        " WHERE d.decision_id = i.decision_id)"
    ).fetchone()[0]
    assert leftovers == 0
    # The seed is within 90 days, so it is untouched.
    assert after[0] >= 30


def _at(decision: Decision, moment: datetime) -> Decision:
    from dataclasses import replace

    return replace(decision, computed_at=moment)


def test_the_retention_job_runs_by_itself(
    app: Flask,
    owner: psycopg.Connection,
    products: dict[int, int],
) -> None:
    old = _at(_full(products), datetime.now(UTC) - timedelta(days=120))
    with psycopg.connect(owner.info.dsn, autocommit=True) as writer:
        decision_store.record(writer, old)
    assert decision_store.exists(owner, old.decision_id)
    kit = app.extensions["mosaiq_kit"]
    retention.start(app, kit, interval=0.2)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and decision_store.exists(
            owner, old.decision_id
        ):
            time.sleep(0.1)
        assert not decision_store.exists(owner, old.decision_id)
    finally:
        app.extensions["recommendations_retention"].set()
        owner.execute(
            "DELETE FROM recommendations.recommendation_decision"
            " WHERE decision_id = %s",
            (old.decision_id,),
        )


def test_anonymization_clears_the_customer_and_keeps_the_figures(
    service: psycopg.Connection, products: dict[int, int]
) -> None:
    decision = _full(products)
    decision_store.record(service, decision)
    changed = decision_store.clear_customer(service, CUSTOMER)
    assert changed >= 1
    stored = decision_store.read(service, decision.decision_id)
    assert stored.customer_id is None
    from dataclasses import replace

    assert stored == replace(decision, customer_id=None)
    assert stored.as_dict()["customer_id"] is None
    _valid("RecommendationDecision", stored.as_dict())


def test_removing_a_customer_row_leaves_its_decisions_without_a_customer(
    owner_url: str, products: dict[int, int]
) -> None:
    with psycopg.connect(owner_url) as connection:
        connection.execute(
            "INSERT INTO customer (customer_id, name, email, phone,"
            " registration_channel_id, registered_on)"
            " VALUES (%s, 'Gone', 'gone@example.test', '5500000000', 1, CURRENT_DATE)",
            ("9a9a9a9a-0000-4000-8000-000000000001",),
        )
        decision = _full(products)
        from dataclasses import replace

        decision_store.record(
            connection,
            replace(decision, customer_id="9a9a9a9a-0000-4000-8000-000000000001"),
        )
        connection.execute(
            "DELETE FROM customer WHERE customer_id = %s",
            ("9a9a9a9a-0000-4000-8000-000000000001",),
        )
        stored = decision_store.read(connection, decision.decision_id)
        assert stored.customer_id is None and len(stored.items) == 4
        connection.rollback()


# ---------- the schema ----------


def test_the_tables_hold_ids_figures_and_codes_only(owner: psycopg.Connection) -> None:
    columns = owner.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = 'recommendations'"
    ).fetchall()
    assert {table for table, *_ in columns} == set(TABLES)
    assert {kind for *_, kind in columns} <= {
        "uuid",
        "smallint",
        "integer",
        "bigint",
        "numeric",
        "text",
        "timestamp with time zone",
    }
    text = {(table, column) for table, column, kind in columns if kind == "text"}
    # Every text column is a code, so each is constrained to a list.
    assert text == {
        ("recommendation_decision", "status"),
        ("recommendation_decision", "store_source"),
        ("recommendation_exclusion", "reason"),
        ("recommendation_item", "kind"),
        ("recommendation_item", "unavailable_reason"),
        ("recommendation_reason", "signal"),
    }
    for table, column in text:
        (guarded,) = owner.execute(
            "SELECT count(*) FROM pg_constraint WHERE contype = 'c'"
            " AND conrelid = %s::regclass AND pg_get_constraintdef(oid) LIKE %s",
            (f"recommendations.{table}", f"%({column} %"),
        ).fetchone()
        guarded += owner.execute(
            "SELECT count(*) FROM pg_constraint WHERE contype = 'c'"
            " AND conrelid = %s::regclass AND pg_get_constraintdef(oid) ~ %s",
            (f"recommendations.{table}", rf"\m{column}\M"),
        ).fetchone()[0]
        assert guarded >= 1, (table, column)
    assert not [
        column
        for _, column, _ in columns
        if re.search(r"name|email|phone|sku|price|note|text", column)
    ]


def test_the_service_may_write_and_expire_decisions_and_nothing_else(
    owner: psycopg.Connection,
) -> None:
    for table in TABLES:
        held = {
            privilege
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            )
            if owner.execute(
                "SELECT has_table_privilege('svc_recommendations', %s, %s)",
                (f"recommendations.{table}", privilege),
            ).fetchone()[0]
        }
        assert held == {"SELECT", "INSERT", "DELETE"}, table
    columns = owner.execute(
        "SELECT a.attname FROM pg_attribute AS a"
        " WHERE a.attrelid = 'recommendations.recommendation_decision'::regclass"
        " AND a.attnum > 0 AND NOT a.attisdropped"
        " AND has_column_privilege('svc_recommendations',"
        " 'recommendations.recommendation_decision', a.attnum, 'UPDATE')"
    ).fetchall()
    assert columns == [("customer_id",)]
    for role in ("retail_app", "svc_engagement", "svc_stock"):
        (usage,) = owner.execute(
            "SELECT has_schema_privilege(%s, 'recommendations', 'USAGE')", (role,)
        ).fetchone()
        assert usage is False, role


@pytest.mark.parametrize(
    "table, statement",
    [
        (
            "item",
            "INSERT INTO recommendations.recommendation_item"
            " (decision_id, position, product_id, category_id, kind, quantity_on_hand,"
            "  alternative_for)"
            " SELECT decision_id, 90, 1, 1, 'primary', 1, 2"
            " FROM recommendations.recommendation_decision LIMIT 1",
        ),
        (
            "item",
            "INSERT INTO recommendations.recommendation_item"
            " (decision_id, position, product_id, category_id, kind, quantity_on_hand)"
            " SELECT decision_id, 90, 1, 1, 'alternative', 1"
            " FROM recommendations.recommendation_decision LIMIT 1",
        ),
        (
            "item",
            "INSERT INTO recommendations.recommendation_item"
            " (decision_id, position, product_id, category_id, kind, quantity_on_hand,"
            "  unavailable_reason)"
            " SELECT decision_id, 90, 1, 1, 'elsewhere', 1, 'out_of_stock'"
            " FROM recommendations.recommendation_decision LIMIT 1",
        ),
        (
            "item",
            "INSERT INTO recommendations.recommendation_item"
            " (decision_id, position, product_id, category_id, kind, quantity_on_hand)"
            " SELECT decision_id, 90, 1, 1, 'primary', 0"
            " FROM recommendations.recommendation_decision LIMIT 1",
        ),
        (
            "reason",
            "INSERT INTO recommendations.recommendation_reason"
            " (decision_id, position, signal, segment_buyers)"
            " SELECT decision_id, 1, 'purchase_history', 3"
            " FROM recommendations.recommendation_item LIMIT 1",
        ),
        (
            "reason",
            "INSERT INTO recommendations.recommendation_reason"
            " (decision_id, position, signal)"
            " SELECT decision_id, 1, 'segment'"
            " FROM recommendations.recommendation_item LIMIT 1",
        ),
        (
            "decision",
            "INSERT INTO recommendations.recommendation_decision"
            " (status, computed_at, store_id) VALUES ('recommended', now(), 1)",
        ),
        (
            "decision",
            "INSERT INTO recommendations.recommendation_decision"
            " (status, computed_at) VALUES ('invented', now())",
        ),
        (
            "exclusion",
            "INSERT INTO recommendations.recommendation_exclusion"
            " (decision_id, reason, product_count)"
            " SELECT decision_id, 'already_bought', 0"
            " FROM recommendations.recommendation_decision LIMIT 1",
        ),
    ],
)
def test_the_schema_refuses_what_contradicts_its_kind_or_signal(
    service: psycopg.Connection, table: str, statement: str
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        service.execute(statement)


def test_the_reasons_of_an_item_cannot_name_a_signal_twice(
    service: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.UniqueViolation):
        service.execute(
            "INSERT INTO recommendations.recommendation_reason"
            " (decision_id, position, signal, segment_buyers)"
            " SELECT decision_id, position, 'segment', 1"
            " FROM recommendations.recommendation_reason"
            " WHERE signal = 'segment' LIMIT 1"
        )
