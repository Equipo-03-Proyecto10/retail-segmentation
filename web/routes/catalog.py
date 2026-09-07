"""The consultation module (F3-05).

A signed-in user browses the catalog, customers, stock and segments: list,
search, open. Read-only by construction — every route is GET and calls only
reading functions in web.db. Writing the catalog is the administrator's job
and stays in web/routes/admin.py behind `catalog.write`.

Gating (ADR-0010):
  * products, stock  -> catalog.read  (a catalog surface)
  * customers, segments -> segment.read (the substrate of segmentation; RF-13)
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request

from web.db import get_connection
from web.db.categories import get_category
from web.db.channels import get_channel
from web.db.customers import (
    get_customer,
    list_customers,
    list_customers_in_segment,
    list_interest_categories,
    list_preferred_channels,
)
from web.db.inventory import LOW_STOCK_THRESHOLD, list_stock
from web.db.products import get_product, list_products
from web.db.segments import get_segment, get_segment_rule, list_segments
from web.db.stores import list_all_stores
from web.middleware.authz import CATALOG_READ, SEGMENT_READ, requires
from web.services.catalog import parse_pagination

bp = Blueprint("catalog", __name__, url_prefix="/catalog")

_PER_PAGE = 20


def _page_args() -> tuple[int, str | None, str]:
    """The pagination and search parameters every listing route reads."""
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None
    return page, search, search or ""


def _total_pages(total: int) -> int:
    return max(1, (total + _PER_PAGE - 1) // _PER_PAGE)


@bp.get("/")
@requires(CATALOG_READ)
def index() -> str:
    """The hub: one link per section the visitor may reach."""
    return render_template("catalog/index.html")


# ---------- products ----------


@bp.get("/products")
@requires(CATALOG_READ)
def products() -> str:
    page, search, search_value = _page_args()
    rows, total = list_products(
        get_connection(), search=search, page=page, per_page=_PER_PAGE
    )
    return render_template(
        "catalog/products.html",
        products=rows,
        page=page,
        total_pages=_total_pages(total),
        total=total,
        search=search_value,
    )


@bp.get("/products/<int:product_id>")
@requires(CATALOG_READ)
def product_detail(product_id: int) -> str:
    connection = get_connection()
    product = get_product(connection, product_id)
    if product is None:
        abort(404)

    category = get_category(connection, product.category_id)
    return render_template(
        "catalog/product_detail.html", product=product, category=category
    )


# ---------- customers ----------


@bp.get("/customers")
@requires(SEGMENT_READ)
def customers() -> str:
    page, search, search_value = _page_args()
    rows, total = list_customers(
        get_connection(), search=search, page=page, per_page=_PER_PAGE
    )
    return render_template(
        "catalog/customers.html",
        customers=rows,
        page=page,
        total_pages=_total_pages(total),
        total=total,
        search=search_value,
    )


@bp.get("/customers/<uuid:customer_id>")
@requires(SEGMENT_READ)
def customer_detail(customer_id) -> str:
    connection = get_connection()
    customer = get_customer(connection, customer_id)
    if customer is None:
        abort(404)

    channel = get_channel(connection, customer.registration_channel_id)
    segment = (
        get_segment(connection, customer.current_segment_id)
        if customer.current_segment_id is not None
        else None
    )
    return render_template(
        "catalog/customer_detail.html",
        customer=customer,
        registration_channel=channel,
        segment=segment,
        interests=list_interest_categories(connection, customer.customer_id),
        preferred_channels=list_preferred_channels(connection, customer.customer_id),
    )


# ---------- stock ----------


@bp.get("/stock")
@requires(CATALOG_READ)
def stock() -> str:
    connection = get_connection()
    page, search, search_value = _page_args()

    store_raw = request.args.get("store", "").strip()
    store_id = int(store_raw) if store_raw.isdigit() else None

    rows, total = list_stock(
        connection,
        store_id=store_id,
        search=search,
        page=page,
        per_page=_PER_PAGE,
    )
    return render_template(
        "catalog/stock.html",
        rows=rows,
        stores=list_all_stores(connection),
        store_id=store_id,
        low_stock_threshold=LOW_STOCK_THRESHOLD,
        page=page,
        total_pages=_total_pages(total),
        total=total,
        search=search_value,
    )


# ---------- segments ----------


@bp.get("/segments")
@requires(SEGMENT_READ)
def segments() -> str:
    page, search, search_value = _page_args()
    rows, total = list_segments(
        get_connection(), search=search, page=page, per_page=_PER_PAGE
    )
    return render_template(
        "catalog/segments.html",
        segments=rows,
        page=page,
        total_pages=_total_pages(total),
        total=total,
        search=search_value,
    )


@bp.get("/segments/<int:segment_id>")
@requires(SEGMENT_READ)
def segment_detail(segment_id: int) -> str:
    connection = get_connection()
    segment = get_segment(connection, segment_id)
    if segment is None:
        abort(404)

    page = parse_pagination(request.args.get("page"))
    members, total = list_customers_in_segment(
        connection, segment_id, page=page, per_page=_PER_PAGE
    )
    return render_template(
        "catalog/segment_detail.html",
        segment=segment,
        rule=get_segment_rule(connection, segment.rule_id),
        members=members,
        page=page,
        total_pages=_total_pages(total),
        total=total,
    )
