"""The reversal rules of RN-70 (ADR-0062): validation, time limits, canonical hash.

Everything here is pure: no database and no request. The transaction itself is
in `store.record_reversal`, and the routes are in `service.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mosaiq_kit import ApiError

MAX_QUANTITY = 2_147_483_647
MAX_ID = 9_223_372_036_854_775_807
# ADR-0062: "the sale's calendar day" and "within 30 days" are configuration
# defaults, not requirements.
DEFAULT_CANCELLATION_DAYS = 0
DEFAULT_RETURN_DAYS = 30
FLOOR_ROLE = "SALES_ASSOCIATE"


def _error(status: int, code: str, message: str, field: str, detail: str) -> ApiError:
    return ApiError(status, code, message, [{"field": field, "code": detail}])


def forbidden_role() -> ApiError:
    return ApiError(
        403,
        "forbidden_role",
        "The authenticated role cannot perform this operation.",
    )


def _blank(value: Any) -> bool:
    return not isinstance(value, str) or value.strip() == ""


def validate(body: dict[str, Any]) -> dict[str, Any]:
    """Check what the body alone can say and return it normalized.

    The kit has already enforced the schema (types, `additionalProperties`,
    the kind's enum, 200-character reasons). The rest of the contract's 422 and
    409 codes that need no database are decided here.
    """

    sale_id = body["sale_id"]
    if (
        isinstance(sale_id, bool)
        or not isinstance(sale_id, int)
        or not 1 <= sale_id <= MAX_ID
    ):
        raise _error(
            422, "unknown_sale", "The sale does not exist.", "sale_id", "unknown"
        )
    kind = body["kind"]
    lines = body.get("lines")
    if kind == "CANCELLATION":
        if lines:
            raise _error(
                422,
                "unexpected_lines",
                "A cancellation reverses the whole sale and takes no lines.",
                "lines",
                "not_allowed",
            )
        if _blank(body.get("reason")):
            raise _error(
                422,
                "reason_required",
                "A reversal needs a reason.",
                "reason",
                "required",
            )
        return {
            "sale_id": sale_id,
            "kind": kind,
            "reason": body["reason"].strip(),
            "lines": [],
        }
    if not lines:
        raise _error(
            422,
            "no_return_lines",
            "A return needs at least one line.",
            "lines",
            "empty",
        )
    if body.get("reason") is not None:
        raise _error(
            422,
            "invalid_body",
            "A return states its reason on each line.",
            "reason",
            "not_allowed",
        )
    seen: set[int] = set()
    normalized = []
    for index, line in enumerate(lines):
        product_id = line["product_id"]
        if product_id in seen:
            raise _error(
                409,
                "duplicate_product_line",
                "A product appears on more than one line.",
                f"lines[{index}].product_id",
                "duplicate",
            )
        seen.add(product_id)
        quantity = line["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise _error(
                422,
                "invalid_quantity",
                "A quantity is not a positive whole number.",
                f"lines[{index}].quantity",
                "not_positive",
            )
        if quantity > MAX_QUANTITY:
            raise _error(
                422,
                "invalid_quantity",
                "A quantity is larger than a sale line can hold.",
                f"lines[{index}].quantity",
                "out_of_range",
            )
        if _blank(line.get("reason")):
            raise _error(
                422,
                "reason_required",
                "A reversal needs a reason.",
                f"lines[{index}].reason",
                "required",
            )
        normalized.append(
            {
                "product_id": product_id,
                "quantity": quantity,
                "reason": line["reason"].strip(),
                "resellable": line.get("resellable", True) is not False,
            }
        )
    return {
        "sale_id": sale_id,
        "kind": kind,
        "reason": None,
        "lines": normalized,
    }


def canonical(request: dict[str, Any], sub: str) -> bytes:
    """The request hash a retry is compared with.

    It covers the caller's `sub`, `sale_id`, `kind`, the cancellation reason and
    the lines sorted by `product_id` with their quantity, reason and
    `resellable`, so a JSON retry and an XML retry hash the same.
    """

    value = {
        "sub": sub,
        "sale_id": request["sale_id"],
        "kind": request["kind"],
        "reason": request["reason"],
        "lines": [
            {
                "product_id": line["product_id"],
                "quantity": line["quantity"],
                "reason": line["reason"],
                "resellable": line["resellable"],
            }
            for line in sorted(request["lines"], key=lambda item: item["product_id"])
        ],
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).digest()


def _days(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value >= 0 else default


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("SALES_TIMEZONE", "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def window_open(kind: str, occurred_at: datetime, now: datetime | None = None) -> bool:
    """Whether the sale is still inside the time allowed for this kind.

    A cancellation is accepted for `SALES_CANCELLATION_WINDOW_DAYS` calendar
    days after the sale's own day, in `SALES_TIMEZONE` (default 0, so the
    sale's calendar day). A return is accepted for `SALES_RETURN_WINDOW_DAYS`
    days after `occurred_at` (default 30).
    """

    now = now or datetime.now(UTC)
    if kind == "CANCELLATION":
        zone = _zone()
        last_day = occurred_at.astimezone(zone).date() + timedelta(
            days=_days("SALES_CANCELLATION_WINDOW_DAYS", DEFAULT_CANCELLATION_DAYS)
        )
        return now.astimezone(zone).date() <= last_day
    return now - occurred_at <= timedelta(
        days=_days("SALES_RETURN_WINDOW_DAYS", DEFAULT_RETURN_DAYS)
    )
