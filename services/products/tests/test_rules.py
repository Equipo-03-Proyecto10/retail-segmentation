"""The business rule against PostgreSQL: every bullet of the story."""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from lxml import etree

from app.service import _map_write_error

from .conftest import FIRST_TEST_ID, headers, product_body

pytestmark = pytest.mark.usefixtures("owner_url")


def create(client, **changes):
    return client.post("/v1/products", json=product_body(**changes), headers=headers())


def test_create_returns_201_location_and_the_product(client) -> None:
    response = create(client)
    assert response.status_code == 201
    assert response.headers["Location"] == f"/v1/products/{FIRST_TEST_ID + 1}"
    assert response.get_json() == {
        "product_id": FIRST_TEST_ID + 1,
        "sku": "TST-00001",
        "name": "Test product 1",
        "category_id": 1,
        "list_price": "12.50",
        "is_active": True,
        "image_path": None,
    }
    fetched = client.get(f"/v1/products/{FIRST_TEST_ID + 1}", headers=headers())
    assert fetched.status_code == 200
    assert fetched.get_json() == response.get_json()


def test_a_duplicate_sku_is_409_sku_taken(client) -> None:
    assert create(client).status_code == 201
    response = create(client, offset=2, sku="TST-00001")
    assert response.status_code == 409
    error = response.get_json()
    assert error["code"] == "sku_taken"
    assert error["details"] == [{"field": "sku", "code": "duplicate"}]


def test_a_duplicate_product_id_is_409_product_id_taken(client) -> None:
    assert create(client).status_code == 201
    response = create(client, sku="TST-OTHER")
    assert response.status_code == 409
    assert response.get_json()["code"] == "product_id_taken"


def test_an_unknown_category_is_422_unknown_category(client) -> None:
    response = create(client, category_id=32767)
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "unknown_category"
    assert error["details"] == [{"field": "category_id", "code": "unknown"}]


def test_a_negative_price_is_422_invalid_body_naming_list_price(client) -> None:
    response = create(client, list_price="-1.00")
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "invalid_body"
    assert {"field": "list_price", "code": "invalid_format"} in error["details"]
    assert (
        client.get(f"/v1/products/{FIRST_TEST_ID + 1}", headers=headers()).status_code
        == 404
    )


def test_the_check_constraint_maps_to_the_same_refusal(service_dsn) -> None:
    """The CHECK answers when a body somehow passes the schema."""

    with psycopg.connect(service_dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            connection.execute(
                "INSERT INTO public.product "
                "(product_id, sku, name, category_id, list_price) "
                "VALUES (%s, %s, %s, %s, %s)",
                (FIRST_TEST_ID + 9, "TST-NEG", "Negative", 1, Decimal("-1.00")),
            )
    error = _map_write_error(raised.value)
    assert error.status == 422
    assert error.code == "invalid_body"
    assert error.details == [{"field": "list_price", "code": "out_of_range"}]


def test_a_patch_can_deactivate_and_the_product_stays_readable(client) -> None:
    create(client)
    url = f"/v1/products/{FIRST_TEST_ID + 1}"
    patched = client.patch(url, json={"is_active": False}, headers=headers())
    assert patched.status_code == 200
    assert patched.get_json()["is_active"] is False
    fetched = client.get(url, headers=headers())
    assert fetched.status_code == 200
    assert fetched.get_json()["is_active"] is False
    listed = client.get("/v1/products?active=false&sku=TST-00001", headers=headers())
    assert [item["product_id"] for item in listed.get_json()["products"]] == [
        FIRST_TEST_ID + 1
    ]


def test_a_patch_edits_name_category_and_price(client) -> None:
    create(client)
    response = client.patch(
        f"/v1/products/{FIRST_TEST_ID + 1}",
        json={"name": "Renamed", "category_id": 2, "list_price": "0.00"},
        headers=headers(),
    )
    assert response.status_code == 200
    body = response.get_json()
    assert (body["name"], body["category_id"], body["list_price"]) == (
        "Renamed",
        2,
        "0.00",
    )


@pytest.mark.parametrize("field", ["sku", "image_path", "product_id"])
def test_a_patch_refuses_fields_the_service_does_not_own(client, field) -> None:
    create(client)
    response = client.patch(
        f"/v1/products/{FIRST_TEST_ID + 1}", json={field: "x"}, headers=headers()
    )
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_body"
    assert response.get_json()["details"][0]["field"] == field


def test_a_patch_to_an_unknown_category_is_422(client) -> None:
    create(client)
    response = client.patch(
        f"/v1/products/{FIRST_TEST_ID + 1}",
        json={"category_id": 32767},
        headers=headers(),
    )
    assert response.status_code == 422
    assert response.get_json()["code"] == "unknown_category"


def test_a_patch_of_a_missing_product_is_404(client) -> None:
    response = client.patch(
        f"/v1/products/{FIRST_TEST_ID + 5}", json={"name": "x"}, headers=headers()
    )
    assert response.status_code == 404
    assert response.get_json()["code"] == "product_not_found"


def test_there_is_no_delete_route_and_no_delete_privilege(client, owner) -> None:
    create(client)
    response = client.delete(f"/v1/products/{FIRST_TEST_ID + 1}", headers=headers())
    assert response.status_code == 405
    assert (
        client.get(f"/v1/products/{FIRST_TEST_ID + 1}", headers=headers()).status_code
        == 200
    )
    assert owner.execute(
        "SELECT has_table_privilege('svc_products', 'public.product', 'DELETE')"
    ).fetchone() == (False,)


def test_the_service_role_cannot_delete_even_directly(service_dsn) -> None:
    with psycopg.connect(service_dsn) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("DELETE FROM public.product WHERE product_id = %s", (1,))


def test_image_path_is_not_writable_by_the_service_role(service_dsn) -> None:
    with psycopg.connect(service_dsn) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "UPDATE public.product SET image_path = %s WHERE product_id = %s",
                ("x.png", 1),
            )


