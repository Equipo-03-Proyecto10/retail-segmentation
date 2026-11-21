"""Rule sets are governed: a draft, a publication, a new version (F22-09, RN-46)."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import psycopg
import pytest
from flask import Flask
from flask.testing import FlaskClient

from tests.conftest import ROOT, Shop, days_ago

pytestmark = pytest.mark.postgres
JSON = {"Content-Type": "application/json", "Accept": "application/json"}

# Four boxes that tile the 125 triples of scores: recency and frequency each split
# in two, every monetary score in every box.
TILES = [
    ("CHAMPION", 4, 5, 4, 5, 1, 5),
    ("LOYAL", 4, 5, 1, 3, 1, 5),
    ("AT_RISK", 1, 3, 4, 5, 1, 5),
    ("LOST", 1, 3, 1, 3, 1, 5),
]


def definition(name: str | None = None, window_days: int = 180) -> dict[str, Any]:
    return {
        "name": name or "test rule set " + uuid.uuid4().hex[:12],
        "window_days": window_days,
        "recency_boundaries": [14, 35, 70, 120],
        "frequency_boundaries": [8, 5, 3, 2],
        "monetary_boundaries": ["1200.00", "800.00", "450.00", "200.00"],
        "bands": [
            dict(
                zip(
                    [
                        "label_code",
                        "r_min",
                        "r_max",
                        "f_min",
                        "f_max",
                        "m_min",
                        "m_max",
                    ],
                    b,
                    strict=False,
                )
            )
            for b in TILES
        ],
        "fallback_label_code": "LOST",
    }


@pytest.fixture
def made(owner: psycopg.Connection) -> Iterator[list[int]]:
    """The rule sets a test makes. A published one is never deleted, so the owner
    clears them with triggers off, which only a superuser may do."""
    ids: list[int] = []
    yield ids
    owner.execute("SET session_replication_role = replica")
    try:
        owner.execute(
            "DELETE FROM rfm.customer_profile WHERE rule_set_id = ANY(%s)", (ids,)
        )
        owner.execute(
            "DELETE FROM rfm.rule_set_band WHERE rule_set_id = ANY(%s)", (ids,)
        )
        owner.execute(
            "DELETE FROM rfm.rule_set WHERE parent_rule_set_id = ANY(%s)", (ids,)
        )
        owner.execute("DELETE FROM rfm.rule_set WHERE rule_set_id = ANY(%s)", (ids,))
    finally:
        owner.execute("SET session_replication_role = origin")


class Analyst:
    """An administrator calling the service, and the rule sets they make."""

    def __init__(
        self,
        client: FlaskClient,
        headers: dict[str, str],
        owner: psycopg.Connection,
        made: list[int],
    ):
        self.client = client
        self.headers = {**headers, **JSON}
        self.owner = owner
        self.made = made

    def create(self, body: dict[str, Any] | None = None) -> Any:
        response = self.client.post(
            "/v1/rfm-rule-sets", json=body or definition(), headers=self.headers
        )
        if response.status_code == 201:
            self.made.append(response.get_json()["rule_set_id"])
        return response

    def made_published(self, body: dict[str, Any] | None = None) -> int:
        created = self.create(body)
        assert created.status_code == 201
        rule_set_id = int(created.get_json()["rule_set_id"])
        published = self.client.post(
            f"/v1/rfm-rule-sets/{rule_set_id}/publication", headers=self.headers
        )
        assert published.status_code == 200
        return rule_set_id

    def row(self, rule_set_id: int) -> tuple[Any, ...]:
        found = self.owner.execute(
            "SELECT name, window_days, state, version_no, parent_rule_set_id,"
            " (SELECT count(*) FROM rfm.rule_set_band b"
            "   WHERE b.rule_set_id = r.rule_set_id)"
            " FROM rfm.rule_set r WHERE rule_set_id = %s",
            (rule_set_id,),
        ).fetchone()
        assert found is not None
        return found

    def events(self, rule_set_id: int) -> list[tuple[Any, ...]]:
        return self.owner.execute(
            "SELECT kind, outcome, actor_type, actor_user_id::text, actor_role, source,"
            " subject_type, rule_set_version, details ->> 'action'"
            " FROM audit_events.domain_event WHERE subject_type = 'rule_set'"
            " AND subject_id = %s ORDER BY seq",
            (str(rule_set_id),),
        ).fetchall()

    def count_events(self) -> int:
        row = self.owner.execute(
            "SELECT count(*) FROM audit_events.domain_event"
            " WHERE subject_type = 'rule_set'"
        ).fetchone()
        assert row is not None
        return int(row[0])


@pytest.fixture
def analyst(
    build_app: Callable[..., Flask],
    token: Callable[..., dict[str, str]],
    owner: psycopg.Connection,
    made: list[int],
) -> Analyst:
    return Analyst(build_app().test_client(), token("ADMIN"), owner, made)


def admin_id(owner: psycopg.Connection) -> str:
    row = owner.execute(
        "SELECT min(u.user_id::text) FROM public.app_user AS u"
        " JOIN public.role AS r USING (role_id) WHERE u.is_active AND r.code = 'ADMIN'"
    ).fetchone()
    assert row is not None
    return str(row[0])


# --- Creation: validated and stored as a draft ------------------------------


def test_a_rule_set_is_created_as_a_draft_with_its_author(
    analyst: Analyst, owner: psycopg.Connection
) -> None:
    response = analyst.create()
    assert response.status_code == 201
    body = response.get_json()
    assert (body["state"], body["version_no"], body["published"]) == ("DRAFT", 1, False)
    assert (body["cancelled_sales"], body["returned_sales"]) == ("DEDUCT", "DEDUCT")
    assert body["parent_rule_set_id"] is None and body["published_at"] is None
    assert body["created_by"] == admin_id(owner) and len(body["bands"]) == 4
    assert response.headers["Location"] == f"/v1/rfm-rule-sets/{body['rule_set_id']}"
    fetched = analyst.client.get(
        response.headers["Location"], headers=analyst.headers
    ).get_json()
    assert fetched == body


def test_a_rule_set_arrives_as_xml_too(analyst: Analyst) -> None:
    name = "xml rule set " + uuid.uuid4().hex[:12]
    text = (ROOT / "contracts/rfm-engine/examples/rule-set.request.xml").read_text()
    response = analyst.client.post(
        "/v1/rfm-rule-sets",
        data=text.replace("Quarterly bands 2026-Q4", name),
        headers={
            **analyst.headers,
            "Content-Type": "application/xml",
            "Accept": "application/xml",
        },
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    assert "<state>DRAFT</state>" in response.get_data(as_text=True)
    analyst.made.append(
        int(response.get_data(as_text=True).split("<rule_set_id>")[1].split("<")[0])
    )


def _refusal(analyst: Analyst, body: dict[str, Any], code: str) -> None:
    before = analyst.count_events()
    response = analyst.create(body)
    assert response.status_code == 422, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["code"] == "invalid_body"
    assert [detail["code"] for detail in payload["details"]] == [code]
    found = analyst.owner.execute(
        "SELECT count(*) FROM rfm.rule_set WHERE name = %s", (body["name"],)
    ).fetchone()
    assert found == (0,), "a refused rule set stores nothing"
    assert analyst.count_events() == before, "a refused change is not audited"


def test_bands_that_leave_a_triple_out_are_refused(analyst: Analyst) -> None:
    body = definition()
    body["bands"] = body["bands"][:3]
    _refusal(analyst, body, "bands_gap")


def test_bands_that_overlap_are_refused(analyst: Analyst) -> None:
    body = definition()
    body["bands"].append({**body["bands"][0], "label_code": "TWICE"})
    _refusal(analyst, body, "bands_overlap")


def test_boundaries_out_of_order_are_refused(analyst: Analyst) -> None:
    body = definition()
    body["recency_boundaries"] = [14, 70, 35, 120]
    _refusal(analyst, body, "not_monotonic")
    body = definition()
    body["frequency_boundaries"] = [2, 3, 5, 8]
    _refusal(analyst, body, "not_monotonic")
    body = definition()
    body["monetary_boundaries"] = ["200.00", "450.00", "800.00", "1200.00"]
    _refusal(analyst, body, "not_monotonic")


def test_a_band_whose_minimum_exceeds_its_maximum_is_refused(analyst: Analyst) -> None:
    body = definition()
    body["bands"][0]["r_min"], body["bands"][0]["r_max"] = 5, 4
    _refusal(analyst, body, "band_range")


@pytest.mark.parametrize(
    "treatments, fields",
    [
        ({"cancelled_sales": "KEEP"}, ["cancelled_sales"]),
        ({"returned_sales": "KEEP"}, ["returned_sales"]),
        (
            {"cancelled_sales": "KEEP", "returned_sales": "KEEP"},
            ["cancelled_sales", "returned_sales"],
        ),
    ],
)
def test_a_sale_treatment_other_than_deduct_is_refused(
    analyst: Analyst, treatments: dict[str, str], fields: list[str]
) -> None:
    """The contract's SaleTreatment lists DEDUCT alone (ADR-0062 stands): KEEP would
    score a cancelled sale as sold, and needs a record that supersedes it."""
    body = {**definition(), **treatments}
    before = analyst.count_events()
    response = analyst.create(body)
    assert response.status_code == 422, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["code"] == "invalid_body"
    assert sorted(d["field"] for d in payload["details"]) == fields
    assert {d["code"] for d in payload["details"]} == {"not_allowed"}
    found = analyst.owner.execute(
        "SELECT count(*) FROM rfm.rule_set WHERE name = %s", (body["name"],)
    ).fetchone()
    assert found == (0,) and analyst.count_events() == before


def test_the_treatments_may_be_stated_and_are_deduct(analyst: Analyst) -> None:
    body = {**definition(), "cancelled_sales": "DEDUCT", "returned_sales": "DEDUCT"}
    response = analyst.create(body)
    assert response.status_code == 201, response.get_data(as_text=True)
    stored = response.get_json()
    assert (stored["cancelled_sales"], stored["returned_sales"]) == ("DEDUCT",) * 2


def test_the_table_refuses_keep_whoever_writes_it(owner: psycopg.Connection) -> None:
    """Behind the service's validation, the table says it again (a direct INSERT)."""
    for cancelled, returned, constraint in (
        ("KEEP", "DEDUCT", "rule_set_cancelled_sales_deduct"),
        ("DEDUCT", "KEEP", "rule_set_returned_sales_deduct"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation) as refused:
            with owner.transaction():
                owner.execute(
                    "INSERT INTO rfm.rule_set (name, window_days, recency_boundaries,"
                    " frequency_boundaries, monetary_boundaries, fallback_label_code,"
                    " cancelled_sales, returned_sales)"
                    " VALUES (%s, 30, '{1,2,3,4}', '{4,3,2,1}', '{4,3,2,1}', 'LOST',"
                    " %s, %s)",
                    ("keep " + uuid.uuid4().hex[:12], cancelled, returned),
                )
        assert refused.value.diag.constraint_name == constraint


def test_a_name_in_use_is_a_conflict(analyst: Analyst) -> None:
    body = definition()
    assert analyst.create(body).status_code == 201
    again = analyst.create(copy.deepcopy(body))
    assert again.status_code == 409
    assert again.get_json()["code"] == "rule_set_name_taken"


def test_only_the_administrator_writes_rule_sets(
    build_app: Callable[..., Flask], token: Callable[..., dict[str, str]]
) -> None:
    client = build_app().test_client()
    response = client.post(
        "/v1/rfm-rule-sets", json=definition(), headers={**token("ANALYST"), **JSON}
    )
    assert response.status_code == 403


# --- A draft is edited, a published rule set never ---------------------------


def test_a_draft_is_edited_in_place_and_its_bands_replaced(analyst: Analyst) -> None:
    created = analyst.create().get_json()
    rule_set_id = created["rule_set_id"]
    body = definition(window_days=90)
    body["bands"] = body["bands"][:2] + [
        {**body["bands"][2], "r_min": 1, "r_max": 3, "f_min": 4, "f_max": 5},
        body["bands"][3],
    ]
    response = analyst.client.put(
        f"/v1/rfm-rule-sets/{rule_set_id}", json=body, headers=analyst.headers
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    edited = response.get_json()
    assert (edited["window_days"], edited["state"], edited["name"]) == (
        90,
        "DRAFT",
        body["name"],
    )
    assert analyst.row(rule_set_id)[5] == 4
    assert [e[8] for e in analyst.events(rule_set_id)] == [
        "RULE_SET_CREATED",
        "RULE_SET_EDITED",
    ]


def test_an_invalid_edit_leaves_the_draft_as_it_was(analyst: Analyst) -> None:
    created = analyst.create(definition(window_days=120)).get_json()
    rule_set_id = created["rule_set_id"]
    body = definition(window_days=30)
    body["bands"] = body["bands"][:3]
    response = analyst.client.put(
        f"/v1/rfm-rule-sets/{rule_set_id}", json=body, headers=analyst.headers
    )
    assert response.status_code == 422
    assert analyst.row(rule_set_id)[1] == 120 and analyst.row(rule_set_id)[5] == 4


def test_a_published_rule_set_refuses_an_edit(analyst: Analyst) -> None:
    rule_set_id = analyst.made_published()
    before = analyst.row(rule_set_id)
    events = analyst.count_events()
    response = analyst.client.put(
        f"/v1/rfm-rule-sets/{rule_set_id}",
        json=definition(window_days=30),
        headers=analyst.headers,
    )
    assert response.status_code == 409
    assert response.get_json()["code"] == "rule_set_published"
    assert analyst.row(rule_set_id) == before
    assert analyst.count_events() == events


def test_the_table_itself_refuses_any_change_to_a_published_rule_set(
    analyst: Analyst, owner: psycopg.Connection, service: psycopg.Connection
) -> None:
    rule_set_id = analyst.made_published()
    for statement in (
        "UPDATE rfm.rule_set SET window_days = 30 WHERE rule_set_id = %s",
        "DELETE FROM rfm.rule_set WHERE rule_set_id = %s",
        "DELETE FROM rfm.rule_set_band WHERE rule_set_id = %s",
        "UPDATE rfm.rule_set_band SET label_code = 'X' WHERE rule_set_id = %s",
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match="published"):
            owner.execute(statement, (rule_set_id,))
    with pytest.raises(psycopg.errors.CheckViolation, match="published"):
        service.execute(
            "INSERT INTO rfm.rule_set_band (rule_set_id, band_no, label_code, r_min,"
            " r_max, f_min, f_max, m_min, m_max)"
            " VALUES (%s, 99, 'X', 1, 1, 1, 1, 1, 1)",
            (rule_set_id,),
        )
    assert analyst.row(rule_set_id)[1] == 180 and analyst.row(rule_set_id)[5] == 4


def test_the_database_refuses_to_publish_bands_that_do_not_partition(
    owner: psycopg.Connection, made: list[int]
) -> None:
    row = owner.execute(
        "INSERT INTO rfm.rule_set (name, window_days, recency_boundaries,"
        " frequency_boundaries, monetary_boundaries, fallback_label_code)"
        " VALUES (%s, 30, '{1,2,3,4}', '{4,3,2,1}', '{4,3,2,1}', 'LOST')"
        " RETURNING rule_set_id",
        ("gappy " + uuid.uuid4().hex[:12],),
    ).fetchone()
    assert row is not None
    made.append(row[0])
    owner.execute(
        "INSERT INTO rfm.rule_set_band (rule_set_id, band_no, label_code, r_min, r_max,"
        " f_min, f_max, m_min, m_max) VALUES (%s, 1, 'LOST', 1, 5, 1, 5, 1, 4)",
        (row[0],),
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="partition"):
        owner.execute(
            "UPDATE rfm.rule_set SET state = 'PUBLISHED', published_at = now()"
            " WHERE rule_set_id = %s",
            (row[0],),
        )


def test_the_service_may_not_delete_a_rule_set(
    analyst: Analyst, service: psycopg.Connection
) -> None:
    rule_set_id = int(analyst.create().get_json()["rule_set_id"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        service.execute(
            "DELETE FROM rfm.rule_set WHERE rule_set_id = %s", (rule_set_id,)
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        service.execute(
            "UPDATE rfm.rule_set SET version_no = 9 WHERE rule_set_id = %s",
            (rule_set_id,),
        )


# --- A change to a published rule set is a new version -----------------------


def test_a_new_version_is_a_draft_and_the_published_one_stays(
    analyst: Analyst, owner: psycopg.Connection
) -> None:
    rule_set_id = analyst.made_published()
    published = analyst.row(rule_set_id)
    response = analyst.client.post(
        f"/v1/rfm-rule-sets/{rule_set_id}/versions",
        json=definition(window_days=90),
        headers=analyst.headers,
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    version = response.get_json()
    analyst.made.append(version["rule_set_id"])
    assert (
        version["state"],
        version["version_no"],
        version["parent_rule_set_id"],
        version["window_days"],
    ) == ("DRAFT", 2, rule_set_id, 90)
    assert version["created_by"] == admin_id(owner)
    assert analyst.row(rule_set_id) == published, "the past is not rewritten"
    assert analyst.events(version["rule_set_id"])[0][8] == "RULE_SET_VERSIONED"
    # The new version is a draft like any other: it is edited, then published.
    assert (
        analyst.client.put(
            f"/v1/rfm-rule-sets/{version['rule_set_id']}",
            json={**definition(window_days=60), "name": version["name"]},
            headers=analyst.headers,
        ).status_code
        == 200
    )
    frozen = analyst.client.post(
        f"/v1/rfm-rule-sets/{version['rule_set_id']}/publication",
        headers=analyst.headers,
    )
    assert frozen.get_json()["state"] == "PUBLISHED"


def test_a_rule_set_has_one_next_version_and_only_a_published_one_has_versions(
    analyst: Analyst,
) -> None:
    rule_set_id = analyst.made_published()
    first = analyst.client.post(
        f"/v1/rfm-rule-sets/{rule_set_id}/versions",
        json=definition(),
        headers=analyst.headers,
    )
    assert first.status_code == 201
    analyst.made.append(first.get_json()["rule_set_id"])
    second = analyst.client.post(
        f"/v1/rfm-rule-sets/{rule_set_id}/versions",
        json=definition(),
        headers=analyst.headers,
    )
    assert (second.status_code, second.get_json()["code"]) == (
        409,
        "rule_set_version_exists",
    )
    draft = analyst.client.post(
        f"/v1/rfm-rule-sets/{first.get_json()['rule_set_id']}/versions",
        json=definition(),
        headers=analyst.headers,
    )
    assert (draft.status_code, draft.get_json()["code"]) == (
        409,
        "rule_set_not_published",
    )


def test_an_unknown_rule_set_is_not_found(analyst: Analyst) -> None:
    for call in (
        lambda: analyst.client.get("/v1/rfm-rule-sets/999999", headers=analyst.headers),
        lambda: analyst.client.put(
            "/v1/rfm-rule-sets/999999", json=definition(), headers=analyst.headers
        ),
        lambda: analyst.client.post(
            "/v1/rfm-rule-sets/999999/versions",
            json=definition(),
            headers=analyst.headers,
        ),
        lambda: analyst.client.post(
            "/v1/rfm-rule-sets/999999/publication", headers=analyst.headers
        ),
    ):
        response = call()
        assert (response.status_code, response.get_json()["code"]) == (
            404,
            "rule_set_not_found",
        )


def test_a_published_rule_set_is_published_once(analyst: Analyst) -> None:
    rule_set_id = analyst.made_published()
    again = analyst.client.post(
        f"/v1/rfm-rule-sets/{rule_set_id}/publication", headers=analyst.headers
    )
    assert (again.status_code, again.get_json()["code"]) == (409, "rule_set_published")


def test_the_listing_tells_published_from_draft(analyst: Analyst) -> None:
    rule_set_id = analyst.made_published()
    draft = int(analyst.create().get_json()["rule_set_id"])
    published = analyst.client.get(
        "/v1/rfm-rule-sets?published=true&size=100", headers=analyst.headers
    ).get_json()["rule_sets"]
    ids = {item["rule_set_id"] for item in published}
    assert rule_set_id in ids and draft not in ids and 1 in ids
    drafts = analyst.client.get(
        "/v1/rfm-rule-sets?published=false&size=100", headers=analyst.headers
    ).get_json()["rule_sets"]
    assert draft in {item["rule_set_id"] for item in drafts}


# --- Every change is audited with its author ---------------------------------


def test_each_change_is_a_domain_event_naming_its_author(
    analyst: Analyst, owner: psycopg.Connection
) -> None:
    author = admin_id(owner)
    rule_set_id = analyst.made_published()
    version = analyst.client.post(
        f"/v1/rfm-rule-sets/{rule_set_id}/versions",
        json=definition(),
        headers=analyst.headers,
    ).get_json()
    analyst.made.append(version["rule_set_id"])
    assert analyst.events(rule_set_id) == [
        (
            "CRITICAL_ACTION",
            "SUCCESS",
            "USER",
            author,
            "ADMIN",
            "svc_rfm",
            "rule_set",
            1,
            a,
        )
        for a in ("RULE_SET_CREATED", "RULE_SET_PUBLISHED")
    ]
    assert analyst.events(version["rule_set_id"]) == [
        (
            "CRITICAL_ACTION",
            "SUCCESS",
            "USER",
            author,
            "ADMIN",
            "svc_rfm",
            "rule_set",
            2,
            "RULE_SET_VERSIONED",
        )
    ]


def test_a_change_that_cannot_be_audited_is_not_made(
    analyst: Analyst, owner: psycopg.Connection, service: psycopg.Connection
) -> None:
    """The event and the change commit together or not at all (ADR-0072)."""
    owner.execute(
        "REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text,"
        " timestamptz, text, uuid, text, smallint, text, text, bigint, bigint,"
        " text, jsonb) FROM svc_rfm"
    )
    try:
        body = definition()
        response = analyst.client.post(
            "/v1/rfm-rule-sets", json=body, headers=analyst.headers
        )
    finally:
        owner.execute(
            "GRANT EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text,"
            " timestamptz, text, uuid, text, smallint, text, text, bigint, bigint,"
            " text, jsonb) TO svc_rfm"
        )
    assert response.status_code == 503
    found = owner.execute(
        "SELECT count(*) FROM rfm.rule_set WHERE name = %s", (body["name"],)
    ).fetchone()
    assert found == (0,)


# --- A rule set decides how a reversed sale counts ---------------------------


def test_a_published_rule_set_leaves_a_reversed_sale_out_of_the_profile(
    analyst: Analyst, shop: Shop, service: psycopg.Connection, now: datetime
) -> None:
    from app import recompute

    customer = shop.customer()
    shop.sale(customer, days_ago(now, 10), quantity=2, unit_price="50.00")
    cancelled = shop.sale(customer, days_ago(now, 5), quantity=1, unit_price="30.00")
    shop.reverse(cancelled)
    own = analyst.made_published(definition())
    with service.transaction():
        recompute.customer(service, customer, own, now)
    scored = shop.profile(customer)
    assert scored is not None
    assert (scored[3], str(scored[4])) == (1, "100.00")


def test_the_listing_keeps_the_v1_shape_and_the_record_carries_the_governance(
    analyst: Analyst,
) -> None:
    listed = analyst.client.get(
        "/v1/rfm-rule-sets?size=100", headers=analyst.headers
    ).get_json()["rule_sets"]
    seeded = next(item for item in listed if item["rule_set_id"] == 1)
    assert "state" not in seeded and seeded["published"] is True
    record = analyst.client.get(
        "/v1/rfm-rule-sets/1", headers=analyst.headers
    ).get_json()
    assert (record["state"], record["version_no"], record["parent_rule_set_id"]) == (
        "PUBLISHED",
        1,
        None,
    )
    assert record["cancelled_sales"] == "DEDUCT" and record["published_at"] is not None
    seeded_draft = analyst.client.get(
        "/v1/rfm-rule-sets/2", headers=analyst.headers
    ).get_json()
    assert (seeded_draft["state"], seeded_draft["parent_rule_set_id"]) == ("DRAFT", 1)


# --- A profile is scored under a published rule set only (ADR-0064) ----------


def _draft(owner: psycopg.Connection) -> int:
    row = owner.execute(
        "SELECT min(rule_set_id) FROM rfm.rule_set WHERE state = 'DRAFT'"
    ).fetchone()
    assert row is not None and row[0] is not None, "the seed holds a draft"
    return int(row[0])


def test_a_customer_is_not_recomputed_under_a_draft(
    shop: Shop, owner: psycopg.Connection, service: psycopg.Connection, now: datetime
) -> None:
    from app import recompute

    customer = shop.customer()
    shop.sale(customer, days_ago(now, 10))
    with pytest.raises(recompute.RuleSetNotPublished):
        with service.transaction():
            recompute.customer(service, customer, _draft(owner), now)
    assert shop.profile(customer) is None, "a refused recomputation writes nothing"
    with service.transaction():
        recompute.customer(service, customer, 1, now)
    assert shop.profile(customer) is not None


def test_a_sale_event_is_refused_while_the_rule_set_in_force_is_a_draft(
    build_app: Callable[..., Flask],
    monkeypatch: pytest.MonkeyPatch,
    shop: Shop,
    owner: psycopg.Connection,
    now: datetime,
) -> None:
    from app import recompute

    customer = shop.customer()
    sale = shop.sale(customer, days_ago(now, 3))
    monkeypatch.setenv("RFM_RULE_SET_ID", str(_draft(owner)))
    app = build_app()
    with pytest.raises(recompute.RuleSetNotPublished):
        app.extensions["rfm_handle_sale"](
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "INSERT",
                "entity_id": str(sale),
                "occurred_at": "2026-11-01T12:00:00Z",
                "v": "1",
            }
        )
    assert shop.profile(customer) is None


def test_the_scheduled_pass_is_refused_under_a_draft_and_records_nothing(
    shop: Shop,
    owner: psycopg.Connection,
    service_url: str,
) -> None:
    from mosaiq_kit.database import Database

    from app import ageing, recompute

    owner.execute("DELETE FROM rfm.pass_run")
    db = Database(service_url)
    try:
        with pytest.raises(recompute.RuleSetNotPublished):
            ageing.run_pass(db, _draft(owner))
    finally:
        db.close()
    found = owner.execute("SELECT count(*) FROM rfm.pass_run").fetchone()
    assert found == (0,), "a refused pass leaves no run RUNNING"


# --- A band edit and a publication that race (direct SQL) --------------------


def _waits_on_a_lock(owner: psycopg.Connection, pid: int) -> bool:
    for _ in range(100):
        row = owner.execute(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)
        ).fetchone()
        if row is not None and row[0] == "Lock":
            return True
        time.sleep(0.05)
    return False


def test_a_band_edit_and_a_publication_cannot_both_commit(
    analyst: Analyst, owner: psycopg.Connection, owner_url: str
) -> None:
    """Through raw SQL, a transaction that deletes a draft's band and one that
    publishes the draft run side by side. The publication must wait for the edit and
    then judge the bands the edit left, or refuse; the two never both commit."""
    created = analyst.create()
    assert created.status_code == 201
    rule_set_id = int(created.get_json()["rule_set_id"])
    editor = psycopg.connect(owner_url)
    publisher = psycopg.connect(owner_url, autocommit=True)
    outcome: list[str] = []

    def publish() -> None:
        try:
            publisher.execute(
                "UPDATE rfm.rule_set SET state = 'PUBLISHED', published_at = now()"
                " WHERE rule_set_id = %s",
                (rule_set_id,),
            )
            outcome.append("published")
        except psycopg.errors.CheckViolation as refused:
            outcome.append(str(refused.diag.constraint_name))

    try:
        editor.execute(
            "DELETE FROM rfm.rule_set_band WHERE rule_set_id = %s AND band_no = 1",
            (rule_set_id,),
        )
        worker = threading.Thread(target=publish)
        worker.start()
        assert _waits_on_a_lock(
            owner, publisher.info.backend_pid
        ), "the publication does not wait for a band edit that is still open"
        editor.commit()
        worker.join(10)
    finally:
        editor.close()
        publisher.close()
    assert outcome == ["rule_set_bands_partition"], outcome
    state = analyst.row(rule_set_id)[2]
    assert state == "DRAFT"


def test_a_band_edit_that_starts_during_a_publication_is_refused_once_it_commits(
    analyst: Analyst, owner: psycopg.Connection, owner_url: str
) -> None:
    created = analyst.create()
    assert created.status_code == 201
    rule_set_id = int(created.get_json()["rule_set_id"])
    publisher = psycopg.connect(owner_url)
    editor = psycopg.connect(owner_url, autocommit=True)
    outcome: list[str] = []

    def edit() -> None:
        try:
            editor.execute(
                "DELETE FROM rfm.rule_set_band WHERE rule_set_id = %s AND band_no = 1",
                (rule_set_id,),
            )
            outcome.append("edited")
        except psycopg.errors.CheckViolation as refused:
            outcome.append(str(refused.diag.constraint_name))

    try:
        publisher.execute(
            "UPDATE rfm.rule_set SET state = 'PUBLISHED', published_at = now()"
            " WHERE rule_set_id = %s",
            (rule_set_id,),
        )
        worker = threading.Thread(target=edit)
        worker.start()
        assert _waits_on_a_lock(
            owner, editor.info.backend_pid
        ), "the band edit does not wait for a publication that is still open"
        publisher.commit()
        worker.join(10)
    finally:
        publisher.close()
        editor.close()
    assert outcome == ["rule_set_published_immutable"], outcome
    assert analyst.row(rule_set_id)[2:6] == ("PUBLISHED", 1, None, 4)
