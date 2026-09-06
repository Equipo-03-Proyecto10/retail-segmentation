"""PostgreSQL connection lifecycle and the data-access boundary.

Every SQL statement the application runs belongs in this package, and every one
of them is parameterized — no string interpolation into SQL, anywhere, ever.
Routes and services call functions from here; they never carry SQL themselves.

DDL is not here either: the schema is `sql/01_schema.sql` and nothing else
creates or alters a table.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import psycopg
from flask import Flask, current_app, g
from psycopg import Connection

from web.config import Config

DatabaseConnector = Callable[[str], Connection[Any]]

_CONNECTION_KEY = "database_connection"
_CONNECTOR_KEY = "database_connector"


def connect(database_url: str) -> Connection[Any]:
    """Open a PostgreSQL connection using the configured URL."""
    return psycopg.connect(database_url)


def init_app(app: Flask, connector: DatabaseConnector | None = None) -> None:
    """Verify PostgreSQL at startup and register the request lifecycle."""
    connector = connect if connector is None else connector
    config: Config = app.config["APP_CONFIG"]

    # Authentication and network errors surface while the process starts,
    # rather than on the first visitor's request. The probe is not retained:
    # request work receives its own connection through `get_connection`.
    startup_connection = connector(config.database_url)
    startup_connection.close()

    app.extensions[_CONNECTOR_KEY] = connector
    app.teardown_appcontext(close_connection)


def get_connection() -> Connection[Any]:
    """Return one connection per Flask application context."""
    connection = g.get(_CONNECTION_KEY)
    if connection is None or connection.closed:
        config: Config = current_app.config["APP_CONFIG"]
        connector = cast(DatabaseConnector, current_app.extensions[_CONNECTOR_KEY])
        connection = connector(config.database_url)
        setattr(g, _CONNECTION_KEY, connection)

    return cast(Connection[Any], connection)


def close_connection(_error: BaseException | None = None) -> None:
    """Close the current context's connection, if one was opened."""
    connection = g.pop(_CONNECTION_KEY, None)
    if connection is not None:
        connection.close()