def test_a_missing_product_is_404(client) -> None:
    response = client.get(f"/v1/products/{FIRST_TEST_ID + 7}", headers=headers())
    assert response.status_code == 404
    assert response.get_json()["code"] == "product_not_found"


@pytest.mark.parametrize(
    ("role", "method", "expected"),
    [
        ("ANALYST", "post", 403),
        ("STORE_MANAGER", "post", 403),
        ("ANALYST", "patch", 403),
        ("ANALYST", "get", 200),
        ("AUDITOR", "get", 200),
        ("INVENTORY_PLANNER", "get", 200),
        ("CUSTOMER", "get", 403),
    ],
)
def test_roles_follow_the_contract(client, role, method, expected) -> None:
    if method == "post":
        response = client.post(
            "/v1/products", json=product_body(), headers=headers(role)
        )
    elif method == "patch":
        response = client.patch(
            "/v1/products/1", json={"name": "x"}, headers=headers(role)
        )
    else:
        response = client.get("/v1/products", headers=headers(role))
    assert response.status_code == expected
    if expected == 403:
        assert response.get_json()["code"] == "forbidden_role"


def test_the_audit_log_names_the_caller(client, admin_user, owner) -> None:
    create_response = client.post(
        "/v1/products", json=product_body(), headers=headers(sub=admin_user)
    )
    assert create_response.status_code == 201
    client.patch(
        f"/v1/products/{FIRST_TEST_ID + 1}",
        json={"name": "Audited"},
        headers=headers(sub=admin_user),
    )
    rows = owner.execute(
        "SELECT action, user_id::text FROM public.audit_log "
        "WHERE entity = %s AND entity_pk = %s ORDER BY audit_id",
        ("product", str(FIRST_TEST_ID + 1)),
    ).fetchall()
    assert rows == [("INSERT", admin_user), ("UPDATE", admin_user)]


