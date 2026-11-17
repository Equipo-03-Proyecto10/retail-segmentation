"""Anonymize a customer in place (F21-19, ADR-0066 section 3), through the real
service and the real schema.

Each test builds a throwaway customer with an identity, sales, segment history,
an RFM profile and an active loyalty card, anonymizes it through
`PUT /v1/customers/{customer_id}/anonymization`, and checks what is gone, what
stayed and what reconciles. An anonymized row is final (the schema's trigger),
so the fixture removes the customer and its sales itself.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from lxml import etree
from test_http import NAMESPACE, valid

import redis
from app.anonymization import PLACEHOLDER
from app.service import OutboxRelay, relay_redis

pytestmark = pytest.mark.postgres

STREAM = "stream:customers.v1.anonymized"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
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


def path(customer_id: str) -> str:
    return f"/v1/customers/{customer_id}/anonymization"


def xml_request(reason: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<AnonymizeCustomerRequest xmlns="{NAMESPACE}">'
        f"<reason>{reason}</reason></AnonymizeCustomerRequest>"
    ).encode()


class Subject:
    """A customer with an identity, a history and a card, built as the owner."""

    def __init__(self, owner: psycopg.Connection, unique: str, actor: str) -> None:
        self.owner = owner
        self.id = str(uuid.uuid4())
        self.name = unique
        self.email = f"{unique}@example.test"
        self.phone = "5559" + unique[-6:].replace("-", "0")[:6]
        self.card_code = "".join(secrets.choice(CROCKFORD) for _ in range(26))
        owner.execute(
            "INSERT INTO customer (customer_id, name, email, phone,"
            " registration_channel_id) VALUES (%s, %s, %s, %s, 1)",
            (self.id, self.name, self.email, self.phone),
        )
        # An UPDATE of the identity, so the trail holds it before and after.
        owner.execute(
            "UPDATE customer SET phone = phone WHERE customer_id = %s", (self.id,)
        )
        for day, lines in (
            (40, [(1, 2, "10.00"), (2, 1, "5.50")]),
            (3, [(1, 3, "7.25")]),
        ):
            total = sum(q * Decimal(p) for _, q, p in lines)
            with owner.transaction():
                self._without_relays()
                tid = owner.execute(
                    "INSERT INTO transaction (source_transaction_id, customer_id,"
                    " store_id, channel_id, occurred_at, total, data_origin)"
                    " VALUES (%s, %s, 1, 1, now() - make_interval(days => %s), %s,"
                    " 'OBSERVED') RETURNING transaction_id",
                    (f"anon-{uuid.uuid4().hex[:20]}", self.id, day, total),
                ).fetchone()[0]
                for product, quantity, price in lines:
                    owner.execute(
                        "INSERT INTO transaction_line (transaction_id, product_id,"
                        " quantity, unit_price) VALUES (%s, %s, %s, %s)",
                        (tid, product, quantity, price),
                    )
        with owner.transaction():
            self._without_relays()
            owner.execute(
                "INSERT INTO customer_segment_history (customer_id, run_id,"
                " label_code, recency_last_purchase_at, frequency_count,"
                " monetary_total, r_score, f_score, m_score)"
                " VALUES (%s, 1, 'LOYAL', now(), 2, 41.75, 5, 3, 4)",
                (self.id,),
            )
            owner.execute(
                "INSERT INTO rfm.customer_profile (customer_id, state, rule_set_id,"
                " r_days, f_count, m_total, r_score, f_score, m_score, label_code,"
                " via_fallback, computed_at) SELECT %s, 'scored', min(rule_set_id),"
                " 3, 2, 41.75, 5, 3, 4, 'LOYAL', false, now() FROM rfm.rule_set",
                (self.id,),
            )
        owner.execute(
            "INSERT INTO customers.loyalty_card (customer_id, code, issued_by)"
            " VALUES (%s, %s, %s)",
            (self.id, self.card_code, actor),
        )

    def _without_relays(self) -> None:
        """Keep the fixture's sales and profile out of other boundaries' outboxes.

        The capture triggers of sales and rfm-engine would leave relay rows in
        a database every other suite expects to be empty after the seed. The
        rows written under this setting are valid, so no check is missed.
        """

        self.owner.execute("SET LOCAL session_replication_role = replica")

    def snapshot(self) -> dict[str, Any]:
        """Everything the story says must still reconcile after anonymization."""

        one = lambda q: self.owner.execute(q, (self.id,)).fetchall()  # noqa: E731
        return {
            "sales": one(
                "SELECT count(*), sum(total), max(occurred_at) FROM transaction"
                " WHERE customer_id = %s"
            ),
            "lines": one(
                "SELECT count(*), sum(l.quantity * l.unit_price) FROM transaction t"
                " JOIN transaction_line l USING (transaction_id)"
                " WHERE t.customer_id = %s"
            ),
            "net": one(
                "SELECT count(*), sum(net_amount), sum(net_quantity)"
                " FROM net_sale_line WHERE customer_id = %s"
            ),
            "rfm_from_sales": one(
                "SELECT extract(day FROM now() - max(occurred_at))::int,"
                " count(*), sum(total) FROM transaction WHERE customer_id = %s"
            ),
            "history": one(
                "SELECT history_id, run_id, label_code, recency_last_purchase_at,"
                " frequency_count, monetary_total, r_score, f_score, m_score,"
                " valid_from, valid_to FROM customer_segment_history"
                " WHERE customer_id = %s ORDER BY history_id"
            ),
            "profile": one(
                "SELECT state, rule_set_id, r_days, f_count, m_total, r_score,"
                " f_score, m_score, label_code, computed_at FROM rfm.customer_profile"
                " WHERE customer_id = %s"
            ),
            "registration": one(
                "SELECT customer_id, registration_channel_id, registered_on"
                " FROM customer WHERE customer_id = %s"
            ),
        }

    def trail(self) -> list[tuple[Any, ...]]:
        """The customer's audit entries without their payloads."""

        return self.owner.execute(
            "SELECT audit_id, user_id, entity, entity_pk, action, executed_at"
            " FROM audit_log WHERE entity = 'customer' AND entity_pk = %s"
            " ORDER BY audit_id",
            (self.id,),
        ).fetchall()

    def in_trail(self, *needles: str) -> int:
        return self.owner.execute(
            "SELECT count(*) FROM audit_log WHERE data_before::text LIKE ANY(%s)"
            " OR data_after::text LIKE ANY(%s)",
            ([f"%{n}%" for n in needles], [f"%{n}%" for n in needles]),
        ).fetchone()[0]

    def remove(self) -> None:
        self.owner.execute(
            "DELETE FROM customers.loyalty_card WHERE customer_id = %s", (self.id,)
        )
        self.owner.execute("DELETE FROM transaction WHERE customer_id = %s", (self.id,))
        self.owner.execute("DELETE FROM customer WHERE customer_id = %s", (self.id,))
        self.owner.execute(
            "DELETE FROM customers_events.outbox WHERE entity_id = %s", (self.id,)
        )


