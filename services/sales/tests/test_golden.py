"""The sales port held to the web's captured outcomes (ADR-0039, E-17).

``tests/golden/sales.json`` records one-line calls to the web's ``ingest_row``
on a fresh seed. Each case is replayed here as a one-line ``POST /v1/sales``
with a fresh idempotency key and a fresh database copy. Accepted cases must
leave the captured state behind; refused cases map the captured sentence to
the stable sales-service code decided by ADR-0039.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

FIXTURE = json.loads((Path(__file__).parent / "golden/sales.json").read_text())
CASES = {case["id"]: case for case in FIXTURE["cases"]}

# ADR-0039 deliberately changes these seams. These are design differences,
# not fixture drift: the fixture is a row port, while this endpoint is a sale.
DIFFERS = {
    "whole-sale": (
        "The phone endpoint refuses the whole sale and rolls back every line; "
        "the web fixture records the result of one CSV row at a time."
    ),
    "error-codes": (
        "The web records English rejection sentences; the service returns the "
        "contract's stable machine-readable codes."
    ),
    "service-namespace": (
        "The web refuses a CSV source id in svc-sales:, while svc_sales must "
        "create that reserved namespace itself, so the HTTP case is accepted."
    ),
    "known-key": (
        "A known Idempotency-Key replays the original phone sale instead of "
        "appending another line to its transaction."
    ),
}

REFUSALS = {
    "future-dated": (422, "future_dated"),
    "before-registration": (422, "before_registration"),
    "inactive-product": (422, "inactive_product"),
    "zero-quantity": (422, "invalid_quantity"),
    "negative-quantity": (422, "invalid_quantity"),
    "zero-price": (422, "invalid_price"),
    "negative-price": (422, "invalid_price"),
    "three-decimal-price": (422, "invalid_price"),
    "unknown-customer": (422, "unknown_customer"),
    "unknown-product": (422, "unknown_product"),
    "unknown-store": (422, "unknown_store"),
    "unknown-channel": (422, "unknown_channel"),
    "no-inventory-row": (422, "no_stock_record"),
    "insufficient-stock": (409, "stock_unavailable"),
}


def _anchor(owner: psycopg.Connection) -> datetime:
    """The live seed's anchor, as tests.golden.capture reads it.

    The seed dates everything from the moment it loads, so a case's
    days_before_anchor is counted from this database's newest run, never from
    the fixture header's capture time.
    """

    (anchor,) = owner.execute("SELECT max(run_at) FROM segmentation_run").fetchone()
    assert anchor is not None, "load sql/02_seed_30_per_table.sql first"
    return anchor


def _occurred_at(value: str | dict[str, int], anchor: datetime) -> str:
    if isinstance(value, str):
        return value
    return (anchor - timedelta(days=value["days_before_anchor"])).isoformat()


def _body(case: dict[str, Any], anchor: datetime) -> dict[str, Any]:
    given = case["input"]
    return {
        "customer_id": given["customer_id"],
        "store_id": given["store_id"],
        "channel_id": given["channel_id"],
        "occurred_at": _occurred_at(given["occurred_at"], anchor),
        "lines": [
            {
                "product_id": given["product_id"],
                "quantity": given["quantity"],
                "unit_price": given["unit_price"],
            }
        ],
    }


def _post(
    client: Any,
    token: Callable[..., dict[str, str]],
    body: dict[str, Any],
    key: uuid.UUID,
) -> Any:
    return client.post(
        "/v1/sales",
        json=body,
        headers={**token("STORE_MANAGER"), "Idempotency-Key": str(key)},
    )


def _apply_setup(case_id: str, owner: psycopg.Connection) -> None:
    if case_id == "inactive-product":
        owner.execute(
            "UPDATE product SET is_active = %s WHERE product_id = %s",
            (False, 2),
        )


def _observed(case_id: str, owner: psycopg.Connection) -> dict[str, Any]:
    if case_id != "accepted-line":
        return {}
    quantity = owner.execute(
        "SELECT quantity_on_hand FROM inventory"
        " WHERE store_id = %s AND product_id = %s",
        (1, 1),
    ).fetchone()
    assert quantity is not None
    return {"quantity_on_hand_after": quantity[0]}


def test_the_fixture_is_the_sales_port_and_every_refusal_has_a_mapping() -> None:
    assert FIXTURE["header"]["port"] == "sales"
    refused = {
        case_id
        for case_id, case in CASES.items()
        if case["outcome"]["outcome"] == "refused" and case_id != "service-namespace"
    }
    assert set(REFUSALS) == refused
    assert set(DIFFERS) == {
        "whole-sale",
        "error-codes",
        "service-namespace",
        "known-key",
    }


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_service_matches_the_captured_sales_outcome(
    case_id: str,
    client: Any,
    token: Callable[..., dict[str, str]],
    owner: psycopg.Connection,
) -> None:
    case = CASES[case_id]
    _apply_setup(case_id, owner)
    try:
        reply = _post(client, token, _body(case, _anchor(owner)), uuid.uuid4())
        answer = (
            reply.status_code,
            (reply.get_json(silent=True) or {}).get("code"),
        )

        if case_id == "service-namespace":
            assert answer == (201, None), DIFFERS["service-namespace"]
            return
        if case["outcome"]["outcome"] == "accepted":
            assert answer == (201, None)
            assert _observed(case_id, owner) == case["outcome"].get("observed", {})
            return
        assert answer == REFUSALS[case_id]
    finally:
        if case_id == "inactive-product":
            owner.execute(
                "UPDATE product SET is_active = %s WHERE product_id = %s",
                (True, 2),
            )


def test_a_known_key_replays_instead_of_appending(
    client: Any,
    token: Callable[..., dict[str, str]],
    owner: psycopg.Connection,
) -> None:
    body = _body(CASES["accepted-line"], _anchor(owner))
    key = uuid.uuid4()
    first = _post(client, token, body, key)
    replay = _post(client, token, body, key)

    assert first.status_code == 201
    assert replay.status_code == 200, DIFFERS["known-key"]
    assert replay.headers["Idempotent-Replayed"] == "true"
    source_id = "svc-sales:" + str(key)
    rows = owner.execute(
        "SELECT count(*) FROM transaction_line AS l"
        " JOIN public.transaction AS t USING (transaction_id)"
        " WHERE t.source_transaction_id = %s",
        (source_id,),
    ).fetchone()
    assert rows == (1,)
