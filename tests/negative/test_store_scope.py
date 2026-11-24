"""Operations F19-03 narrows refuse another store's rows (F19-07, #757).

The four floor roles work only in their assigned stores (ADR-0060). For each
narrowed operation of sales, engagement and check-ins, a real floor account
assigned to one store asks for another and is refused 403 `forbidden_store`,
in JSON and in XML, with no data. The accounts are throwaway ones, assigned
through users-service by the administrator, so no seeded account changes.

Not narrowed, and so not here: stock levels, catalogs, customers, segments and
recommendations (ADR-0060), and every role F19-03 leaves unconfined.

Cases that need a row in the other store (a read by id) make one as `ADMIN`,
which persists, so they run on the local stack only. Every refusal case
writes nothing.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from tests.negative.stack import Evidence, Reply, Session, Stack, require

SALES = "/api/sales/v1"
ENGAGEMENT = "/api/engagement/v1"
CHECK_INS = "/api/check-ins/v1"
STORE_ASSIGNMENTS = "/api/users/v1/store-assignments"
COMMON = "{https://mosaiq.maxthecoder.online/xml/common/v1}"
JSON, XML = "application/json", "application/xml"


def xml_code(reply: Reply) -> str | None:
    if reply.media_type != XML or not reply.body:
        return None
    try:
        return ET.fromstring(reply.body).findtext(f"{COMMON}code")
    except ET.ParseError:
        return None


def outcome(reply: Reply) -> str:
    code = reply.code() or xml_code(reply)
    return f"{reply.status} {code}" if code else str(reply.status)


@dataclass
class Floor:
    """Two throwaway floor accounts and the two stores the cases use."""

    stack: Stack
    manager: Session
    seller: Session
    home: int
    other: int
    product_id: int
    list_price: str
    customer_id: str
    channel_id: int

    def assign(self, session: Session, stores: list[int]) -> None:
        reply = self.stack.edge.json(
            "PUT",
            f"{STORE_ASSIGNMENTS}/{session.account.user_id}",
            {"store_ids": stores},
            token=self.stack.token("ADMIN"),
        )
        require(reply, 200)

    def call(
        self,
        session: Session,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        accept: str = JSON,
        headers: dict[str, str] | None = None,
    ) -> Reply:
        edge = self.stack.edge
        if body is None:
            return edge.call(
                method, path, token=session.token(), accept=accept, headers=headers
            )
        return edge.json(
            method,
            path,
            body,
            token=session.token(),
            accept=accept,
            headers=headers,
        )

    def admin(self, method: str, path: str, body: dict | None = None, **kwargs):
        return self.call(self.stack.sessions["ADMIN"], method, path, body, **kwargs)

    def sale(self, store_id: int) -> dict:
        return {
            "customer_id": self.customer_id,
            "store_id": store_id,
            "channel_id": self.channel_id,
            "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "lines": [
                {
                    "product_id": self.product_id,
                    "quantity": 1,
                    "unit_price": self.list_price,
                }
            ],
        }

    def event(self, store_id: int) -> dict:
        return {
            "client_event_id": str(uuid.uuid4()),
            "type": "QR_SCAN",
            "store_id": store_id,
            "product_id": self.product_id,
            "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    def check_in(self, store_id: int) -> dict:
        return {
            "client_check_in_id": str(uuid.uuid4()),
            "store_id": store_id,
            "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }


@pytest.fixture(scope="module")
def floor(deployed_stack: Stack) -> Iterator[Floor]:
    stack = deployed_stack
    token = stack.token("ADMIN")
    edge = stack.edge
    stores = edge.call("GET", "/api/stores/v1/stores?size=100", token=token)
    require(stores, 200)
    store_ids = [
        s["store_id"] for s in stores.json()["stores"] if s.get("is_active", True)
    ]
    levels = edge.call("GET", "/api/stock/v1/stock-levels?size=100", token=token)
    require(levels, 200)
    level = next(x for x in levels.json()["stock_levels"] if x["quantity_on_hand"] > 0)
    other = level["store_id"]
    home = next(s for s in store_ids if s != other)
    # The sale is charged its product's list price, so the discount rule
    # (RN-13) never refuses a sale these cases expect to be accepted.
    product = edge.call(
        "GET", f"/api/products/v1/products/{level['product_id']}", token=token
    )
    require(product, 200)
    customers = edge.call("GET", "/api/customers/v1/customers?size=1", token=token)
    require(customers, 200)
    channels = edge.call("GET", "/api/sales/v1/channels", token=token)
    require(channels, 200)

    with (
        stack.throwaway_account("STORE_MANAGER") as manager_account,
        stack.throwaway_account("SALES_ASSOCIATE") as seller_account,
    ):
        scope = Floor(
            stack,
            stack.sign_in(manager_account),
            stack.sign_in(seller_account),
            home,
            other,
            level["product_id"],
            product.json()["list_price"],
            customers.json()["customers"][0]["customer_id"],
            channels.json()["channels"][0]["channel_id"],
        )
        scope.assign(scope.manager, [home])
        scope.assign(scope.seller, [home])
        yield scope


@dataclass
class Case:
    """One narrowed operation, asked for the store the caller is not in."""

    area: str
    label: str
    role: str  # "manager" or "seller"
    request: Callable[[Floor], tuple[str, str, dict | None]]


CASES = [
    Case(
        "sales",
        "POST /v1/sales (recordSale), another store",
        "seller",
        lambda f: ("POST", f"{SALES}/sales", f.sale(f.other)),
    ),
    Case(
        "sales",
        "GET /v1/sales (listSales), another store's filter",
        "manager",
        lambda f: ("GET", f"{SALES}/sales?store_id={f.other}", None),
    ),
    Case(
        "sales",
        "GET /v1/sale-reversals (listSaleReversals), another store's filter",
        "manager",
        lambda f: ("GET", f"{SALES}/sale-reversals?store_id={f.other}", None),
    ),
    Case(
        "sales",
        "GET /v1/sale-promotion-totals (listSalePromotionTotals), another store",
        "manager",
        lambda f: (
            "GET",
            f"{SALES}/sale-promotion-totals?store_id={f.other}"
            "&from=2026-01-01T00:00:00Z&to=2027-01-01T00:00:00Z",
            None,
        ),
    ),
    Case(
        "engagement",
        "POST /v1/engagement-events, another store",
        "seller",
        lambda f: ("POST", f"{ENGAGEMENT}/engagement-events", f.event(f.other)),
    ),
    Case(
        "engagement",
        "GET /v1/engagement-events, another store's filter",
        "manager",
        lambda f: ("GET", f"{ENGAGEMENT}/engagement-events?store_id={f.other}", None),
    ),
    Case(
        "engagement",
        "GET /v1/engagement-summaries, another store's filter",
        "manager",
        lambda f: (
            "GET",
            f"{ENGAGEMENT}/engagement-summaries?group_by=type&store_id={f.other}",
            None,
        ),
    ),
    Case(
        "check-ins",
        "POST /v1/check-ins, another store",
        "seller",
        lambda f: ("POST", f"{CHECK_INS}/check-ins", f.check_in(f.other)),
    ),
    Case(
        "check-ins",
        "GET /v1/check-ins, another store's filter",
        "manager",
        lambda f: ("GET", f"{CHECK_INS}/check-ins?store_id={f.other}", None),
    ),
]


def idempotent(method: str, path: str) -> dict[str, str] | None:
    """Sales writes need a key; the refusal precedes it, so it is never kept."""
    if method == "POST" and path.endswith(("/sales", "/sale-reversals")):
        return {"Idempotency-Key": str(uuid.uuid4())}
    return None


@pytest.mark.parametrize("accept", [JSON, XML])
@pytest.mark.parametrize("case", CASES, ids=[c.label for c in CASES])
def test_a_narrowed_operation_refuses_another_store(
    floor: Floor, evidence: Evidence, case: Case, accept: str
) -> None:
    session = floor.manager if case.role == "manager" else floor.seller
    method, path, body = case.request(floor)
    reply = floor.call(
        session, method, path, body, accept=accept, headers=idempotent(method, path)
    )
    refused = reply.status == 403 and (reply.code() or xml_code(reply)) == (
        "forbidden_store"
    )
    # No data: the refusal names no sale, event, check-in or count.
    refused &= b'"total"' not in reply.body and b"<total" not in reply.body
    held = evidence.record(
        "store scope",
        f"{case.label}, {'XML' if accept == XML else 'JSON'}",
        "403 forbidden_store",
        outcome(reply),
        refused,
    )
    assert held, reply.outcome()


def test_a_list_without_a_store_filter_names_only_the_callers_stores(
    floor: Floor, evidence: Evidence
) -> None:
    """The unfiltered lists carry nothing of another store."""
    held = True
    for _area, path, key in (
        ("sales", f"{SALES}/sales?size=100", "sales"),
        ("engagement", f"{ENGAGEMENT}/engagement-events?size=100", "engagement_events"),
        ("check-ins", f"{CHECK_INS}/check-ins?size=100", "check_ins"),
    ):
        reply = floor.call(floor.manager, "GET", path)
        require(reply, 200)
        stores = {row["store_id"] for row in reply.json()[key]}
        held &= evidence.record(
            "store scope",
            f"GET {path.split('?')[0].removeprefix('/api/')}, no store filter",
            f"only store {floor.home}",
            f"stores {sorted(stores)}",
            stores <= {floor.home},
        )
    assert held


def test_roles_left_unconfined_see_every_store(
    floor: Floor, evidence: Evidence
) -> None:
    held = True
    for path in (
        f"{SALES}/sales?store_id={floor.other}",
        f"{ENGAGEMENT}/engagement-events?store_id={floor.other}",
        f"{CHECK_INS}/check-ins?store_id={floor.other}",
    ):
        reply = floor.admin("GET", path)
        held &= evidence.check("store scope", f"ADMIN on GET {path}", reply, 200)
    assert held


def test_a_removed_assignment_is_refused_on_the_next_call(
    floor: Floor, evidence: Evidence
) -> None:
    """Real time, no fakes: the replace commits, the very next call is refused."""
    session = floor.manager
    path = f"{SALES}/sales?store_id={floor.home}"
    held = evidence.check(
        "store scope",
        "an assigned store before the removal",
        floor.call(session, "GET", path),
        200,
    )
    floor.assign(session, [])
    try:
        held &= evidence.check(
            "store scope",
            "the same call after the assignment is removed",
            floor.call(session, "GET", path),
            403,
            "forbidden_store",
        )
    finally:
        floor.assign(session, [floor.home])
    assert held


def test_reads_by_id_refuse_another_stores_rows(
    deployed_stack: Stack, floor: Floor, evidence: Evidence
) -> None:
    if deployed_stack.is_instance:
        evidence.not_run(
            "store scope",
            "reads and reversals by id of another store's rows",
            "They need a sale, a reversal, an event and a check-in in another "
            "store, which persist; the local stack's run covers them.",
        )
        pytest.skip("rows in another store persist; local stack only")

    def made(reply: Reply, key: str) -> str:
        require(reply, 201)
        return str(reply.json()[key])

    keyed = lambda: {"Idempotency-Key": str(uuid.uuid4())}  # noqa: E731
    sale = made(
        floor.admin("POST", f"{SALES}/sales", floor.sale(floor.other), headers=keyed()),
        "sale_id",
    )
    spare = made(
        floor.admin("POST", f"{SALES}/sales", floor.sale(floor.other), headers=keyed()),
        "sale_id",
    )
    reversal = made(
        floor.admin(
            "POST",
            f"{SALES}/sale-reversals",
            {"sale_id": int(spare), "kind": "CANCELLATION", "reason": "F19-07 case"},
            headers=keyed(),
        ),
        "reversal_id",
    )
    event = made(
        floor.admin(
            "POST", f"{ENGAGEMENT}/engagement-events", floor.event(floor.other)
        ),
        "event_id",
    )
    check_in = made(
        floor.admin("POST", f"{CHECK_INS}/check-ins", floor.check_in(floor.other)),
        "check_in_id",
    )

    reads = (
        ("GET /v1/sales/{sale_id}", "GET", f"{SALES}/sales/{sale}", None, floor.seller),
        (
            "GET /v1/sale-reversals/{reversal_id}",
            "GET",
            f"{SALES}/sale-reversals/{reversal}",
            None,
            floor.manager,
        ),
        (
            "POST /v1/sale-reversals",
            "POST",
            f"{SALES}/sale-reversals",
            {"sale_id": int(sale), "kind": "CANCELLATION", "reason": "F19-07 case"},
            floor.seller,
        ),
        (
            "GET /v1/engagement-events/{event_id}",
            "GET",
            f"{ENGAGEMENT}/engagement-events/{event}",
            None,
            floor.manager,
        ),
        (
            "GET /v1/check-ins/{check_in_id}",
            "GET",
            f"{CHECK_INS}/check-ins/{check_in}",
            None,
            floor.manager,
        ),
    )
    held = True
    for label, method, path, body, session in reads:
        for accept in (JSON, XML):
            reply = floor.call(
                session,
                method,
                path,
                body,
                accept=accept,
                headers=idempotent(method, path),
            )
            code = reply.code() or xml_code(reply)
            held &= evidence.record(
                "store scope",
                f"{label}, {'XML' if accept == XML else 'JSON'}",
                "403 forbidden_store",
                outcome(reply),
                reply.status == 403 and code == "forbidden_store",
            )
    assert held
