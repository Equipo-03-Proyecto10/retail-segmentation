"""A sales file reaches the service as chunks of rows and is applied row by row
(F26-08, ADR-0070, RN-37): the control routes, the worker, the rejection report,
the repeated file, resume and cancel, against real PostgreSQL and Redis."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from lxml import etree

from app import import_worker
from tests.conftest import idempotency_headers

NS = "https://mosaiq.maxthecoder.online/xml/sales/v1"
XML = {"Accept": "application/xml"}
HEADER = [
    "transaction_id",
    "customer_id",
    "store_id",
    "channel_id",
    "occurred_at",
    "product_id",
    "quantity",
    "unit_price",
]
PREFIX = "svc-sales:imp:"


# ---- helpers ---------------------------------------------------------------------


@pytest.fixture
def world(owner):
    """A customer, a channel and two stocked products at one store."""
    rows = owner.execute(
        "SELECT i.store_id, i.product_id FROM public.inventory AS i"
        " JOIN public.product AS p USING (product_id)"
        " WHERE p.is_active AND i.quantity_on_hand >= 10"
        " ORDER BY i.store_id, i.product_id"
    ).fetchall()
    store = rows[0][0]
    products = [product for found, product in rows if found == store][:3]
    # Enough stock that no row is refused for it unless a test makes it so; the
    # suite's cleanup puts the seed's quantities back.
    owner.execute(
        "UPDATE public.inventory SET quantity_on_hand = 500"
        " WHERE store_id = %s AND product_id = ANY(%s)",
        (store, products),
    )
    customer, registered = owner.execute(
        "SELECT customer_id, registered_on FROM public.customer"
        " ORDER BY registered_on, customer_id LIMIT 1"
    ).fetchone()
    channel = owner.execute("SELECT min(channel_id) FROM public.channel").fetchone()[0]
    return {
        "store": store,
        "products": products,
        "customer": str(customer),
        "registered": registered,
        "channel": channel,
    }


def when(days: float = 1.0) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def row(world: dict, line: int, tid: str, **cells) -> dict:
    """A row of the file as a chunk carries it: its line and eight text cells."""
    values = {
        "transaction_id": tid,
        "customer_id": world["customer"],
        "store_id": str(world["store"]),
        "channel_id": str(world["channel"]),
        "occurred_at": when(),
        "product_id": str(world["products"][0]),
        "quantity": "1",
        "unit_price": "10.00",
    }
    values.update({key: str(value) for key, value in cells.items()})
    return {"line": line, "cells": [values[name] for name in HEADER]}


def new_id() -> str:
    return "T-" + uuid.uuid4().hex[:12]


def open_import(client, token, total: int, chunks: int = 1, key=None, role="ADMIN"):
    body = {
        "contract_version": 1,
        "header": HEADER,
        "total_rows": total,
        "chunk_count": chunks,
    }
    return client.post(
        "/v1/sale-imports",
        json=body,
        headers=idempotency_headers(token(role), key),
    )


def put_chunk(client, token, import_id: int, sequence: int, rows: list[dict]):
    return client.put(
        f"/v1/sale-imports/{import_id}/chunks/{sequence}",
        json={"rows": rows},
        headers=token("ADMIN"),
    )


def chunk_xml(rows: list[dict]) -> bytes:
    body = ""
    for item in rows:
        cells = "".join(f"<cells>{cell}</cells>" for cell in item["cells"])
        body += f"<rows><line>{item['line']}</line>{cells}</rows>"
    return f'<SaleImportChunk xmlns="{NS}">{body}</SaleImportChunk>'.encode()


def work(service_url: str, **kwargs) -> int:
    """What the worker does, run to idle in this process."""
    with psycopg.connect(service_url, autocommit=True) as connection:
        return import_worker.run_until_idle(connection, rate=100_000, **kwargs)


def load(client, token, service_url, rows: list[dict], *, per_chunk: int = 150):
    """Open an import for `rows`, send them in chunks and let the worker finish."""
    chunks = [rows[i : i + per_chunk] for i in range(0, len(rows), per_chunk)]
    opened = open_import(client, token, len(rows), len(chunks))
    assert opened.status_code == 201, opened.get_json()
    import_id = opened.get_json()["import_id"]
    for sequence, chunk in enumerate(chunks):
        staged = put_chunk(client, token, import_id, sequence, chunk)
        assert staged.status_code == 202, staged.get_json()
    work(service_url)
    return import_id


def status(client, token, import_id: int) -> dict:
    return client.get(
        f"/v1/sale-imports/{import_id}", headers=token("ADMIN")
    ).get_json()


def rejections(client, token, import_id: int) -> dict[int, dict]:
    page = client.get(
        f"/v1/sale-imports/{import_id}/rejections?size=100", headers=token("ADMIN")
    ).get_json()
    return {item["line"]: item for item in page["sale_import_rejections"]}


def sales(owner, tid: str):
    return owner.execute(
        "SELECT transaction_id, total, store_id FROM public.transaction"
        " WHERE source_transaction_id = %s",
        (PREFIX + tid,),
    ).fetchone()


def on_hand(owner, world: dict, product: int) -> int:
    return owner.execute(
        "SELECT quantity_on_hand FROM public.inventory"
        " WHERE store_id = %s AND product_id = %s",
        (world["store"], product),
    ).fetchone()[0]


# ---- opening an import -----------------------------------------------------------


def test_an_import_is_opened_with_its_counts_and_replayed_by_its_key(
    client, token, owner
):
    key = str(uuid.uuid4())
    opened = open_import(client, token, 9, 2, key)
    assert opened.status_code == 201
    body = opened.get_json()
    assert opened.headers["Location"] == f"/v1/sale-imports/{body['import_id']}"
    assert {k: v for k, v in body.items() if k not in {"import_id", "created_at"}} == {
        "status": "RECEIVING",
        "contract_version": 1,
        "total_rows": 9,
        "chunk_count": 2,
        "next_sequence": 0,
        "received": 0,
        "accepted": 0,
        "rejected": 0,
        "pending": 0,
        "finished_at": None,
        "created_by": owner.execute(
            "SELECT user_id::text FROM public.app_user AS u JOIN public.role AS r"
            " USING (role_id) WHERE r.code = 'ADMIN'"
        ).fetchone()[0],
    }
    replay = open_import(client, token, 9, 2, key)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.get_json() == body
    assert owner.execute("SELECT count(*) FROM sales.import").fetchone() == (1,)
    other = open_import(client, token, 10, 2, key)
    assert other.status_code == 409
    assert other.get_json()["code"] == "idempotency_key_reused"


def test_an_import_in_xml_validates_against_the_xsd(app, client, token):
    request = (
        f'<SaleImportRequest xmlns="{NS}"><contract_version>1</contract_version>'
        + "".join(f"<header>{name}</header>" for name in HEADER)
        + "<total_rows>3</total_rows><chunk_count>1</chunk_count></SaleImportRequest>"
    ).encode()
    response = client.post(
        "/v1/sale-imports",
        data=request,
        content_type="application/xml",
        headers={**idempotency_headers(token("ADMIN")), **XML},
    )
    assert response.status_code == 201, response.data
    document = etree.fromstring(response.data)
    app.extensions["mosaiq_kit"].contract.xml_schema.assertValid(document)
    assert document.find(f"{{{NS}}}finished_at") is None


def test_one_import_is_open_at_a_time(client, token, owner):
    first = open_import(client, token, 1).get_json()
    refused = open_import(client, token, 1)
    assert refused.status_code == 409
    error = refused.get_json()
    assert error["code"] == "import_in_progress"
    assert str(first["import_id"]) in error["message"]
    # Cancelling the first lets another open.
    cancelled = client.put(
        f"/v1/sale-imports/{first['import_id']}/status",
        json={"status": "CANCELLED"},
        headers=token("ADMIN"),
    )
    assert cancelled.status_code == 200
    assert open_import(client, token, 1).status_code == 201


def test_the_database_allows_one_open_import_whoever_inserts(service_url, owner):
    owner.execute("DELETE FROM sales.import")
    user = owner.execute("SELECT min(user_id::text) FROM public.app_user").fetchone()[0]
    statement = (
        "INSERT INTO sales.import (idempotency_key, request_sha256, contract_version,"
        " total_rows, chunk_count, created_by)"
        " VALUES (gen_random_uuid(), decode(repeat('00', 32), 'hex'), 1, 1, 1, %s)"
    )
    with psycopg.connect(service_url, autocommit=True) as connection:
        connection.execute(statement, (user,))
        with pytest.raises(psycopg.errors.UniqueViolation) as raised:
            connection.execute(statement, (user,))
        assert raised.value.diag.constraint_name == "import_one_open"


@pytest.mark.parametrize(
    "mutation,field,code",
    [
        ({"contract_version": 2}, "contract_version", "unsupported_version"),
        ({"total_rows": 0}, "total_rows", "out_of_range"),
        ({"total_rows": 100_001}, "total_rows", "out_of_range"),
        ({"chunk_count": 0}, "chunk_count", "out_of_range"),
        ({"chunk_count": 668}, "chunk_count", "out_of_range"),
        (
            {"header": HEADER[:3] + ["occured_at"] + HEADER[4:]},
            "header[3]",
            "unexpected_column",
        ),
        ({"header": list(reversed(HEADER))}, "header[0]", "unexpected_column"),
    ],
)
@pytest.mark.parametrize("xml", [False, True])
def test_an_import_that_breaks_the_contract_is_422(
    client, token, mutation, field, code, xml
):
    body = {
        "contract_version": 1,
        "header": HEADER,
        "total_rows": 5,
        "chunk_count": 1,
    } | mutation
    headers = idempotency_headers(token("ADMIN"))
    if xml:
        text = (
            f'<SaleImportRequest xmlns="{NS}">'
            f"<contract_version>{body['contract_version']}</contract_version>"
            + "".join(f"<header>{name}</header>" for name in body["header"])
            + f"<total_rows>{body['total_rows']}</total_rows>"
            f"<chunk_count>{body['chunk_count']}</chunk_count></SaleImportRequest>"
        )
        response = client.post(
            "/v1/sale-imports",
            data=text.encode(),
            content_type="application/xml",
            headers={**headers, **XML},
        )
        assert response.status_code == 422
        assert b"invalid_body" in response.data
        return
    response = client.post("/v1/sale-imports", json=body, headers=headers)
    assert response.status_code == 422, response.get_json()
    error = response.get_json()
    assert error["code"] == "invalid_body"
    assert {"field": field, "code": code} in error["details"]


def test_only_the_administrator_imports_and_a_key_is_required(client, token):
    for role in ("STORE_MANAGER", "ANALYST", "SALES_ASSOCIATE", "INVENTORY_PLANNER"):
        response = open_import(client, token, 1, role=role)
        assert response.status_code == 403
        assert response.get_json()["code"] == "forbidden_role"
    for path, method in (
        ("/v1/sale-imports", "get"),
        ("/v1/sale-imports/1", "get"),
        ("/v1/sale-imports/1/rejections", "get"),
        ("/v1/sale-imports/1/chunks/0", "put"),
        ("/v1/sale-imports/1/status", "put"),
    ):
        response = getattr(client, method)(
            path, json={}, headers=token("STORE_MANAGER")
        )
        assert response.status_code == 403, path
    missing = client.post(
        "/v1/sale-imports",
        json={
            "contract_version": 1,
            "header": HEADER,
            "total_rows": 1,
            "chunk_count": 1,
        },
        headers=token("ADMIN"),
    )
    assert missing.status_code == 400
    assert missing.get_json()["code"] == "idempotency_key_required"


# ---- sending chunks --------------------------------------------------------------


def test_chunks_are_staged_in_order_and_the_last_one_starts_processing(
    client, token, world
):
    opened = open_import(client, token, 3, 2).get_json()
    import_id = opened["import_id"]
    first = put_chunk(
        client, token, import_id, 0, [row(world, 2, new_id()), row(world, 3, new_id())]
    )
    assert first.status_code == 202
    body = first.get_json()
    assert (body["status"], body["next_sequence"], body["received"]) == (
        "RECEIVING",
        1,
        2,
    )
    last = put_chunk(client, token, import_id, 1, [row(world, 4, new_id())])
    assert last.status_code == 202
    assert last.get_json()["status"] == "PROCESSING"
    assert last.get_json()["next_sequence"] == 2


def test_a_chunk_sent_twice_is_one_chunk_and_another_body_is_a_conflict(
    client, token, owner, world
):
    import_id = open_import(client, token, 4, 2).get_json()["import_id"]
    rows = [row(world, 2, new_id()), row(world, 3, new_id())]
    first = put_chunk(client, token, import_id, 0, rows)
    replay = put_chunk(client, token, import_id, 0, rows)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.get_json() == first.get_json()
    # The same chunk as XML is the same body.
    xml = client.put(
        f"/v1/sale-imports/{import_id}/chunks/0",
        data=chunk_xml(rows),
        content_type="application/xml",
        headers={**token("ADMIN"), **XML},
    )
    assert xml.status_code == 200 and xml.headers["Idempotent-Replayed"] == "true"
    assert owner.execute(
        "SELECT count(*), sum(row_count) FROM sales.import_chunk WHERE import_id = %s",
        (import_id,),
    ).fetchone() == (1, 2)
    other = put_chunk(client, token, import_id, 0, [row(world, 2, new_id())])
    assert other.status_code == 409
    assert other.get_json()["code"] == "chunk_conflict"


def test_a_chunk_out_of_order_names_the_next_one(client, token, world):
    import_id = open_import(client, token, 3, 3).get_json()["import_id"]
    put_chunk(client, token, import_id, 0, [row(world, 2, new_id())])
    skipped = put_chunk(client, token, import_id, 2, [row(world, 3, new_id())])
    assert skipped.status_code == 409
    error = skipped.get_json()
    assert error["code"] == "chunk_out_of_order"
    assert "expects chunk 1 next" in error["message"]
    assert error["details"] == [{"field": "next_sequence", "code": "out_of_order"}]
    assert status(client, token, import_id)["next_sequence"] == 1


def test_a_chunk_is_refused_by_structure_and_nothing_else(client, token, world):
    import_id = open_import(client, token, 400, 3).get_json()["import_id"]
    too_many = [row(world, line, new_id()) for line in range(2, 153)]
    response = put_chunk(client, token, import_id, 0, too_many)
    assert response.status_code == 422
    assert response.get_json()["details"][0]["field"] == "rows"
    long_cell = row(world, 2, new_id())
    long_cell["cells"][4] = "x" * 257
    response = put_chunk(client, token, import_id, 0, [long_cell])
    assert response.status_code == 422
    assert {"field": "rows[0].cells[4]", "code": "too_long"} in response.get_json()[
        "details"
    ]
    decreasing = [row(world, 3, new_id()), row(world, 3, new_id())]
    response = put_chunk(client, token, import_id, 0, decreasing)
    assert response.status_code == 422
    assert {"field": "rows[1].line", "code": "not_increasing"} in response.get_json()[
        "details"
    ]
    # Nothing was staged by the refusals; a bad value is a row's, not a chunk's.
    bad_value = row(world, 2, new_id(), quantity="three")
    ok = put_chunk(client, token, import_id, 0, [bad_value])
    assert ok.status_code == 202
    # Lines keep increasing across chunks.
    behind = put_chunk(client, token, import_id, 1, [row(world, 2, new_id())])
    assert behind.status_code == 422
    assert behind.get_json()["details"][0] == {
        "field": "rows[0].line",
        "code": "not_increasing",
    }
    beyond = put_chunk(client, token, import_id, 3, [row(world, 9, new_id())])
    assert beyond.status_code == 422
    assert beyond.get_json()["details"][0]["field"] == "sequence"


def test_the_rows_must_add_up_to_the_total_the_import_announced(client, token, world):
    import_id = open_import(client, token, 3, 2).get_json()["import_id"]
    put_chunk(client, token, import_id, 0, [row(world, 2, new_id())])
    short = put_chunk(client, token, import_id, 1, [row(world, 3, new_id())])
    assert short.status_code == 422
    error = short.get_json()
    assert error["code"] == "row_count_mismatch"
    assert error["details"] == [{"field": "total_rows", "code": "mismatch"}]
    # The import stays RECEIVING and the refused chunk was not staged.
    after = status(client, token, import_id)
    assert (after["status"], after["received"], after["next_sequence"]) == (
        "RECEIVING",
        1,
        1,
    )
    # More rows than announced are refused before the last chunk too.
    heavy = put_chunk(
        client,
        token,
        import_id,
        1,
        [row(world, 3, new_id()), row(world, 4, new_id()), row(world, 5, new_id())],
    )
    assert heavy.status_code == 422 and heavy.get_json()["code"] == "row_count_mismatch"


def test_a_chunk_of_an_unknown_or_closed_import(client, token, world):
    missing = put_chunk(client, token, 999999, 0, [row(world, 2, new_id())])
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "sale_import_not_found"
    import_id = open_import(client, token, 1).get_json()["import_id"]
    put_chunk(client, token, import_id, 0, [row(world, 2, new_id())])
    closed = put_chunk(client, token, import_id, 1, [row(world, 3, new_id())])
    assert closed.status_code == 409
    assert closed.get_json()["code"] == "import_not_receiving"
    assert closed.get_json()["details"] == [
        {"field": "status", "code": "not_receiving"}
    ]


# ---- the worker ------------------------------------------------------------------


def test_every_accepted_row_is_a_sale_through_the_same_rule(
    client, token, owner, world, service_url
):
    first, second = world["products"][:2]
    stock = {p: on_hand(owner, world, p) for p in (first, second)}
    sale = new_id()
    rows = [
        row(world, 2, sale, product_id=first, quantity=2, unit_price="10.00"),
        # A second line of the same sale, appended to its header.
        row(world, 3, sale, product_id=second, quantity=1, unit_price="4.25"),
        row(world, 4, new_id(), product_id=first, quantity=1, unit_price="7.50"),
    ]
    import_id = load(client, token, service_url, rows)
    done = status(client, token, import_id)
    assert (done["status"], done["received"], done["accepted"], done["rejected"]) == (
        "COMPLETED",
        3,
        3,
        0,
    )
    assert done["pending"] == 0 and done["finished_at"] is not None
    sale_id, total, store = sales(owner, sale)
    assert (total, store) == (Decimal("24.25"), world["store"])
    assert owner.execute(
        "SELECT product_id, quantity, unit_price FROM public.transaction_line"
        " WHERE transaction_id = %s ORDER BY product_id",
        (sale_id,),
    ).fetchall() == sorted([(first, 2, Decimal("10.00")), (second, 1, Decimal("4.25"))])
    # The sale is recorded like any other: observed, captured, stock consumed.
    assert owner.execute(
        "SELECT t.data_origin, c.import_id, c.recorded_by::text"
        " FROM public.transaction AS t JOIN sales.sale_capture AS c USING"
        " (transaction_id) WHERE t.transaction_id = %s",
        (sale_id,),
    ).fetchone() == (
        "OBSERVED",
        import_id,
        done["created_by"],
    )
    assert on_hand(owner, world, first) == stock[first] - 3
    assert on_hand(owner, world, second) == stock[second] - 1
    # One accepted line, one outcome pointing at it.
    assert owner.execute(
        "SELECT line, transaction_id, product_id FROM sales.import_row_outcome"
        " WHERE import_id = %s AND outcome = 'ACCEPTED' ORDER BY line LIMIT 2",
        (import_id,),
    ).fetchall() == [(2, sale_id, first), (3, sale_id, second)]
    # CI's reconciliation query (ADR-0070 Compliance) returns nothing: a completed
    # import has an outcome for every row it announced, and no chunk keeps its rows.
    assert owner.execute(
        "SELECT count(*) FROM sales.import AS i WHERE i.status = 'COMPLETED'"
        " AND (i.total_rows <> (SELECT COALESCE(SUM(c.row_count), 0)"
        "      FROM sales.import_chunk AS c WHERE c.import_id = i.import_id)"
        "   OR i.total_rows <> (SELECT count(*) FROM sales.import_row_outcome AS o"
        "      WHERE o.import_id = i.import_id))"
    ).fetchone() == (0,)
    assert owner.execute(
        "SELECT count(*) FROM sales.import_chunk WHERE status = 'DONE'"
        " AND rows IS NOT NULL"
    ).fetchone() == (0,)
    # `svc-sales:` headers and captures still match one to one (ADR-0039).
    assert owner.execute(
        "SELECT count(*) FROM public.transaction AS t FULL JOIN sales.sale_capture"
        " AS c USING (transaction_id) WHERE (t.source_transaction_id LIKE"
        " 'svc-sales:%%') IS DISTINCT FROM (c.transaction_id IS NOT NULL)"
    ).fetchone() == (0,)


def test_each_failing_row_gets_its_own_reason_and_the_counts_add_up(
    client, token, owner, world, service_url
):
    p0, p1, p2 = world["products"]
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat(timespec="seconds")
    before = datetime.combine(
        world["registered"] - timedelta(days=2), datetime.min.time(), UTC
    ).isoformat()
    inactive = owner.execute(
        "SELECT product_id FROM public.product WHERE NOT is_active LIMIT 1"
    ).fetchone()
    other_store = owner.execute(
        "SELECT min(store_id) FROM public.store WHERE store_id <> %s",
        (world["store"],),
    ).fetchone()[0]
    other_customer = owner.execute(
        "SELECT customer_id::text FROM public.customer"
        " WHERE customer_id::text <> %s AND registered_on <= current_date - 2"
        " ORDER BY customer_id LIMIT 1",
        (world["customer"],),
    ).fetchone()[0]
    sale = new_id()
    rows = [
        row(world, 2, new_id()),  # accepted
        row(world, 3, new_id(), occurred_at=future),
        row(world, 4, new_id(), occurred_at=before),
        row(world, 5, new_id(), occurred_at="2026-10-02T14:03:00"),
        row(world, 6, new_id(), occurred_at="soon"),
        row(world, 7, new_id(), product_id=2_000_000_000),
        row(world, 8, new_id(), quantity=0),
        row(world, 9, new_id(), quantity="two"),
        row(world, 10, new_id(), unit_price="19.999"),
        row(world, 11, new_id(), unit_price="-3"),
        row(world, 12, new_id(), unit_price="abc"),
        row(world, 13, new_id(), customer_id=uuid.uuid4()),
        row(world, 14, new_id(), customer_id="not-a-uuid"),
        row(world, 15, new_id(), store_id=32000),
        row(world, 16, new_id(), channel_id=32000),
        row(world, 17, new_id(), quantity=10**6),
        row(world, 18, "=1+1"),
        row(world, 19, "x" * 51),
        row(world, 20, "   "),
        {"line": 21, "cells": ["T-short", world["customer"]]},
        row(world, 22, sale, product_id=p0),  # accepted
        row(world, 23, sale, product_id=p0),  # repeats line 22
        row(world, 24, sale, product_id=p1, store_id=other_store),
        row(world, 25, sale, product_id=p2, customer_id=other_customer),
    ]
    if inactive:
        rows.append(row(world, 26, new_id(), product_id=inactive[0]))
    import_id = load(client, token, service_url, rows)
    done = status(client, token, import_id)
    refused = rejections(client, token, import_id)
    assert done["status"] == "COMPLETED"
    assert done["received"] == len(rows) == done["accepted"] + done["rejected"]
    assert done["accepted"] == 2 and done["rejected"] == len(rows) - 2
    assert done["pending"] == 0
    # No refused row left a sale behind: two sales, one of them one line.
    made = owner.execute(
        "SELECT count(*) FROM public.transaction WHERE source_transaction_id LIKE %s",
        (PREFIX + "%",),
    ).fetchone()[0]
    assert made == 2

    def got(line: int) -> tuple[str, str | None]:
        return refused[line]["code"], refused[line]["field"]

    assert got(3) == ("future_dated", "occurred_at")
    assert got(4) == ("before_registration", "occurred_at")
    assert got(5) == ("missing_offset", "occurred_at")
    assert got(6) == ("invalid_value", "occurred_at")
    assert got(7) == ("unknown_product", "product_id")
    assert got(8) == ("invalid_quantity", "quantity")
    assert got(9) == ("invalid_value", "quantity")
    assert got(10) == ("invalid_price", "unit_price")
    assert got(11) == ("invalid_price", "unit_price")
    assert got(12) == ("invalid_value", "unit_price")
    assert got(13) == ("unknown_customer", "customer_id")
    assert got(14) == ("invalid_value", "customer_id")
    assert got(15) == ("unknown_store", "store_id") or got(15) == (
        "invalid_value",
        "store_id",
    )
    assert got(16) == ("unknown_channel", "channel_id") or got(16) == (
        "invalid_value",
        "channel_id",
    )
    assert got(17) == ("stock_unavailable", "quantity")
    assert got(18) == ("formula_like_id", "transaction_id")
    assert got(19) == ("id_too_long", "transaction_id")
    assert got(20) == ("invalid_value", "transaction_id")
    assert got(21) == ("wrong_cell_count", None)
    assert got(24) == ("header_mismatch", "store_id")
    assert got(25) == ("header_mismatch", "customer_id")
    duplicate = refused[23]
    assert (duplicate["code"], duplicate["duplicate_of"], duplicate["first_line"]) == (
        "duplicate_row",
        "FILE",
        22,
    )
    assert duplicate["sale_id"] is None and duplicate["field"] is None
    if inactive:
        assert got(26) == ("inactive_product", "product_id")
    # Each sentence names its line and quotes the cell, never more than 64 characters.
    assert refused[8]["message"].startswith("Line 8: ")
    assert '"0"' in refused[8]["message"]
    assert all(len(item["message"]) < 400 for item in refused.values())
    assert refused[19]["message"].count("x") <= 64
    # The report is ordered by line and its total is `rejected`.
    page = client.get(
        f"/v1/sale-imports/{import_id}/rejections?size=5&page=2", headers=token("ADMIN")
    ).get_json()
    assert page["page_info"] == {"page": 2, "size": 5, "total": done["rejected"]}
    assert [item["line"] for item in page["sale_import_rejections"]] == sorted(
        item["line"] for item in page["sale_import_rejections"]
    )


def test_a_row_with_no_stock_record_or_not_enough_stock_changes_nothing(
    client, token, owner, world, service_url
):
    product = world["products"][2]
    owner.execute(
        "DELETE FROM public.inventory WHERE store_id = %s AND product_id = %s",
        (world["store"], product),
    )
    tid = new_id()
    import_id = load(
        client, token, service_url, [row(world, 2, tid, product_id=product)]
    )
    refused = rejections(client, token, import_id)
    assert (refused[2]["code"], refused[2]["field"]) == (
        "no_stock_record",
        "product_id",
    )
    assert sales(owner, tid) is None


def test_a_file_sent_twice_creates_no_sale_twice(
    client, token, owner, world, service_url
):
    sale = new_id()
    rows = [
        row(world, 2, sale, product_id=world["products"][0]),
        row(world, 3, sale, product_id=world["products"][1]),
        row(world, 4, new_id()),
    ]
    first = load(client, token, service_url, rows)
    stock = on_hand(owner, world, world["products"][0])
    count = owner.execute(
        "SELECT count(*) FROM public.transaction_line AS l JOIN public.transaction"
        " AS t USING (transaction_id) WHERE t.source_transaction_id LIKE %s",
        (PREFIX + "%",),
    ).fetchone()[0]
    second = load(client, token, service_url, rows)
    done = status(client, token, second)
    assert (done["status"], done["accepted"], done["rejected"]) == ("COMPLETED", 0, 3)
    assert owner.execute(
        "SELECT count(*) FROM public.transaction_line AS l JOIN public.transaction"
        " AS t USING (transaction_id) WHERE t.source_transaction_id LIKE %s",
        (PREFIX + "%",),
    ).fetchone() == (count,)
    assert on_hand(owner, world, world["products"][0]) == stock
    sale_id = sales(owner, sale)[0]
    refused = rejections(client, token, second)
    assert {
        (item["code"], item["duplicate_of"], item["sale_id"], item["first_line"])
        for item in refused.values()
    } == {
        ("duplicate_row", "RECORDED_SALE", sale_id, None),
        ("duplicate_row", "RECORDED_SALE", sales(owner, rows[2]["cells"][0])[0], None),
    }
    assert first != second
    assert refused[2]["message"].endswith("nothing was added.")


def test_the_web_and_the_service_refuse_each_others_file(
    client, token, owner, world, service_url, retail_url
):
    # A file the web's page loaded first: the import refuses every row of it.
    web_id = "test-csv-" + uuid.uuid4().hex[:10]
    with psycopg.connect(retail_url) as connection:
        web_sale = connection.execute(
            "INSERT INTO public.transaction (source_transaction_id, customer_id,"
            " store_id, channel_id, occurred_at, total, data_origin)"
            " VALUES (%s, %s, %s, %s, now() - interval '1 hour', 0, 'OBSERVED')"
            " RETURNING transaction_id",
            (web_id, world["customer"], world["store"], world["channel"]),
        ).fetchone()[0]
    import_id = load(client, token, service_url, [row(world, 2, web_id)])
    refused = rejections(client, token, import_id)[2]
    assert (refused["code"], refused["duplicate_of"], refused["sale_id"]) == (
        "duplicate_row",
        "RECORDED_SALE",
        web_sale,
    )
    assert owner.execute(
        "SELECT count(*) FROM public.transaction WHERE source_transaction_id = %s",
        (PREFIX + web_id,),
    ).fetchone() == (0,)
    # And a file the service imported first: the web's insert of the same id is
    # refused by the database with SQLSTATE 22023, which ingest_row reports as a
    # malformed row.
    imported = new_id()
    load(client, token, service_url, [row(world, 2, imported)])
    with psycopg.connect(retail_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InvalidParameterValue) as raised:
            connection.execute(
                "INSERT INTO public.transaction (source_transaction_id, customer_id,"
                " store_id, channel_id, occurred_at, total, data_origin)"
                " VALUES (%s, %s, %s, %s, now(), 0, 'OBSERVED')",
                (imported, world["customer"], world["store"], world["channel"]),
            )
        assert "other path" in str(raised.value)


def test_concurrent_loads_of_one_id_by_both_paths_record_it_once(
    owner, world, service_url, retail_url
):
    tid = "test-csv-" + uuid.uuid4().hex[:10]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def web() -> None:
        with psycopg.connect(retail_url, autocommit=True) as connection:
            barrier.wait()
            try:
                connection.execute(
                    "INSERT INTO public.transaction (source_transaction_id,"
                    " customer_id, store_id, channel_id, occurred_at, total,"
                    " data_origin) VALUES (%s, %s, %s, %s, now(), 0, 'OBSERVED')",
                    (tid, world["customer"], world["store"], world["channel"]),
                )
                outcomes.append("web")
            except psycopg.errors.InvalidParameterValue:
                outcomes.append("refused")

    def service() -> None:
        with psycopg.connect(service_url, autocommit=True) as connection:
            barrier.wait()
            try:
                connection.execute(
                    "INSERT INTO public.transaction (source_transaction_id,"
                    " customer_id, store_id, channel_id, occurred_at, total,"
                    " data_origin) VALUES (%s, %s, %s, %s, now(), 0, 'OBSERVED')",
                    (PREFIX + tid, world["customer"], world["store"], world["channel"]),
                )
                outcomes.append("service")
            except psycopg.errors.InvalidParameterValue:
                outcomes.append("refused")

    threads = [threading.Thread(target=web), threading.Thread(target=service)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    # Whichever path won, the other was refused by the database: the id was
    # recorded once, under one spelling.
    assert sorted(outcomes) in (["refused", "service"], ["refused", "web"])
    assert owner.execute(
        "SELECT count(*) FROM public.transaction"
        " WHERE source_transaction_id IN (%s, %s)",
        (tid, PREFIX + tid),
    ).fetchone() == (1,)


def test_a_worker_that_stops_midway_resumes_without_repeating_a_line(
    client, token, owner, world, service_url
):
    rows = [row(world, line, new_id()) for line in range(2, 12)]
    opened = open_import(client, token, len(rows), 1).get_json()
    import_id = opened["import_id"]
    assert put_chunk(client, token, import_id, 0, rows).status_code == 202
    judged = []

    def stop_after_four() -> bool:
        return len(judged) >= 4

    # A worker that dies after four rows: each committed with its outcome.
    with psycopg.connect(service_url, autocommit=True) as connection:
        original = import_worker.process_row

        def counting(*args, **kwargs):
            result = original(*args, **kwargs)
            judged.append(result)
            return result

        import_worker.process_row = counting
        try:
            import_worker.run_once(connection, rate=100_000, stop=stop_after_four)
        finally:
            import_worker.process_row = original
    midway = status(client, token, import_id)
    assert (midway["status"], midway["accepted"], midway["pending"]) == (
        "PROCESSING",
        4,
        6,
    )
    made = owner.execute(
        "SELECT count(*) FROM public.transaction WHERE source_transaction_id LIKE %s",
        (PREFIX + "%",),
    ).fetchone()[0]
    assert made == 4
    # Another worker continues at the first line without an outcome.
    work(service_url)
    done = status(client, token, import_id)
    assert (done["status"], done["accepted"], done["pending"]) == ("COMPLETED", 10, 0)
    assert owner.execute(
        "SELECT count(*), count(DISTINCT line) FROM sales.import_row_outcome"
        " WHERE import_id = %s",
        (import_id,),
    ).fetchone() == (10, 10)
    assert owner.execute(
        "SELECT count(*) FROM public.transaction WHERE source_transaction_id LIKE %s",
        (PREFIX + "%",),
    ).fetchone() == (10,)
    # A chunk that is done keeps no copy of its rows.
    assert owner.execute(
        "SELECT status, rows IS NULL FROM sales.import_chunk WHERE import_id = %s",
        (import_id,),
    ).fetchall() == [("DONE", True)]


def test_a_row_the_worker_already_judged_is_skipped_when_it_comes_back(
    client, token, owner, world, service_url
):
    tid = new_id()
    import_id = open_import(client, token, 1).get_json()["import_id"]
    put_chunk(client, token, import_id, 0, [row(world, 2, tid)])
    with psycopg.connect(service_url, autocommit=True) as connection:
        cells = row(world, 2, tid)["cells"]
        kwargs = {
            "import_id": import_id,
            "created_by": status(client, token, import_id)["created_by"],
            "line": 2,
            "cells": cells,
        }
        assert import_worker.process_row(connection, **kwargs) == "accepted"
        assert import_worker.process_row(connection, **kwargs) == "skipped"
    assert owner.execute(
        "SELECT count(*) FROM public.transaction WHERE source_transaction_id = %s",
        (PREFIX + tid,),
    ).fetchone() == (1,)


def test_two_workers_never_apply_one_chunk_twice(
    client, token, owner, world, service_url
):
    rows = [row(world, line, new_id()) for line in range(2, 22)]
    opened = open_import(client, token, len(rows), 1).get_json()
    put_chunk(client, token, opened["import_id"], 0, rows)
    threads = [threading.Thread(target=work, args=(service_url,)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    done = status(client, token, opened["import_id"])
    assert (done["status"], done["accepted"]) == ("COMPLETED", 20)
    assert owner.execute(
        "SELECT count(*) FROM public.transaction WHERE source_transaction_id LIKE %s",
        (PREFIX + "%",),
    ).fetchone() == (20,)


# ---- cancel, expiry, reads -------------------------------------------------------


def test_cancelling_stops_the_unapplied_chunks_and_keeps_the_accepted_rows(
    client, token, owner, world, service_url
):
    rows = [row(world, line, new_id()) for line in range(2, 8)]
    import_id = open_import(client, token, 6, 3).get_json()["import_id"]
    put_chunk(client, token, import_id, 0, rows[:2])
    put_chunk(client, token, import_id, 1, rows[2:4])
    work(service_url)
    cancelled = client.put(
        f"/v1/sale-imports/{import_id}/status",
        json={"status": "CANCELLED"},
        headers=token("ADMIN"),
    )
    assert cancelled.status_code == 200
    body = cancelled.get_json()
    assert (body["status"], body["accepted"], body["received"], body["pending"]) == (
        "CANCELLED",
        4,
        4,
        0,
    )
    assert body["finished_at"] is not None
    # A second cancel answers the same import, and no more chunks are taken.
    again = client.put(
        f"/v1/sale-imports/{import_id}/status",
        json={"status": "CANCELLED"},
        headers=token("ADMIN"),
    )
    assert again.status_code == 200 and again.get_json() == body
    late = put_chunk(client, token, import_id, 2, rows[4:])
    assert late.status_code == 409
    assert late.get_json()["code"] == "import_not_receiving"
    assert work(service_url) == 0
    assert owner.execute(
        "SELECT count(*) FROM public.transaction WHERE source_transaction_id LIKE %s",
        (PREFIX + "%",),
    ).fetchone() == (4,)


def test_cancelling_with_staged_rows_keeps_them_in_pending(
    client, token, owner, world, service_url
):
    rows = [row(world, line, new_id()) for line in range(2, 6)]
    import_id = open_import(client, token, 4, 1).get_json()["import_id"]
    put_chunk(client, token, import_id, 0, rows)
    cancelled = client.put(
        f"/v1/sale-imports/{import_id}/status",
        json={"status": "CANCELLED"},
        headers=token("ADMIN"),
    ).get_json()
    assert (cancelled["received"], cancelled["accepted"], cancelled["pending"]) == (
        4,
        0,
        4,
    )
    assert work(service_url) == 0
    # The staged payload is dropped, the count is kept.
    assert owner.execute(
        "SELECT rows IS NULL, row_count FROM sales.import_chunk WHERE import_id = %s",
        (import_id,),
    ).fetchone() == (True, 4)


def test_a_finished_import_cannot_be_cancelled_and_one_that_is_unknown_is_404(
    client, token, world, service_url
):
    import_id = load(client, token, service_url, [row(world, 2, new_id())])
    finished = client.put(
        f"/v1/sale-imports/{import_id}/status",
        json={"status": "CANCELLED"},
        headers=token("ADMIN"),
    )
    assert finished.status_code == 409
    assert finished.get_json()["code"] == "import_finished"
    unknown = client.put(
        "/v1/sale-imports/999999/status",
        json={"status": "CANCELLED"},
        headers=token("ADMIN"),
    )
    assert unknown.status_code == 404
    bad = client.put(
        f"/v1/sale-imports/{import_id}/status",
        json={"status": "COMPLETED"},
        headers=token("ADMIN"),
    )
    assert bad.status_code == 422
    assert bad.get_json()["code"] == "invalid_body"
    for path in ("/v1/sale-imports/999999", "/v1/sale-imports/999999/rejections"):
        assert client.get(path, headers=token("ADMIN")).status_code == 404


def test_an_import_idle_for_a_day_expires_and_drops_what_it_staged(
    client, token, owner, world, service_url
):
    rows = [row(world, line, new_id()) for line in range(2, 5)]
    import_id = open_import(client, token, 3, 2).get_json()["import_id"]
    put_chunk(client, token, import_id, 0, rows)
    owner.execute(
        "UPDATE sales.import SET last_chunk_at = now() - interval '25 hours'"
        " WHERE import_id = %s",
        (import_id,),
    )
    assert work(service_url) == 0
    expired = status(client, token, import_id)
    assert (expired["status"], expired["received"], expired["pending"]) == (
        "EXPIRED",
        3,
        3,
    )
    assert expired["finished_at"] is not None
    assert owner.execute(
        "SELECT rows IS NULL FROM sales.import_chunk WHERE import_id = %s",
        (import_id,),
    ).fetchone() == (True,)
    cancel = client.put(
        f"/v1/sale-imports/{import_id}/status",
        json={"status": "CANCELLED"},
        headers=token("ADMIN"),
    )
    assert cancel.status_code == 409
    assert open_import(client, token, 1).status_code == 201


def test_the_list_is_newest_first_and_the_rejection_report_survives(
    app, client, token, world, service_url
):
    first = load(client, token, service_url, [row(world, 2, new_id(), quantity=0)])
    second = load(client, token, service_url, [row(world, 2, new_id())])
    listing = client.get("/v1/sale-imports", headers=token("ADMIN")).get_json()
    ids = [item["import_id"] for item in listing["sale_imports"]]
    assert ids.index(second) < ids.index(first)
    assert listing["page_info"]["total"] == len(ids)
    # The report stays readable after COMPLETED, in XML too.
    xml = client.get(
        f"/v1/sale-imports/{first}/rejections", headers={**token("ADMIN"), **XML}
    )
    assert xml.status_code == 200
    document = etree.fromstring(xml.data)
    app.extensions["mosaiq_kit"].contract.xml_schema.assertValid(document)
    assert document.find(f"{{{NS}}}SaleImportRejection/{{{NS}}}code").text == (
        "invalid_quantity"
    )
    listed = client.get("/v1/sale-imports", headers={**token("ADMIN"), **XML})
    app.extensions["mosaiq_kit"].contract.xml_schema.assertValid(
        etree.fromstring(listed.data)
    )
    for query in ("page=0", "size=101"):
        assert (
            client.get("/v1/sale-imports?" + query, headers=token("ADMIN")).status_code
            == 422
        )


# ---- the sales it records are sales ----------------------------------------------


def test_an_open_import_does_not_stop_a_phone_sale_and_replays_stay_unrelated(
    client, token, owner, world
):
    open_import(client, token, 1)
    body = {
        "customer_id": world["customer"],
        "store_id": world["store"],
        "channel_id": world["channel"],
        "occurred_at": when(),
        "lines": [
            {"product_id": world["products"][0], "quantity": 1, "unit_price": "10.00"}
        ],
    }
    response = client.post(
        "/v1/sales", json=body, headers=idempotency_headers(token("STORE_MANAGER"))
    )
    assert response.status_code == 201


def test_an_accepted_row_reaches_the_recorded_stream_like_any_other_sale(
    build_app, token, owner, world, service_url, redis_connection
):
    stream = "stream:sales.v1.recorded"
    owner.execute("DELETE FROM sales_events.outbox")
    redis_connection.delete(stream)
    app = build_app(relay=True)
    client = app.test_client()
    sale = new_id()
    rows = [
        row(world, 2, sale, product_id=world["products"][0]),
        row(world, 3, sale, product_id=world["products"][1]),
        row(world, 4, new_id(), quantity=0),
    ]
    load(client, token, service_url, rows)
    sale_id = sales(owner, sale)[0]
    # The same outbox rows a phone sale or a CSV row leaves: one for the header
    # insert and one for the appended line's total.
    assert owner.execute(
        "SELECT stream, event_type FROM sales_events.outbox WHERE entity_id = %s"
        " ORDER BY occurred_at",
        (str(sale_id),),
    ).fetchall() == [(stream, "INSERT"), (stream, "UPDATE")]
    deadline = time.monotonic() + 15
    entries = []
    while time.monotonic() < deadline and len(entries) < 2:
        entries = [
            entry
            for entry in redis_connection.xrange(stream)
            if entry[1]["entity_id"] == str(sale_id)
        ]
        time.sleep(0.05)
    assert [entry[1]["event_type"] for entry in entries] == ["INSERT", "UPDATE"]
    # A refused row raised nothing.
    assert owner.execute("SELECT count(*) FROM sales_events.outbox").fetchone() == (2,)


def test_the_worker_runs_as_its_own_process_and_stops_on_sigterm(
    client, token, owner, world, service_url
):
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path

    rows = [row(world, line, new_id()) for line in range(2, 6)]
    import_id = open_import(client, token, 4, 1).get_json()["import_id"]
    assert put_chunk(client, token, import_id, 0, rows).status_code == 202
    environment = {
        **os.environ,
        "DATABASE_URL": service_url,
        "SALES_IMPORT_POLL_SECONDS": "0.1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "app.import_worker"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if status(client, token, import_id)["status"] == "COMPLETED":
                break
            time.sleep(0.1)
        assert status(client, token, import_id)["accepted"] == 4
    finally:
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=15) == 0
