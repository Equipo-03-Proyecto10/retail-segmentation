"""The four floor roles work only in their assigned stores (F19-07, ADR-0060).

Every case runs against real PostgreSQL and the real `api_users.store_assignment_v1`
view, with no fake and no cache: the assignment is read in the request that needs
it, so a removed one stops working on the next call.
"""

from __future__ import annotations

import time
import uuid

import pytest
from lxml import etree

from tests.conftest import FLOOR, idempotency_headers, sale_xml, set_assignments
from tests.test_reversals import cancellation, make_sale  # noqa: F401

NS = "https://mosaiq.maxthecoder.online/xml/common/v1"
XML = {"Accept": "application/xml"}


@pytest.fixture
def assign(owner_url, owner, role_subjects):
    """Set one floor role's stores for a case, and put what was there back."""
    before: dict[str, list[int]] = {}

    def subject_of(role: str) -> str:
        if role in role_subjects:
            return role_subjects[role]
        return owner.execute(
            "SELECT min(u.user_id::text) FROM public.app_user AS u"
            " JOIN public.role AS r USING (role_id) WHERE r.code = %s"
            " AND u.is_active",
            (role,),
        ).fetchone()[0]

    def put(role: str, stores: list[int]) -> str:
        subject = subject_of(role)
        before.setdefault(
            subject,
            [
                row[0]
                for row in owner.execute(
                    "SELECT store_id FROM users.store_assignment"
                    " WHERE user_id = %s ORDER BY store_id",
                    (subject,),
                )
            ],
        )
        set_assignments(owner_url, subject, stores)
        return subject

    yield put
    for subject, stores in before.items():
        set_assignments(owner_url, subject, stores)


def other_store(owner, store_id: int) -> int:
    return owner.execute(
        "SELECT min(store_id) FROM public.store WHERE store_id <> %s", (store_id,)
    ).fetchone()[0]


def sale_count(owner) -> int:
    return owner.execute("SELECT count(*) FROM public.transaction").fetchone()[0]


def refused_without_data(response, field: str | None = None) -> None:
    assert response.status_code == 403, response.get_data(as_text=True)
    body = response.get_json()
    assert body["code"] == "forbidden_store"
    assert set(body) == {"code", "message", "correlation_id", "details"}
    if field is not None:
        assert body["details"] == [{"field": field, "code": "other_store"}]


def refused_in_xml(response) -> None:
    assert response.status_code == 403, response.get_data(as_text=True)
    assert response.mimetype == "application/xml"
    document = etree.fromstring(response.data)
    assert document.tag == f"{{{NS}}}Error"
    assert document.findtext(f"{{{NS}}}code") == "forbidden_store"
    assert {child.tag.rsplit("}", 1)[1] for child in document} <= {
        "code",
        "message",
        "correlation_id",
        "details",
    }


# ---- recording a sale ---------------------------------------------------------------


@pytest.mark.parametrize("role", FLOOR)
def test_a_floor_role_records_a_sale_only_at_an_assigned_store(
    client, token, owner, sale_body, assign, role
):
    assign(role, [sale_body["store_id"]])
    created = client.post(
        "/v1/sales", json=sale_body, headers=idempotency_headers(token(role))
    )
    assert created.status_code == 201, created.get_json()

    elsewhere = other_store(owner, sale_body["store_id"])
    assign(role, [elsewhere])
    before = sale_count(owner)
    stock = owner.execute(
        "SELECT sum(quantity_on_hand) FROM public.inventory"
    ).fetchone()
    refused = client.post(
        "/v1/sales", json=sale_body, headers=idempotency_headers(token(role))
    )
    refused_without_data(refused, "store_id")
    assert sale_count(owner) == before
    assert (
        owner.execute("SELECT sum(quantity_on_hand) FROM public.inventory").fetchone()
        == stock
    )


