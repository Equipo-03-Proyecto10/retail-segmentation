"""The retention candidates (ADR-0066 section 3, #1061), through the real service
and the real schema.

A candidate is a customer that is not anonymized and whose latest activity, the
latest of its registration date and the UTC dates of its latest sale and latest
visit, falls before the UTC date 36 months ago. The fixture builds one customer
per case around that cutoff, as the owner, and removes them afterwards. The list
is checked against the same rule computed here from the base tables, so the
seed's own customers are covered too.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from lxml import etree
from test_http import NAMESPACE, valid

pytestmark = pytest.mark.postgres

PATH = "/v1/customers/retention-candidates"
ROLES = (
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "INVENTORY_PLANNER",
    "AUDITOR",
    "CUSTOMER",
    "SALES_ASSOCIATE",
    "LOYALTY_OFFICER",
    "COMMERCIAL_SUPERVISOR",
)


def cutoff(owner: psycopg.Connection) -> date:
    """The service's cutoff, computed the way it computes it."""

    return owner.execute(
        "SELECT ((now() AT TIME ZONE 'UTC')::date - make_interval(months => 36))::date"
    ).fetchone()[0]


def at(day: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), UTC)


class World:
    """One customer per case, each with the activity its name says."""

    def __init__(self, owner: psycopg.Connection, unique: str, actor: str) -> None:
        self.owner = owner
        self.actor = actor
        self.cut = cutoff(owner)
        self.ids: dict[str, str] = {}
        self.store = owner.execute("SELECT min(store_id) FROM store").fetchone()[0]
        self.product = owner.execute("SELECT min(product_id) FROM product").fetchone()[
            0
        ]
        c = self.cut
        # name: (registered_on, sales, visits, anonymized)
        cases = {
            "old_sale": (
                c - timedelta(days=1000),
                [at(c - timedelta(days=200))],
                [],
                False,
            ),
            "visit_keeps": (
                c - timedelta(days=1000),
                [at(c - timedelta(days=200))],
                [at(c + timedelta(days=100))],
                False,
            ),
            "never_active": (c - timedelta(days=500), [], [], False),
            "recently_registered": (c + timedelta(days=10), [], [], False),
            # 00:30 UTC on the cutoff day is the cutoff day, not before it, even
            # though it is still the evening before in Mexico City.
            "sale_on_the_cutoff": (
                c - timedelta(days=900),
                [at(c, 0, 30)],
                [],
                False,
            ),
            "visit_the_day_before": (
                c - timedelta(days=900),
                [],
                [at(c - timedelta(days=1), 23, 30)],
                False,
            ),
            "recent_sale": (
                c - timedelta(days=1000),
                [at(c + timedelta(days=300))],
                [],
                False,
            ),
            "anonymized": (c - timedelta(days=1000), [], [], True),
        }
        for name, (registered_on, sales, visits, anonymized) in cases.items():
            self.ids[name] = self._customer(
                f"{unique}-{name}", registered_on, sales, visits, anonymized
            )

    def _customer(
        self,
        name: str,
        registered_on: date,
        sales: list[datetime],
        visits: list[datetime],
        anonymized: bool,
    ) -> str:
        customer_id = str(uuid.uuid4())
        owner = self.owner
        with owner.transaction():
            # Keep the fixture out of the audit trail and of other boundaries'
            # outboxes, which every suite expects empty after the seed. The rows
            # are valid, so nothing the read depends on is skipped.
            owner.execute("SET LOCAL session_replication_role = replica")
            if anonymized:
                owner.execute(
                    "INSERT INTO customer (customer_id, name, registration_channel_id,"
                    " registered_on, anonymized_at)"
                    " VALUES (%s, 'Anonymized customer', 1, %s, now())",
                    (customer_id, registered_on),
                )
            else:
                owner.execute(
                    "INSERT INTO customer (customer_id, name, registration_channel_id,"
                    " registered_on) VALUES (%s, %s, 1, %s)",
                    (customer_id, name, registered_on),
                )
            for occurred_at in sales:
                tid = owner.execute(
                    "INSERT INTO transaction (source_transaction_id, customer_id,"
                    " store_id, channel_id, occurred_at, total, data_origin)"
                    " VALUES (%s, %s, %s, 1, %s, %s, 'OBSERVED')"
                    " RETURNING transaction_id",
                    (
                        f"retention-{uuid.uuid4().hex[:20]}",
                        customer_id,
                        self.store,
                        occurred_at,
                        Decimal("10.00"),
                    ),
                ).fetchone()[0]
                owner.execute(
                    "INSERT INTO transaction_line (transaction_id, product_id,"
                    " quantity, unit_price) VALUES (%s, %s, 1, %s)",
                    (tid, self.product, Decimal("10.00")),
                )
            for occurred_at in visits:
                owner.execute(
                    "INSERT INTO checkins.customer_visit (customer_id, store_id,"
                    " occurred_at, recorded_by) VALUES (%s, %s, %s, %s)",
                    (customer_id, self.store, occurred_at, self.actor),
                )
        return customer_id

    def remove(self) -> None:
        ids = list(self.ids.values())
        self.owner.execute(
            "DELETE FROM checkins.customer_visit WHERE customer_id = ANY(%s::uuid[])",
            (ids,),
        )
        self.owner.execute(
            "DELETE FROM transaction WHERE customer_id = ANY(%s::uuid[])", (ids,)
        )
        self.owner.execute(
            "DELETE FROM customer WHERE customer_id = ANY(%s::uuid[])", (ids,)
        )