@pytest.fixture
def subject(owner: psycopg.Connection, unique: str, actor: str) -> Iterator[Subject]:
    made = Subject(owner, unique, actor)
    yield made
    made.remove()


@pytest.fixture
def admin(token) -> dict[str, str]:
    return token("ADMIN")


def anonymize(client, admin, customer_id: str, reason: str = "SUBJECT_REQUEST"):
    return client.put(path(customer_id), json={"reason": reason}, headers=admin)


def test_the_identity_is_gone_and_the_history_reconciles(
    client, admin, owner, subject, actor
):
    before = subject.snapshot()
    trail_before = subject.trail()
    assert len(trail_before) == 2  # the INSERT and the UPDATE
    assert subject.in_trail(subject.name, subject.email, subject.phone) > 0

    response = anonymize(client, admin, subject.id)
    assert response.status_code == 200, response.get_json()
    answer = response.get_json()
    assert answer["customer_id"] == subject.id
    assert answer["outcome"] == "ANONYMIZED" and answer["anonymized_at"]

    # Name, email, phone and the account link are gone; the key stays.
    row = owner.execute(
        "SELECT name, email, phone, user_id, anonymized_at IS NOT NULL"
        " FROM customer WHERE customer_id = %s",
        (subject.id,),
    ).fetchone()
    assert row == (PLACEHOLDER, None, None, None, True)

    # Sales, lines, net units, R, F and M (stored and recomputed), the segment
    # history, the profile and the registration reconcile exactly.
    after = subject.snapshot()
    assert after == before
    assert before["sales"][0][0] == 2 and before["lines"][0][0] == 3

    # The audit trail still holds every entry it held, with the same key,
    # action, time and actor, plus the one the anonymization wrote; and it holds
    # no name, email or phone of the customer anywhere.
    trail_after = subject.trail()
    assert trail_after[: len(trail_before)] == trail_before
    assert len(trail_after) == len(trail_before) + 1
    assert trail_after[-1][4] == "UPDATE" and str(trail_after[-1][1]) == actor
    assert subject.in_trail(subject.name, subject.email, subject.phone) == 0
    payload = owner.execute(
        "SELECT data_before, data_after FROM audit_log WHERE entity = 'customer'"
        " AND entity_pk = %s AND action = 'UPDATE' ORDER BY audit_id LIMIT 1",
        (subject.id,),
    ).fetchone()
    assert payload[0]["name"] == payload[1]["name"] == "[redacted]"
    assert payload[0]["phone"] == "[redacted]"
    # What is not identity stays in the payload.
    assert payload[0]["registration_channel_id"] == 1
    assert payload[0]["customer_id"] == subject.id


