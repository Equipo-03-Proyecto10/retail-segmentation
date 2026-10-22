"""The category rule against PostgreSQL, through the real service."""

from __future__ import annotations

import psycopg
import pytest
from lxml import etree

from app.service import refusal

pytestmark = pytest.mark.postgres
COMMON_NAMESPACE = "https://mosaiq.maxthecoder.online/xml/common/v1"


def create(client, headers, category_id, name, parent=None):
    payload = {"category_id": category_id, "name": name}
    if parent is not None:
        payload["parent_category_id"] = parent
    return client.post("/v1/categories", json=payload, headers=headers)


def outbox(owner, category_id):
    return owner.execute(
        "SELECT event_type FROM categories_events.outbox"
        " WHERE entity_id = %s ORDER BY occurred_at, event_type",
        (str(category_id),),
    ).fetchall()


@pytest.fixture
def tree(client, admin_headers, new_id, unique):
    """root -> mid -> leaf, created through the service."""

    ids = [new_id(), new_id(), new_id()]
    parent = None
    for index, category_id in enumerate(ids):
        assert (
            create(client, admin_headers, category_id, f"{unique}-{index}", parent)
        ).status_code == 201
        parent = category_id
    return ids


def test_create_get_and_list(client, admin_headers, new_id, unique, owner):
    category_id = new_id()
    created = create(client, admin_headers, category_id, unique)
    assert created.status_code == 201
    assert created.headers["Location"] == f"/v1/categories/{category_id}"
    assert created.get_json() == {
        "category_id": category_id,
        "name": unique,
        "parent_category_id": None,
    }
    assert outbox(owner, category_id) == [("INSERT",)]
    fetched = client.get(f"/v1/categories/{category_id}", headers=admin_headers)
    assert fetched.get_json() == created.get_json()
    listed = client.get("/v1/categories", headers=admin_headers).get_json()
    assert listed["page_info"]["total"] >= 31
    assert [c["category_id"] for c in listed["categories"]] == sorted(
        c["category_id"] for c in listed["categories"]
    )


def test_children_and_descendants(client, admin_headers, tree):
    root, mid, leaf = tree
    children = client.get(
        "/v1/categories", query_string={"parent_id": root}, headers=admin_headers
    ).get_json()
    assert [c["category_id"] for c in children["categories"]] == [mid]
    below = client.get(f"/v1/categories/{root}/descendants", headers=admin_headers)
    # The contract orders descendants by category_id, not by depth.
    assert [c["category_id"] for c in below.get_json()["categories"]] == sorted(
        [mid, leaf]
    )
    none = client.get(f"/v1/categories/{leaf}/descendants", headers=admin_headers)
    assert none.get_json()["categories"] == []
    assert none.get_json()["page_info"]["total"] == 0
    missing = client.get("/v1/categories/1999/descendants", headers=admin_headers)
    assert missing.status_code == 404
    odd = client.get(
        "/v1/categories", query_string={"parent_id": "x"}, headers=admin_headers
    )
    assert odd.status_code == 422


def test_duplicate_id_and_name_get_409(client, admin_headers, new_id, unique):
    first = new_id()
    assert create(client, admin_headers, first, unique).status_code == 201
    same_id = create(client, admin_headers, first, unique + "-b")
    assert (same_id.status_code, same_id.get_json()["code"]) == (
        409,
        "category_id_taken",
    )
    same_name = create(client, admin_headers, new_id(), unique)
    assert (same_name.status_code, same_name.get_json()["code"]) == (
        409,
        "category_name_taken",
    )


def test_unknown_parent_gets_422(client, admin_headers, new_id, unique, owner):
    response = create(client, admin_headers, new_id(), unique, parent=31999)
    assert response.status_code == 422
    assert response.get_json()["code"] == "unknown_parent_category"
    moved = create(client, admin_headers, new_id(), unique + "-m")
    patched = client.patch(
        f"/v1/categories/{moved.get_json()['category_id']}",
        json={"parent_category_id": 31999},
        headers=admin_headers,
    )
    assert (patched.status_code, patched.get_json()["code"]) == (
        422,
        "unknown_parent_category",
    )


