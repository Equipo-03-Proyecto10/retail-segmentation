"""One reading of the numbers and dates that arrive in a query string or a form.

`str.isdigit()` and `int()` accept Unicode digits, so `²` is not a number to
PostgreSQL but is one to `isdigit()`, and `٢٩` is read as 29. A value past the
column's range is a `NumericValueOutOfRange` from the database, and a date at
the edge of the calendar overflows the arithmetic done on it afterwards. Both
surfaced as 500s (#349). Every route reads these values here instead, so the
answer to "is this a valid id" is the same everywhere: ASCII digits, in range.

A function returns None for a value that cannot be one and never raises. What
an empty or missing value means (no filter, or a default) is each route's own
decision, so callers test for emptiness first.
"""

from __future__ import annotations

import re
from datetime import date

# The largest value an INTEGER column holds. Every identifier the interface
# accepts is an INTEGER, SMALLINT or BIGINT key, so this bound refuses what the
# widest one the routes compare against would reject.
INT_MAX = 2**31 - 1
# The same for a BIGINT key, such as segmentation_run.run_id.
BIGINT_MAX = 2**63 - 1

# A calendar the data can meaningfully be in. Date arithmetic on the ends of
# `datetime.date` (a window before 0001-01-01) overflows, so the ends are not
# offered; this is wide enough for any sale, campaign or audit entry.
DATE_MIN = date(1900, 1, 1)
DATE_MAX = date(2100, 12, 31)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def whole_number(raw: str | None, maximum: int = INT_MAX) -> int | None:
    """A non-negative whole number written in ASCII digits, at most `maximum`."""
    if raw is None or not (raw.isascii() and raw.isdigit()):
        return None
    value = int(raw)
    return value if value <= maximum else None


def iso_date(raw: str | None) -> date | None:
    """A `YYYY-MM-DD` date inside [DATE_MIN, DATE_MAX].

    Stricter than `date.fromisoformat`, which also takes `20260101` and ISO week
    dates: the interface offers one format, so it accepts one.
    """
    if raw is None or _ISO_DATE.fullmatch(raw) is None:
        return None
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return None
    return value if DATE_MIN <= value <= DATE_MAX else None


def page_number(raw: str | None) -> int:
    """A page number, 1 for anything that is not a usable one."""
    value = whole_number(raw)
    return value if value else 1
