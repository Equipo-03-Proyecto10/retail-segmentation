"""Commercial status, attributes, photo, barcode and relations (F21-10)."""

from __future__ import annotations

import random

import psycopg
import pytest
from lxml import etree

from app import gtin

from .conftest import FIRST_TEST_ID, headers, product_body

pytestmark = pytest.mark.usefixtures("owner_url")

NAMESPACE = "https://mosaiq.maxthecoder.online/xml/products/v1"
PHOTO_BASE = "https://shop.example/photos/"


def pid(offset: int) -> int:
    return FIRST_TEST_ID + offset


def create(client, offset: int = 1) -> int:
    response = client.post(
        "/v1/products",
        json=product_body(offset, sku=f"TST-D{offset:04d}"),
        headers=headers(),
    )
    assert response.status_code == 201, response.data
    return pid(offset)


def details_url(offset_or_id: int) -> str:
    return f"/v1/products/{offset_or_id}/details"


def gtin13(serial: int) -> str:
    body = f"7509{serial:08d}"
    return body + str(gtin.check_digit(body))


def patch_details(client, product_id: int, body: dict, **extra):
    return client.patch(details_url(product_id), json=body, headers=headers(**extra))


def get_details(client, product_id: int, **extra):
    return client.get(details_url(product_id), headers=headers(**extra))


def put_relations(client, product_id: int, kind: str, ids: list[int], **extra):
    return client.put(
        f"/v1/products/{product_id}/relations/{kind}",
        json={"related_product_ids": ids},
        headers=headers(**extra),
    )


def get_relations(client, product_id: int, query: str = ""):
    return client.get(f"/v1/products/{product_id}/relations{query}", headers=headers())


def as_web(owner_url: str, statement: str, params: tuple) -> None:
    """A write by the frozen web, which connects as retail_app."""

    with psycopg.connect(owner_url, autocommit=True) as connection:
        connection.execute("SET ROLE retail_app")
        connection.execute(statement, params)


# -- reading ------------------------------------------------------------------


def test_a_seeded_product_has_status_attributes_and_barcode(client) -> None:
    response = get_details(client, 1)
    assert response.status_code == 200
    body = response.get_json()
    assert list(body) == [
        "product_id",
        "sku",
        "name",
        "category_id",
        "list_price",
        "is_active",
        "commercial_status",
        "attributes",
        "photo_url",
        "barcode",
    ]
    assert body["commercial_status"] == "active"
    assert body["is_active"] is True
    assert [item["name"] for item in body["attributes"]] == [
        "brand",
        "color",
        "pack_size",
    ]
    assert body["barcode"] == "7501000000012"
    assert body["photo_url"] is None


def test_a_product_with_no_details_reads_from_is_active(client) -> None:
    product_id = create(client)
    body = get_details(client, product_id).get_json()
    assert body["commercial_status"] == "active"
    assert (body["attributes"], body["barcode"], body["photo_url"]) == ([], None, None)
    client.patch(
        f"/v1/products/{product_id}", json={"is_active": False}, headers=headers()
    )
    assert get_details(client, product_id).get_json()["commercial_status"] == (
        "discontinued"
    )


def test_the_photo_url_is_the_configured_base_and_the_web_path(
    build_app, owner
) -> None:
    client = build_app(PRODUCTS_PHOTO_BASE_URL=PHOTO_BASE).test_client()
    product_id = create(client)
    owner.execute(
        "UPDATE public.product SET image_path = %s WHERE product_id = %s",
        ("a b/1001-camera.webp", product_id),
    )
    body = get_details(client, product_id).get_json()
    assert body["photo_url"] == PHOTO_BASE + "a%20b/1001-camera.webp"


def test_the_base_url_must_be_absolute(build_app) -> None:
    with pytest.raises(ValueError, match="PRODUCTS_PHOTO_BASE_URL"):
        build_app(PRODUCTS_PHOTO_BASE_URL="/relative/")


