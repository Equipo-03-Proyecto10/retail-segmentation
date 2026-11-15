"""Consent and restrictions (F24-06, ADR-0066, ADR-0067), over the real views.

A customer whose PERSONALIZATION is not granted is answered ``none_match`` with
nothing of theirs read, on every request and before the cache. A restricted
category and everything below it never appears. The log is append-only, so each
test that changes it puts it back with a later row, never a delete.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from flask.testing import FlaskClient
from lxml import etree

from app import rule, store
from app.cache import result_key

ROOT = Path(__file__).resolve().parents[1]
NS = "{https://mosaiq.maxthecoder.online/xml/recommendations/v1}"
FIXTURE = json.loads((ROOT / "tests/golden/recommendations.json").read_text())
CUSTOMER = next(
    case["input"]["customer_id"]
    for case in FIXTURE["cases"]
    if case["outcome"]["status"] == "recommended"
)
ADMIN_USER = "11111111-1111-1111-1111-000000000001"
# Seeded with PERSONALIZATION withdrawn (every fifth customer).
WITHDRAWN = "00000000-0000-0000-0000-000000000010"
_CONSENT = (
    "INSERT INTO preferences.consent_decision"
    " (customer_id, purpose, decision, recorded_at, recorded_by, source, basis)"
    " VALUES (%s, 'PERSONALIZATION', %s, now(), %s, 'STAFF_ON_BEHALF', 'EXPLICIT')"
)
_RESTRICTION = (
    "INSERT INTO preferences.restriction_decision"
    " (customer_id, kind, category_id, decision, recorded_at, recorded_by, source)"
    " VALUES (%s, 'INTEREST_CATEGORY', %s, %s, now(), %s, 'STAFF_ON_BEHALF')"
)


def _ask(client: FlaskClient, token: Callable[..., dict], customer: str, **extra: str):
    response = client.get(
        "/v1/recommendations",
        query_string={"customer_id": customer, "limit": "50", **extra},
        headers=token(),
    )
    assert response.status_code == 200
    return response


@contextmanager
def withdrawn(owner: psycopg.Connection, customer: str) -> Iterator[None]:
    owner.execute(_CONSENT, (customer, "WITHDRAWN", ADMIN_USER))
    try:
        yield
    finally:
        owner.execute(_CONSENT, (customer, "GRANTED", ADMIN_USER))


@contextmanager
def restricted(
    owner: psycopg.Connection, customer: str, category: int
) -> Iterator[None]:
    owner.execute(_RESTRICTION, (customer, category, "IMPOSED", ADMIN_USER))
    try:
        yield
    finally:
        owner.execute(_RESTRICTION, (customer, category, "LIFTED", ADMIN_USER))


@contextmanager
def revoked(owner: psycopg.Connection, view: str) -> Iterator[None]:
    owner.execute(f"REVOKE SELECT ON {view} FROM svc_recommendations")  # noqa: S608
    try:
        yield
    finally:
        owner.execute(f"GRANT SELECT ON {view} TO svc_recommendations")  # noqa: S608


def _parents(owner: psycopg.Connection) -> dict[int, int | None]:
    return dict(
        owner.execute(
            "SELECT category_id, parent_category_id"
            " FROM api_categories.category_tree_v1"
        ).fetchall()
    )


def _subtree(owner: psycopg.Connection, root: int) -> set[int]:
    parents = _parents(owner)
    return {c for c in parents if root in rule.lineage(c, parents)}


def _nothing(body: dict[str, Any]) -> None:
    assert body["status"] == "none_match"
    assert body["items"] == []
    assert body["label_code"] is None
    assert body["run_id"] is None
    assert body["usual_store_id"] is None


def test_a_withdrawn_customer_gets_nothing_and_nothing_of_theirs_is_read(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    _nothing(_ask(client, token, WITHDRAWN).get_json())


def test_the_withdrawn_answer_validates_against_the_contract(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    schema = etree.XMLSchema(
        etree.parse(str(ROOT / "contracts/recommendations/recommendations.xsd"))
    )
    response = client.get(
        "/v1/recommendations",
        query_string={"customer_id": WITHDRAWN},
        headers={**token(), "Accept": "application/xml"},
    )
    assert response.status_code == 200
    document = etree.fromstring(response.data)
    schema.assertValid(document)
    assert document.findtext(f"{NS}status") == "none_match"
    items = document.find(f"{NS}items")
    assert items is None or len(items) == 0


def test_a_withdrawal_is_seen_by_the_next_read_through_the_cache(
    client: FlaskClient,
    token: Callable[..., dict],
    owner: psycopg.Connection,
    redis_connection: Any,
) -> None:
    before = _ask(client, token, CUSTOMER).get_json()
    assert before["status"] == "recommended"
    # The recommended answer is cached; consent is read before the cache.
    assert _ask(client, token, CUSTOMER).get_json() == before
    with withdrawn(owner, CUSTOMER):
        _nothing(_ask(client, token, CUSTOMER).get_json())
        # The withdrawn answer is never stored.
        assert not [
            key
            for key in redis_connection.scan_iter("recommendations:cache:*")
            if CUSTOMER in key and ":none:" in key
        ]
    assert _ask(client, token, CUSTOMER).get_json()["status"] == "recommended"


def test_a_customer_with_no_decision_is_not_personalized(
    owner: psycopg.Connection,
) -> None:
    assert not store.personalization_granted(owner, str(uuid.uuid4()))
    assert store.personalization_granted(owner, CUSTOMER)
    assert not store.personalization_granted(owner, WITHDRAWN)


@pytest.mark.parametrize(
    "view",
    ["api_preferences.consent_state_v1", "api_preferences.restriction_v1"],
)
def test_a_view_that_cannot_be_read_withdraws_personalization(
    client: FlaskClient,
    token: Callable[..., dict],
    owner: psycopg.Connection,
    view: str,
) -> None:
    with revoked(owner, view):
        _nothing(_ask(client, token, CUSTOMER).get_json())
    assert _ask(client, token, CUSTOMER).get_json()["status"] == "recommended"


def test_a_restricted_category_and_those_below_it_never_appear(
    client: FlaskClient, token: Callable[..., dict], owner: psycopg.Connection
) -> None:
    parents = _parents(owner)
    items = _ask(client, token, CUSTOMER).get_json()["items"]
    # The top of the first item's lineage covers it and everything below.
    chosen = rule.lineage(items[0]["category_id"], parents)[-1]
    covered = _subtree(owner, chosen)
    with restricted(owner, CUSTOMER, chosen):
        body = _ask(client, token, CUSTOMER).get_json()
        assert not any(item["category_id"] in covered for item in body["items"])
        for item in body["items"]:
            for reason in item["reasons"]:
                assert reason["category_id"] not in covered


def test_a_restriction_covers_the_subtree_whatever_the_signal() -> None:
    parents = {1: None, 2: 1, 3: 2, 4: None}
    stocked = [rule.Stocked(10, 3, 5), rule.Stocked(11, 4, 5)]
    kwargs: dict[str, Any] = {
        "preferred": {1, 4},
        "category_purchases": {},
        "parents": parents,
        "total_purchases": 0,
        "purchased": set(),
        "buyers": {10: 2, 11: 2},
        "limit": 10,
    }
    assert [i["product_id"] for i in rule.rank(stocked, **kwargs)] == [10, 11]
    # Category 1 is above 3, which the product carries; 4 is beside it.
    only = rule.rank(stocked, restricted={1}, **kwargs)
    assert [i["product_id"] for i in only] == [11]


def test_a_restriction_is_seen_by_the_next_read_through_the_cache(
    client: FlaskClient, token: Callable[..., dict], owner: psycopg.Connection
) -> None:
    items = _ask(client, token, CUSTOMER).get_json()["items"]
    _ask(client, token, CUSTOMER)  # cached
    category = items[0]["category_id"]
    covered = _subtree(owner, category)
    with restricted(owner, CUSTOMER, category):
        after = _ask(client, token, CUSTOMER).get_json()["items"]
        assert not any(item["category_id"] in covered for item in after)
    back = _ask(client, token, CUSTOMER).get_json()["items"]
    assert any(item["category_id"] in covered for item in back)


# ---------- the rule and the key, with no database ----------


class _Spy:
    """Reads that refuse everything but consent."""

    def __init__(self, granted: bool, restricted: frozenset[int] = frozenset()):
        self.granted = granted
        self.restricted = restricted
        self.calls: list[str] = []

    def personalization_granted(self, customer_id: str) -> bool:
        self.calls.append("consent")
        return self.granted

    def restricted_categories(self, customer_id: str) -> frozenset[int]:
        self.calls.append("restrictions")
        return self.restricted

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"{name} was read for a customer who is not granted")


def test_nothing_but_consent_is_read_when_it_is_not_granted() -> None:
    spy = _Spy(granted=False)
    result = rule.answer(spy, CUSTOMER, datetime.now(UTC), 10)  # type: ignore[arg-type]
    assert result.status == "none_match" and result.items == []
    assert spy.calls == ["consent"]


def test_an_unreadable_view_is_not_granted() -> None:
    class Broken(_Spy):
        def restricted_categories(self, customer_id: str) -> frozenset[int]:
            raise rule.Unreadable("no")

    assert not rule.eligibility(Broken(True), CUSTOMER).personalization  # type: ignore[arg-type]


def test_the_cache_key_carries_the_restrictions() -> None:
    plain = result_key(CUSTOMER, 3, 10)
    assert result_key(CUSTOMER, 3, 10, frozenset({4, 2})) == plain + ":r2-4"
    assert result_key(CUSTOMER, 3, 10, frozenset({2, 4})) != plain
