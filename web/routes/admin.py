"""Administrator routes: catalog CRUD.

Authorization comes from the F4-01 (#69) middleware: listings demand
`catalog.read`, which six roles hold, and every mutation demands
`catalog.write`, which only ADMIN holds. The gate is default-deny, so a view
added here without a declaration is refused rather than exposed.
"""

from __future__ import annotations

from decimal import Decimal

from flask import Blueprint, redirect, render_template, request, url_for
from psycopg.errors import UniqueViolation

from web.db import get_connection
from web.db.categories import (
    create_category,
    delete_category,
    get_category,
    list_all_categories,
    list_categories,
    update_category,
)
from web.db.channels import (
    create_channel,
    delete_channel,
    get_channel,
    list_channels,
    update_channel,
)
from web.db.products import (
    create_product,
    delete_product,
    get_product,
    list_products,
    update_product,
)
from web.db.roles import create_role, delete_role, get_role, list_roles, update_role
from web.db.stores import (
    create_store,
    delete_store,
    get_store,
    list_stores,
    update_store,
)
from web.db.users import list_role_options, list_users
from web.middleware.authz import (
    CATALOG_READ,
    CATALOG_WRITE,
    USER_READ,
    USER_WRITE,
    requires,
)
from web.services.catalog import (
    parse_pagination,
    validate_category,
    validate_channel,
    validate_product,
    validate_role,
    validate_store,
)
from web.services.users import (
    SingleAdministratorError,
    UnknownRoleError,
    UnknownUserError,
    create_user,
    set_active,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")

_PER_PAGE = 20


# ---------- stores ----------


@bp.get("/stores")
@requires(CATALOG_READ)
def list_stores_view():
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    stores, total = list_stores(
        connection, search=search, page=page, per_page=_PER_PAGE
    )
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    return render_template(
        "admin/stores.html",
        stores=stores,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/stores/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_store_view():
    if request.method == "GET":
        return render_template("admin/store_form.html", store=None, errors={})

    connection = get_connection()
    store_id = request.form.get("store_id", "")
    name = request.form.get("name", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()

    errors = validate_store(name=name, city=city, state=state)
    if not store_id.isdigit():
        errors["store_id"] = "Store ID must be a whole number."

    if errors:
        return (
            render_template(
                "admin/store_form.html",
                store={
                    "store_id": store_id,
                    "name": name,
                    "city": city,
                    "state": state,
                },
                errors=errors,
            ),
            400,
        )

    create_store(connection, store_id=int(store_id), name=name, city=city, state=state)
    return redirect(url_for("admin.list_stores_view"))


@bp.route("/stores/<int:store_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_store_view(store_id: int):
    connection = get_connection()
    store = get_store(connection, store_id)
    if store is None:
        return render_template("errors/error.html", code=404, name="Not Found"), 404

    if request.method == "GET":
        return render_template("admin/store_form.html", store=store, errors={})

    name = request.form.get("name", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()
    is_active = request.form.get("is_active") == "on"

    errors = validate_store(name=name, city=city, state=state)
    if errors:
        return (
            render_template(
                "admin/store_form.html",
                store={
                    "store_id": store_id,
                    "name": name,
                    "city": city,
                    "state": state,
                    "is_active": is_active,
                },
                errors=errors,
            ),
            400,
        )

    update_store(
        connection, store_id, name=name, city=city, state=state, is_active=is_active
    )
    return redirect(url_for("admin.list_stores_view"))


@bp.post("/stores/<int:store_id>/delete")
@requires(CATALOG_WRITE)
def delete_store_view(store_id: int):
    connection = get_connection()
    deleted = delete_store(connection, store_id)

    if not deleted:
        page = parse_pagination(request.args.get("page"))
        stores, total = list_stores(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        return (
            render_template(
                "admin/stores.html",
                stores=stores,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=(
                    "Cannot delete this store: other records still reference it."
                ),
            ),
            409,
        )

    return redirect(url_for("admin.list_stores_view"))


# ---------- categories ----------


@bp.get("/categories")
@requires(CATALOG_READ)
def list_categories_view():
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    categories, total = list_categories(
        connection, search=search, page=page, per_page=_PER_PAGE
    )
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    return render_template(
        "admin/categories.html",
        categories=categories,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/categories/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_category_view():
    connection = get_connection()
    all_categories = list_all_categories(connection)

    if request.method == "GET":
        return render_template(
            "admin/category_form.html",
            category=None,
            errors={},
            all_categories=all_categories,
        )

    category_id = request.form.get("category_id", "")
    name = request.form.get("name", "").strip()
    parent_raw = request.form.get("parent_category_id", "")
    parent_category_id = int(parent_raw) if parent_raw else None

    errors = validate_category(name=name)
    if not category_id.isdigit():
        errors["category_id"] = "Category ID must be a whole number."

    if errors:
        return (
            render_template(
                "admin/category_form.html",
                category={
                    "category_id": category_id,
                    "name": name,
                    "parent_category_id": parent_category_id,
                },
                errors=errors,
                all_categories=all_categories,
            ),
            400,
        )

    error = create_category(
        connection,
        category_id=int(category_id),
        name=name,
        parent_category_id=parent_category_id,
    )
    if error:
        return (
            render_template(
                "admin/category_form.html",
                category={
                    "category_id": category_id,
                    "name": name,
                    "parent_category_id": parent_category_id,
                },
                errors={"category_id": error},
                all_categories=all_categories,
            ),
            409,
        )

    return redirect(url_for("admin.list_categories_view"))


@bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_category_view(category_id: int):
    connection = get_connection()
    category = get_category(connection, category_id)
    if category is None:
        return render_template("errors/error.html", code=404, name="Not Found"), 404

    all_categories = [
        c for c in list_all_categories(connection) if c.category_id != category_id
    ]

    if request.method == "GET":
        return render_template(
            "admin/category_form.html",
            category=category,
            errors={},
            all_categories=all_categories,
        )

    name = request.form.get("name", "").strip()
    parent_raw = request.form.get("parent_category_id", "")
    parent_category_id = int(parent_raw) if parent_raw else None

    errors = validate_category(name=name)
    if errors:
        return (
            render_template(
                "admin/category_form.html",
                category={
                    "category_id": category_id,
                    "name": name,
                    "parent_category_id": parent_category_id,
                },
                errors=errors,
                all_categories=all_categories,
            ),
            400,
        )

    update_category(
        connection, category_id, name=name, parent_category_id=parent_category_id
    )
    return redirect(url_for("admin.list_categories_view"))


@bp.post("/categories/<int:category_id>/delete")
@requires(CATALOG_WRITE)
def delete_category_view(category_id: int):
    connection = get_connection()
    deleted = delete_category(connection, category_id)

    if not deleted:
        page = parse_pagination(request.args.get("page"))
        categories, total = list_categories(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        return (
            render_template(
                "admin/categories.html",
                categories=categories,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=(
                    "Cannot delete this category: "
                    "products or subcategories still reference it."
                ),
            ),
            409,
        )

    return redirect(url_for("admin.list_categories_view"))


# ---------- channels ----------


@bp.get("/channels")
@requires(CATALOG_READ)
def list_channels_view():
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    channels, total = list_channels(
        connection, search=search, page=page, per_page=_PER_PAGE
    )
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    return render_template(
        "admin/channels.html",
        channels=channels,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/channels/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_channel_view():
    if request.method == "GET":
        return render_template("admin/channel_form.html", channel=None, errors={})

    connection = get_connection()
    channel_id = request.form.get("channel_id", "")
    name = request.form.get("name", "").strip()

    errors = validate_channel(name=name)
    if not channel_id.isdigit():
        errors["channel_id"] = "Channel ID must be a whole number."

    if errors:
        return (
            render_template(
                "admin/channel_form.html",
                channel={"channel_id": channel_id, "name": name},
                errors=errors,
            ),
            400,
        )

    error = create_channel(connection, channel_id=int(channel_id), name=name)
    if error:
        return (
            render_template(
                "admin/channel_form.html",
                channel={"channel_id": channel_id, "name": name},
                errors={"channel_id": error},
            ),
            409,
        )

    return redirect(url_for("admin.list_channels_view"))


@bp.route("/channels/<int:channel_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_channel_view(channel_id: int):
    connection = get_connection()
    channel = get_channel(connection, channel_id)
    if channel is None:
        return render_template("errors/error.html", code=404, name="Not Found"), 404

    if request.method == "GET":
        return render_template("admin/channel_form.html", channel=channel, errors={})

    name = request.form.get("name", "").strip()

    errors = validate_channel(name=name)
    if errors:
        return (
            render_template(
                "admin/channel_form.html",
                channel={"channel_id": channel_id, "name": name},
                errors=errors,
            ),
            400,
        )

    error = update_channel(connection, channel_id, name=name)
    if error:
        return (
            render_template(
                "admin/channel_form.html",
                channel={"channel_id": channel_id, "name": name},
                errors={"name": error},
            ),
            409,
        )

    return redirect(url_for("admin.list_channels_view"))


@bp.post("/channels/<int:channel_id>/delete")
@requires(CATALOG_WRITE)
def delete_channel_view(channel_id: int):
    connection = get_connection()
    deleted = delete_channel(connection, channel_id)

    if not deleted:
        page = parse_pagination(request.args.get("page"))
        channels, total = list_channels(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        return (
            render_template(
                "admin/channels.html",
                channels=channels,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=(
                    "Cannot delete this channel: other records still reference it."
                ),
            ),
            409,
        )

    return redirect(url_for("admin.list_channels_view"))


# ---------- products ----------


@bp.get("/products")
@requires(CATALOG_READ)
def list_products_view():
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    products, total = list_products(
        connection, search=search, page=page, per_page=_PER_PAGE
    )
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    return render_template(
        "admin/products.html",
        products=products,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/products/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_product_view():
    connection = get_connection()
    all_categories = list_all_categories(connection)

    if request.method == "GET":
        return render_template(
            "admin/product_form.html",
            product=None,
            errors={},
            all_categories=all_categories,
        )

    product_id = request.form.get("product_id", "")
    sku = request.form.get("sku", "").strip()
    name = request.form.get("name", "").strip()
    category_id_raw = request.form.get("category_id", "")
    list_price_raw = request.form.get("list_price", "").strip()

    errors = validate_product(
        sku=sku, name=name, category_id=category_id_raw, list_price=list_price_raw
    )
    if not product_id.isdigit():
        errors["product_id"] = "Product ID must be a whole number."

    if errors:
        return (
            render_template(
                "admin/product_form.html",
                product={
                    "product_id": product_id,
                    "sku": sku,
                    "name": name,
                    "category_id": category_id_raw,
                    "list_price": list_price_raw,
                },
                errors=errors,
                all_categories=all_categories,
            ),
            400,
        )

    error = create_product(
        connection,
        product_id=int(product_id),
        sku=sku,
        name=name,
        category_id=int(category_id_raw),
        list_price=Decimal(list_price_raw),
    )
    if error:
        return (
            render_template(
                "admin/product_form.html",
                product={
                    "product_id": product_id,
                    "sku": sku,
                    "name": name,
                    "category_id": category_id_raw,
                    "list_price": list_price_raw,
                },
                errors={"sku": error},
                all_categories=all_categories,
            ),
            409,
        )

    return redirect(url_for("admin.list_products_view"))


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_product_view(product_id: int):
    connection = get_connection()
    product = get_product(connection, product_id)
    if product is None:
        return render_template("errors/error.html", code=404, name="Not Found"), 404

    all_categories = list_all_categories(connection)

    if request.method == "GET":
        return render_template(
            "admin/product_form.html",
            product=product,
            errors={},
            all_categories=all_categories,
        )

    sku = request.form.get("sku", "").strip()
    name = request.form.get("name", "").strip()
    category_id_raw = request.form.get("category_id", "")
    list_price_raw = request.form.get("list_price", "").strip()
    is_active = request.form.get("is_active") == "on"

    errors = validate_product(
        sku=sku, name=name, category_id=category_id_raw, list_price=list_price_raw
    )
    if errors:
        return (
            render_template(
                "admin/product_form.html",
                product={
                    "product_id": product_id,
                    "sku": sku,
                    "name": name,
                    "category_id": category_id_raw,
                    "list_price": list_price_raw,
                    "is_active": is_active,
                },
                errors=errors,
                all_categories=all_categories,
            ),
            400,
        )

    error = update_product(
        connection,
        product_id,
        sku=sku,
        name=name,
        category_id=int(category_id_raw),
        list_price=Decimal(list_price_raw),
        is_active=is_active,
    )
    if error:
        return (
            render_template(
                "admin/product_form.html",
                product={
                    "product_id": product_id,
                    "sku": sku,
                    "name": name,
                    "category_id": category_id_raw,
                    "list_price": list_price_raw,
                    "is_active": is_active,
                },
                errors={"sku": error},
                all_categories=all_categories,
            ),
            409,
        )

    return redirect(url_for("admin.list_products_view"))


@bp.post("/products/<int:product_id>/delete")
@requires(CATALOG_WRITE)
def delete_product_view(product_id: int):
    connection = get_connection()
    deleted = delete_product(connection, product_id)

    if not deleted:
        page = parse_pagination(request.args.get("page"))
        products, total = list_products(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        return (
            render_template(
                "admin/products.html",
                products=products,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=(
                    "Cannot delete this product: other records still reference it."
                ),
            ),
            409,
        )

    return redirect(url_for("admin.list_products_view"))


# ---------- roles ----------


@bp.get("/roles")
@requires(CATALOG_READ)
def list_roles_view():
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    roles, total = list_roles(connection, search=search, page=page, per_page=_PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    return render_template(
        "admin/roles.html",
        roles=roles,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/roles/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_role_view():
    if request.method == "GET":
        return render_template("admin/role_form.html", role=None, errors={})

    connection = get_connection()
    role_id = request.form.get("role_id", "")
    code = request.form.get("code", "").strip()
    description = request.form.get("description", "").strip() or None

    errors = validate_role(code=code, description=description or "")
    if not role_id.isdigit():
        errors["role_id"] = "Role ID must be a whole number."

    if errors:
        return (
            render_template(
                "admin/role_form.html",
                role={"role_id": role_id, "code": code, "description": description},
                errors=errors,
            ),
            400,
        )

    error = create_role(
        connection, role_id=int(role_id), code=code, description=description
    )
    if error:
        return (
            render_template(
                "admin/role_form.html",
                role={"role_id": role_id, "code": code, "description": description},
                errors={"code": error},
            ),
            409,
        )

    return redirect(url_for("admin.list_roles_view"))


@bp.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_role_view(role_id: int):
    connection = get_connection()
    role = get_role(connection, role_id)
    if role is None:
        return render_template("errors/error.html", code=404, name="Not Found"), 404

    if request.method == "GET":
        return render_template("admin/role_form.html", role=role, errors={})

    code = request.form.get("code", "").strip()
    description = request.form.get("description", "").strip() or None

    errors = validate_role(code=code, description=description or "")
    if errors:
        return (
            render_template(
                "admin/role_form.html",
                role={"role_id": role_id, "code": code, "description": description},
                errors=errors,
            ),
            400,
        )

    error = update_role(connection, role_id, code=code, description=description)
    if error:
        return (
            render_template(
                "admin/role_form.html",
                role={"role_id": role_id, "code": code, "description": description},
                errors={"code": error},
            ),
            409,
        )

    return redirect(url_for("admin.list_roles_view"))


@bp.post("/roles/<int:role_id>/delete")
@requires(CATALOG_WRITE)
def delete_role_view(role_id: int):
    connection = get_connection()
    deleted = delete_role(connection, role_id)

    if not deleted:
        page = parse_pagination(request.args.get("page"))
        roles, total = list_roles(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        return (
            render_template(
                "admin/roles.html",
                roles=roles,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=(
                    "Cannot delete this role: other records still reference it."
                ),
            ),
            409,
        )

    return redirect(url_for("admin.list_roles_view"))


# ---------- users ----------


@bp.get("/users")
@requires(USER_READ)
def list_users_view():
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    users, total = list_users(connection, search=search, page=page, per_page=_PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    return render_template(
        "admin/users.html",
        users=users,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/users/new", methods=["GET", "POST"])
@requires(USER_WRITE)
def create_user_view():
    connection = get_connection()
    roles = list_role_options(connection)

    if request.method == "GET":
        return render_template(
            "admin/user_form.html", user=None, errors={}, roles=roles
        )

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    role_code = request.form.get("role_code", "")

    errors = {}
    if not name:
        errors["name"] = "Name is required."
    if not email or "@" not in email:
        errors["email"] = "A valid email is required."
    if not password or len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."
    if not role_code:
        errors["role_code"] = "Role is required."

    if errors:
        return (
            render_template(
                "admin/user_form.html",
                user={"name": name, "email": email, "role_code": role_code},
                errors=errors,
                roles=roles,
            ),
            400,
        )

    try:
        create_user(
            connection, name=name, email=email, password=password, role_code=role_code
        )
        connection.commit()
    except (SingleAdministratorError, UnknownRoleError) as error:
        return (
            render_template(
                "admin/user_form.html",
                user={"name": name, "email": email, "role_code": role_code},
                errors={"role_code": str(error)},
                roles=roles,
            ),
            409,
        )
    except UniqueViolation:
        connection.rollback()
        return (
            render_template(
                "admin/user_form.html",
                user={"name": name, "email": email, "role_code": role_code},
                errors={"email": "A user with that email already exists."},
                roles=roles,
            ),
            409,
        )

    return redirect(url_for("admin.list_users_view"))


@bp.post("/users/<uuid:user_id>/deactivate")
@requires(USER_WRITE)
def deactivate_user_view(user_id):
    connection = get_connection()
    try:
        set_active(connection, user_id, is_active=False)
        connection.commit()
    except (SingleAdministratorError, UnknownUserError) as error:
        page = parse_pagination(request.args.get("page"))
        users, total = list_users(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        return (
            render_template(
                "admin/users.html",
                users=users,
                page=page,
                total_pages=total_pages,
                search="",
                action_error=str(error),
            ),
            409,
        )

    return redirect(url_for("admin.list_users_view"))


@bp.post("/users/<uuid:user_id>/activate")
@requires(USER_WRITE)
def activate_user_view(user_id):
    connection = get_connection()
    try:
        set_active(connection, user_id, is_active=True)
        connection.commit()
    except UnknownUserError:
        return render_template("errors/error.html", code=404, name="Not Found"), 404

    return redirect(url_for("admin.list_users_view"))