def test_the_refusal_is_the_same_in_xml(client, token, owner, sale_body, assign):
    assign("SALES_ASSOCIATE", [other_store(owner, sale_body["store_id"])])
    response = client.post(
        "/v1/sales",
        data=sale_xml(sale_body),
        content_type="application/xml",
        headers={**idempotency_headers(token("SALES_ASSOCIATE")), **XML},
    )
    refused_in_xml(response)


def test_a_floor_user_with_no_assignment_works_in_no_store(
    client, token, owner, sale_body, assign
):
    assign("SALES_ASSOCIATE", [])
    refused_without_data(
        client.post(
            "/v1/sales",
            json=sale_body,
            headers=idempotency_headers(token("SALES_ASSOCIATE")),
        ),
        "store_id",
    )
    listed = client.get("/v1/sales", headers=token("SALES_ASSOCIATE"))
    assert listed.status_code == 200
    assert listed.get_json()["sales"] == []
    assert listed.get_json()["page_info"]["total"] == 0


def test_a_replay_is_refused_once_the_store_is_no_longer_the_callers(
    client, token, owner, sale_body, assign
):
    key = str(uuid.uuid4())
    headers = idempotency_headers(token("STORE_MANAGER"), key)
    assign("STORE_MANAGER", [sale_body["store_id"]])
    assert client.post("/v1/sales", json=sale_body, headers=headers).status_code == 201
    assign("STORE_MANAGER", [other_store(owner, sale_body["store_id"])])
    refused_without_data(
        client.post("/v1/sales", json=sale_body, headers=headers), "store_id"
    )


# ---- reading sales ------------------------------------------------------------------


@pytest.fixture
def sales_in_two_stores(client, token, owner, make_sale):  # noqa: F811
    first = make_sale()
    second = make_sale(store_id=other_store(owner, first["store_id"]))
    return first, second


@pytest.mark.parametrize("role", FLOOR + ("COMMERCIAL_SUPERVISOR",))
def test_a_floor_role_reads_only_the_sales_of_its_stores(
    client, token, owner, sales_in_two_stores, assign, role
):
    mine, theirs = sales_in_two_stores
    subject = assign(role, [mine["store_id"]])
    headers = token(role, sub=subject)

    listed = client.get("/v1/sales?size=100", headers=headers)
    assert listed.status_code == 200
    stores = {sale["store_id"] for sale in listed.get_json()["sales"]}
    assert stores == {mine["store_id"]}
    assert listed.get_json()["page_info"]["total"] == len(listed.get_json()["sales"])

    refused_without_data(
        client.get(f"/v1/sales?store_id={theirs['store_id']}", headers=headers),
        "store_id",
    )
    own = client.get(f"/v1/sales/{mine['sale_id']}", headers=headers)
    assert own.status_code == 200
    refused_without_data(
        client.get(f"/v1/sales/{theirs['sale_id']}", headers=headers), "sale_id"
    )
    refused_in_xml(
        client.get(f"/v1/sales/{theirs['sale_id']}", headers={**headers, **XML})
    )
    refused_in_xml(
        client.get(
            f"/v1/sales?store_id={theirs['store_id']}", headers={**headers, **XML}
        )
    )
    # An id nobody holds is still a 404: the narrowing hides no row that exists
    # for the caller, and invents none.
    assert client.get("/v1/sales/2147483647", headers=headers).status_code == 404


@pytest.mark.parametrize("role", ["ADMIN", "ANALYST"])
def test_a_role_that_is_not_confined_reads_every_store(
    client, token, owner, sales_in_two_stores, role
):
    mine, theirs = sales_in_two_stores
    headers = token(role)
    for sale in (mine, theirs):
        assert (
            client.get(
                f"/v1/sales?store_id={sale['store_id']}", headers=headers
            ).status_code
            == 200
        )
        assert (
            client.get(f"/v1/sales/{sale['sale_id']}", headers=headers).status_code
            == 200
        )
    stores = {
        sale["store_id"]
        for sale in client.get("/v1/sales?size=100", headers=headers).get_json()[
            "sales"
        ]
    }
    assert {mine["store_id"], theirs["store_id"]} <= stores