def test_a_missing_product_is_404_on_every_new_operation(client) -> None:
    missing = pid(77)
    for response in (
        get_details(client, missing),
        patch_details(client, missing, {"commercial_status": "paused"}),
        get_relations(client, missing),
        put_relations(client, missing, "substitute", [1]),
    ):
        assert response.status_code == 404
        assert response.get_json()["code"] == "product_not_found"


def test_the_details_xml_is_valid_against_the_xsd(client, xsd) -> None:
    response = client.get(details_url(1), headers=headers(Accept="application/xml"))
    assert response.status_code == 200
    root = etree.fromstring(response.data)
    xsd.assertValid(root)
    q = etree.QName
    assert [q(child).localname for child in root][:8] == [
        "product_id",
        "sku",
        "name",
        "category_id",
        "list_price",
        "is_active",
        "commercial_status",
        "attributes",
    ]
    assert [
        child.findtext(f"{{{NAMESPACE}}}name")
        for child in root.findall(f"{{{NAMESPACE}}}attributes")
    ] == ["brand", "color", "pack_size"]
    assert root.findtext(f"{{{NAMESPACE}}}barcode") == "7501000000012"
    # No photo: the element is omitted, not empty.
    assert root.find(f"{{{NAMESPACE}}}photo_url") is None


# -- roles --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "operation", "expected"),
    [
        ("ANALYST", "get_details", 200),
        ("AUDITOR", "get_details", 200),
        ("INVENTORY_PLANNER", "get_relations", 200),
        ("STORE_MANAGER", "get_relations", 200),
        ("MARKETING", "get_details", 200),
        ("SALES_ASSOCIATE", "get_details", 200),
        ("LOYALTY_OFFICER", "get_relations", 200),
        ("COMMERCIAL_SUPERVISOR", "get_details", 200),
        ("CUSTOMER", "get_details", 403),
        ("ANALYST", "patch_details", 403),
        ("STORE_MANAGER", "patch_details", 403),
        ("ANALYST", "put_relations", 403),
        ("MARKETING", "put_relations", 403),
    ],
)
def test_reads_follow_the_nine_roles_and_writes_are_admin_only(
    client, role, operation, expected
) -> None:
    if operation == "get_details":
        response = client.get(details_url(1), headers=headers(role))
    elif operation == "get_relations":
        response = client.get("/v1/products/1/relations", headers=headers(role))
    elif operation == "patch_details":
        response = client.patch(
            details_url(1), json={"commercial_status": "paused"}, headers=headers(role)
        )
    else:
        response = client.put(
            "/v1/products/1/relations/substitute",
            json={"related_product_ids": []},
            headers=headers(role),
        )
    assert response.status_code == expected
    if expected == 403:
        assert response.get_json()["code"] == "forbidden_role"


# -- commercial status and is_active ------------------------------------------


@pytest.mark.parametrize("status", ["paused", "discontinued"])
def test_a_status_off_sale_clears_is_active_everywhere_it_is_read(
    client, owner, status
) -> None:
    product_id = create(client)
    response = patch_details(client, product_id, {"commercial_status": status})
    assert response.status_code == 200
    body = response.get_json()
    assert (body["commercial_status"], body["is_active"]) == (status, False)
    assert (
        client.get(f"/v1/products/{product_id}", headers=headers()).get_json()[
            "is_active"
        ]
        is False
    )
    # What sales reads (RN-37 refuses a product that is not active).
    owner.execute("SET ROLE svc_sales")
    row = owner.execute(
        "SELECT is_active FROM api_products.product_ref_v1 WHERE product_id = %s",
        (product_id,),
    ).fetchone()
    owner.execute("RESET ROLE")
    assert row == (False,)
    listed = client.get(
        "/v1/products?active=false&sku=TST-D0001", headers=headers()
    ).get_json()
    assert [item["product_id"] for item in listed["products"]] == [product_id]


