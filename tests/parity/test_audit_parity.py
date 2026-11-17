"""Audit parity between web writes and service writes (F20-08).

RN-28 lists the audited tables. Of those, `category`, `product`, `app_user`
and `campaign` are written both by the web and by a service (the svc_* grants
in contracts/db/grants.csv; the web writes no `customer`). For each change
both paths can make, the administrator makes it once through the web's form
and once through the service's contract, and the `audit_log` rows each change
left are compared by shape: table, action, the columns of each payload, the
columns the change altered, and the acting user. Then GET /v1/audit-entries
must return every one of those rows as stored, with no credential (RN-29),
and no service role may update or delete `audit_log` (RN-30).

They create categories, products, users and campaigns on the throwaway stack
named by PARITY_DATABASE_URL; never point them at the instance.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import psycopg
import pytest
from psycopg import sql

from tests.parity.compare import (
    AuditDifference,
    audit_shape,
    credentials_in,
    diff_audit,
)
from tests.parity.stack import env


@dataclass
class Change:
    """One change made both ways: each callable makes it and returns the
    entity_pk of the row it touched."""

    name: str
    entity: str
    by_web: Callable[[], str]
    by_service: Callable[[], str]


def _report(found) -> str:
    return "\n".join(str(d) for d in found)


def _ok(reply, *statuses: int):
    assert reply.status in statuses, f"{reply.status} {reply.body!r}"
    return reply.json() if reply.body else None


def _rows(db, entity: str, pk: str, after: int) -> list[dict]:
    rows = db(
        "SELECT audit_id, user_id::text, entity, entity_pk, action, "
        "data_before, data_after FROM audit_log "
        "WHERE entity = %s AND entity_pk = %s AND audit_id > %s ORDER BY audit_id",
        (entity, pk, after),
    )
    names = ("audit_id", "user_id", "entity", "entity_pk", "action")
    return [
        dict(zip(names, r[:5], strict=True), data_before=r[5], data_after=r[6])
        for r in rows
    ]


def _changes(db, web, audit) -> list[Change]:
    tag = uuid.uuid4().hex[:8]
    [[category_base]] = db("SELECT coalesce(max(category_id), 0) FROM category")
    [[product_base]] = db("SELECT coalesce(max(product_id), 0) FROM product")
    [[parent]] = db("SELECT min(category_id) FROM category")
    ids = {"web": category_base + 1001, "service": category_base + 1002}
    products = {"web": product_base + 1001, "service": product_base + 1002}
    users: dict[str, str] = {}
    campaigns: dict[str, int] = {}
    starts = date.today() + timedelta(days=30)
    ends = starts + timedelta(days=30)

    def category_insert_web() -> str:
        web.submit(
            "/admin/categories/new",
            {
                "category_id": str(ids["web"]),
                "name": f"Parity web {tag}",
                "parent_category_id": "",
            },
        )
        return str(ids["web"])

    def category_insert_service() -> str:
        _ok(
            audit.call(
                "categories",
                "POST",
                "categories",
                {"category_id": ids["service"], "name": f"Parity service {tag}"},
            ),
            201,
        )
        return str(ids["service"])

    def category_update_web() -> str:
        web.submit(
            f"/admin/categories/{ids['web']}/edit",
            {"name": f"Parity web {tag} renamed", "parent_category_id": ""},
        )
        return str(ids["web"])

    def category_update_service() -> str:
        _ok(
            audit.call(
                "categories",
                "PATCH",
                f"categories/{ids['service']}",
                {"name": f"Parity service {tag} renamed"},
            ),
            200,
        )
        return str(ids["service"])

    def product_insert_web() -> str:
        web.submit(
            "/admin/products/new",
            {
                "product_id": str(products["web"]),
                "sku": f"PAR-W-{tag}",
                "name": f"Parity web {tag}",
                "category_id": str(parent),
                "list_price": "10.00",
                "is_active": "on",
            },
        )
        return str(products["web"])

    def product_insert_service() -> str:
        _ok(
            audit.call(
                "products",
                "POST",
                "products",
                {
                    "product_id": products["service"],
                    "sku": f"PAR-S-{tag}",
                    "name": f"Parity service {tag}",
                    "category_id": parent,
                    "list_price": "10.00",
                },
            ),
            201,
        )
        return str(products["service"])

    def product_update_web() -> str:
        web.submit(
            f"/admin/products/{products['web']}/edit",
            {
                "sku": f"PAR-W-{tag}",
                "name": f"Parity web {tag}",
                "category_id": str(parent),
                "list_price": "12.00",
                "is_active": "on",
            },
        )
        return str(products["web"])

    def product_update_service() -> str:
        _ok(
            audit.call(
                "products",
                "PATCH",
                f"products/{products['service']}",
                {"list_price": "12.00"},
            ),
            200,
        )
        return str(products["service"])

    def user_insert_web() -> str:
        email = f"parity-web-{tag}@example.com"
        web.submit(
            "/admin/users/new",
            {
                "name": f"Parity web {tag}",
                "email": email,
                "password": "parity-" + uuid.uuid4().hex,
                "role_code": "ANALYST",
            },
        )
        [[users["web"]]] = db(
            "SELECT user_id::text FROM app_user WHERE email = %s", (email,)
        )
        return users["web"]

    def user_insert_service() -> str:
        body = _ok(
            audit.call(
                "users",
                "POST",
                "users",
                {
                    "name": f"Parity service {tag}",
                    "email": f"parity-service-{tag}@example.com",
                    "role_code": "ANALYST",
                    "password": "parity-" + uuid.uuid4().hex,
                },
            ),
            201,
        )
        users["service"] = body["user_id"]
        return users["service"]

    def user_deactivate_web() -> str:
        web.submit("/admin/users", {}, action=f"/admin/users/{users['web']}/deactivate")
        return users["web"]

    def user_deactivate_service() -> str:
        _ok(
            audit.call("users", "POST", f"users/{users['service']}/deactivation"),
            200,
            204,
        )
        return users["service"]

    def campaign_fields(side: str) -> dict[str, str]:
        return {
            "name": f"Parity {side} {tag}",
            "label_code": "LOYAL",
            "starts_on": starts.isoformat(),
            "ends_on": ends.isoformat(),
        }

    def campaign_insert_web() -> str:
        fields = campaign_fields("web")
        web.submit("/campaigns/new", fields)
        [[campaigns["web"]]] = db(
            "SELECT campaign_id FROM campaign WHERE name = %s", (fields["name"],)
        )
        return str(campaigns["web"])

    def campaign_insert_service() -> str:
        body = _ok(
            audit.call("campaigns", "POST", "campaigns", campaign_fields("service")),
            201,
        )
        campaigns["service"] = body["campaign_id"]
        return str(campaigns["service"])

    def campaign_update_web() -> str:
        web.submit(
            f"/campaigns/{campaigns['web']}/edit",
            campaign_fields("web") | {"name": f"Parity web {tag} renamed"},
        )
        return str(campaigns["web"])

    def campaign_update_service() -> str:
        _ok(
            audit.call(
                "campaigns",
                "PATCH",
                f"campaigns/{campaigns['service']}",
                {"name": f"Parity service {tag} renamed"},
            ),
            200,
        )
        return str(campaigns["service"])

    def category_delete_web() -> str:
        web.submit(
            "/admin/categories",
            {"confirm": "yes"},
            action=f"/admin/categories/{ids['web']}/delete",
        )
        return str(ids["web"])

    def category_delete_service() -> str:
        _ok(audit.call("categories", "DELETE", f"categories/{ids['service']}"), 204)
        return str(ids["service"])

    return [
        Change(
            "category INSERT", "category", category_insert_web, category_insert_service
        ),
        Change(
            "category UPDATE", "category", category_update_web, category_update_service
        ),
        Change("product INSERT", "product", product_insert_web, product_insert_service),
        Change("product UPDATE", "product", product_update_web, product_update_service),
        Change("app_user INSERT", "app_user", user_insert_web, user_insert_service),
        Change(
            "app_user UPDATE", "app_user", user_deactivate_web, user_deactivate_service
        ),
        Change(
            "campaign INSERT", "campaign", campaign_insert_web, campaign_insert_service
        ),
        Change(
            "campaign UPDATE", "campaign", campaign_update_web, campaign_update_service
        ),
        Change(
            "category DELETE", "category", category_delete_web, category_delete_service
        ),
    ]


@pytest.fixture(scope="module")
def recorded(db, web, audit) -> dict[str, tuple[list[dict], list[dict]]]:
    """Every change made both ways, in order, with the audit rows each left."""

    found = {}
    for change in _changes(db, web, audit):
        sides = []
        for make in (change.by_web, change.by_service):
            [[mark]] = db("SELECT coalesce(max(audit_id), 0) FROM audit_log")
            sides.append(_rows(db, change.entity, make(), mark))
        found[change.name] = (sides[0], sides[1])
    return found


@pytest.fixture(scope="module")
def actor(db) -> str:
    [[user_id]] = db(
        "SELECT user_id::text FROM app_user WHERE lower(email) = lower(%s)",
        (env("PARITY_WEB_EMAIL"),),
    )
    return user_id


def test_each_change_leaves_the_same_audit_rows_both_ways(
    recorded, actor, differences
) -> None:
    for change, (by_web, by_service) in recorded.items():
        differences.extend(
            diff_audit(
                change,
                [audit_shape(r, actor) for r in by_web],
                [audit_shape(r, actor) for r in by_service],
            )
        )
    print("\n" + _report(differences) if differences else "\nno difference")
    assert not differences, _report(differences)


def _plain(value):
    """JSON as a client reads it: the database's Decimal and datetime values
    become what json gives back."""

    return json.loads(json.dumps(value, default=str))


def test_audit_trail_returns_each_row_and_no_credential(
    recorded, audit, differences
) -> None:
    for change, sides in recorded.items():
        for side, rows in zip(("web", "service"), sides, strict=True):
            for row in rows:
                listed = audit.audit_entries(row["entity"], row["entity_pk"])
                entries = {e["audit_id"]: e for e in listed["audit_entries"]}
                served = entries.get(row["audit_id"])
                what = f"{side} row {row['audit_id']}"
                if served is None:
                    differences.append(
                        AuditDifference(change, f"{what} not returned", row, None)
                    )
                    continue
                for name in ("user_id", "entity", "entity_pk", "action"):
                    if served[name] != row[name]:
                        differences.append(
                            AuditDifference(
                                change, f"{what} {name}", row[name], served[name]
                            )
                        )
                for name in ("data_before", "data_after"):
                    if _plain(served[name]) != _plain(row[name]):
                        differences.append(
                            AuditDifference(
                                change, f"{what} {name}", row[name], served[name]
                            )
                        )
                for where in credentials_in(listed) + credentials_in(_plain(row)):
                    differences.append(
                        AuditDifference(
                            change, f"{what} credential at {where}", True, None
                        )
                    )
    print("\n" + _report(differences) if differences else "\nno difference")
    assert not differences, _report(differences)


def test_no_service_role_may_update_or_delete_the_audit_log(db) -> None:
    roles = [
        r[0]
        for r in db(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE %s "
            "OR rolname = %s ORDER BY rolname",
            ("svc\\_%", "retail_app"),
        )
    ]
    assert any(r.startswith("svc_") for r in roles), "no svc_* role exists"
    allowed = []
    with psycopg.connect(env("PARITY_DATABASE_URL")) as connection:
        for role in roles:
            for statement in (
                # Constants and no WHERE: each needs only the privilege it tests,
                # not SELECT, so a refusal is the UPDATE or DELETE refused.
                "UPDATE audit_log SET entity = 'parity'",
                "DELETE FROM audit_log",
            ):
                try:
                    with connection.transaction(force_rollback=True):
                        connection.execute(
                            sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role))
                        )
                        connection.execute(statement)
                    allowed.append(f"{role}: {statement}")
                except psycopg.errors.InsufficientPrivilege:
                    pass
    assert not allowed, "RN-30: allowed\n" + "\n".join(allowed)
