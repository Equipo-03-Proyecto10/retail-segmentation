"""#295 and #317: every ILIKE search call site must escape the user's own %
and _ before wrapping the term, not read them back as wildcards.

Each function is called with a MagicMock connection and the parameter it
hands to cursor.execute is checked directly, rather than asserting on SQL
text, since the escaping happens in Python before the statement is built.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from web.db.categories import list_categories
from web.db.channels import list_channels
from web.db.customers import list_customers
from web.db.inventory import list_stock
from web.db.products import list_products
from web.db.roles import list_roles
from web.db.segments import list_segments
from web.db.stores import list_stores
from web.db.users import list_users


def _connection() -> MagicMock:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    return connection


def test_list_products_escapes_a_percent_sign() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_products(connection, search="%", page=1, per_page=20)

    pattern = cursor.execute.call_args_list[0].args[1][0]
    assert pattern == "%\\%%"


def test_list_customers_escapes_an_underscore() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_customers(connection, search="_", page=1, per_page=20)

    pattern = cursor.execute.call_args_list[0].args[1][0]
    assert pattern == "%\\_%"


def test_list_stock_escapes_a_percent_sign() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_stock(connection, store_id=None, search="%", page=1, per_page=20)

    parameters = cursor.execute.call_args_list[0].args[1]
    assert parameters["search"] == "%\\%%"


def test_list_segments_escapes_an_underscore() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_segments(connection, search="_", page=1, per_page=20)

    pattern = cursor.execute.call_args_list[0].args[1][0]
    assert pattern == "%\\_%"


def test_list_categories_escapes_a_percent_sign() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_categories(connection, search="%", page=1, per_page=20)

    pattern = cursor.execute.call_args_list[0].args[1][0]
    assert pattern == "%\\%%"


def test_list_channels_escapes_an_underscore() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_channels(connection, search="_", page=1, per_page=20)

    pattern = cursor.execute.call_args_list[0].args[1][0]
    assert pattern == "%\\_%"


def test_list_users_escapes_a_percent_sign() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_users(connection, search="%", page=1, per_page=20)

    pattern = cursor.execute.call_args_list[0].args[1][0]
    assert pattern == "%\\%%"


def test_list_stores_escapes_an_underscore() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_stores(connection, search="_", page=1, per_page=20)

    pattern = cursor.execute.call_args_list[0].args[1][0]
    assert pattern == "%\\_%"


def test_list_roles_escapes_a_percent_sign() -> None:
    connection = _connection()
    cursor = connection.cursor.return_value.__enter__.return_value

    list_roles(connection, search="%", page=1, per_page=20)

    pattern = cursor.execute.call_args_list[0].args[1][0]
    assert pattern == "%\\%%"