def test_active_puts_the_product_back_on_sale(client) -> None:
    product_id = create(client)
    patch_details(client, product_id, {"commercial_status": "discontinued"})
    body = patch_details(client, product_id, {"commercial_status": "active"}).get_json()
    assert (body["commercial_status"], body["is_active"]) == ("active", True)
    assert (
        client.get(f"/v1/products/{product_id}", headers=headers()).get_json()[
            "is_active"
        ]
        is True
    )


def test_patch_product_is_active_moves_the_status(client) -> None:
    product_id = create(client)
    url = f"/v1/products/{product_id}"
    client.patch(url, json={"is_active": False}, headers=headers())
    assert get_details(client, product_id).get_json()["commercial_status"] == (
        "discontinued"
    )
    client.patch(url, json={"is_active": True}, headers=headers())
    assert get_details(client, product_id).get_json()["commercial_status"] == "active"


def test_patch_product_is_active_leaves_a_paused_product_paused(client) -> None:
    product_id = create(client)
    patch_details(client, product_id, {"commercial_status": "paused"})
    client.patch(
        f"/v1/products/{product_id}", json={"is_active": False}, headers=headers()
    )
    body = get_details(client, product_id).get_json()
    assert (body["commercial_status"], body["is_active"]) == ("paused", False)
    client.patch(
        f"/v1/products/{product_id}", json={"is_active": True}, headers=headers()
    )
    body = get_details(client, product_id).get_json()
    assert (body["commercial_status"], body["is_active"]) == ("active", True)


def test_a_web_edit_of_is_active_moves_the_status_too(client, owner_url) -> None:
    product_id = create(client)
    as_web(
        owner_url,
        "UPDATE public.product SET is_active = false WHERE product_id = %s",
        (product_id,),
    )
    body = get_details(client, product_id).get_json()
    assert (body["commercial_status"], body["is_active"]) == ("discontinued", False)
    as_web(
        owner_url,
        "UPDATE public.product SET is_active = true WHERE product_id = %s",
        (product_id,),
    )
    assert get_details(client, product_id).get_json()["commercial_status"] == "active"


def test_a_direct_write_of_the_status_moves_is_active_and_a_bad_one_is_refused(
    client, service_dsn
) -> None:
    """Even a direct write by the service role cannot leave them apart."""

    product_id = create(client)
    with psycopg.connect(service_dsn, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO products.product_status VALUES (%s, %s)",
            (product_id, "paused"),
        )
        row = connection.execute(
            "SELECT is_active FROM public.product WHERE product_id = %s", (product_id,)
        ).fetchone()
        assert row == (False,)
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE products.product_status SET commercial_status = %s "
                "WHERE product_id = %s",
                ("retired", product_id),
            )


@pytest.mark.parametrize("field", ["photo_url", "image_path", "product_id", "sku"])
def test_the_photo_and_other_fields_are_refused_in_a_patch(client, field) -> None:
    product_id = create(client)
    response = patch_details(client, product_id, {field: "https://x/y.png"})
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "invalid_body"
    assert error["details"][0]["field"] == field


def test_an_empty_patch_and_an_unknown_status_are_invalid_body(client) -> None:
    product_id = create(client)
    for body in ({}, {"commercial_status": "retired"}, {"commercial_status": None}):
        response = patch_details(client, product_id, body)
        assert response.status_code == 422, body
        assert response.get_json()["code"] == "invalid_body"


# -- attributes ---------------------------------------------------------------