def test_no_client_read_returns_personal_data_in_json_or_xml(
    client, admin, token, subject
):
    anonymize(client, admin, subject.id)
    identity = (subject.name, subject.email, subject.phone)

    for role in ("ADMIN", "STORE_MANAGER", "ANALYST", "MARKETING", "AUDITOR"):
        read = client.get(f"/v1/customers/{subject.id}", headers=token(role))
        assert read.status_code == 200
        assert read.get_json() == {
            "customer_id": subject.id,
            "name": PLACEHOLDER,
            "email": None,
            "phone": None,
            "registration_channel_id": 1,
            "registered_on": read.get_json()["registered_on"],
        }
        xml = client.get(
            f"/v1/customers/{subject.id}",
            headers={**token(role), "Accept": "application/xml"},
        )
        assert valid(xml.data)
        tree = etree.fromstring(xml.data)
        names = {etree.QName(child).localname for child in tree}
        assert "email" not in names and "phone" not in names
        assert tree.find(f"{{{NAMESPACE}}}name").text == PLACEHOLDER
        assert not any(value.encode() in xml.data for value in identity)

    for role in ("SALES_ASSOCIATE", "LOYALTY_OFFICER"):
        floor = client.get(
            f"/v1/customers/{subject.id}/floor-view", headers=token(role)
        )
        assert floor.status_code == 200
        assert floor.get_json()["name"] == PLACEHOLDER
        assert not floor.get_json().get("email") and not floor.get_json().get("phone")
        assert not any(v in floor.get_data(as_text=True) for v in identity)

    # No search finds the person any more, by name, email or phone.
    for query in (f"q={subject.name}", f"q={subject.phone}", f"email={subject.email}"):
        found = client.get(f"/v1/customers?{query}", headers=admin)
        assert found.get_json()["customers"] == [], query
    for role in ("LOYALTY_OFFICER", "SALES_ASSOCIATE"):
        found = client.get(
            f"/v1/customers/floor-search?q={subject.name}", headers=token(role)
        )
        assert found.get_json()["customers"] == []
    # It is found as what it now is.
    found = client.get("/v1/customers?q=Anonymized%20customer&size=100", headers=admin)
    assert subject.id in {c["customer_id"] for c in found.get_json()["customers"]}


def test_a_correction_and_a_new_card_are_refused_and_the_old_card_is_revoked(
    client, admin, token, owner, subject, actor
):
    anonymize(client, admin, subject.id, "RETENTION_EXPIRED")

    for accept in ("application/json", "application/xml"):
        patch = client.patch(
            f"/v1/customers/{subject.id}",
            json={"name": "Someone Else"},
            headers={**token("STORE_MANAGER"), "Accept": accept},
        )
        assert patch.status_code == 409, patch.data
        assert b"customer_anonymized" in patch.data
        card = client.post(
            "/v1/loyalty-cards",
            json={"customer_id": subject.id},
            headers={**token("LOYALTY_OFFICER"), "Accept": accept},
        )
        assert card.status_code == 409, card.data
        assert b"customer_anonymized" in card.data
    assert owner.execute(
        "SELECT name FROM customer WHERE customer_id = %s", (subject.id,)
    ).fetchone() == (PLACEHOLDER,)

    cards = client.get(f"/v1/loyalty-cards?customer_id={subject.id}", headers=admin)
    body = cards.get_json()
    assert body["loyalty_state"] == "REVOKED"
    [card] = body["loyalty_cards"]
    assert (card["status"], card["revoked_by"], card["revocation_reason"]) == (
        "REVOKED",
        actor,
        "OTHER",
    )
    lookup = client.post(
        "/v1/loyalty-cards/lookup",
        json={"code": subject.card_code},
        headers=token("SALES_ASSOCIATE"),
    )
    assert lookup.status_code == 409 and lookup.get_json()["code"] == "card_revoked"


