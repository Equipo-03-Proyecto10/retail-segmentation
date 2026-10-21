"""The rule of RN-08, against PostgreSQL through the real service."""

from __future__ import annotations

import threading
from datetime import date, timedelta

import psycopg
import pytest

from app.service import like_pattern, refusal

pytestmark = pytest.mark.postgres

CHANNEL = 3


def body(unique: str, **fields):
    data = {
        "name": unique,
        "email": unique + "@example.com",
        "phone": "+52-55-0000-0000",
        "registration_channel_id": CHANNEL,
    }
    data.update(fields)
    return data


def register(client, headers, payload):
    return client.post("/v1/customers", json=payload, headers=headers)


def count(owner, unique: str) -> int:
    return owner.execute(
        "SELECT count(*) FROM customer WHERE name = %s", (unique,)
    ).fetchone()[0]


def test_registration_stores_the_current_date_by_default(
    client, manager, owner, unique
):
    response = register(client, manager, body(unique))
    assert response.status_code == 201
    created = response.get_json()
    assert response.headers["Location"] == "/v1/customers/" + created["customer_id"]
    today = owner.execute("SELECT CURRENT_DATE").fetchone()[0]
    assert created["registered_on"] == today.isoformat()
    assert (
        client.get(response.headers["Location"], headers=manager).get_json() == created
    )


def test_a_second_customer_with_the_same_email_gets_409(client, manager, unique):
    assert register(client, manager, body(unique)).status_code == 201
    other = body(unique + "-b", email=unique + "@example.com")
    response = register(client, manager, other)
    assert response.status_code == 409
    assert response.get_json()["code"] == "email_taken"
    assert response.get_json()["details"] == [{"field": "email", "code": "not_unique"}]


def test_two_concurrent_registrations_with_one_email_give_one_201_one_409(
    app, manager, owner, unique
):
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def attempt(suffix: str) -> None:
        local = app.test_client()
        barrier.wait()
        response = register(
            local, manager, body(unique + suffix, email=unique + "@x.com")
        )
        statuses.append(response.status_code)

    threads = [threading.Thread(target=attempt, args=(s,)) for s in ("-a", "-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [201, 409]
    rows = owner.execute(
        "SELECT count(*) FROM customer WHERE email = %s", (unique + "@x.com",)
    ).fetchone()[0]
    assert rows == 1


def test_a_channel_that_does_not_exist_gets_422(client, manager, owner, unique):
    response = register(client, manager, body(unique, registration_channel_id=32000))
    assert response.status_code == 422
    assert response.get_json()["code"] == "unknown_channel"
    assert count(owner, unique) == 0


def test_a_future_registration_date_gets_422_and_writes_nothing(
    client, manager, owner, unique
):
    tomorrow = date.today() + timedelta(days=2)
    response = register(
        client, manager, body(unique, registered_on=tomorrow.isoformat())
    )
    assert response.status_code == 422
    assert response.get_json()["code"] == "future_registration"
    assert response.get_json()["details"] == [
        {"field": "registered_on", "code": "future_date"}
    ]
    assert count(owner, unique) == 0


def test_an_explicit_past_date_is_kept(client, manager, unique):
    response = register(client, manager, body(unique, registered_on="2020-02-29"))
    assert response.status_code == 201
    assert response.get_json()["registered_on"] == "2020-02-29"


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "a b@c.com", "a@@c.com"])
def test_an_email_breaking_the_check_gets_422_naming_email(
    client, manager, owner, unique, email
):
    response = register(client, manager, body(unique, email=email))
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_body"
    assert any(d["field"] == "email" for d in response.get_json()["details"])
    assert count(owner, unique) == 0


