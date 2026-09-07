"""Validation for catalog forms (store, category, product, channel, role).

Validators return field errors; product validation also returns the parsed
Decimal price so it is never parsed a second time. Write services own the
transaction and raise CatalogConflict for expected database refusals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import wraps

from psycopg import Connection
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from web.db import categories, channels, products, roles, stores
from web.db.transactions import atomic


@dataclass(frozen=True)
class ProductValidation:
    errors: dict[str, str]
    price: Decimal | None


def parse_category_parent(raw: str) -> tuple[int | None, dict[str, str]]:
    """Read the optional parent without letting malformed input reach SQL."""
    if not raw:
        return None, {}
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if not 0 <= value <= 32767:
        return None, {
            "parent_category_id": (
                "Parent category must be a whole number " "between 0 and 32767."
            )
        }
    return value, {}


def validate_store(*, name: str, city: str, state: str) -> dict[str, str]:
    """Validate store fields."""
    errors: dict[str, str] = {}

    if not name or not name.strip():
        errors["name"] = "Name is required."
    elif len(name) > 100:
        errors["name"] = "Name must be 100 characters or fewer."

    if not city or not city.strip():
        errors["city"] = "City is required."
    elif len(city) > 80:
        errors["city"] = "City must be 80 characters or fewer."

    if not state or not state.strip():
        errors["state"] = "State is required."
    elif len(state) > 80:
        errors["state"] = "State must be 80 characters or fewer."

    return errors


def validate_category(*, name: str) -> dict[str, str]:
    """Validate category fields."""
    errors: dict[str, str] = {}

    if not name or not name.strip():
        errors["name"] = "Name is required."
    elif len(name) > 80:
        errors["name"] = "Name must be 80 characters or fewer."

    return errors


def parse_pagination(page_param: str | None, per_page_default: int = 20) -> int:
    """Parse the page query parameter, defaulting to 1 on anything invalid."""
    try:
        page = int(page_param) if page_param else 1
    except ValueError:
        return 1

    return page if 1 <= page <= 2_147_483_647 else 1


def validate_channel(*, name: str) -> dict[str, str]:
    """Validate channel fields."""
    errors: dict[str, str] = {}

    if not name or not name.strip():
        errors["name"] = "Name is required."
    elif len(name) > 60:
        errors["name"] = "Name must be 60 characters or fewer."

    return errors


def validate_product(
    *, sku: str, name: str, category_id: str, list_price: str
) -> ProductValidation:
    """Validate product fields. category_id is checked only for shape here —
    whether it actually exists is a database concern (ForeignKeyViolation)."""
    errors: dict[str, str] = {}

    if not sku or not sku.strip():
        errors["sku"] = "SKU is required."
    elif len(sku) > 40:
        errors["sku"] = "SKU must be 40 characters or fewer."

    if not name or not name.strip():
        errors["name"] = "Name is required."
    elif len(name) > 150:
        errors["name"] = "Name must be 150 characters or fewer."

    if not category_id or not category_id.isdigit():
        errors["category_id"] = "Category is required."

    price = None
    if not list_price:
        errors["list_price"] = "List price is required."
    else:
        try:
            price = Decimal(list_price)
            if not price.is_finite():
                errors["list_price"] = "List price must be a finite number."
            elif price < 0:
                errors["list_price"] = "List price cannot be negative."
            elif price >= Decimal("99999999.995"):
                errors["list_price"] = "List price must round to 99999999.99 or less."
            else:
                price = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            errors["list_price"] = "List price must be a number."

    return ProductValidation(errors, None if "list_price" in errors else price)


def validate_role(*, code: str, description: str) -> dict[str, str]:
    """Validate role fields."""
    errors: dict[str, str] = {}

    if not code or not code.strip():
        errors["code"] = "Code is required."
    elif len(code) > 40:
        errors["code"] = "Code must be 40 characters or fewer."

    if description and len(description) > 160:
        errors["description"] = "Description must be 160 characters or fewer."

    return errors


class CatalogConflict(Exception):
    """A catalog write refused with a message for a specific field."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def _catalog_write(entity: str, operation: str):
    """Share transaction ownership and failure translation across catalogs."""

    def decorate(function):
        write = atomic(function)

        @wraps(function)
        def execute(connection, *args, **kwargs):
            try:
                result = write(connection, *args, **kwargs)
            except (UniqueViolation, ForeignKeyViolation) as error:
                logging.getLogger(__name__).info(
                    "catalog_refused entity=%s operation=%s reason=%s",
                    entity,
                    operation,
                    type(error).__name__,
                )
                if isinstance(error, ForeignKeyViolation):
                    if operation == "delete":
                        raise CatalogConflict(
                            "",
                            f"Cannot delete this {entity}: "
                            "other records still reference it.",
                        ) from error
                    field = (
                        "parent_category_id" if entity == "category" else "category_id"
                    )
                    raise CatalogConflict(
                        field, "That category does not exist."
                    ) from error
                field = {"role": "code", "product": "sku"}.get(entity, "name")
                constraint = error.diag.constraint_name
                if constraint and constraint.endswith("_pkey"):
                    field = f"{entity}_id"
                description = {"role": "code", "product": "SKU"}.get(entity, "name")
                if operation == "create":
                    description = f"ID or {description}"
                raise CatalogConflict(
                    field, f"A {entity} with that {description} already exists."
                ) from error
            logging.getLogger(__name__).info(
                "catalog_succeeded entity=%s operation=%s",
                entity,
                operation,
            )
            return result

        return execute

    return decorate