def test_attributes_are_replaced_as_a_set(client) -> None:
    product_id = create(client)
    first = patch_details(
        client,
        product_id,
        {
            "attributes": [
                {"name": "color", "value": "red"},
                {"name": "size", "value": "L"},
            ]
        },
    )
    assert first.status_code == 200
    assert first.get_json()["attributes"] == [
        {"name": "color", "value": "red"},
        {"name": "size", "value": "L"},
    ]
    second = patch_details(
        client,
        product_id,
        {
            "attributes": [
                {"name": "color", "value": "blue"},
                {"name": "weight", "value": "2 kg"},
            ]
        },
    )
    assert second.get_json()["attributes"] == [
        {"name": "color", "value": "blue"},
        {"name": "weight", "value": "2 kg"},
    ]


def test_replacing_attributes_writes_and_audits_only_the_rows_that_differ(
    client, owner
) -> None:
    product_id = create(client)
    body = {
        "attributes": [
            {"name": "color", "value": "red"},
            {"name": "size", "value": "L"},
        ]
    }
    patch_details(client, product_id, body)
    patch_details(client, product_id, body)
    patch_details(
        client,
        product_id,
        {
            "attributes": [
                {"name": "color", "value": "red"},
                {"name": "size", "value": "M"},
            ]
        },
    )
    rows = owner.execute(
        "SELECT action, data_after ->> 'name' FROM public.audit_log "
        "WHERE entity = %s AND entity_pk = %s ORDER BY audit_id",
        ("product_attribute", str(product_id)),
    ).fetchall()
    assert rows == [("INSERT", "color"), ("INSERT", "size"), ("UPDATE", "size")]


@pytest.mark.parametrize(
    "attributes",
    [
        [],
        [{"name": "Color", "value": "red"}],
        [{"name": "color", "value": ""}],
        [{"name": "color", "value": "x" * 121}],
        [{"name": f"n{index}", "value": "v"} for index in range(21)],
        [{"name": "color", "value": "red", "extra": "x"}],
    ],
)
def test_attributes_outside_the_schema_are_invalid_body(client, attributes) -> None:
    product_id = create(client)
    response = patch_details(client, product_id, {"attributes": attributes})
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_body"


def test_two_attributes_of_one_name_are_invalid_body(client) -> None:
    product_id = create(client)
    response = patch_details(
        client,
        product_id,
        {
            "attributes": [
                {"name": "color", "value": "red"},
                {"name": "color", "value": "blue"},
            ]
        },
    )
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "invalid_body"
    assert error["details"] == [{"field": "attributes", "code": "duplicate"}]


def test_twenty_attributes_are_accepted(client) -> None:
    product_id = create(client)
    attributes = [{"name": f"n{index:02d}", "value": "v"} for index in range(20)]
    response = patch_details(client, product_id, {"attributes": attributes})
    assert response.status_code == 200
    assert len(response.get_json()["attributes"]) == 20


# -- barcode ------------------------------------------------------------------


def test_a_barcode_is_set_replaced_and_never_cleared(client) -> None:
    product_id = create(client)
    assert (
        patch_details(client, product_id, {"barcode": "036000291452"}).get_json()[
            "barcode"
        ]
        == "036000291452"
    )
    assert patch_details(client, product_id, {"barcode": gtin13(1)}).get_json()[
        "barcode"
    ] == gtin13(1)
    cleared = patch_details(client, product_id, {"barcode": None})
    assert cleared.status_code == 422
    assert cleared.get_json()["code"] == "invalid_body"
    assert get_details(client, product_id).get_json()["barcode"] == gtin13(1)


@pytest.mark.parametrize("code", ["4006381333932", "036000291453", "96385075"])
def test_a_wrong_check_digit_is_422_invalid_barcode(client, code) -> None:
    product_id = create(client)
    response = patch_details(client, product_id, {"barcode": code})
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "invalid_barcode"
    assert error["details"] == [{"field": "barcode", "code": "check_digit"}]


@pytest.mark.parametrize(
    "code", ["1234567", "12345678901", "abcdefgh", "4006381333931\n"]
)
def test_a_barcode_outside_the_pattern_is_invalid_body(client, code) -> None:
    product_id = create(client)
    response = patch_details(client, product_id, {"barcode": code})
    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_body"


