"""Administrator routes: catalog CRUD.

Authorization comes from the F4-01 (#69) middleware: listings demand
`catalog.read`, which six roles hold, and every mutation demands
`catalog.write`, which only ADMIN holds. The gate is default-deny, so a view
added here without a declaration is refused rather than exposed.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask.typing import ResponseReturnValue

from web.db import get_connection
from web.db.categories import (
    get_category,
    list_all_categories,
    list_categories,
)
from web.db.channels import (
    get_channel,
    list_channels,
)
from web.db.products import (
    get_product,
    list_products,
)
from web.db.roles import get_role, list_roles
from web.db.stores import (
    get_store,
    list_stores,
)
from web.db.users import list_role_options, list_users
from web.middleware.authz import (
    CATALOG_READ,
    CATALOG_WRITE,
    USER_READ,
    USER_WRITE,
    requires,
)
from web.routes.pagination import redirect_last_page
from web.services.catalog import (
    INT_MAX,
    CatalogConflict,
    create_category,
    create_channel,
    create_product,
    create_role,
    create_store,
    delete_category,
    delete_channel,
    delete_product,
    delete_role,
    delete_store,
    parse_category_parent,
    parse_identifier,
    parse_pagination,
    update_category,
    update_channel,
    update_product,
    update_role,
    update_store,
    validate_category,
    validate_channel,
    validate_product,
    validate_role,
    validate_store,
)
from web.services.pagination import page_count
from web.services.uploads import (
    UploadRejected,
    delete_product_image,
    save_product_image,
)
from web.services.users import (
    MINIMUM_PASSWORD_LENGTH,
    DuplicateEmailError,
    SingleAdministratorError,
    UnknownRoleError,
    UnknownUserError,
    create_user,
    set_active,
    validate_user,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")

_PER_PAGE = 20


@bp.get("/catalogs")
@requires(CATALOG_READ)
def catalog_index() -> ResponseReturnValue:
    return render_template("admin/catalogs.html")


# ---------- stores ----------


@bp.get("/stores")
@requires(CATALOG_READ)
def list_stores_view() -> ResponseReturnValue:
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    stores, total = list_stores(
        connection, search=search, page=page, per_page=_PER_PAGE
    )
    total_pages = page_count(total, _PER_PAGE)

    if response := redirect_last_page(page, total_pages):
        return response
    return render_template(
        "admin/stores.html",
        stores=stores,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/stores/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_store_view() -> ResponseReturnValue:
    if request.method == "GET":
        return render_template("admin/store_form.html", store=None, errors={})

    connection = get_connection()
    store_id = request.form.get("store_id", "")
    name = request.form.get("name", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()

    errors = validate_store(name=name, city=city, state=state)
    store_key, key_errors = parse_identifier(
        store_id, field="store_id", label="Store ID"
    )
    errors.update(key_errors)

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

    try:
        create_store(connection, store_id=store_key, name=name, city=city, state=state)
    except CatalogConflict as error:
        return (
            render_template(
                "admin/store_form.html",
                store=request.form,
                errors={error.field: str(error)},
            ),
            409,
        )
    flash("Store created.", "success")
    return redirect(url_for("admin.list_stores_view"))


@bp.route("/stores/<int:store_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_store_view(store_id: int) -> ResponseReturnValue:
    connection = get_connection()
    store = get_store(connection, store_id)
    if store is None:
        abort(404)

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

    try:
        update_store(
            connection, store_id, name=name, city=city, state=state, is_active=is_active
        )
    except CatalogConflict as error:
        return (
            render_template(
                "admin/store_form.html",
                store=dict(request.form, store_id=store_id, is_active=is_active),
                errors={error.field: str(error)},
            ),
            409,
        )
    flash("Store updated.", "success")
    return redirect(url_for("admin.list_stores_view"))


@bp.post("/stores/<int:store_id>/delete")
@requires(CATALOG_WRITE)
def delete_store_view(store_id: int) -> ResponseReturnValue:
    connection = get_connection()
    record = get_store(connection, store_id)
    if record is None:
        abort(404)
    if request.form.get("confirm") != "yes":
        return render_template(
            "admin/confirm_delete.html",
            entity="store",
            record=record,
            list_endpoint="admin.list_stores_view",
        )
    try:
        delete_store(connection, store_id)

    except CatalogConflict as error:
        page = parse_pagination(request.args.get("page"))
        stores, total = list_stores(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = page_count(total, _PER_PAGE)
        return (
            render_template(
                "admin/stores.html",
                stores=stores,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=str(error),
            ),
            409,
        )

    flash("Store deleted.", "success")
    return redirect(url_for("admin.list_stores_view"))


# ---------- categories ----------


@bp.get("/categories")
@requires(CATALOG_READ)
def list_categories_view() -> ResponseReturnValue:
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    categories, total = list_categories(
        connection, search=search, page=page, per_page=_PER_PAGE
    )
    total_pages = page_count(total, _PER_PAGE)

    if response := redirect_last_page(page, total_pages):
        return response
    return render_template(
        "admin/categories.html",
        categories=categories,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/categories/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_category_view() -> ResponseReturnValue:
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
    parent_category_id, errors = parse_category_parent(parent_raw)
    errors.update(validate_category(name=name))
    category_key, key_errors = parse_identifier(
        category_id, field="category_id", label="Category ID"
    )
    errors.update(key_errors)

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

    try:
        create_category(
            connection,
            category_id=category_key,
            name=name,
            parent_category_id=parent_category_id,
        )
    except CatalogConflict as error:
        return (
            render_template(
                "admin/category_form.html",
                category={
                    "category_id": category_id,
                    "name": name,
                    "parent_category_id": parent_category_id,
                },
                errors={error.field: str(error)},
                all_categories=all_categories,
            ),
            409,
        )

    flash("Category created.", "success")
    return redirect(url_for("admin.list_categories_view"))


@bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_category_view(category_id: int) -> ResponseReturnValue:
    connection = get_connection()
    category = get_category(connection, category_id)
    if category is None:
        abort(404)

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
    parent_category_id, errors = parse_category_parent(parent_raw)
    errors.update(validate_category(name=name))
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

    try:
        update_category(
            connection, category_id, name=name, parent_category_id=parent_category_id
        )
    except CatalogConflict as error:
        return (
            render_template(
                "admin/category_form.html",
                category=dict(
                    request.form,
                    category_id=category_id,
                    parent_category_id=parent_category_id,
                ),
                errors={error.field: str(error)},
                all_categories=all_categories,
            ),
            409,
        )
    flash("Category updated.", "success")
    return redirect(url_for("admin.list_categories_view"))


@bp.post("/categories/<int:category_id>/delete")
@requires(CATALOG_WRITE)
def delete_category_view(category_id: int) -> ResponseReturnValue:
    connection = get_connection()
    record = get_category(connection, category_id)
    if record is None:
        abort(404)
    if request.form.get("confirm") != "yes":
        return render_template(
            "admin/confirm_delete.html",
            entity="category",
            record=record,
            list_endpoint="admin.list_categories_view",
        )
    try:
        delete_category(connection, category_id)

    except CatalogConflict as error:
        page = parse_pagination(request.args.get("page"))
        categories, total = list_categories(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = page_count(total, _PER_PAGE)
        return (
            render_template(
                "admin/categories.html",
                categories=categories,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=str(error),
            ),
            409,
        )

    flash("Category deleted.", "success")
    return redirect(url_for("admin.list_categories_view"))


# ---------- channels ----------


@bp.get("/channels")
@requires(CATALOG_READ)
def list_channels_view() -> ResponseReturnValue:
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    channels, total = list_channels(
        connection, search=search, page=page, per_page=_PER_PAGE
    )
    total_pages = page_count(total, _PER_PAGE)

    if response := redirect_last_page(page, total_pages):
        return response
    return render_template(
        "admin/channels.html",
        channels=channels,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/channels/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_channel_view() -> ResponseReturnValue:
    if request.method == "GET":
        return render_template("admin/channel_form.html", channel=None, errors={})

    connection = get_connection()
    channel_id = request.form.get("channel_id", "")
    name = request.form.get("name", "").strip()

    errors = validate_channel(name=name)
    channel_key, key_errors = parse_identifier(
        channel_id, field="channel_id", label="Channel ID"
    )
    errors.update(key_errors)

    if errors:
        return (
            render_template(
                "admin/channel_form.html",
                channel={"channel_id": channel_id, "name": name},
                errors=errors,
            ),
            400,
        )

    try:
        create_channel(connection, channel_id=channel_key, name=name)
    except CatalogConflict as error:
        return (
            render_template(
                "admin/channel_form.html",
                channel={"channel_id": channel_id, "name": name},
                errors={error.field: str(error)},
            ),
            409,
        )

    flash("Channel created.", "success")
    return redirect(url_for("admin.list_channels_view"))


@bp.route("/channels/<int:channel_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_channel_view(channel_id: int) -> ResponseReturnValue:
    connection = get_connection()
    channel = get_channel(connection, channel_id)
    if channel is None:
        abort(404)

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

    try:
        update_channel(connection, channel_id, name=name)
    except CatalogConflict as error:
        return (
            render_template(
                "admin/channel_form.html",
                channel={"channel_id": channel_id, "name": name},
                errors={error.field: str(error)},
            ),
            409,
        )

    flash("Channel updated.", "success")
    return redirect(url_for("admin.list_channels_view"))


@bp.post("/channels/<int:channel_id>/delete")
@requires(CATALOG_WRITE)
def delete_channel_view(channel_id: int) -> ResponseReturnValue:
    connection = get_connection()
    record = get_channel(connection, channel_id)
    if record is None:
        abort(404)
    if request.form.get("confirm") != "yes":
        return render_template(
            "admin/confirm_delete.html",
            entity="channel",
            record=record,
            list_endpoint="admin.list_channels_view",
        )
    try:
        delete_channel(connection, channel_id)

    except CatalogConflict as error:
        page = parse_pagination(request.args.get("page"))
        channels, total = list_channels(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = page_count(total, _PER_PAGE)
        return (
            render_template(
                "admin/channels.html",
                channels=channels,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=str(error),
            ),
            409,
        )

    flash("Channel deleted.", "success")
    return redirect(url_for("admin.list_channels_view"))


# ---------- products ----------


@bp.get("/products")
@requires(CATALOG_READ)
def list_products_view() -> ResponseReturnValue:
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    products, total = list_products(
        connection, search=search, page=page, per_page=_PER_PAGE
    )
    total_pages = page_count(total, _PER_PAGE)

    if response := redirect_last_page(page, total_pages):
        return response
    return render_template(
        "admin/products.html",
        products=products,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/products/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_product_view() -> ResponseReturnValue:
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
    image_file = request.files.get("image")

    validation = validate_product(
        sku=sku, name=name, category_id=category_id_raw, list_price=list_price_raw
    )
    errors = validation.errors
    product_key, key_errors = parse_identifier(
        product_id, field="product_id", label="Product ID", maximum=INT_MAX
    )
    errors.update(key_errors)

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

    saved = None
    config = current_app.config["APP_CONFIG"]
    if image_file and image_file.filename:
        try:
            saved = save_product_image(image_file, config)
        except UploadRejected as error:
            return (
                render_template(
                    "admin/product_form.html",
                    product=request.form,
                    errors={"image": str(error)},
                    all_categories=all_categories,
                ),
                400,
            )

    try:
        create_product(
            connection,
            product_id=product_key,
            sku=sku,
            name=name,
            category_id=int(category_id_raw),
            list_price=validation.price,
            image_path=saved.relative_path if saved else None,
        )
    except CatalogConflict as error:
        if saved:
            delete_product_image(saved.relative_path, config)
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
                errors={error.field: str(error)},
                all_categories=all_categories,
            ),
            409,
        )

    flash("Product created.", "success")
    return redirect(url_for("admin.list_products_view"))


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_product_view(product_id: int) -> ResponseReturnValue:
    connection = get_connection()
    product = get_product(connection, product_id)
    if product is None:
        abort(404)

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
    image_file = request.files.get("image")

    validation = validate_product(
        sku=sku, name=name, category_id=category_id_raw, list_price=list_price_raw
    )
    errors = validation.errors
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

    # Validate the image before writing anything, so a rejected file never
    # leaves the product's other fields half-updated.
    if image_file and image_file.filename:
        config = current_app.config["APP_CONFIG"]
        try:
            saved = save_product_image(image_file, config)
        except UploadRejected as error:
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
                        "image_path": product.image_path,
                    },
                    errors={"image": str(error)},
                    all_categories=all_categories,
                ),
                400,
            )

    try:
        update_product(
            connection,
            product_id,
            sku=sku,
            name=name,
            category_id=int(category_id_raw),
            list_price=validation.price,
            is_active=is_active,
            image_path=(
                saved.relative_path if image_file and image_file.filename else None
            ),
        )
    except CatalogConflict as error:
        if image_file and image_file.filename:
            delete_product_image(saved.relative_path, config)
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
                errors={error.field: str(error)},
                all_categories=all_categories,
            ),
            409,
        )

    if image_file and image_file.filename:
        old_path = product.image_path
        if old_path:
            delete_product_image(old_path, config)

    flash("Product updated.", "success")
    return redirect(url_for("admin.list_products_view"))


@bp.post("/products/<int:product_id>/delete")
@requires(CATALOG_WRITE)
def delete_product_view(product_id: int) -> ResponseReturnValue:
    connection = get_connection()
    record = get_product(connection, product_id)
    if record is None:
        abort(404)
    if request.form.get("confirm") != "yes":
        return render_template(
            "admin/confirm_delete.html",
            entity="product",
            record=record,
            list_endpoint="admin.list_products_view",
        )
    try:
        delete_product(connection, product_id)

    except CatalogConflict as error:
        page = parse_pagination(request.args.get("page"))
        products, total = list_products(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = page_count(total, _PER_PAGE)
        return (
            render_template(
                "admin/products.html",
                products=products,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=str(error),
            ),
            409,
        )

    if record.image_path:
        delete_product_image(record.image_path, current_app.config["APP_CONFIG"])
    flash("Product deleted.", "success")
    return redirect(url_for("admin.list_products_view"))


# ---------- roles ----------


@bp.get("/roles")
@requires(CATALOG_READ)
def list_roles_view() -> ResponseReturnValue:
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    roles, total = list_roles(connection, search=search, page=page, per_page=_PER_PAGE)
    total_pages = page_count(total, _PER_PAGE)

    if response := redirect_last_page(page, total_pages):
        return response
    return render_template(
        "admin/roles.html",
        roles=roles,
        minimum_password_length=MINIMUM_PASSWORD_LENGTH,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/roles/new", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def create_role_view() -> ResponseReturnValue:
    if request.method == "GET":
        return render_template("admin/role_form.html", role=None, errors={})

    connection = get_connection()
    role_id = request.form.get("role_id", "")
    code = request.form.get("code", "").strip()
    description = request.form.get("description", "").strip() or None

    errors = validate_role(code=code, description=description or "")
    role_key, key_errors = parse_identifier(role_id, field="role_id", label="Role ID")
    errors.update(key_errors)

    if errors:
        return (
            render_template(
                "admin/role_form.html",
                role={"role_id": role_id, "code": code, "description": description},
                errors=errors,
            ),
            400,
        )

    try:
        create_role(connection, role_id=role_key, code=code, description=description)
    except CatalogConflict as error:
        return (
            render_template(
                "admin/role_form.html",
                role={"role_id": role_id, "code": code, "description": description},
                errors={error.field: str(error)},
            ),
            409,
        )

    flash("Role created.", "success")
    return redirect(url_for("admin.list_roles_view"))


@bp.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@requires(CATALOG_WRITE)
def edit_role_view(role_id: int) -> ResponseReturnValue:
    connection = get_connection()
    role = get_role(connection, role_id)
    if role is None:
        abort(404)

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

    try:
        update_role(connection, role_id, code=code, description=description)
    except CatalogConflict as error:
        return (
            render_template(
                "admin/role_form.html",
                role={"role_id": role_id, "code": code, "description": description},
                errors={error.field: str(error)},
            ),
            409,
        )

    flash("Role updated.", "success")
    return redirect(url_for("admin.list_roles_view"))


@bp.post("/roles/<int:role_id>/delete")
@requires(CATALOG_WRITE)
def delete_role_view(role_id: int) -> ResponseReturnValue:
    connection = get_connection()
    record = get_role(connection, role_id)
    if record is None:
        abort(404)
    if request.form.get("confirm") != "yes":
        return render_template(
            "admin/confirm_delete.html",
            entity="role",
            record=record,
            list_endpoint="admin.list_roles_view",
        )
    try:
        delete_role(connection, role_id)

    except CatalogConflict as error:
        page = parse_pagination(request.args.get("page"))
        roles, total = list_roles(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = page_count(total, _PER_PAGE)
        return (
            render_template(
                "admin/roles.html",
                roles=roles,
                minimum_password_length=MINIMUM_PASSWORD_LENGTH,
                page=page,
                total_pages=total_pages,
                search="",
                delete_error=str(error),
            ),
            409,
        )

    flash("Role deleted.", "success")
    return redirect(url_for("admin.list_roles_view"))


# ---------- users ----------


@bp.get("/users")
@requires(USER_READ)
def list_users_view() -> ResponseReturnValue:
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None

    users, total = list_users(connection, search=search, page=page, per_page=_PER_PAGE)
    total_pages = page_count(total, _PER_PAGE)

    if response := redirect_last_page(page, total_pages):
        return response
    return render_template(
        "admin/users.html",
        users=users,
        page=page,
        total_pages=total_pages,
        search=search or "",
    )


@bp.route("/users/new", methods=["GET", "POST"])
@requires(USER_WRITE)
def create_user_view() -> ResponseReturnValue:
    connection = get_connection()
    roles = list_role_options(connection)

    if request.method == "GET":
        return render_template(
            "admin/user_form.html",
            user=None,
            errors={},
            roles=roles,
            minimum_password_length=MINIMUM_PASSWORD_LENGTH,
        )

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    role_code = request.form.get("role_code", "")

    errors = validate_user(
        name=name, email=email, password=password, role_code=role_code
    )

    if errors:
        return (
            render_template(
                "admin/user_form.html",
                user={"name": name, "email": email, "role_code": role_code},
                errors=errors,
                roles=roles,
                minimum_password_length=MINIMUM_PASSWORD_LENGTH,
            ),
            400,
        )

    try:
        create_user(
            connection, name=name, email=email, password=password, role_code=role_code
        )
    except (SingleAdministratorError, UnknownRoleError) as error:
        return (
            render_template(
                "admin/user_form.html",
                user={"name": name, "email": email, "role_code": role_code},
                errors={"role_code": str(error)},
                roles=roles,
                minimum_password_length=MINIMUM_PASSWORD_LENGTH,
            ),
            409,
        )
    except DuplicateEmailError as error:
        return (
            render_template(
                "admin/user_form.html",
                user={"name": name, "email": email, "role_code": role_code},
                errors={"email": str(error)},
                roles=roles,
                minimum_password_length=MINIMUM_PASSWORD_LENGTH,
            ),
            409,
        )

    flash("User created.", "success")
    return redirect(url_for("admin.list_users_view"))


@bp.post("/users/<uuid:user_id>/deactivate")
@requires(USER_WRITE)
def deactivate_user_view(user_id: UUID) -> ResponseReturnValue:
    connection = get_connection()
    try:
        set_active(connection, user_id, is_active=False)
    except (SingleAdministratorError, UnknownUserError) as error:
        page = parse_pagination(request.args.get("page"))
        users, total = list_users(
            connection, search=None, page=page, per_page=_PER_PAGE
        )
        total_pages = page_count(total, _PER_PAGE)
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

    flash("User deactivated.", "success")
    return redirect(url_for("admin.list_users_view"))


@bp.post("/users/<uuid:user_id>/activate")
@requires(USER_WRITE)
def activate_user_view(user_id: UUID) -> ResponseReturnValue:
    connection = get_connection()
    try:
        set_active(connection, user_id, is_active=True)
    except UnknownUserError:
        abort(404)

    flash("User activated.", "success")
    return redirect(url_for("admin.list_users_view"))


@bp.get("/products/image/<path:filename>")
@requires(CATALOG_READ)
def product_image(filename: str) -> ResponseReturnValue:
    config = current_app.config["APP_CONFIG"]
    response = send_from_directory(Path(config.upload_dir).resolve(), filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
