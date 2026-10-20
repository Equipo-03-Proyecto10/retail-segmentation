"""Public response and error types."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


class Money(str):
    """A decimal amount with an explicit two-place wire representation."""


def money(value: Decimal | int | str) -> Money:
    """Return a two-place money string without changing bare Decimal behavior."""

    amount = Decimal(value).quantize(Decimal("0.01"))
    return Money(f"{amount:.2f}")


@dataclass(slots=True)
class ApiError(Exception):
    """An error that is safe to expose through the common error envelope."""

    status: int
    code: str
    message: str
    details: list[dict[str, str]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Representation:
    root: str
    data: dict[str, Any]
    status: int = 200
    headers: dict[str, str] | None = None
    xsi_type: str | None = None


@dataclass(slots=True)
class Collection:
    root: str
    item: str
    key: str
    items: list[dict[str, Any]]
    status: int = 200
    headers: dict[str, str] | None = None


@dataclass(slots=True)
class Page:
    root: str
    item: str
    key: str
    items: list[dict[str, Any]]
    page: int
    total: int
    size: int | None = None
    status: int = 200
    headers: dict[str, str] | None = None
    # The JSON member holding page, size and total. Contracts say `page_info`;
    # one whose v1 froze another name (users: `page`) sets it. XML is always
    # common:PageInfo.
    info_key: str = "page_info"


@dataclass(slots=True)
class NoContent:
    headers: dict[str, str] | None = None