def test_running_it_twice_changes_nothing(client, admin, owner, subject):
    first = anonymize(client, admin, subject.id)
    assert first.get_json()["outcome"] == "ANONYMIZED"

    def everything() -> dict[str, list]:
        tables = {
            "customer": "SELECT * FROM customer ORDER BY customer_id",
            "loyalty_card": "SELECT * FROM customers.loyalty_card ORDER BY card_id",
            "audit_log": "SELECT * FROM audit_log ORDER BY audit_id",
            "domain_event": "SELECT event_id, entry_hash FROM audit_events.domain_event"
            " ORDER BY recorded_at, event_id",
            "outbox": "SELECT event_id, entity_id, relayed_at FROM"
            " customers_events.outbox ORDER BY event_id",
            "history": "SELECT * FROM customer_segment_history ORDER BY history_id",
        }
        return {name: owner.execute(q).fetchall() for name, q in tables.items()}

    snapshot = everything()
    for reason, accept in (
        ("SUBJECT_REQUEST", "application/json"),
        ("RETENTION_EXPIRED", "application/xml"),
    ):
        again = client.put(
            path(subject.id),
            json={"reason": reason},
            headers={**admin, "Accept": accept},
        )
        assert again.status_code == 200
        if accept == "application/json":
            body = again.get_json()
            assert body["outcome"] == "ALREADY_ANONYMIZED"
            assert body["anonymized_at"] == first.get_json()["anonymized_at"]
        else:
            assert valid(again.data) and b"ALREADY_ANONYMIZED" in again.data
        assert everything() == snapshot, "a second run changed a table"


def test_the_run_is_audited_once_naming_the_administrator(
    client, admin, owner, subject, actor
):
    anonymize(client, admin, subject.id, "SUBJECT_REQUEST")
    anonymize(client, admin, subject.id, "SUBJECT_REQUEST")
    events = owner.execute(
        "SELECT kind, outcome, actor_type, actor_user_id::text, actor_role, source,"
        " subject_type, details FROM audit_events.domain_event WHERE subject_id = %s"
        " ORDER BY recorded_at, event_id",
        (subject.id,),
    ).fetchall()
    assert len(events) == 2
    assert {e[7]["action"]: e[7]["reason_code"] for e in events} == {
        "CUSTOMER_ANONYMIZED": "SUBJECT_REQUEST",
        "AUDIT_PAYLOAD_REDACTED": "ANONYMIZATION",
    }
    for kind, outcome, actor_type, user, role, source, subject_type, _ in events:
        assert (kind, outcome, actor_type, user, role, source, subject_type) == (
            "CRITICAL_ACTION",
            "SUCCESS",
            "USER",
            actor,
            "ADMIN",
            "svc_customers",
            "customer",
        )
    # The events name the customer by id and never carry the identity.
    assert not any(
        value in json.dumps(e[7], default=str)
        for e in events
        for value in (subject.name, subject.email)
    )


def test_an_audit_that_cannot_be_recorded_leaves_the_customer_untouched(
    client, admin, owner, subject
):
    signature = (
        "api_audit.record_event_v1(uuid, text, text, timestamptz, text, uuid, text,"
        " smallint, text, text, bigint, bigint, text, jsonb)"
    )
    before = subject.snapshot(), subject.trail()
    owner.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM svc_customers")
    try:
        refused = anonymize(client, admin, subject.id)
    finally:
        owner.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO svc_customers")
    assert refused.status_code == 503
    assert refused.get_json()["code"] == "audit_unavailable"
    row = owner.execute(
        "SELECT name, email, phone, anonymized_at FROM customer WHERE customer_id = %s",
        (subject.id,),
    ).fetchone()
    assert row == (subject.name, subject.email, subject.phone, None)
    assert (subject.snapshot(), subject.trail()) == before
    assert subject.in_trail(subject.email) > 0, "nothing was redacted"
    cards = owner.execute(
        "SELECT status FROM customers.loyalty_card WHERE customer_id = %s",
        (subject.id,),
    ).fetchall()
    assert cards == [("ACTIVE",)]
    assert owner.execute(
        "SELECT count(*) FROM customers_events.outbox WHERE entity_id = %s",
        (subject.id,),
    ).fetchone() == (0,)
    # And the operation works once the log is writable again.
    assert anonymize(client, admin, subject.id).status_code == 200