def test_a_category_cannot_be_its_own_parent_or_ancestor(client, admin_headers, tree):
    root, mid, leaf = tree
    for target, parent in ((root, root), (root, leaf), (root, mid), (mid, leaf)):
        response = client.patch(
            f"/v1/categories/{target}",
            json={"parent_category_id": parent},
            headers=admin_headers,
        )
        assert response.status_code == 409, (target, parent)
        assert response.get_json()["code"] == "category_cycle"
    still = client.get(f"/v1/categories/{root}", headers=admin_headers).get_json()
    assert still["parent_category_id"] is None


def test_create_with_itself_as_parent_is_refused_without_500(
    client, admin_headers, new_id, unique
):
    category_id = new_id()
    response = create(client, admin_headers, category_id, unique, parent=category_id)
    assert response.status_code == 422


def test_rename_keeps_the_parent_and_null_makes_a_root(client, admin_headers, tree):
    root, mid, _ = tree
    renamed = client.patch(
        f"/v1/categories/{mid}",
        json={"name": "svc-test-renamed-" + str(mid)},
        headers=admin_headers,
    )
    assert renamed.status_code == 200
    assert renamed.get_json()["parent_category_id"] == root
    detached = client.patch(
        f"/v1/categories/{mid}",
        json={"parent_category_id": None},
        headers=admin_headers,
    )
    assert detached.get_json()["parent_category_id"] is None
    assert (
        client.patch(
            f"/v1/categories/{mid}", json={}, headers=admin_headers
        ).status_code
        == 422
    )
    unknown = client.patch(
        "/v1/categories/31998", json={"name": "x"}, headers=admin_headers
    )
    assert (unknown.status_code, unknown.get_json()["code"]) == (
        404,
        "category_not_found",
    )


def add_product(owner, category_id, unique):
    owner.execute(
        "INSERT INTO product (product_id, sku, name, category_id, list_price)"
        " VALUES ((SELECT coalesce(max(product_id), 0) + 1 FROM product), %s, %s, %s, 1)",
        (unique, unique, category_id),
    )


def test_delete_with_products_children_or_interest_gets_409(
    client, admin_headers, tree, owner, unique
):
    root, mid, leaf = tree
    add_product(owner, leaf, unique)
    response = client.delete(f"/v1/categories/{leaf}", headers=admin_headers)
    assert (response.status_code, response.get_json()["code"]) == (
        409,
        "category_has_products",
    )
    response = client.delete(f"/v1/categories/{root}", headers=admin_headers)
    assert (response.status_code, response.get_json()["code"]) == (
        409,
        "category_has_children",
    )
    owner.execute("DELETE FROM product WHERE sku = %s", (unique,))
    customer = owner.execute("SELECT customer_id FROM customer LIMIT 1").fetchone()[0]
    owner.execute(
        "INSERT INTO customer_interest_category (customer_id, category_id) VALUES (%s, %s)",
        (customer, leaf),
    )
    response = client.delete(f"/v1/categories/{leaf}", headers=admin_headers)
    assert (response.status_code, response.get_json()["code"]) == (
        409,
        "category_has_customer_interest",
    )
    kept = owner.execute(
        "SELECT count(*) FROM customer_interest_category WHERE category_id = %s",
        (leaf,),
    ).fetchone()[0]
    assert kept == 1
    assert ("DELETE",) not in outbox(owner, leaf)


def test_deleting_a_free_leaf_answers_204_and_adds_one_delete_row(
    client, admin_headers, new_id, unique, owner
):
    category_id = new_id()
    create(client, admin_headers, category_id, unique)
    assert (
        client.delete(
            f"/v1/categories/{category_id}", headers=admin_headers
        ).status_code
        == 204
    )
    assert outbox(owner, category_id) == [("INSERT",), ("DELETE",)]
    gone = client.delete(f"/v1/categories/{category_id}", headers=admin_headers)
    assert (gone.status_code, gone.get_json()["code"]) == (404, "category_not_found")


def test_a_write_is_audited_with_the_callers_identity(
    client, admin_headers, new_id, unique, owner, actor
):
    category_id = new_id()
    create(client, admin_headers, category_id, unique)
    client.patch(
        f"/v1/categories/{category_id}",
        json={"name": unique + "x"},
        headers=admin_headers,
    )
    client.delete(f"/v1/categories/{category_id}", headers=admin_headers)
    rows = owner.execute(
        "SELECT action, user_id::text FROM audit_log"
        " WHERE entity = 'category' AND entity_pk = %s ORDER BY audit_id",
        (str(category_id),),
    ).fetchall()
    assert rows == [("INSERT", actor), ("UPDATE", actor), ("DELETE", actor)]


