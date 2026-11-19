"""Promotions and valid discounts on a phone sale (F25-05, RN-13, #836).

`unit_price` stays the price actually charged (RN-13). What it is compared with
is read in the sale's own transaction: the product's `list_price` from
`api_products.product_ref_v1`, the store's `max_discount_percent` from
`api_stores.store_directory_v2`, and the promotions of
`api_campaigns.promotion_window_v1`. Only `POST /v1/sales` applies this rule; a
sale import keeps the rules it states, so an imported history is judged as the
web judges it.

- A line charged below its list price names the promotion it relies on, or it is
  `discount_without_promotion`.
- A named promotion is always validated: it is a row of the view (a campaign
  that is `ACTIVE` or `FINISHED`) for the sale's store and channel, and the
  sale's own day (`occurred_at` in `SALES_TIMEZONE`) lies within its validity, or
  it is `promotion_not_applicable`. The day is the sale's, never the database's
  `CURRENT_DATE`, so a sale queued offline on a promotion's last day is judged
  on that day when it syncs, in the service's time zone.
- The discount allowed is the smaller of the promotion's percentage and the
  store's cap. The lowest accepted price is the list price times
  (1 - percent / 100), rounded half up to the cent; below it the line is
  `discount_above_promotion` or `discount_above_store_cap`, after whichever is
  the tighter limit.
- A line charged at or above its list price is accepted, with or without a
  promotion.

Each line gets its `list_price`, which `store.insert_sale` keeps in
`sales.sale_line_price`. The promotion is kept only on a line it discounted, so
a report of a promotion counts only the lines it priced.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg

from . import store
from .reversal import business_zone
from .salerules import line_error

CENT = Decimal("0.01")
HUNDRED = Decimal(100)


def lowest_price(list_price: Decimal, percent: Decimal) -> Decimal:
    """The lowest price a discount of `percent` allows, rounded half up."""
    return (list_price * (HUNDRED - percent) / HUNDRED).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def apply_prices(
    connection: psycopg.Connection,
    body: dict[str, Any],
    occurred_at: datetime,
    lines: list[dict[str, Any]],
) -> None:
    """Refuse a discount the rule does not allow, and give each line its list
    price. Called after `validate_sale_references`, so every product exists."""
    prices = store.list_prices(connection, [line["product_id"] for line in lines])
    named = sorted(
        {line["promotion_id"] for line in lines if line["promotion_id"] is not None}
    )
    directory = store.store_cap(connection, body["store_id"])
    if directory is None or not store.channel_exists(connection, body["channel_id"]):
        # The store or the channel is unknown; the header's foreign key answers
        # unknown_store or unknown_channel, so no pricing code is given first.
        for line in lines:
            line["list_price"] = prices[line["product_id"]]
        return
    cap = directory[0]
    promotions = store.promotion_windows(
        connection, named, body["store_id"], body["channel_id"]
    )
    day = occurred_at.astimezone(business_zone()).date()
    for index, line in enumerate(lines):
        list_price = prices[line["product_id"]]
        line["list_price"] = list_price
        promotion_id = line["promotion_id"]
        promotion = None
        if promotion_id is not None:
            promotion = promotions.get(promotion_id)
            if promotion is None or not promotion[1] <= day <= promotion[2]:
                raise line_error(
                    422,
                    "promotion_not_applicable",
                    "A promotion is not active for this store, channel and date.",
                    index,
                    "promotion_id",
                    "not_applicable",
                )
        charged = line["unit_price"]
        if charged >= list_price:
            # No discount was granted, so the line keeps no promotion.
            line["promotion_id"] = None
            continue
        if promotion is None:
            raise line_error(
                422,
                "discount_without_promotion",
                "A line is charged below its list price without naming a promotion.",
                index,
                "unit_price",
                "below_list_price",
            )
        percent = promotion[0]
        if cap is not None and cap < percent:
            if charged < lowest_price(list_price, cap):
                raise line_error(
                    422,
                    "discount_above_store_cap",
                    "A line is charged below what the store's commercial rule"
                    " allows.",
                    index,
                    "unit_price",
                    "below_store_cap",
                )
        elif charged < lowest_price(list_price, percent):
            raise line_error(
                422,
                "discount_above_promotion",
                "A line is charged below what its promotion allows.",
                index,
                "unit_price",
                "below_promotion",
            )