@_catalog_write("role", "create")
def create_role(
    connection: Connection, *, role_id: int, code: str, description: str | None
) -> None:
    """Create a role, translating constraint refusals."""
    roles.create_role(connection, role_id=role_id, code=code, description=description)


@_catalog_write("role", "update")
def update_role(
    connection: Connection, role_id: int, *, code: str, description: str | None
) -> None:
    """Update a role, translating constraint refusals."""
    roles.update_role(connection, role_id, code=code, description=description)


@_catalog_write("role", "delete")
def delete_role(connection: Connection, role_id: int) -> None:
    """Delete a role, translating constraint refusals."""
    roles.delete_role(connection, role_id)


@_catalog_write("product", "create")
def create_product(
    connection: Connection,
    *,
    product_id: int,
    sku: str,
    name: str,
    category_id: int,
    list_price: Decimal,
    image_path: str | None = None,
) -> None:
    """Create a product, translating constraint refusals."""
    products.create_product(
        connection,
        product_id=product_id,
        sku=sku,
        name=name,
        category_id=category_id,
        list_price=list_price,
        image_path=image_path,
    )


@_catalog_write("product", "update")
def update_product(
    connection: Connection,
    product_id: int,
    *,
    sku: str,
    name: str,
    category_id: int,
    list_price: Decimal,
    is_active: bool,
    image_path: str | None = None,
) -> None:
    """Update a product, translating constraint refusals."""
    products.update_product(
        connection,
        product_id,
        sku=sku,
        name=name,
        category_id=category_id,
        list_price=list_price,
        is_active=is_active,
        image_path=image_path,
    )


@_catalog_write("product", "delete")
def delete_product(connection: Connection, product_id: int) -> None:
    """Delete a product, translating constraint refusals."""
    products.delete_product(connection, product_id)


@_catalog_write("channel", "create")
def create_channel(connection: Connection, *, channel_id: int, name: str) -> None:
    """Create a channel, translating constraint refusals."""
    channels.create_channel(connection, channel_id=channel_id, name=name)


@_catalog_write("channel", "update")
def update_channel(connection: Connection, channel_id: int, *, name: str) -> None:
    """Update a channel, translating constraint refusals."""
    channels.update_channel(connection, channel_id, name=name)


@_catalog_write("channel", "delete")
def delete_channel(connection: Connection, channel_id: int) -> None:
    """Delete a channel, translating constraint refusals."""
    channels.delete_channel(connection, channel_id)


@_catalog_write("category", "create")
def create_category(
    connection: Connection,
    *,
    category_id: int,
    name: str,
    parent_category_id: int | None,
) -> None:
    """Create a category, translating constraint refusals."""
    categories.create_category(
        connection,
        category_id=category_id,
        name=name,
        parent_category_id=parent_category_id,
    )


@_catalog_write("category", "update")
def update_category(
    connection: Connection,
    category_id: int,
    *,
    name: str,
    parent_category_id: int | None,
) -> None:
    """Update a category, translating constraint refusals."""
    categories.update_category(
        connection, category_id, name=name, parent_category_id=parent_category_id
    )


@_catalog_write("category", "delete")
def delete_category(connection: Connection, category_id: int) -> None:
    """Delete a category, translating constraint refusals."""
    categories.delete_category(connection, category_id)


@_catalog_write("store", "create")
def create_store(
    connection: Connection, *, store_id: int, name: str, city: str, state: str
) -> None:
    """Create a store, translating constraint refusals."""
    stores.create_store(
        connection, store_id=store_id, name=name, city=city, state=state
    )


@_catalog_write("store", "update")
def update_store(
    connection: Connection,
    store_id: int,
    *,
    name: str,
    city: str,
    state: str,
    is_active: bool,
) -> None:
    """Update a store, translating constraint refusals."""
    stores.update_store(
        connection, store_id, name=name, city=city, state=state, is_active=is_active
    )


@_catalog_write("store", "delete")
def delete_store(connection: Connection, store_id: int) -> None:
    """Delete a store, translating constraint refusals."""
    stores.delete_store(connection, store_id)