def test_mapped_refusals_come_from_constraint_names(service_url, new_id):
    with psycopg.connect(service_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            connection.execute(
                "INSERT INTO category (category_id, name, parent_category_id)"
                " VALUES (%s, %s, %s)",
                (31500, "svc-test-self", 31500),
            )
    mapped = refusal(raised.value, deleting=False)
    assert (mapped.status, mapped.code) == (409, "category_cycle")


# The guard -----------------------------------------------------------------


def test_the_guard_holds_for_services_only(service_url, owner, new_id, unique):
    named, free = new_id(), new_id()
    owner.execute(
        "INSERT INTO category (category_id, name) VALUES (%s, %s), (%s, %s)",
        (named, unique + "-n", free, unique + "-f"),
    )
    customer = owner.execute("SELECT customer_id FROM customer LIMIT 1").fetchone()[0]
    owner.execute(
        "INSERT INTO customer_interest_category (customer_id, category_id) VALUES (%s, %s)",
        (customer, named),
    )
    with psycopg.connect(service_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as raised:
            connection.execute("DELETE FROM category WHERE category_id = %s", (named,))
        assert raised.value.diag.constraint_name == "category_has_customer_interest"
        # The guard can read the table: a category nobody names is deleted.
        deleted = connection.execute(
            "DELETE FROM category WHERE category_id = %s", (free,)
        )
        assert deleted.rowcount == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM customer_interest_category")
    # The web's role still cascades, and nothing here is kept.
    with owner.transaction():
        owner.execute("SET LOCAL ROLE retail_app")
        owner.execute("DELETE FROM category WHERE category_id = %s", (named,))
        left = owner.execute(
            "SELECT count(*) FROM customer_interest_category WHERE category_id = %s",
            (named,),
        ).fetchone()[0]
        assert left == 0
        raise_rollback = psycopg.Rollback()
        raise raise_rollback
    assert (
        owner.execute(
            "SELECT count(*) FROM customer_interest_category WHERE category_id = %s",
            (named,),
        ).fetchone()[0]
        == 1
    )


def test_the_definer_functions_follow_the_rules(owner):
    rows = owner.execute(
        "SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef,"
        " has_function_privilege('public', p.oid, 'EXECUTE'), p.proconfig"
        " FROM pg_proc p WHERE p.proname IN ('fn_category_delete_guard', 'capture_changed')"
        " AND p.pronamespace IN ('public'::regnamespace, 'categories_events'::regnamespace)"
        " ORDER BY 1"
    ).fetchall()
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("capture_changed", "definer_categories", True, False),
        ("fn_category_delete_guard", "definer_guard", True, False),
    ]
    assert all(any(c.startswith("search_path=") for c in r[4]) for r in rows)


def test_the_view_publishes_the_tree_to_recommendations(owner, tree):
    with owner.transaction():
        owner.execute("SET LOCAL ROLE svc_recommendations")
        rows = owner.execute(
            "SELECT category_id, parent_category_id, name FROM api_categories.category_tree_v1"
            " WHERE category_id = %s",
            (tree[1],),
        ).fetchall()
        assert rows[0][:2] == (tree[1], tree[0])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with owner.transaction():
                owner.execute("SELECT * FROM public.category")


@pytest.mark.parametrize("value", [" 1 ", "1_0", "+1", "", "1.0"])
@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_parent_id_refusals_are_422_invalid_query(client, admin_headers, value, accept):
    response = client.get(
        "/v1/categories",
        query_string={"parent_id": value},
        headers={**admin_headers, "Accept": accept},
    )
    assert response.status_code == 422
    if accept == "application/json":
        body = response.get_json()
        assert body["code"] == "invalid_query"
        assert body["details"] == [{"field": "parent_id", "code": "invalid_type"}]
    else:
        root = etree.fromstring(response.data)
        assert root.findtext(f"{{{COMMON_NAMESPACE}}}code") == "invalid_query"
        detail = root.find(f"{{{COMMON_NAMESPACE}}}details")
        assert detail is not None
        assert detail.findtext(f"{{{COMMON_NAMESPACE}}}field") == "parent_id"
        assert detail.findtext(f"{{{COMMON_NAMESPACE}}}code") == "invalid_type"