def test_a_refused_history_read_is_still_recorded(
    client, token, owner, sales_in_two_stores, assign
):
    mine, theirs = sales_in_two_stores
    assign("SALES_ASSOCIATE", [mine["store_id"]])
    correlation = "test-scope-" + uuid.uuid4().hex
    response = client.get(
        f"/v1/sales?customer_id={theirs['customer_id']}&store_id={theirs['store_id']}",
        headers={**token("SALES_ASSOCIATE"), "X-Correlation-ID": correlation},
    )
    refused_without_data(response, "store_id")
    assert owner.execute(
        "SELECT outcome FROM audit_events.domain_event WHERE correlation_id = %s",
        (correlation,),
    ).fetchall() == [("REFUSED",)]


# ---- reversals ----------------------------------------------------------------------


@pytest.mark.parametrize("role", FLOOR)
def test_a_floor_role_reverses_and_reads_reversals_only_in_its_stores(
    client, token, owner, sales_in_two_stores, assign, role
):
    mine, theirs = sales_in_two_stores
    subject = assign(role, [mine["store_id"]])
    headers = token(role, sub=subject)

    refused_without_data(
        client.post(
            "/v1/sale-reversals",
            json=cancellation(theirs),
            headers=idempotency_headers(headers),
        ),
        "sale_id",
    )
    assert owner.execute(
        "SELECT count(*) FROM sales.sale_reversal WHERE transaction_id = %s",
        (theirs["sale_id"],),
    ).fetchone() == (0,)
    made = client.post(
        "/v1/sale-reversals",
        json=cancellation(mine),
        headers=idempotency_headers(headers),
    )
    assert made.status_code == 201, made.get_json()

    admin = token("ADMIN")
    other = client.post(
        "/v1/sale-reversals",
        json=cancellation(theirs),
        headers=idempotency_headers(admin),
    ).get_json()

    listed = client.get("/v1/sale-reversals?size=100", headers=headers).get_json()
    assert {item["store_id"] for item in listed["sale_reversals"]} == {mine["store_id"]}
    refused_without_data(
        client.get(
            f"/v1/sale-reversals?store_id={theirs['store_id']}", headers=headers
        ),
        "store_id",
    )
    assert (
        client.get(
            f"/v1/sale-reversals/{made.get_json()['reversal_id']}", headers=headers
        ).status_code
        == 200
    )
    refused_without_data(
        client.get(f"/v1/sale-reversals/{other['reversal_id']}", headers=headers),
        "reversal_id",
    )
    refused_in_xml(
        client.get(
            f"/v1/sale-reversals/{other['reversal_id']}", headers={**headers, **XML}
        )
    )


def test_an_unconfined_role_reverses_in_any_store(client, token, sales_in_two_stores):
    _mine, theirs = sales_in_two_stores
    response = client.post(
        "/v1/sale-reversals",
        json=cancellation(theirs),
        headers=idempotency_headers(token("ADMIN")),
    )
    assert response.status_code == 201, response.get_json()


# ---- a removed assignment, in real time ---------------------------------------------


def test_a_removed_assignment_is_refused_on_the_next_call(
    client, token, owner, owner_url, sale_body, assign, role_subjects
):
    """No fake clock and no cache: the bound is the commit of the removal."""
    store_id = sale_body["store_id"]
    subject = assign("SALES_ASSOCIATE", [store_id])
    headers = token("SALES_ASSOCIATE")

    assert (
        client.get(f"/v1/sales?store_id={store_id}", headers=headers).status_code == 200
    )
    started = time.monotonic()
    owner.execute(
        "DELETE FROM users.store_assignment WHERE user_id = %s AND store_id = %s",
        (subject, store_id),
    )
    refused = client.get(f"/v1/sales?store_id={store_id}", headers=headers)
    elapsed = time.monotonic() - started

    refused_without_data(refused, "store_id")
    # The same token, still valid for minutes, is refused within the one
    # request that follows the removal.
    assert elapsed < 5
    created = client.post(
        "/v1/sales", json=sale_body, headers=idempotency_headers(headers)
    )
    refused_without_data(created, "store_id")
