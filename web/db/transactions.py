"""Transaction lifecycle for service units of work (ADR-0014)."""

import logging
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import Concatenate, ParamSpec, TypeVar

from psycopg import Connection

_P = ParamSpec("_P")
_R = TypeVar("_R")
_owners: ContextVar[tuple[Connection, ...]] = ContextVar(
    "transaction_owners", default=()
)


def atomic(
    function: Callable[Concatenate[Connection, _P], _R]
) -> Callable[Concatenate[Connection, _P], _R]:
    """Commit one service operation; nested services join the same owner."""

    @wraps(function)
    def execute(connection: Connection, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        owners = _owners.get()
        if any(owner is connection for owner in owners):
            return function(connection, *args, **kwargs)
        token = _owners.set((*owners, connection))
        try:
            result = function(connection, *args, **kwargs)
            connection.commit()
            logging.getLogger(__name__).info(
                "write_succeeded operation=%s.%s",
                function.__module__,
                function.__name__,
            )
            return result
        except BaseException as error:
            connection.rollback()
            logging.getLogger(__name__).info(
                "write_refused operation=%s.%s reason=%s",
                function.__module__,
                function.__name__,
                type(error).__name__,
            )
            raise
        finally:
            _owners.reset(token)

    return execute
