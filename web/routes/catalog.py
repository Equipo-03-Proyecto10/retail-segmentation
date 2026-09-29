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

from uuid import UUID

from flask import Blueprint, Response, abort, make_response, render_template, request

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
from web.db.segments import (
    get_current_assignment,
    get_label_name,
    get_segment,
    get_segment_rule,
    list_segments,
)
from web.db.stores import list_all_stores
from web.middleware.authz import CATALOG_READ, SEGMENT_READ, requires
from web.parsing import whole_number
from web.routes.pagination import redirect_last_page
from web.services.catalog import SMALLINT_MAX, parse_pagination
from web.services.consumption_profile import UnknownCustomer, build_profile
from web.services.pagination import page_count
from web.services.recommendations import recommend
from web.services.segmentation import (
    MAX_WINDOW_DAYS,
    MIN_WINDOW_DAYS,
    InvalidWindow,
    parse_window,
)

bp = Blueprint("catalog", __name__, url_prefix="/catalog")

_PER_PAGE = 20


def _page_args() -> tuple[int, str | None, str]:
    """The pagination and search parameters every listing route reads."""
    page = parse_pagination(request.args.get("page"))
    search = request.args.get("q", "").strip() or None
    return page, search, search or ""


@bp.get("/")
@requires(CATALOG_READ)
def index() -> str:
    """The hub: one link per section the visitor may reach."""
    return render_template("catalog/index.html")


# ---------- products ----------


