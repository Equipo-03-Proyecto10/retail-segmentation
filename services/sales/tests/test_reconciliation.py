"""Reconciling an import with the sales already recorded and its quality report
(F26-09, ADR-0070 section 5): look-alike sales are listed for a person and
nothing changes silently; a load's report counts its failures by code; a
resolution is made once and audited with its author. Real PostgreSQL and Redis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from lxml import etree

from tests.test_imports import (
    NS,
    XML,
    load,
    new_id,
    on_hand,
    open_import,
    row,
    status,
    when,
)

RESOLUTION = "/reconciliation-cases/{}/resolution"


@pytest.fixture
def service_connection(service_url):
    """The service's own role, as the API and the worker connect."""
    with psycopg.connect(service_url) as connection:
        yield connection


def reconcile(client, token, import_id: int, role: str = "ADMIN"):
    return client.post(
        f"/v1/sale-imports/{import_id}/reconciliation", headers=token(role)
    )


def cases(client, token, import_id: int, query: str = "", role: str = "ADMIN"):
    return client.get(
        f"/v1/sale-imports/{import_id}/reconciliation-cases{query}", headers=token(role)
    )


def settle(client, token, import_id: int, case_id: int, body: dict, role="ADMIN"):
    return client.put(
        f"/v1/sale-imports/{import_id}{RESOLUTION.format(case_id)}",
        json=body,
        headers=token(role),
    )


def quality(client, token, import_id: int, role: str = "ADMIN"):
    return client.get(f"/v1/sale-imports/{import_id}/quality", headers=token(role))


def counts(owner: psycopg.Connection) -> tuple:
    return owner.execute(
        "SELECT (SELECT count(*) FROM public.transaction),"
        " (SELECT count(*) FROM public.transaction_line),"
        " (SELECT coalesce(sum(total), 0) FROM public.transaction),"
        " (SELECT coalesce(sum(quantity_on_hand), 0) FROM public.inventory)"
    ).fetchone()


def twin_import(client, token, service_url, world, **second):
    """An earlier import and a later one whose row looks like the earlier one's
    under another source id. Returns both import ids."""
    at = when(2)
    first = load(client, token, service_url, [row(world, 2, new_id(), occurred_at=at)])
    later = load(
        client, token, service_url, [row(world, 2, new_id(), occurred_at=at, **second)]
    )
    return first, later


def test_a_row_that_looks_like_a_recorded_sale_is_listed_and_nothing_changes(
    client, token, owner, world, service_url
):
    first, later = twin_import(client, token, service_url, world)
    before = counts(owner)
    ran = reconcile(client, token, later)
    assert ran.status_code == 200, ran.get_json()
    assert ran.get_json() == {
        "import_id": later,
        "added": 1,
        "found": 1,
        "open": 1,
        "distinct": 0,
        "duplicate": 0,
    }
    # Listing changes no sale, no line, no total and no stock.
    assert counts(owner) == before
    assert status(client, token, later)["accepted"] == 1
    page = cases(client, token, later).get_json()
    assert page["page_info"]["total"] == 1
    case = page["sale_reconciliation_cases"][0]
    assert case["resolution"] is None and case["resolved_by"] is None
    assert case["imported_source_id"].startswith("svc-sales:imp:")
    assert case["matched_source_id"].startswith("svc-sales:imp:")
    assert case["imported_source_id"] != case["matched_source_id"]
    assert case["matched_sale_id"] < case["imported_sale_id"]
    # The earlier import has no look-alike of its own.
    assert reconcile(client, token, first).get_json()["found"] == 0


