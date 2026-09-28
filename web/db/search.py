"""One helper for every ILIKE search across web/db (#295).

`ILIKE` treats `%` and `_` in the search term as wildcards and `\\` as its
escape character (PostgreSQL's default). Building the pattern as
`f"%{search}%"` let a user's own `%` or `_` change what matched instead of
being matched literally; escaping the term before wrapping it fixes that for
every call site at once.
"""

from __future__ import annotations


def ilike_pattern(search: str) -> str:
    """Wrap `search` for a `%s ILIKE %s` parameter, matching it literally."""
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
