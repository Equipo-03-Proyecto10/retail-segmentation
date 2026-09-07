"""PostgreSQL connection lifecycle and the data-access boundary.

Every SQL statement the application runs belongs in this package, and every one
of them is parameterized — no string interpolation into SQL, anywhere, ever.
Routes and services call functions from here; they never carry SQL themselves.

DDL is not here either: the schema is `sql/01_schema.sql` and nothing else
creates or alters a table.

Write functions leave transaction ownership to services. Only the lifecycle
helper in `transactions.py` commits or rolls back (ADR-0014).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import psycopg
from flask import Flask, current_app, g
from psycopg import Connection

from web.config import Config

DatabaseConnector = Callable[[str], Connection[Any]]
ConnectionInitializer = Callable[[Connection[Any]], None]

_CONNECTION_KEY = "database_connection"
_CONNECTOR_KEY = "database_connector"
_INITIALIZER_KEY = "database_connection_initializer"


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


def set_connection_initializer(app: Flask, initializer: ConnectionInitializer) -> None:
    """Run `initializer` on every connection this application opens.

    The seam exists for one thing: the audit triggers need to be told who is
    acting, and only the layers above know. Registering a callback keeps that
    knowledge out of here — this package holds SQL, not sessions.
    """
    app.extensions[_INITIALIZER_KEY] = initializer


def get_connection() -> Connection[Any]:
    """Return one connection per Flask application context."""
    connection = g.get(_CONNECTION_KEY)
    if connection is None or connection.closed:
        config: Config = current_app.config["APP_CONFIG"]
        connector = cast(DatabaseConnector, current_app.extensions[_CONNECTOR_KEY])
        connection = connector(config.database_url)
        setattr(g, _CONNECTION_KEY, connection)

        initializer = current_app.extensions.get(_INITIALIZER_KEY)
        if initializer is not None:
            initializer(connection)

    return cast(Connection[Any], connection)


def close_connection(_error: BaseException | None = None) -> None:
    """Close the current context's connection, if one was opened."""
    connection = g.pop(_CONNECTION_KEY, None)
    if connection is not None:
        connection.close()