@pytest.fixture
def world(owner: psycopg.Connection, unique: str, actor: str) -> Iterator[World]:
    made = World(owner, unique, actor)
    yield made
    made.remove()


def expected(owner: psycopg.Connection, cut: date) -> list[tuple[str, date]]:
    """The rule, computed from the base tables rather than the published views."""

    rows = owner.execute(
        "SELECT c.customer_id::text, c.registered_on,"
        " (SELECT max(t.occurred_at) FROM transaction AS t"
        "  WHERE t.customer_id = c.customer_id),"
        " (SELECT max(v.occurred_at) FROM checkins.customer_visit AS v"
        "  WHERE v.customer_id = c.customer_id)"
        " FROM customer AS c WHERE c.anonymized_at IS NULL"
    ).fetchall()
    found = []
    for customer_id, registered_on, last_sale, last_visit in rows:
        dates = [registered_on] + [
            moment.astimezone(UTC).date()
            for moment in (last_sale, last_visit)
            if moment is not None
        ]
        if max(dates) < cut:
            found.append((customer_id, max(dates)))
    return sorted(found, key=lambda item: (item[1], item[0]))


def every_page(client, admin, size: int = 100) -> tuple[list[dict], dict]:
    items: list[dict] = []
    page = 1
    while True:
        response = client.get(f"{PATH}?page={page}&size={size}", headers=admin)
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        items.extend(body["candidates"])
        if page * size >= body["page_info"]["total"]:
            return items, body
        page += 1


@pytest.fixture
def admin(token) -> dict[str, str]:
    return token("ADMIN")