def test_a_sale_recorded_by_the_phone_is_a_match_too(
    client, token, owner, world, service_url
):
    at = when(2)
    sale = client.post(
        "/v1/sales",
        json={
            "customer_id": world["customer"],
            "store_id": world["store"],
            "channel_id": world["channel"],
            "occurred_at": at,
            "lines": [
                {
                    "product_id": world["products"][0],
                    "quantity": 1,
                    "unit_price": "10.00",
                }
            ],
        },
        headers={**token("ADMIN"), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert sale.status_code == 201, sale.get_json()
    later = load(client, token, service_url, [row(world, 2, new_id(), occurred_at=at)])
    case = reconcile(client, token, later).get_json()
    assert case["added"] == 1
    listed = cases(client, token, later).get_json()["sale_reconciliation_cases"][0]
    assert listed["matched_sale_id"] == sale.get_json()["sale_id"]


@pytest.mark.parametrize(
    "different",
    [
        {"quantity": 2},
        {"unit_price": "11.00"},
        {"product_id_index": 1},
        {"occurred_at_shift": True},
    ],
)
def test_a_sale_that_differs_in_what_matters_is_not_listed(
    client, token, owner, world, service_url, different
):
    at = when(2)
    load(client, token, service_url, [row(world, 2, new_id(), occurred_at=at)])
    cells: dict = {"occurred_at": at}
    if "product_id_index" in different:
        cells["product_id"] = world["products"][1]
    elif "occurred_at_shift" in different:
        cells["occurred_at"] = when(3)
    else:
        cells.update(different)
    later = load(client, token, service_url, [row(world, 2, new_id(), **cells)])
    assert reconcile(client, token, later).get_json()["found"] == 0


def test_a_look_alike_inside_one_import_is_listed_once_against_the_earlier_line(
    client, token, owner, world, service_url
):
    at = when(2)
    only = load(
        client,
        token,
        service_url,
        [
            row(world, 2, new_id(), occurred_at=at),
            row(world, 3, new_id(), occurred_at=at),
            row(world, 4, new_id(), occurred_at=at),
        ],
    )
    ran = reconcile(client, token, only).get_json()
    # Three identical sales make the pairs (2nd, 1st), (3rd, 1st) and (3rd, 2nd).
    assert ran["added"] == 3 and ran["found"] == 3


def test_running_again_lists_only_what_is_new_and_leaves_settled_cases_alone(
    client, token, owner, world, service_url
):
    _first, later = twin_import(client, token, service_url, world)
    reconcile(client, token, later)
    case_id = cases(client, token, later).get_json()["sale_reconciliation_cases"][0][
        "case_id"
    ]
    assert (
        settle(client, token, later, case_id, {"resolution": "DISTINCT"}).status_code
        == 200
    )
    again = reconcile(client, token, later).get_json()
    assert again == {
        "import_id": later,
        "added": 0,
        "found": 1,
        "open": 0,
        "distinct": 1,
        "duplicate": 0,
    }


def test_only_a_finished_import_is_reconciled_and_an_unknown_one_is_404(
    client, token, world
):
    opened = open_import(client, token, 2)
    import_id = opened.get_json()["import_id"]
    refused = reconcile(client, token, import_id)
    assert refused.status_code == 409
    assert refused.get_json()["code"] == "import_not_finished"
    assert reconcile(client, token, 999_999_999).status_code == 404
    assert cases(client, token, 999_999_999).status_code == 404
    assert quality(client, token, 999_999_999).status_code == 404
    gone = settle(client, token, 999_999_999, 1, {"resolution": "DISTINCT"})
    assert gone.status_code == 404


def test_a_resolution_is_audited_with_its_author_and_is_made_once(
    client, token, owner, world, service_url, role_subjects, service_connection
):
    _first, later = twin_import(client, token, service_url, world)
    reconcile(client, token, later)
    case_id = cases(client, token, later).get_json()["sale_reconciliation_cases"][0][
        "case_id"
    ]
    done = settle(
        client,
        token,
        later,
        case_id,
        {"resolution": "DUPLICATE", "note": "Re-keyed by the till export."},
        role="ANALYST",
    )
    assert done.status_code == 200, done.get_json()
    body = done.get_json()
    assert body["resolution"] == "DUPLICATE"
    assert body["resolved_by"] == role_subjects["ANALYST"]
    assert body["resolved_at"] is not None
    assert body["note"] == "Re-keyed by the till export."
    stored = owner.execute(
        "SELECT resolution, resolved_by::text, note FROM sales.reconciliation_case"
        " WHERE case_id = %s",
        (case_id,),
    ).fetchone()
    assert stored == (
        "DUPLICATE",
        role_subjects["ANALYST"],
        "Re-keyed by the till export.",
    )

    # The same decision again, by someone else, is the decision that stands.
    replay = settle(
        client, token, later, case_id, {"resolution": "DUPLICATE"}, role="ADMIN"
    )
    assert replay.status_code == 200
    assert replay.get_json() == body
    # The other decision is refused and changes nothing.
    other = settle(
        client, token, later, case_id, {"resolution": "DISTINCT"}, role="ADMIN"
    )
    assert other.status_code == 409
    assert other.get_json()["code"] == "case_resolved"
    assert (
        cases(client, token, later).get_json()["sale_reconciliation_cases"][0] == body
    )

    # The database holds it whoever writes: not the owner, not the service.
    for connection in (owner, service_connection):
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE sales.reconciliation_case SET resolution = 'DISTINCT',"
                " resolved_by = %s WHERE case_id = %s",
                (role_subjects["ADMIN"], case_id),
            )
        connection.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "DELETE FROM sales.reconciliation_case WHERE case_id = %s", (case_id,)
        )
    owner.rollback()