def test_a_batch_of_fifty_ids_is_one_query_with_any(app, client, monkeypatch) -> None:
    for offset in range(1, 4):
        create(client, offset=offset, sku=f"TST-B{offset}")
    ids = [FIRST_TEST_ID + offset for offset in range(1, 4)] + list(range(1, 48))
    assert len(ids) == 50
    statements: list[str] = []
    original = psycopg.Connection.execute

    def spy(self, query, *args, **kwargs):
        text = query if isinstance(query, str) else query.as_string(self)
        statements.append(text)
        return original(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", spy)
    response = client.get(
        "/v1/products?size=100&ids=" + ",".join(map(str, ids)), headers=headers()
    )
    monkeypatch.undo()
    assert response.status_code == 200
    body = response.get_json()
    # The 40 seeded products and the 3 created ones; 7 ids are unknown.
    assert body["page_info"]["total"] == 43
    reads = [text for text in statements if "FROM public.product" in text]
    assert len(reads) == 1
    assert "product_id = ANY(" in reads[0]


def test_unknown_ids_are_omitted(client) -> None:
    create(client)
    response = client.get(
        f"/v1/products?ids={FIRST_TEST_ID + 1},{FIRST_TEST_ID + 99}", headers=headers()
    )
    assert [item["product_id"] for item in response.get_json()["products"]] == [
        FIRST_TEST_ID + 1
    ]


def test_fifty_one_ids_are_422_too_many_ids(client) -> None:
    response = client.get(
        "/v1/products?ids=" + ",".join(str(n) for n in range(1, 52)),
        headers=headers(),
    )
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "too_many_ids"
    assert error["details"] == [{"field": "ids", "code": "max_items"}]


@pytest.mark.parametrize(
    "query",
    ["ids=1,a", "ids=0", "ids=1,,2", "category_id=0", "active=maybe", "q="],
)
def test_malformed_filters_are_422_invalid_query(client, query) -> None:
    response = client.get("/v1/products?" + query, headers=headers())
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_query"


@pytest.mark.parametrize("query", ["q=%00", "q=a%00b", "sku=%00", "sku=TST%00"])
def test_a_nul_in_a_text_filter_is_422_invalid_query(client, query) -> None:
    # PostgreSQL text cannot hold U+0000; the filter never reaches it (#580).
    response = client.get("/v1/products?" + query, headers=headers())
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "invalid_query"
    assert error["details"] == [
        {"field": query.split("=")[0], "code": "invalid_format"}
    ]


# Refused by mosaiq-kit 0.1.3 for every Python service (#580, #581, #582).
KIT_0_1_3 = pytest.mark.xfail(
    reason="mosaiq-kit 0.1.3 answers these; the kit bump removes this mark",
    strict=True,
)


@KIT_0_1_3
def test_a_page_beyond_the_kit_bound_is_invalid_pagination(client) -> None:
    response = client.get("/v1/products?page=99999999999999999999", headers=headers())
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_pagination"


@KIT_0_1_3
@pytest.mark.parametrize("field,value", [("name", "a\x00b"), ("sku", "TST-\x00")])
def test_a_nul_in_a_body_field_is_422_invalid_body(client, field, value) -> None:
    created = create(client, **{field: value})
    assert created.status_code == 422
    assert created.get_json()["code"] == "invalid_body"
    assert create(client).status_code == 201
    updated = client.patch(
        f"/v1/products/{FIRST_TEST_ID + 1}",
        json={"name": "a\x00b"},
        headers=headers(),
    )
    assert updated.status_code == 422
    assert updated.get_json()["code"] == "invalid_body"


@KIT_0_1_3
def test_an_xml_comment_in_the_body_is_skipped(client) -> None:
    body = (
        '<CreateProductRequest xmlns="https://mosaiq.maxthecoder.online/xml/products/v1">'
        f"<!--c--><?pi x?><product_id>{FIRST_TEST_ID + 1}</product_id>"
        "<sku>TST-XML</sku><name>Xml<!--c--> product</name>"
        "<category_id>1</category_id><list_price>9.99</list_price>"
        "</CreateProductRequest>"
    )
    created = client.post(
        "/v1/products", data=body, content_type="application/xml", headers=headers()
    )
    assert created.status_code == 201, created.data
    assert created.get_json()["name"] == "Xml product"


def test_pagination_and_filters(client) -> None:
    for offset in range(1, 4):
        create(client, offset=offset, sku=f"TST-P{offset}", name=f"Pager {offset}")
    first = client.get("/v1/products?q=pager&size=2", headers=headers()).get_json()
    assert first["page_info"] == {"page": 1, "size": 2, "total": 3}
    assert len(first["products"]) == 2
    second = client.get("/v1/products?q=PAGER&size=2&page=2", headers=headers())
    assert len(second.get_json()["products"]) == 1
    beyond = client.get("/v1/products?q=pager&size=2&page=9", headers=headers())
    assert beyond.get_json()["products"] == []
    assert beyond.get_json()["page_info"]["total"] == 3
    bad = client.get("/v1/products?page=0", headers=headers())
    assert bad.get_json()["code"] == "invalid_pagination"
    by_sku = client.get("/v1/products?sku=TST-P2", headers=headers()).get_json()
    assert [item["name"] for item in by_sku["products"]] == ["Pager 2"]


def test_xml_round_trip_validates_against_the_xsd(client, xsd) -> None:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CreateProductRequest xmlns="https://mosaiq.maxthecoder.online/xml/products/v1">'
        f"<product_id>{FIRST_TEST_ID + 1}</product_id><sku>TST-XML</sku>"
        "<name>Xml product</name><category_id>1</category_id>"
        "<list_price>9.99</list_price></CreateProductRequest>"
    )
    created = client.post(
        "/v1/products",
        data=body,
        content_type="application/xml",
        headers=headers(Accept="application/xml"),
    )
    assert created.status_code == 201
    xsd.assertValid(etree.fromstring(created.data))
    listed = client.get(
        "/v1/products?sku=TST-XML", headers=headers(Accept="application/xml")
    )
    assert listed.status_code == 200
    xsd.assertValid(etree.fromstring(listed.data))
    missing = client.get(
        f"/v1/products/{FIRST_TEST_ID + 8}", headers=headers(Accept="application/xml")
    )
    assert missing.status_code == 404
    assert (
        client.get("/v1/products", headers=headers(Accept="text/html")).status_code
        == 406
    )