def test_a_barcode_held_by_another_product_is_409_barcode_taken(client) -> None:
    product_id = create(client)
    response = patch_details(client, product_id, {"barcode": "7501000000012"})
    assert response.status_code == 409
    error = response.get_json()
    assert error["code"] == "barcode_taken"
    assert error["details"] == [{"field": "barcode", "code": "unique"}]


def test_the_same_barcode_again_on_the_same_product_is_fine(client) -> None:
    product_id = create(client)
    for _ in range(2):
        assert (
            patch_details(client, product_id, {"barcode": gtin13(2)}).status_code == 200
        )


def test_a_refused_patch_changes_nothing(client) -> None:
    product_id = create(client)
    response = patch_details(
        client,
        product_id,
        {
            "commercial_status": "paused",
            "attributes": [{"name": "color", "value": "red"}],
            "barcode": "7501000000012",
        },
    )
    assert response.status_code == 409
    body = get_details(client, product_id).get_json()
    assert (body["commercial_status"], body["is_active"]) == ("active", True)
    assert (body["attributes"], body["barcode"]) == ([], None)


def test_the_python_and_the_sql_check_digit_agree(owner) -> None:
    generator = random.Random(790)
    codes = ["4006381333931", "036000291452", "96385075", "00000000", "1234567"]
    for length in (8, 12, 13, 14):
        for _ in range(60):
            body = "".join(generator.choice("0123456789") for _ in range(length - 1))
            valid = body + str(gtin.check_digit(body))
            codes += [valid, valid[:-1] + str((int(valid[-1]) + 1) % 10)]
    for code in codes:
        (sql_result,) = owner.execute(
            "SELECT products.gtin_is_valid(%s)", (code,)
        ).fetchone()
        assert sql_result == gtin.is_valid(code), code


def test_the_check_constraint_refuses_a_bad_barcode_below_the_service(
    client, service_dsn
) -> None:
    product_id = create(client)
    with psycopg.connect(service_dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            connection.execute(
                "INSERT INTO products.product_barcode VALUES (%s, %s)",
                (product_id, "4006381333932"),
            )
    from app.service import _map_write_error

    error = _map_write_error(raised.value)
    assert (error.status, error.code) == (422, "invalid_barcode")


# -- relations ----------------------------------------------------------------


def test_relations_are_replaced_listed_and_ordered_by_kind_then_id(client) -> None:
    one, two, three, four = (create(client, offset) for offset in (1, 2, 3, 4))
    assert put_relations(client, one, "substitute", [four, three]).status_code == 200
    assert put_relations(client, one, "complement", [two]).status_code == 200
    listed = get_relations(client, one).get_json()
    assert listed == {
        "product_id": one,
        "product_relations": [
            {"related_product_id": two, "kind": "complement"},
            {"related_product_id": three, "kind": "substitute"},
            {"related_product_id": four, "kind": "substitute"},
        ],
        "page_info": {"page": 1, "size": 20, "total": 3},
    }
    only = get_relations(client, one, "?kind=substitute").get_json()
    assert [item["related_product_id"] for item in only["product_relations"]] == [
        three,
        four,
    ]
    assert only["page_info"]["total"] == 2


def test_a_replace_returns_the_resulting_set_and_is_idempotent(client) -> None:
    one, two, three = (create(client, offset) for offset in (1, 2, 3))
    first = put_relations(client, one, "substitute", [two, three])
    again = put_relations(client, one, "substitute", [three, two])
    assert first.get_json() == again.get_json()
    assert first.get_json() == {
        "product_id": one,
        "product_relations": [
            {"related_product_id": two, "kind": "substitute"},
            {"related_product_id": three, "kind": "substitute"},
        ],
        "page_info": {"page": 1, "size": 20, "total": 2},
    }
    shrunk = put_relations(client, one, "substitute", [three]).get_json()
    assert [item["related_product_id"] for item in shrunk["product_relations"]] == [
        three
    ]


def test_an_empty_set_removes_one_kind_and_leaves_the_other(client) -> None:
    one, two, three = (create(client, offset) for offset in (1, 2, 3))
    put_relations(client, one, "substitute", [two])
    put_relations(client, one, "complement", [three])
    cleared = put_relations(client, one, "substitute", []).get_json()
    assert cleared["product_relations"] == []
    assert cleared["page_info"]["total"] == 0
    remaining = get_relations(client, one).get_json()["product_relations"]
    assert remaining == [{"related_product_id": three, "kind": "complement"}]


def test_a_relation_is_directed(client) -> None:
    one, two = create(client, 1), create(client, 2)
    put_relations(client, one, "substitute", [two])
    assert get_relations(client, two).get_json()["product_relations"] == []


def test_a_product_cannot_relate_to_itself(client, owner) -> None:
    one, two = create(client, 1), create(client, 2)
    response = put_relations(client, one, "substitute", [two, one])
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "self_relation"
    assert error["details"] == [{"field": "related_product_ids", "code": "self"}]
    assert get_relations(client, one).get_json()["product_relations"] == []
    # The schema says it too, for a writer that is not the service.
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO products.product_relation VALUES (%s, %s, %s)",
            (one, one, "substitute"),
        )


