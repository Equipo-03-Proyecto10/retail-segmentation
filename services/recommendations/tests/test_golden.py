"""The RN-40 port held to the web's captured answers (ADR-0045, E-17).

``tests/golden/recommendations.json`` records the web's ``recommend`` for every
seeded customer and three synthetic ones, as of the seed's anchor. Each case is
replayed here through the port, reading the published views as
``svc_recommendations``, inside one rolled-back transaction (the synthetic
setup is part of it).

The web records its reasons as sentences. The port returns ids and figures, so
this test, and only this test, reads the names the sentences use and builds
them again from the port's answer: the item and per-reason category ids, the
segment buyers, the purchase history and the window then all have to agree
with the web, word for word.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.rule import recommend, share
from app.service import ViewReads
from app.store import current_label

FIXTURE = json.loads(
    (Path(__file__).parent / "golden/recommendations.json").read_text()
)
CASES = FIXTURE["cases"]
LIMIT = 10  # the web's default, which the capture used

# Where the port deliberately answers differently, by the case's service_differs.
WEB_STATUS = {"no_open_assignment": "no_segment", "unassigned_result": "no_segment"}

_ADD_CUSTOMER = (
    "INSERT INTO customer (customer_id, name, registration_channel_id, registered_on)"
    " VALUES (%s, %s, 1, %s)"
)
_ADD_RUN = (
    "INSERT INTO segmentation_run"
    " (method, window_days, parameters, customer_count, run_at, data_origin)"
    " VALUES ('RFM_RULES', 180, '{\"window_days\": 180}'::jsonb, 1, %s, 'SEEDED')"
    " RETURNING run_id"
)
_ADD_ROW = (
    "INSERT INTO customer_segment_history (customer_id, run_id, label_code, valid_from)"
    " VALUES (%s, %s, %s, %s)"
)
SYNTHETIC = {
    "synthetic-no-history": None,
    "synthetic-unassigned": (None,),
    "synthetic-no-sale": ("LOYAL",),
}


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _days(count: int) -> str:
    return f"{count} {_plural(count, 'day', 'days')}"


class Names:
    """The names the web's sentences use, read as the owner for this test only."""

    def __init__(self, owner: psycopg.Connection):
        self.categories = dict(owner.execute("SELECT category_id, name FROM category"))
        self.labels = dict(owner.execute("SELECT label_code, name FROM segment_label"))
        self.stores = dict(owner.execute("SELECT store_id, name FROM store"))
        self.products = dict(owner.execute("SELECT product_id, name FROM product"))

    def placed(self, own: int, matched: int) -> str:
        if matched == own:
            return f"In {self.categories[own]}"
        return f"In {self.categories[own]}, part of {self.categories[matched]}"


def _setup(case_id: str, owner: psycopg.Connection, anchor: datetime) -> None:
    if case_id not in SYNTHETIC:
        return
    customer = FIXTURE_INPUT[case_id]["customer_id"]
    owner.execute(_ADD_CUSTOMER, (customer, f"Golden: {case_id}", anchor.date()))
    if SYNTHETIC[case_id] is None:
        return
    (run_id,) = owner.execute(_ADD_RUN, (anchor,)).fetchone()
    owner.execute(_ADD_ROW, (customer, run_id, SYNTHETIC[case_id][0], anchor))


FIXTURE_INPUT = {case["id"]: case["input"] for case in CASES}


def _reason_text(
    reason: dict[str, Any], item: dict[str, Any], web: dict[str, Any], names: Names
) -> str:
    """The web's sentence for one of the port's reasons."""
    own = item["category_id"]
    if reason["signal"] == "segment":
        buyers = reason["segment_buyers"]
        label = names.labels.get(web["label_code"]) or web["label_code"]
        return (
            f"{buyers} other {label} {_plural(buyers, 'customer', 'customers')} "
            f"bought it in the last {_days(web['window_days'])}"
        )
    if reason["signal"] == "preferred_category":
        return (
            f"{names.placed(own, reason['category_id'])}, a category the customer "
            "said they like"
        )
    matched = reason["category_id"]
    source = (
        f"The customer bought from {names.categories[own]}"
        if matched == own
        else f"{names.placed(own, matched)}; the customer bought from "
        f"{names.categories[matched]}"
    )
    return source


def test_the_fixture_is_this_port_and_notes_the_split_of_no_segment() -> None:
    assert FIXTURE["header"]["port"] == "recommendations"
    differs = {case["id"] for case in CASES if "service_differs" in case}
    assert differs == {"synthetic-no-history", "synthetic-unassigned"}
    seeded = [case for case in CASES if case["id"].startswith("customer-")]
    assert len(seeded) == 30


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_port_answers_as_the_web_did(case: dict[str, Any], owner_url: str) -> None:
    web = case["outcome"]
    customer = case["input"]["customer_id"]
    with psycopg.connect(owner_url) as connection:
        (anchor,) = connection.execute(
            "SELECT max(run_at) FROM segmentation_run"
        ).fetchone()
        names = Names(connection)
        _setup(case["id"], connection, anchor)
        # The port reads as itself: only the seven views it is granted.
        connection.execute("SET LOCAL ROLE svc_recommendations")
        label = current_label(connection, customer)
        result = recommend(ViewReads(connection, label), customer, anchor, LIMIT)
        connection.rollback()

    status = WEB_STATUS.get(result.status, result.status)
    assert status == web["status"]
    if result.status in WEB_STATUS:
        assert "service_differs" in case
    assert result.usual_store_id == web["store_id"]
    if web["status"] != "no_segment":
        assert result.label_code == web["label_code"]

    assert [i["product_id"] for i in result.items] == [
        i["product_id"] for i in web["items"]
    ]
    for port_item, web_item in zip(result.items, web["items"], strict=True):
        assert port_item["category_id"] == web_item["category_id"]
        assert port_item["quantity_on_hand"] == web_item["in_stock"]
        assert [r["signal"] for r in port_item["reasons"]] == [
            r["signal"] for r in web_item["reasons"]
        ]
        for reason, web_reason in zip(
            port_item["reasons"], web_item["reasons"], strict=True
        ):
            text = _reason_text(reason, port_item, web, names)
            if reason["signal"] != "purchase_history":
                assert text == web_reason["text"]
                continue
            # "<source> in H of their T purchases in the last N days"
            match = re.fullmatch(
                r"(.*) in (\d+) of their (\d+) purchases in the last (.+)",
                web_reason["text"],
            )
            assert match is not None
            assert text == match.group(1)
            history, total = int(match.group(2)), int(match.group(3))
            assert reason["share"] == share(history, total)
            assert match.group(4) == _days(web["window_days"])
