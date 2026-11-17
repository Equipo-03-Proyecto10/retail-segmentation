"""The import corpus of ADR-0045 and ADR-0070 section 4: the web's captured
outcomes, replayed as one-row imports.

`tests/golden/sales.json` records one-line calls to the web's `ingest_row` on a
fresh seed. `test_golden.py` replays them through `POST /v1/sales`; here the
same cases go through the import worker. A line the web accepted must be
accepted by the import, and a line the web refused must be rejected with the
code `POST /v1/sales` gives it, because both paths call one rule. Where the
paths differ on purpose, the difference is named.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from tests.test_golden import (
    CASES,
    FIXTURE,
    REFUSALS,
    _anchor,
    _apply_setup,
    _observed,
    _occurred_at,
)
from tests.test_imports import PREFIX, load, rejections, status

DIFFERS = {
    "service-namespace": (
        "The web refuses a CSV id that starts with svc-sales:, because it is the"
        " phone's namespace. The import writes every id under svc-sales:imp:, so"
        " the same text is just an id and the row is accepted."
    ),
}


def _cells(case: dict[str, Any], anchor: Any) -> list[str]:
    given = case["input"]
    return [
        given["source_transaction_id"],
        given["customer_id"],
        str(given["store_id"]),
        str(given["channel_id"]),
        _occurred_at(given["occurred_at"], anchor),
        str(given["product_id"]),
        str(given["quantity"]),
        str(given["unit_price"]),
    ]


def test_the_corpus_is_the_sales_port_and_every_refusal_has_its_code() -> None:
    assert FIXTURE["header"]["port"] == "sales"
    refused = {
        case_id
        for case_id, case in CASES.items()
        if case["outcome"]["outcome"] == "refused" and case_id not in DIFFERS
    }
    assert set(REFUSALS) == refused


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_import_judges_a_row_as_the_web_and_the_phone_do(
    case_id: str, client: Any, token: Any, owner: psycopg.Connection, service_url: str
) -> None:
    case = CASES[case_id]
    _apply_setup(case_id, owner)
    try:
        # A fresh id per case, so no case sees another's sale.
        cells = _cells(case, _anchor(owner))
        if case_id != "service-namespace":
            cells[0] = "GOLDEN-" + uuid.uuid4().hex[:10]
        import_id = load(client, token, service_url, [{"line": 2, "cells": cells}])
        done = status(client, token, import_id)
        assert done["status"] == "COMPLETED"
        assert done["received"] == done["accepted"] + done["rejected"] == 1
        if case["outcome"]["outcome"] == "accepted" or case_id in DIFFERS:
            assert (done["accepted"], done["rejected"]) == (1, 0), DIFFERS.get(case_id)
            assert owner.execute(
                "SELECT count(*) FROM public.transaction"
                " WHERE source_transaction_id = %s",
                (PREFIX + cells[0],),
            ).fetchone() == (1,)
            if case_id == "accepted-line":
                assert _observed(case_id, owner) == case["outcome"]["observed"]
            return
        assert (done["accepted"], done["rejected"]) == (0, 1)
        assert rejections(client, token, import_id)[2]["code"] == REFUSALS[case_id][1]
        assert owner.execute(
            "SELECT count(*) FROM public.transaction"
            " WHERE source_transaction_id = %s",
            (PREFIX + cells[0],),
        ).fetchone() == (0,)
    finally:
        if case_id == "inactive-product":
            owner.execute(
                "UPDATE product SET is_active = %s WHERE product_id = %s", (True, 2)
            )