@bp.get("/products")
@requires(CATALOG_READ)
def products() -> str | Response:
    page, search, search_value = _page_args()
    rows, total = list_products(
        get_connection(), search=search, page=page, per_page=_PER_PAGE
    )
    if response := redirect_last_page(page, page_count(total, _PER_PAGE)):
        return response
    return render_template(
        "catalog/products.html",
        products=rows,
        page=page,
        total_pages=page_count(total, _PER_PAGE),
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
def customers() -> str | Response:
    page, search, search_value = _page_args()
    rows, total = list_customers(
        get_connection(), search=search, page=page, per_page=_PER_PAGE
    )
    if response := redirect_last_page(page, page_count(total, _PER_PAGE)):
        return response
    return render_template(
        "catalog/customers.html",
        customers=rows,
        page=page,
        total_pages=page_count(total, _PER_PAGE),
        total=total,
        search=search_value,
    )


@bp.get("/customers/<uuid:customer_id>")
@requires(SEGMENT_READ)
def customer_detail(customer_id: UUID) -> str:
    connection = get_connection()
    customer = get_customer(connection, customer_id)
    if customer is None:
        abort(404)

    channel = get_channel(connection, customer.registration_channel_id)
    # The label is what every method writes (ADR-0018), so it is what the page
    # shows; a segment exists only for a rule-based assignment (#272).
    assignment = get_current_assignment(connection, customer.customer_id)
    label = (
        get_label_name(connection, assignment.label_code)
        if assignment is not None and assignment.label_code is not None
        else None
    )
    segment = (
        get_segment(connection, assignment.segment_id)
        if assignment is not None and assignment.segment_id is not None
        else None
    )
    return render_template(
        "catalog/customer_detail.html",
        customer=customer,
        registration_channel=channel,
        label=label,
        segment=segment,
        interests=list_interest_categories(connection, customer.customer_id),
        preferred_channels=list_preferred_channels(connection, customer.customer_id),
    )


@bp.get("/customers/<uuid:customer_id>/profile")
@requires(SEGMENT_READ)
def customer_profile(customer_id: UUID) -> str | tuple[str, int]:
    """One customer's consumption profile (F8-04).

    Gated on segment.read like the rest of the customer surface (ADR-0010,
    F4-07's permission map). The window is a query parameter read with the same
    parser the segment run uses, so the two refuse the same inputs. A window
    the parser refuses answers 400 and explains itself; the profile is not
    computed at all.
    """
    connection = get_connection()
    raw_window = request.args.get("window")
    try:
        window_days = parse_window(raw_window)
    except InvalidWindow as refusal:
        customer = get_customer(connection, customer_id)
        if customer is None:
            abort(404)
        return (
            render_template(
                "catalog/customer_profile.html",
                customer_id=customer.customer_id,
                customer_name=customer.name,
                profile=None,
                window=raw_window,
                error=str(refusal),
                min_window=MIN_WINDOW_DAYS,
                max_window=MAX_WINDOW_DAYS,
            ),
            400,
        )

    try:
        profile = build_profile(connection, customer_id, window_days=window_days)
    except UnknownCustomer:
        abort(404)

    return render_template(
        "catalog/customer_profile.html",
        customer_id=profile.customer_id,
        customer_name=profile.customer_name,
        profile=profile,
        window=window_days,
        error=None,
        min_window=MIN_WINDOW_DAYS,
        max_window=MAX_WINDOW_DAYS,
    )


@bp.get("/customers/<uuid:customer_id>/recommendations")
@requires(SEGMENT_READ)
def customer_recommendations(customer_id: UUID) -> tuple[Response, int]:
    """One customer's product recommendations, with the reason for each (F10-02).

    Gated on segment.read like the rest of the customer surface (ADR-0010, F4-07's
    permission map). Computed afresh on every request, so a product whose stock
    reaches zero is gone on the next reload, and the response is marked `no-store`
    so a browser does not show an older list. The window is read with the parser
    the segment run uses, and a customer with nothing to recommend is told why.
    """
    connection = get_connection()
    raw_window = request.args.get("window")

    def page(status_code: int, window: object, **context) -> tuple[Response, int]:
        response = make_response(
            render_template(
                "catalog/customer_recommendations.html",
                customer_id=customer_id,
                window=window,
                min_window=MIN_WINDOW_DAYS,
                max_window=MAX_WINDOW_DAYS,
                **context,
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response, status_code

    def refused(refusal: InvalidWindow) -> tuple[Response, int]:
        customer = get_customer(connection, customer_id)
        if customer is None:
            abort(404)
        return page(
            400,
            raw_window,
            customer_name=customer.name,
            result=None,
            error=str(refusal),
        )

    try:
        window_days = parse_window(raw_window)
    except InvalidWindow as refusal:
        return refused(refusal)

    try:
        result = recommend(connection, customer_id, window_days=window_days)
    except UnknownCustomer:
        abort(404)

    return page(
        200,
        window_days,
        customer_name=result.customer_name,
        result=result,
        error=None,
    )


# ---------- stock ----------


@bp.get("/stock")
@requires(CATALOG_READ)
def stock() -> str | Response:
    connection = get_connection()
    page, search, search_value = _page_args()

    store_raw = request.args.get("store", "").strip()
    store_id = whole_number(store_raw, SMALLINT_MAX) if store_raw else None
    if store_raw and store_id is None:
        abort(400)

    rows, total = list_stock(
        connection,
        store_id=store_id,
        search=search,
        page=page,
        per_page=_PER_PAGE,
    )
    if response := redirect_last_page(page, page_count(total, _PER_PAGE)):
        return response
    return render_template(
        "catalog/stock.html",
        rows=rows,
        stores=list_all_stores(connection),
        store_id=store_id,
        low_stock_threshold=LOW_STOCK_THRESHOLD,
        page=page,
        total_pages=page_count(total, _PER_PAGE),
        total=total,
        search=search_value,
    )


# ---------- segments ----------


@bp.get("/segments")
@requires(SEGMENT_READ)
def segments() -> str | Response:
    page, search, search_value = _page_args()
    rows, total = list_segments(
        get_connection(), search=search, page=page, per_page=_PER_PAGE
    )
    if response := redirect_last_page(page, page_count(total, _PER_PAGE)):
        return response
    return render_template(
        "catalog/segments.html",
        segments=rows,
        page=page,
        total_pages=page_count(total, _PER_PAGE),
        total=total,
        search=search_value,
    )


@bp.get("/segments/<int:segment_id>")
@requires(SEGMENT_READ)
def segment_detail(segment_id: int) -> str | Response:
    connection = get_connection()
    segment = get_segment(connection, segment_id)
    if segment is None:
        abort(404)

    page = parse_pagination(request.args.get("page"))
    members, total = list_customers_in_segment(
        connection, segment_id, page=page, per_page=_PER_PAGE
    )
    if response := redirect_last_page(page, page_count(total, _PER_PAGE)):
        return response
    return render_template(
        "catalog/segment_detail.html",
        segment=segment,
        rule=get_segment_rule(connection, segment.rule_id),
        members=members,
        page=page,
        total_pages=page_count(total, _PER_PAGE),
        total=total,
    )