def test_the_list_names_exactly_the_customers_the_rule_names(
    client, admin, owner, world
):
    items, body = every_page(client, admin)
    assert body["inactive_before"] == world.cut.isoformat()
    as_of = datetime.fromisoformat(body["as_of"].replace("Z", "+00:00"))
    assert abs(datetime.now(UTC) - as_of) < timedelta(minutes=5)

    got = [
        (item["customer_id"], date.fromisoformat(item["last_activity_on"]))
        for item in items
    ]
    assert got == expected(owner, world.cut)
    assert body["page_info"]["total"] == len(items)

    names = {item["customer_id"]: item for item in items}
    ids = world.ids
    for case in ("old_sale", "never_active", "visit_the_day_before"):
        assert ids[case] in names, case
    for case in (
        "visit_keeps",
        "recently_registered",
        "sale_on_the_cutoff",
        "recent_sale",
        "anonymized",
    ):
        assert ids[case] not in names, case

    old_sale = names[ids["old_sale"]]
    assert old_sale["last_sale_at"] == at(world.cut - timedelta(days=200)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    assert old_sale["last_visit_at"] is None
    assert old_sale["last_activity_on"] == (world.cut - timedelta(days=200)).isoformat()
    never = names[ids["never_active"]]
    assert never["last_sale_at"] is None and never["last_visit_at"] is None
    assert never["last_activity_on"] == never["registered_on"]
    visited = names[ids["visit_the_day_before"]]
    assert visited["last_activity_on"] == (world.cut - timedelta(days=1)).isoformat()
    # No identity travels in the list.
    assert all(
        set(item)
        == {
            "customer_id",
            "registered_on",
            "last_sale_at",
            "last_visit_at",
            "last_activity_on",
        }
        for item in items
    )


def test_pages_follow_the_order_and_size_is_bounded(client, admin, world):
    first = client.get(f"{PATH}?page=1&size=1", headers=admin).get_json()
    second = client.get(f"{PATH}?page=2&size=1", headers=admin).get_json()
    assert first["page_info"] == {
        "page": 1,
        "size": 1,
        "total": first["page_info"]["total"],
    }
    assert first["page_info"]["total"] >= 3
    a, b = first["candidates"][0], second["candidates"][0]
    assert (a["last_activity_on"], a["customer_id"]) < (
        b["last_activity_on"],
        b["customer_id"],
    )
    too_big = client.get(f"{PATH}?size=101", headers=admin)
    assert too_big.status_code == 422
    assert too_big.get_json()["code"] == "invalid_pagination"


def test_the_xml_page_validates_and_leaves_out_what_is_missing(client, admin, world):
    response = client.get(
        f"{PATH}?size=100", headers={**admin, "Accept": "application/xml"}
    )
    assert response.status_code == 200
    assert valid(response.data)
    tree = etree.fromstring(response.data)
    assert [etree.QName(child).localname for child in tree][:2] == [
        "as_of",
        "inactive_before",
    ]
    assert tree.findtext(f"{{{NAMESPACE}}}inactive_before") == world.cut.isoformat()
    for candidate in tree.iterfind(f"{{{NAMESPACE}}}RetentionCandidate"):
        if (
            candidate.findtext(f"{{{NAMESPACE}}}customer_id")
            == world.ids["never_active"]
        ):
            present = {etree.QName(child).localname for child in candidate}
            assert present == {"customer_id", "registered_on", "last_activity_on"}
            break
    else:
        pytest.fail("never_active is not in the XML page")


def test_the_read_rewrites_and_records_nothing(client, admin, owner, world):
    def state() -> tuple:
        return owner.execute(
            "SELECT (SELECT md5(string_agg(c::text, ',' ORDER BY customer_id))"
            "        FROM customer AS c),"
            " (SELECT count(*) FROM audit_log),"
            " (SELECT count(*) FROM audit_events.domain_event),"
            " (SELECT count(*) FROM customers_events.outbox),"
            " (SELECT count(*) FROM customers.loyalty_card)"
        ).fetchone()

    before = state()
    every_page(client, admin, size=2)
    client.get(PATH, headers={**admin, "Accept": "application/xml"})
    assert state() == before


def test_only_an_administrator_reads_the_list(client, token):
    for role in ROLES:
        response = client.get(PATH, headers=token(role))
        assert response.status_code == 403, role


def test_visits_are_read_only_through_the_published_view(owner):
    """#1061: customers reads check-ins only through api_checkins (ADR-0044)."""

    privileges = owner.execute(
        "SELECT has_schema_privilege('svc_customers', 'checkins', 'USAGE'),"
        " has_table_privilege('svc_customers', 'checkins.customer_visit', 'SELECT'),"
        " has_table_privilege('svc_customers',"
        " 'api_checkins.customer_last_visit_v1', 'SELECT'),"
        " has_table_privilege('svc_customers', 'public.transaction', 'SELECT'),"
        " has_table_privilege('svc_customers', 'api_sales.purchase_line_v1', 'SELECT')"
    ).fetchone()
    assert privileges == (False, False, True, False, True)
    source = (Path(__file__).resolve().parents[1] / "app" / "service.py").read_text()
    assert "checkins." not in source.replace("api_checkins.", "")
    assert "public.transaction" not in source