def test_only_an_administrator_anonymizes_and_the_request_is_validated(
    client, admin, token, owner, subject
):
    for role in ROLES:
        refused = client.put(
            path(subject.id), json={"reason": "SUBJECT_REQUEST"}, headers=token(role)
        )
        assert refused.status_code == 403, role
        assert refused.get_json()["code"] == "forbidden_role"
    assert owner.execute(
        "SELECT anonymized_at FROM customer WHERE customer_id = %s", (subject.id,)
    ).fetchone() == (None,)

    unknown = anonymize(client, admin, str(uuid.uuid4()))
    assert unknown.status_code == 404
    assert unknown.get_json()["code"] == "customer_not_found"
    for bad in ({}, {"reason": "BECAUSE"}, {"reason": "SUBJECT_REQUEST", "x": 1}):
        invalid = client.put(path(subject.id), json=bad, headers=admin)
        assert invalid.status_code == 422, bad
        assert invalid.get_json()["code"] == "invalid_body"
    malformed = client.put(
        path(subject.id),
        data=b"{",
        headers={**admin, "Content-Type": "application/json"},
    )
    assert malformed.status_code == 400

    # An XML request and an XML answer, validated against the XSD.
    done = client.put(
        path(subject.id),
        data=xml_request("SUBJECT_REQUEST"),
        headers={
            **admin,
            "Content-Type": "application/xml",
            "Accept": "application/xml",
        },
    )
    assert done.status_code == 200, done.data
    assert valid(done.data) and b"<outcome>ANONYMIZED</outcome>" in done.data
    wrong = client.put(
        path(subject.id),
        data=xml_request("BECAUSE"),
        headers={**admin, "Content-Type": "application/xml"},
    )
    assert wrong.status_code == 422


def test_the_schema_holds_the_rule_for_every_writer(
    owner, service_url, subject, client, admin
):
    # A marker that leaves an identity behind is refused by the table.
    with pytest.raises(psycopg.errors.CheckViolation) as marked:
        owner.execute(
            "UPDATE customer SET anonymized_at = now() WHERE customer_id = %s",
            (subject.id,),
        )
    assert marked.value.diag.constraint_name == "customer_anonymized_identity_check"

    anonymize(client, admin, subject.id)
    # An anonymized row never changes again, whoever writes it.
    for statement in (
        "UPDATE customer SET name = 'Back Again' WHERE customer_id = %s",
        "UPDATE customer SET anonymized_at = NULL WHERE customer_id = %s",
        "UPDATE customer SET registration_channel_id = 2 WHERE customer_id = %s",
    ):
        with pytest.raises(psycopg.errors.CheckViolation) as final:
            owner.execute(statement, (subject.id,))
        assert final.value.diag.constraint_name == "customer_anonymized"
    # No card is ever issued to it, by a direct INSERT either.
    with pytest.raises(psycopg.errors.CheckViolation) as card:
        owner.execute(
            "INSERT INTO customers.loyalty_card (customer_id, code, issued_by)"
            " SELECT %s::uuid, %s, user_id FROM app_user LIMIT 1",
            (subject.id, "".join(secrets.choice(CROCKFORD) for _ in range(26))),
        )
    assert card.value.diag.constraint_name == "customer_anonymized"

    with psycopg.connect(service_url, autocommit=True) as service:
        # The service role rewrites no audit entry itself, and the redaction
        # function refuses a customer who is not anonymized.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            service.execute("UPDATE audit_log SET data_before = NULL")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            service.execute("DELETE FROM audit_log")
        other = owner.execute(
            "SELECT customer_id FROM customer WHERE anonymized_at IS NULL LIMIT 1"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation) as refused:
            service.execute("SELECT customers.redact_audit_v1(%s)", (other,))
        assert refused.value.diag.constraint_name == "customer_not_anonymized"


def test_the_redaction_is_pure_and_idempotent(owner):
    redact = lambda doc: owner.execute(  # noqa: E731
        "SELECT public.fn_redact_identity(%s::jsonb)", (json.dumps(doc),)
    ).fetchone()[0]
    original = {"name": "Ana", "email": None, "phone": "555", "customer_id": "x"}
    once = redact(original)
    assert once == {
        "name": "[redacted]",
        "email": None,
        "phone": "[redacted]",
        "customer_id": "x",
    }
    assert redact(once) == once
    assert redact({"customer_id": "x"}) == {"customer_id": "x"}
    assert owner.execute("SELECT public.fn_redact_identity(NULL)").fetchone()[0] is None


