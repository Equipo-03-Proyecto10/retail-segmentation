"""Validation for catalog forms (store, category, product, channel, role).

Each function returns a dict of field name -> error message. An empty dict
means the input is valid. Routes use this to mark exactly which field is
wrong, per HU-06's acceptance criteria — never a generic "invalid input".
"""

from __future__ import annotations


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

    return page if page >= 1 else 1


def validate_channel(*, name: str) -> dict[str, str]:
    """Validate channel fields."""
    errors: dict[str, str] = {}

    if not name or not name.strip():
        errors["name"] = "Name is required."
    elif len(name) > 60:
        errors["name"] = "Name must be 60 characters or fewer."

    return errors


def validate_product(*, sku: str, name: str, category_id: str, list_price: str) -> dict[str, str]:
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

    if not list_price:
        errors["list_price"] = "List price is required."
    else:
        try:
            price = float(list_price)
            if price < 0:
                errors["list_price"] = "List price cannot be negative."
        except ValueError:
            errors["list_price"] = "List price must be a number."

    return errors


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