def test_the_check_violation_is_mapped_by_constraint_name(service_url, unique):
    with psycopg.connect(service_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            connection.execute(
                "INSERT INTO customer (name, email, registration_channel_id)"
                " VALUES (%s, %s, %s)",
                (unique, "not-an-email", CHANNEL),
            )
    mapped = refusal(raised.value)
    assert mapped is not None
    assert (mapped.status, mapped.code) == (422, "invalid_body")
    assert mapped.details[0]["field"] == "email"


def test_an_email_may_be_omitted_or_null(client, manager, unique):
    payload = body(unique, email=None, phone=None)
    response = register(client, manager, payload)
    assert response.status_code == 201
    assert response.get_json()["email"] is None
    assert response.get_json()["phone"] is None


@pytest.mark.parametrize("field", ["registered_on", "customer_id"])
def test_a_patch_naming_a_fixed_field_gets_422(client, manager, unique, field):
    created = register(client, manager, body(unique)).get_json()
    value = "2020-01-01" if field == "registered_on" else created["customer_id"]
    response = client.patch(
        "/v1/customers/" + created["customer_id"], json={field: value}, headers=manager
    )
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_body"
    assert any(d["field"] == field for d in response.get_json()["details"])
    after = client.get("/v1/customers/" + created["customer_id"], headers=manager)
    assert after.get_json() == created


def test_a_patch_corrects_only_the_fields_it_names(client, manager, unique):
    created = register(client, manager, body(unique)).get_json()
    url = "/v1/customers/" + created["customer_id"]
    response = client.patch(
        url, json={"phone": None, "name": unique + "-fixed"}, headers=manager
    )
    assert response.status_code == 200
    corrected = response.get_json()
    assert corrected["phone"] is None
    assert corrected["name"] == unique + "-fixed"
    assert corrected["email"] == created["email"]
    assert corrected["registered_on"] == created["registered_on"]


def test_a_patch_to_a_taken_email_or_unknown_channel_is_refused(
    client, manager, unique
):
    first = register(client, manager, body(unique)).get_json()
    second = register(client, manager, body(unique + "-2")).get_json()
    url = "/v1/customers/" + second["customer_id"]
    taken = client.patch(url, json={"email": first["email"]}, headers=manager)
    assert (taken.status_code, taken.get_json()["code"]) == (409, "email_taken")
    channel = client.patch(
        url, json={"registration_channel_id": 32000}, headers=manager
    )
    assert (channel.status_code, channel.get_json()["code"]) == (422, "unknown_channel")


def test_a_patch_with_a_bad_email_gets_422(client, manager, unique):
    created = register(client, manager, body(unique)).get_json()
    response = client.patch(
        "/v1/customers/" + created["customer_id"],
        json={"email": "nope"},
        headers=manager,
    )
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_body"


def test_an_empty_patch_gets_422(client, manager, unique):
    created = register(client, manager, body(unique)).get_json()
    response = client.patch(
        "/v1/customers/" + created["customer_id"], json={}, headers=manager
    )
    assert response.status_code == 422


def test_there_is_no_delete_route(client, manager, owner, unique):
    created = register(client, manager, body(unique)).get_json()
    response = client.delete("/v1/customers/" + created["customer_id"], headers=manager)
    assert response.status_code == 405
    assert count(owner, unique) == 1


def test_the_role_holds_no_delete_privilege(owner):
    row = owner.execute(
        "SELECT has_table_privilege('svc_customers', 'public.customer', 'DELETE'),"
        " has_table_privilege('svc_customers', 'public.customer', 'TRUNCATE')"
    ).fetchone()
    assert row == (False, False)


def test_the_role_cannot_change_registered_on_or_delete(service_url, unique):
    with psycopg.connect(service_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("UPDATE customer SET registered_on = CURRENT_DATE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("DELETE FROM customer WHERE name = %s", (unique,))


def test_an_unknown_id_gets_404(client, manager):
    missing = "00000000-0000-4000-8000-000000000000"
    for response in (
        client.get("/v1/customers/" + missing, headers=manager),
        client.patch("/v1/customers/" + missing, json={"name": "x"}, headers=manager),
    ):
        assert response.status_code == 404
        assert response.get_json()["code"] == "customer_not_found"


# Search --------------------------------------------------------------------


@pytest.fixture
def directory(client, manager, unique):
    names = {
        "percent": unique + " 50% off",
        "plain": unique + " 500 off",
        "under": unique + " a_b",
        "other": unique + " axb",
        "upper": unique + " MIXEDcase",
    }
    for index, name in enumerate(names.values()):
        payload = body(name, email=f"{unique}-{index}@example.com", phone=f"900{index}")
        assert register(client, manager, payload).status_code == 201
    return unique, names


def found(client, manager, **query) -> set[str]:
    response = client.get("/v1/customers", query_string=query, headers=manager)
    assert response.status_code == 200
    return {c["name"] for c in response.get_json()["customers"]}


def test_percent_in_q_is_literal(client, manager, directory):
    unique, names = directory
    assert found(client, manager, q="50%") >= {names["percent"]}
    assert names["plain"] not in found(client, manager, q="50%")
    assert found(client, manager, q=unique + " 50%") == {names["percent"]}


def test_underscore_in_q_is_literal(client, manager, directory):
    _, names = directory
    result = found(client, manager, q="a_b")
    assert names["under"] in result
    assert names["other"] not in result


def test_a_backslash_in_q_is_literal(client, manager, directory):
    assert found(client, manager, q="\\") == set() or all(
        "\\" in name for name in found(client, manager, q="\\")
    )


def test_matching_is_case_insensitive_over_name_email_and_phone(
    client, manager, directory
):
    unique, names = directory
    assert found(client, manager, q="mixedCASE") >= {names["upper"]}
    assert found(client, manager, q=unique.upper() + "-2@EXAMPLE") == {names["under"]}
    assert found(client, manager, q="9003") == {names["other"]}


def test_the_email_filter_is_exact_and_combines_with_q(client, manager, directory):
    unique, names = directory
    exact = unique + "-0@example.com"
    assert found(client, manager, email=exact) == {names["percent"]}
    assert found(client, manager, email=exact, q="a_b") == set()
    assert found(client, manager, email=unique + "-0") == set()


def test_search_pages_and_counts(client, manager, directory):
    unique, _ = directory
    response = client.get(
        "/v1/customers",
        query_string={"q": unique, "size": 2, "page": 3},
        headers=manager,
    )
    data = response.get_json()
    assert data["page_info"] == {"page": 3, "size": 2, "total": 5}
    assert len(data["customers"]) == 1
    bad = client.get("/v1/customers", query_string={"size": 0}, headers=manager)
    assert (bad.status_code, bad.get_json()["code"]) == (422, "invalid_pagination")


@pytest.mark.parametrize(
    "query", [{"q": "\x00"}, {"q": "a\x00b"}, {"email": "a\x00@example.com"}]
)
def test_a_nul_in_a_text_filter_is_refused(client, manager, query):
    # PostgreSQL text cannot hold U+0000; the filter never reaches it (#580).
    response = client.get("/v1/customers", query_string=query, headers=manager)
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "invalid_pagination"
    assert error["details"] == [{"field": next(iter(query)), "code": "invalid_format"}]


# Refused by mosaiq-kit 0.1.3 for every Python service (#580, #581, #582).
KIT_0_1_3 = pytest.mark.xfail(
    reason="mosaiq-kit 0.1.3 answers these; the kit bump removes this mark",
    strict=True,
)


@KIT_0_1_3
def test_a_page_beyond_the_kit_bound_is_invalid_pagination(client, manager):
    response = client.get(
        "/v1/customers", query_string={"page": "9" * 20}, headers=manager
    )
    assert (response.status_code, response.get_json()["code"]) == (
        422,
        "invalid_pagination",
    )


@KIT_0_1_3
@pytest.mark.parametrize(
    "field,value",
    [("name", "a\x00b"), ("email", "a\x00b@example.com"), ("phone", "55\x00")],
)
def test_a_nul_in_a_body_field_is_invalid_body(client, manager, unique, field, value):
    response = register(client, manager, body(unique, **{field: value}))
    assert (response.status_code, response.get_json()["code"]) == (422, "invalid_body")


@KIT_0_1_3
def test_a_nul_in_a_correction_is_invalid_body(client, manager, unique):
    created = register(client, manager, body(unique)).get_json()
    response = client.patch(
        "/v1/customers/" + created["customer_id"],
        json={"name": "a\x00b"},
        headers=manager,
    )
    assert (response.status_code, response.get_json()["code"]) == (422, "invalid_body")


@KIT_0_1_3
def test_an_xml_comment_in_the_body_is_skipped(client, manager, unique):
    created = register(client, manager, body(unique)).get_json()
    response = client.patch(
        "/v1/customers/" + created["customer_id"],
        data=(
            '<CorrectCustomerRequest xmlns="https://mosaiq.maxthecoder.online/xml/'
            f'customers/v1"><!--c--><?pi x?><name>{unique}<!--c-->-x</name>'
            "</CorrectCustomerRequest>"
        ),
        headers={**manager, "Content-Type": "application/xml"},
    )
    assert response.status_code == 200, response.data
    assert response.get_json()["name"] == unique + "-x"


def test_like_pattern_escapes_the_wildcards():
    assert like_pattern("50%") == "%50\\%%"
    assert like_pattern("a_b") == "%a\\_b%"
    assert like_pattern("a\\b") == "%a\\\\b%"


# Audit and published view ----------------------------------------------------


def test_a_write_is_audited_with_the_callers_identity(
    client, manager, owner, actor, unique
):
    created = register(client, manager, body(unique)).get_json()
    client.patch(
        "/v1/customers/" + created["customer_id"], json={"phone": "1"}, headers=manager
    )
    rows = owner.execute(
        "SELECT action, user_id::text FROM audit_log"
        " WHERE entity = 'customer' AND entity_pk = %s ORDER BY audit_id",
        (created["customer_id"],),
    ).fetchall()
    assert rows == [("INSERT", actor), ("UPDATE", actor)]


def test_a_read_leaves_no_actor_on_the_connection(client, manager, owner, unique):
    created = register(client, manager, body(unique)).get_json()
    before = owner.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    client.get("/v1/customers/" + created["customer_id"], headers=manager)
    assert owner.execute("SELECT count(*) FROM audit_log").fetchone()[0] == before


CONSUMERS = (
    "svc_sales",
    "svc_preferences",
    "svc_segments",
    "svc_recommendations",
    "svc_engagement",
)


@pytest.mark.parametrize("role", ("svc_customers",) + CONSUMERS)
def test_the_view_is_readable_and_the_table_is_not_for_consumers(owner_url, role):
    with psycopg.connect(owner_url) as connection:
        with connection.transaction():
            connection.execute(
                psycopg.sql.SQL("SET LOCAL ROLE {}").format(
                    psycopg.sql.Identifier(role)
                )
            )
            rows = connection.execute(
                "SELECT customer_id, registered_on FROM api_customers.customer_ref_v1"
            ).fetchall()
            assert len(rows) >= 30
            if role != "svc_customers":
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute("SELECT name FROM public.customer")


def test_the_view_runs_as_its_owner_and_exposes_two_columns(owner):
    row = owner.execute(
        "SELECT reloptions, (SELECT array_agg(attname::text ORDER BY attnum)"
        " FROM pg_attribute WHERE attrelid = c.oid AND attnum > 0)"
        " FROM pg_class c WHERE oid = 'api_customers.customer_ref_v1'::regclass"
    ).fetchone()
    assert row[0] == ["security_invoker=off"]
    assert row[1] == ["customer_id", "registered_on"]


def test_the_boundary_has_no_outbox_and_no_capture_trigger(owner):
    schemas = owner.execute(
        "SELECT count(*) FROM pg_namespace WHERE nspname = 'customers_events'"
    ).fetchone()[0]
    triggers = owner.execute(
        "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'public.customer'::regclass"
        " AND NOT tgisinternal AND tgname <> 'trg_audit_customer'"
    ).fetchone()[0]
    assert (schemas, triggers) == (0, 0)