def test_every_related_product_must_exist(client) -> None:
    one, two = create(client, 1), create(client, 2)
    response = put_relations(client, one, "complement", [two, pid(99)])
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "unknown_related_product"
    assert error["details"] == [{"field": "related_product_ids", "code": "not_found"}]
    assert get_relations(client, one).get_json()["product_relations"] == []


def test_a_relation_kind_outside_the_enum_is_not_found(client) -> None:
    one = create(client)
    response = client.put(
        f"/v1/products/{one}/relations/upsell",
        json={"related_product_ids": []},
        headers=headers(),
    )
    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"


@pytest.mark.parametrize("query", ["kind=upsell", "kind=", "kind=Substitute"])
def test_an_unknown_kind_filter_is_422_invalid_query(client, query) -> None:
    response = client.get(f"/v1/products/1/relations?{query}", headers=headers())
    assert response.status_code == 422
    error = response.get_json()
    assert error["code"] == "invalid_query"
    assert error["details"] == [{"field": "kind", "code": "enum"}]


def test_relation_bodies_outside_the_schema_are_invalid_body(client) -> None:
    one = create(client)
    for ids in (list(range(1, 52)), [2, 2], ["a"], [0]):
        response = put_relations(client, one, "substitute", ids)
        assert response.status_code == 422, ids
        assert response.get_json()["code"] == "invalid_body"
    missing = client.put(
        f"/v1/products/{one}/relations/substitute", json={}, headers=headers()
    )
    assert missing.status_code == 422


def test_fifty_relations_are_accepted_and_paged(client) -> None:
    ids = [create(client, offset) for offset in range(1, 12)]
    one, others = ids[0], list(range(1, 41)) + ids[1:]
    assert len(others) == 50
    response = put_relations(client, one, "complement", others)
    assert response.status_code == 200
    body = response.get_json()
    # The PUT answers the first page of the default size.
    assert body["page_info"] == {"page": 1, "size": 20, "total": 50}
    assert len(body["product_relations"]) == 20
    second = get_relations(client, one, "?page=2&size=15").get_json()
    assert second["page_info"] == {"page": 2, "size": 15, "total": 50}
    assert [item["related_product_id"] for item in second["product_relations"]] == list(
        range(16, 31)
    )
    beyond = get_relations(client, one, "?page=9").get_json()
    assert beyond["product_relations"] == []
    assert beyond["page_info"]["total"] == 50
    assert get_relations(client, one, "?page=0").get_json()["code"] == (
        "invalid_pagination"
    )