def test_a_later_entry_of_an_anonymized_customer_holds_no_identity(
    client, admin, owner, subject
):
    anonymize(client, admin, subject.id)
    owner.execute(
        "DELETE FROM customers.loyalty_card WHERE customer_id = %s", (subject.id,)
    )
    owner.execute("DELETE FROM transaction WHERE customer_id = %s", (subject.id,))
    owner.execute("DELETE FROM customer WHERE customer_id = %s", (subject.id,))
    entry = owner.execute(
        "SELECT data_before, data_after FROM audit_log WHERE entity = 'customer'"
        " AND entity_pk = %s AND action = 'DELETE'",
        (subject.id,),
    ).fetchone()
    assert entry[0]["name"] == "[redacted]" and entry[1] is None
    assert subject.in_trail(PLACEHOLDER) == 0


def test_the_flag_is_published_to_the_services_that_read_the_reference(
    client, admin, owner, subject
):
    flag = lambda: owner.execute(  # noqa: E731
        "SELECT anonymized FROM api_customers.customer_ref_v1 WHERE customer_id = %s",
        (subject.id,),
    ).fetchone()[0]
    assert flag() is False
    anonymize(client, admin, subject.id)
    assert flag() is True
    assert owner.execute(
        "SELECT count(*) FROM api_customers.customer_ref_v1 WHERE anonymized"
    ).fetchone() == (1,)
    # The key and the registration date are still published: nothing that hangs
    # on the customer stops resolving.
    assert owner.execute(
        "SELECT registered_on IS NOT NULL FROM api_customers.customer_ref_v1"
        " WHERE customer_id = %s",
        (subject.id,),
    ).fetchone() == (True,)
    for role in ("svc_campaigns", "svc_sales", "svc_segments", "svc_preferences"):
        with owner.transaction():
            owner.execute(
                psycopg.sql.SQL("SET LOCAL ROLE {}").format(
                    psycopg.sql.Identifier(role)
                )
            )
            assert owner.execute(
                "SELECT anonymized FROM api_customers.customer_ref_v1"
                " WHERE customer_id = %s",
                (subject.id,),
            ).fetchone() == (True,)


def test_one_event_is_announced_and_the_relay_carries_it(
    client, admin, owner, subject, service_url
):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        if os.environ.get("MOSAIQ_REQUIRE_REDIS") == "1":
            pytest.fail(
                "TEST_REDIS_URL is missing, and MOSAIQ_REQUIRE_REDIS forbids it"
            )
        pytest.skip("TEST_REDIS_URL is not set")
    anonymize(client, admin, subject.id)
    anonymize(client, admin, subject.id)
    rows = owner.execute(
        "SELECT stream, event_type, entity_id, relayed_at FROM customers_events.outbox"
        " WHERE entity_id = %s",
        (subject.id,),
    ).fetchall()
    assert rows == [(STREAM, "UPDATE", subject.id, None)], "one event, announced once"

    admin_redis = redis.Redis.from_url(url, decode_responses=True)
    relay = OutboxRelay(service_url, "customers", [STREAM], admin_redis)
    assert relay.run_once() >= 1
    entries = [
        fields
        for _, fields in admin_redis.xrange(STREAM)
        if fields.get("entity_id") == subject.id
    ]
    assert len(entries) == 1
    assert set(entries[0]) == {
        "event_id",
        "event_type",
        "entity_id",
        "occurred_at",
        "v",
    }
    assert entries[0]["v"] == "1" and entries[0]["event_type"] == "UPDATE"
    # Key-only: no identity travels on the stream.
    assert subject.name not in json.dumps(entries) and PLACEHOLDER not in json.dumps(
        entries
    )
    assert owner.execute(
        "SELECT relayed_at IS NOT NULL FROM customers_events.outbox"
        " WHERE entity_id = %s",
        (subject.id,),
    ).fetchone() == (True,)
    admin_redis.close()


def test_the_service_builds_its_relay_from_the_environment(monkeypatch):
    monkeypatch.setenv("REDIS_CUSTOMERS_PASSWORD", "secret")
    client = relay_redis("redis://redis-state:6379/0")
    kwargs = client.connection_pool.connection_kwargs
    assert (kwargs["username"], kwargs["password"]) == ("svc_customers", "secret")
