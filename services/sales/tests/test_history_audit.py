"""A read of one customer's sales history is logged, and fails closed (F21-25).

ADR-0072 section 4: `GET /v1/sales?customer_id=` records one `HISTORY_READ`
domain event naming who, which customer and when, never what was returned; a
403 on it records one with outcome `REFUSED`; when the event cannot be recorded
the read answers 503 `audit_unavailable` with no rows.
"""

from __future__ import annotations

import uuid

import psycopg

FUNCTION = (
    "api_audit.record_event_v1(uuid, text, text, timestamptz, text, uuid, text,"
    " smallint, text, text, bigint, bigint, text, jsonb)"
)


def _events(owner: psycopg.Connection, correlation_id: str) -> list[tuple]:
    return owner.execute(
        "SELECT kind, outcome, actor_type, actor_user_id::text, actor_role, source,"
        " subject_type, subject_id, details, occurred_at, recorded_at"
        " FROM audit_events.domain_event WHERE correlation_id = %s",
        (correlation_id,),
    ).fetchall()


def _headers(token, role: str) -> tuple[dict[str, str], str]:
    correlation = "test-history-" + uuid.uuid4().hex
    return {**token(role), "X-Correlation-ID": correlation}, correlation


def _customer(owner: psycopg.Connection) -> str:
    return str(
        owner.execute(
            "SELECT customer_id FROM public.customer ORDER BY customer_id LIMIT %s",
            (1,),
        ).fetchone()[0]
    )


def test_a_customer_history_read_records_who_what_and_when_but_not_the_data(
    client, token, owner, role_subjects
):
    customer = _customer(owner)
    headers, correlation = _headers(token, "ANALYST")

    response = client.get(f"/v1/sales?customer_id={customer}", headers=headers)

    assert response.status_code == 200
    rows = _events(owner, correlation)
    assert len(rows) == 1
    kind, outcome, actor_type, actor, role, source, s_type, s_id, details, at, rec = (
        rows[0]
    )
    assert (kind, outcome, actor_type) == ("HISTORY_READ", "SUCCESS", "USER")
    assert (actor, role, source) == (role_subjects["ANALYST"], "ANALYST", "svc_sales")
    assert (s_type, s_id) == ("customer", customer)
    assert at is not None and rec is not None
    # Not the data: details is the history kind and nothing else, and every
    # other column of the event is who, which customer, when, or the chain.
    assert response.get_json()["sales"], "the customer has sales to withhold"
    assert details == {"history": "SALES"}
    whole = owner.execute(
        "SELECT to_jsonb(d) FROM audit_events.domain_event AS d"
        " WHERE correlation_id = %s",
        (correlation,),
    ).fetchone()[0]
    assert {key for key, value in whole.items() if value is not None} == {
        "seq",
        "event_id",
        "kind",
        "outcome",
        "occurred_at",
        "recorded_at",
        "actor_type",
        "actor_user_id",
        "actor_role",
        "source",
        "subject_type",
        "subject_id",
        "correlation_id",
        "details",
        "prev_hash",
        "entry_hash",
    }


def test_a_read_that_names_no_customer_records_nothing(client, token, owner):
    headers, correlation = _headers(token, "ADMIN")

    response = client.get("/v1/sales", headers=headers)

    assert response.status_code == 200
    assert _events(owner, correlation) == []


def test_a_refused_customer_history_read_is_recorded_as_refused(
    client, token, owner, role_subjects
):
    customer = _customer(owner)
    headers, correlation = _headers(token, "INVENTORY_PLANNER")

    response = client.get(f"/v1/sales?customer_id={customer}", headers=headers)

    assert response.status_code == 403
    assert response.get_json()["code"] == "forbidden_role"
    rows = _events(owner, correlation)
    assert [(r[0], r[1], r[3], r[4], r[7]) for r in rows] == [
        (
            "HISTORY_READ",
            "REFUSED",
            role_subjects["INVENTORY_PLANNER"],
            "INVENTORY_PLANNER",
            customer,
        )
    ]


def test_the_read_fails_closed_when_its_event_cannot_be_recorded(client, token, owner):
    customer = _customer(owner)
    headers, correlation = _headers(token, "ADMIN")
    owner.execute(f"REVOKE EXECUTE ON FUNCTION {FUNCTION} FROM svc_sales")
    try:
        response = client.get(f"/v1/sales?customer_id={customer}", headers=headers)
        xml = client.get(
            f"/v1/sales?customer_id={customer}",
            headers={**headers, "Accept": "application/xml"},
        )
        refused, refused_correlation = _headers(token, "INVENTORY_PLANNER")
        forbidden = client.get(f"/v1/sales?customer_id={customer}", headers=refused)
    finally:
        owner.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO svc_sales")

    assert response.status_code == 503
    body = response.get_json()
    assert body["code"] == "audit_unavailable"
    assert "sales" not in body
    assert xml.status_code == 503
    assert b"<code>audit_unavailable</code>" in xml.data
    assert b"<Sale>" not in xml.data
    # A refusal that cannot be recorded is not answered as a plain 403 either.
    assert forbidden.status_code == 503
    assert _events(owner, correlation) == []
    assert _events(owner, refused_correlation) == []