def test_relations_xml_round_trip_validates_against_the_xsd(client, xsd) -> None:
    one, two, three = (create(client, offset) for offset in (1, 2, 3))
    request = (
        f'<ReplaceProductRelationsRequest xmlns="{NAMESPACE}">'
        f"<related_product_ids>{two}</related_product_ids>"
        f"<related_product_ids>{three}</related_product_ids>"
        "</ReplaceProductRelationsRequest>"
    )
    replaced = client.put(
        f"/v1/products/{one}/relations/substitute",
        data=request,
        content_type="application/xml",
        headers=headers(Accept="application/xml"),
    )
    assert replaced.status_code == 200, replaced.data
    root = etree.fromstring(replaced.data)
    xsd.assertValid(root)
    assert etree.QName(root[0]).localname == "product_id"
    assert root[0].text == str(one)
    assert [
        item.findtext(f"{{{NAMESPACE}}}related_product_id")
        for item in root.findall(f"{{{NAMESPACE}}}ProductRelation")
    ] == [str(two), str(three)]
    listed = client.get(
        f"/v1/products/{one}/relations?kind=substitute",
        headers=headers(Accept="application/xml"),
    )
    xsd.assertValid(etree.fromstring(listed.data))
    # XML cannot write an empty list but by leaving the items out.
    empty = client.put(
        f"/v1/products/{one}/relations/substitute",
        data=f'<ReplaceProductRelationsRequest xmlns="{NAMESPACE}"/>',
        content_type="application/xml",
        headers=headers(Accept="application/xml"),
    )
    assert empty.status_code == 200
    xsd.assertValid(etree.fromstring(empty.data))
    assert get_relations(client, one).get_json()["page_info"]["total"] == 0


def test_details_patch_in_xml_validates_both_ways(client, xsd) -> None:
    product_id = create(client)
    request = (
        f'<UpdateProductDetailsRequest xmlns="{NAMESPACE}">'
        "<commercial_status>discontinued</commercial_status>"
        "<attributes><name>color</name><value>white</value></attributes>"
        "<barcode>036000291452</barcode></UpdateProductDetailsRequest>"
    )
    response = client.patch(
        details_url(product_id),
        data=request,
        content_type="application/xml",
        headers=headers(Accept="application/xml"),
    )
    assert response.status_code == 200, response.data
    root = etree.fromstring(response.data)
    xsd.assertValid(root)
    assert root.findtext(f"{{{NAMESPACE}}}commercial_status") == "discontinued"
    assert root.findtext(f"{{{NAMESPACE}}}is_active") == "false"
    refused = client.patch(
        details_url(product_id),
        data=request.replace("036000291452", "036000291453"),
        content_type="application/xml",
        headers=headers(Accept="application/xml"),
    )
    assert refused.status_code == 422
    assert (
        etree.fromstring(refused.data).findtext(
            "{https://mosaiq.maxthecoder.online/xml/common/v1}code"
        )
        == "invalid_barcode"
    )


# -- audit, capture and the published view ------------------------------------


def test_a_relation_write_is_audited_with_the_caller(client, admin_user, owner) -> None:
    one, two, three = (create(client, offset) for offset in (1, 2, 3))
    put_relations(client, one, "substitute", [two, three], sub=admin_user)
    put_relations(client, one, "substitute", [three], sub=admin_user)
    rows = owner.execute(
        "SELECT action, user_id::text, "
        "coalesce(data_after, data_before) ->> 'related_product_id' "
        "FROM public.audit_log WHERE entity = %s AND entity_pk = %s ORDER BY audit_id",
        ("product_relation", str(one)),
    ).fetchall()
    assert rows == [
        ("INSERT", admin_user, str(two)),
        ("INSERT", admin_user, str(three)),
        ("DELETE", admin_user, str(two)),
    ]