def test_a_resolution_must_name_a_known_outcome_and_a_short_note(
    client, token, owner, world, service_url
):
    _first, later = twin_import(client, token, service_url, world)
    reconcile(client, token, later)
    case_id = cases(client, token, later).get_json()["sale_reconciliation_cases"][0][
        "case_id"
    ]
    for body in (
        {"resolution": "MERGE"},
        {"note": "no resolution"},
        {"resolution": "DISTINCT", "note": "x" * 501},
        {"resolution": "DISTINCT", "note": ""},
        {"resolution": "DISTINCT", "extra": 1},
    ):
        refused = settle(client, token, later, case_id, body)
        assert refused.status_code == 422, (body, refused.get_json())
        assert refused.get_json()["code"] == "invalid_body"
    unknown = settle(client, token, later, case_id + 10_000, {"resolution": "DISTINCT"})
    assert unknown.status_code == 404
    assert unknown.get_json()["code"] == "sale_reconciliation_case_not_found"
    # A case belongs to its import: another import's id does not reach it.
    elsewhere = settle(client, token, later + 1, case_id, {"resolution": "DISTINCT"})
    assert elsewhere.status_code == 404
    still_open = cases(client, token, later, "?status=OPEN").get_json()
    assert still_open["page_info"]["total"] == 1


def test_the_list_filters_by_status_and_refuses_an_unknown_one(
    client, token, owner, world, service_url
):
    at = when(2)
    later = load(
        client,
        token,
        service_url,
        [row(world, 2 + n, new_id(), occurred_at=at) for n in range(3)],
    )
    reconcile(client, token, later)
    listed = cases(client, token, later).get_json()["sale_reconciliation_cases"]
    assert [c["case_id"] for c in listed] == sorted(c["case_id"] for c in listed)
    settle(client, token, later, listed[0]["case_id"], {"resolution": "DISTINCT"})
    open_ = cases(client, token, later, "?status=OPEN").get_json()
    done = cases(client, token, later, "?status=RESOLVED").get_json()
    assert open_["page_info"]["total"] == 2 and done["page_info"]["total"] == 1
    assert done["sale_reconciliation_cases"][0]["case_id"] == listed[0]["case_id"]
    bad = cases(client, token, later, "?status=ALL")
    assert bad.status_code == 422 and bad.get_json()["code"] == "invalid_query"
    paged = cases(client, token, later, "?size=1&page=2").get_json()
    assert paged["page_info"] == {"page": 2, "size": 1, "total": 3}
    assert paged["sale_reconciliation_cases"][0]["case_id"] == listed[1]["case_id"]


def test_the_quality_report_counts_failures_by_code_for_each_file(
    client, token, owner, world, service_url
):
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat(timespec="seconds")
    sale = new_id()
    bad = load(
        client,
        token,
        service_url,
        [
            row(world, 2, new_id(), quantity=3),  # accepted, unlike line 6
            row(world, 3, new_id(), quantity=0),
            row(world, 4, new_id(), quantity=0),
            row(world, 5, new_id(), occurred_at=future),
            row(world, 6, sale),  # accepted
            row(world, 7, sale),  # repeats line 6
        ],
    )
    clean = load(client, token, service_url, [row(world, 2, new_id())])
    report = quality(client, token, bad).get_json()
    assert report["import_id"] == bad
    assert report["status"] == "COMPLETED"
    assert (report["received"], report["accepted"], report["rejected"]) == (6, 2, 4)
    assert report["pending"] == 0
    assert report["failures"] == [
        {"code": "invalid_quantity", "count": 2},
        {"code": "duplicate_row", "count": 1},
        {"code": "future_dated", "count": 1},
    ]
    assert sum(item["count"] for item in report["failures"]) == report["rejected"]
    assert {item["code"] for item in report["failures"]} == {
        "invalid_quantity",
        "duplicate_row",
        "future_dated",
    }
    assert report["reconciliation"] == {
        "found": 0,
        "open": 0,
        "distinct": 0,
        "duplicate": 0,
    }
    # Another file has its own report, with nothing of the first one's.
    other = quality(client, token, clean).get_json()
    assert other["import_id"] == clean
    assert other["failures"] == [] and other["rejected"] == 0
    # The report follows the reconciliation.
    reconcile(client, token, bad)
    assert quality(client, token, bad).get_json()["reconciliation"]["found"] == 0