def test_the_outbox_captures_every_writer(client, owner, owner_url) -> None:
    def pending() -> list[tuple[str, str]]:
        return owner.execute(
            "SELECT event_type, entity_id FROM products_events.outbox "
            "WHERE entity_id = %s ORDER BY occurred_at, event_id",
            (str(FIRST_TEST_ID + 1),),
        ).fetchall()

    create(client)
    # The frozen web writes as retail_app and is captured the same way.
    with psycopg.connect(
        owner.info.dsn.replace("password=", "password="), autocommit=True
    ) as web:
        web.execute("SET ROLE retail_app")
        web.execute(
            "UPDATE public.product SET name = %s WHERE product_id = %s",
            ("Web edit", FIRST_TEST_ID + 1),
        )
    owner.execute(
        "DELETE FROM public.product WHERE product_id = %s", (FIRST_TEST_ID + 1,)
    )
    assert [event for event, _ in pending()] == ["INSERT", "UPDATE", "DELETE"]
    streams = owner.execute(
        "SELECT DISTINCT stream FROM products_events.outbox WHERE entity_id = %s",
        (str(FIRST_TEST_ID + 1),),
    ).fetchall()
    assert streams == [("stream:products.v1.changed",)]


def test_a_rolled_back_write_publishes_nothing(owner_url) -> None:
    with psycopg.connect(owner_url) as connection:
        connection.execute(
            "INSERT INTO public.product "
            "(product_id, sku, name, category_id, list_price) "
            "VALUES (%s, %s, %s, %s, %s)",
            (FIRST_TEST_ID + 3, "TST-RB", "Rolled back", 1, 1),
        )
        connection.rollback()
    with psycopg.connect(owner_url) as connection:
        count = connection.execute(
            "SELECT count(*) FROM products_events.outbox WHERE entity_id = %s",
            (str(FIRST_TEST_ID + 3),),
        ).fetchone()
    assert count == (0,)


def test_consumers_read_the_view_and_not_the_table(owner) -> None:
    for role in ("svc_sales", "svc_recommendations", "svc_engagement"):
        assert owner.execute(
            "SELECT has_table_privilege(%s, 'api_products.product_ref_v1', 'SELECT'), "
            "has_table_privilege(%s, 'public.product', 'SELECT')",
            (role, role),
        ).fetchone() == (True, False)
    columns = [
        row[0]
        for row in owner.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            ("api_products", "product_ref_v1"),
        )
    ]
    assert columns == [
        "product_id",
        "sku",
        "name",
        "category_id",
        "list_price",
        "is_active",
    ]


def test_the_capture_function_is_a_pinned_definer_nobody_can_call(owner) -> None:
    row = owner.execute(
        "SELECT pg_get_userbyid(proowner), prosecdef, proconfig, "
        "has_function_privilege('public', oid, 'EXECUTE') "
        "FROM pg_proc WHERE oid = 'products_events.capture_changed()'::regprocedure"
    ).fetchone()
    assert row[0] == "definer_products"
    assert row[1] is True
    assert any(setting.startswith("search_path=") for setting in row[2])
    assert row[3] is False


def test_every_statement_is_parameterized() -> None:
    """No f-string, concatenation or % formatting reaches an execute call."""

    for path in Path(__file__).resolve().parents[1].joinpath("app").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                text = "".join(
                    part.value for part in node.values if isinstance(part, ast.Constant)
                ).upper()
                assert not any(
                    word in text
                    for word in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")
                ), f"{path.name}:{node.lineno} interpolates into SQL"
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "execute"
            ):
                first = node.args[0]
                assert not isinstance(
                    first, ast.JoinedStr | ast.BinOp
                ), f"{path.name}:{node.lineno} builds SQL by string operations"


def test_a_price_with_a_final_newline_is_refused_on_create_and_patch(client) -> None:
    """#594: Python's `$` matches before a final newline; the contract's
    ListPrice pattern means the whole value."""
    response = create(client, list_price="1.00\n")
    assert response.status_code == 422
    assert response.get_json()["details"] == [
        {"field": "list_price", "code": "invalid_format"}
    ]
    create(client)
    response = client.patch(
        f"/v1/products/{FIRST_TEST_ID + 1}",
        json={"list_price": "2.00\n"},
        headers=headers(),
    )
    assert response.status_code == 422
    fetched = client.get(f"/v1/products/{FIRST_TEST_ID + 1}", headers=headers())
    assert fetched.get_json()["list_price"] == "12.50"