def test_the_status_and_barcode_writes_are_audited(client, admin_user, owner) -> None:
    product_id = create(client)
    patch_details(
        client,
        product_id,
        {"commercial_status": "paused", "barcode": gtin13(3)},
        sub=admin_user,
    )
    for entity, action in (("product_status", "INSERT"), ("product_barcode", "INSERT")):
        rows = owner.execute(
            "SELECT action, user_id::text FROM public.audit_log "
            "WHERE entity = %s AND entity_pk = %s ORDER BY audit_id",
            (entity, str(product_id)),
        ).fetchall()
        assert rows == [(action, admin_user)]


def test_every_details_write_reaches_the_changed_stream(client, owner) -> None:
    one, two = create(client, 1), create(client, 2)
    owner.execute(
        "DELETE FROM products_events.outbox WHERE entity_id = %s", (str(one),)
    )
    put_relations(client, one, "complement", [two])
    patch_details(client, one, {"barcode": gtin13(4)})
    rows = owner.execute(
        "SELECT DISTINCT stream, event_type FROM products_events.outbox "
        "WHERE entity_id = %s",
        (str(one),),
    ).fetchall()
    assert rows == [("stream:products.v1.changed", "INSERT")]


def test_recommendations_reads_the_relation_view_and_not_the_table(
    client, owner
) -> None:
    one, two = create(client, 1), create(client, 2)
    put_relations(client, one, "substitute", [two])
    assert owner.execute(
        "SELECT has_table_privilege('svc_recommendations', "
        "'api_products.product_relation_v1', 'SELECT'), "
        "has_table_privilege('svc_recommendations', "
        "'products.product_relation', 'SELECT'), "
        "has_schema_privilege('svc_recommendations', 'products', 'USAGE')"
    ).fetchone() == (True, False, False)
    owner.execute("SET ROLE svc_recommendations")
    rows = owner.execute(
        "SELECT product_id, related_product_id, relation_kind "
        "FROM api_products.product_relation_v1 WHERE product_id = %s",
        (one,),
    ).fetchall()
    owner.execute("RESET ROLE")
    assert rows == [(one, two, "substitute")]
    assert [
        row[0]
        for row in owner.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            ("api_products", "product_relation_v1"),
        )
    ] == ["product_id", "related_product_id", "relation_kind"]


def test_the_service_role_cannot_delete_a_status_or_clear_a_barcode(
    service_dsn,
) -> None:
    with psycopg.connect(service_dsn, autocommit=True) as connection:
        for statement in (
            "DELETE FROM products.product_status",
            "DELETE FROM products.product_barcode",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)


def test_the_sync_function_is_a_pinned_definer_nobody_can_call(owner) -> None:
    row = owner.execute(
        "SELECT pg_get_userbyid(proowner), prosecdef, proconfig, "
        "has_function_privilege('public', oid, 'EXECUTE') FROM pg_proc "
        "WHERE oid = 'products.sync_status_from_product()'::regprocedure"
    ).fetchone()
    assert row[0] == "definer_products"
    assert row[1] is True
    assert any(setting.startswith("search_path=") for setting in row[2])
    assert row[3] is False


def test_deleting_a_product_takes_its_details_with_it(client, owner) -> None:
    one, two = create(client, 1), create(client, 2)
    patch_details(
        client,
        one,
        {
            "commercial_status": "paused",
            "barcode": gtin13(5),
            "attributes": [{"name": "color", "value": "red"}],
        },
    )
    put_relations(client, two, "substitute", [one])
    owner.execute("DELETE FROM public.product WHERE product_id = %s", (one,))
    for query in (
        "SELECT count(*) FROM products.product_status WHERE product_id = %s",
        "SELECT count(*) FROM products.product_attribute WHERE product_id = %s",
        "SELECT count(*) FROM products.product_barcode WHERE product_id = %s",
        "SELECT count(*) FROM products.product_relation WHERE related_product_id = %s",
    ):
        count = owner.execute(query, (one,)).fetchone()
        assert count == (0,), query