def test_the_quality_report_shows_where_the_cases_stand(
    client, token, owner, world, service_url
):
    at = when(2)
    later = load(
        client,
        token,
        service_url,
        [row(world, 2 + n, new_id(), occurred_at=at) for n in range(3)],
    )
    reconcile(client, token, later)
    listed = cases(client, token, later).get_json()["sale_reconciliation_cases"]
    settle(client, token, later, listed[0]["case_id"], {"resolution": "DUPLICATE"})
    settle(client, token, later, listed[1]["case_id"], {"resolution": "DISTINCT"})
    assert quality(client, token, later).get_json()["reconciliation"] == {
        "found": 3,
        "open": 1,
        "distinct": 1,
        "duplicate": 1,
    }


def test_the_roles_that_reconcile_settle_and_read(
    client, token, owner, world, service_url
):
    _first, later = twin_import(client, token, service_url, world)
    allowed_write = {"ADMIN": 200, "ANALYST": 200, "AUDITOR": 403, "STORE_MANAGER": 403}
    for role, expected in allowed_write.items():
        assert reconcile(client, token, later, role).status_code == expected, role
    case_id = cases(client, token, later).get_json()["sale_reconciliation_cases"][0][
        "case_id"
    ]
    for role in ("AUDITOR", "STORE_MANAGER", "MARKETING"):
        refused = settle(
            client, token, later, case_id, {"resolution": "DISTINCT"}, role
        )
        assert refused.status_code == 403, role
    for role, expected in {
        "ADMIN": 200,
        "ANALYST": 200,
        "AUDITOR": 200,
        "STORE_MANAGER": 403,
        "INVENTORY_PLANNER": 403,
    }.items():
        assert cases(client, token, later, role=role).status_code == expected, role
        assert quality(client, token, later, role).status_code == expected, role


def test_the_answers_in_xml_validate_against_the_xsd(
    app, client, token, owner, world, service_url
):
    _first, later = twin_import(client, token, service_url, world)
    schema = app.extensions["mosaiq_kit"].contract.xml_schema
    base = f"/v1/sale-imports/{later}"
    ran = client.post(base + "/reconciliation", headers={**token("ADMIN"), **XML})
    page = client.get(base + "/reconciliation-cases", headers={**token("ADMIN"), **XML})
    report = client.get(base + "/quality", headers={**token("ADMIN"), **XML})
    case_id = (
        etree.fromstring(page.data)
        .find(f"{{{NS}}}SaleReconciliationCase/{{{NS}}}case_id")
        .text
    )
    request = (
        f'<SaleReconciliationResolutionRequest xmlns="{NS}">'
        "<resolution>DUPLICATE</resolution><note>Same ticket.</note>"
        "</SaleReconciliationResolutionRequest>"
    ).encode()
    done = client.put(
        f"{base}{RESOLUTION.format(case_id)}",
        data=request,
        content_type="application/xml",
        headers={**token("ADMIN"), **XML},
    )
    for response in (ran, page, report, done):
        assert response.status_code == 200, response.data
        schema.assertValid(etree.fromstring(response.data))
    assert etree.fromstring(done.data).findtext(f"{{{NS}}}resolution") == "DUPLICATE"


def test_stock_is_what_it_was_after_reconciling_and_settling(
    client, token, owner, world, service_url
):
    _first, later = twin_import(client, token, service_url, world)
    product = world["products"][0]
    held = on_hand(owner, world, product)
    reconcile(client, token, later)
    case_id = cases(client, token, later).get_json()["sale_reconciliation_cases"][0][
        "case_id"
    ]
    settle(client, token, later, case_id, {"resolution": "DUPLICATE"})
    assert on_hand(owner, world, product) == held
