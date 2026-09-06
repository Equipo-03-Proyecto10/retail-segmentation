"""Reading the audit log (F3-11).

The log is append-only and written by triggers. Nothing in this module writes
to it, and there is deliberately no function here that could: RNF-17 says the
application never updates or deletes an entry, and the way to keep that true is
to give the application no way to try.

What the service does is decide what a page of the log is — how it is filtered,
how it is paged, and which fields of an entry actually changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg import Connection

from web.db.audit import (
    AuditEntry,
    AuditEntryDetail,
    audited_entities,
    count_entries,
    get_entry,
    search_entries,
)

PAGE_SIZE = 25

# Never rendered, whatever a payload happens to contain. `fn_audit()` strips
# the hash before the entry is written, so this should never fire; it is here
# because "the view must not reintroduce it from elsewhere" is easier to keep
# true when the view cannot render it at all.
REDACTED_FIELDS = frozenset({"password_hash"})


@dataclass(frozen=True)
class Page:
    """One page of the log, and enough to render the pager."""

    entries: tuple[AuditEntry, ...]
    entities: tuple[str, ...]
    total: int
    page: int
    page_count: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


@dataclass(frozen=True)
class FieldChange:
    """One field of an audited row, before and after."""

    name: str
    before: str | None
    after: str | None
    changed: bool


@dataclass(frozen=True)
class EntryDetail:
    """One entry, with its payload flattened into comparable fields."""

    entry: AuditEntryDetail
    fields: tuple[FieldChange, ...]

    @property
    def changed_fields(self) -> int:
        return sum(1 for field in self.fields if field.changed)


def read_page(
    connection: Connection[Any],
    *,
    entity: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
) -> Page:
    """One page of entries, newest first, with the filter's options."""
    total = count_entries(
        connection, entity=entity, date_from=date_from, date_to=date_to
    )
    page_count = max(1, -(-total // PAGE_SIZE))  # ceiling division
    page = min(max(page, 1), page_count)

    entries = search_entries(
        connection,
        entity=entity,
        date_from=date_from,
        date_to=date_to,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )

    return Page(
        entries=tuple(entries),
        entities=tuple(audited_entities(connection)),
        total=total,
        page=page,
        page_count=page_count,
    )


def read_entry(connection: Connection[Any], audit_id: int) -> EntryDetail | None:
    """One entry with its fields paired up, or None when there is no such entry."""
    entry = get_entry(connection, audit_id)
    if entry is None:
        return None

    return EntryDetail(entry=entry, fields=tuple(_compare(entry)))


def _compare(entry: AuditEntryDetail) -> list[FieldChange]:
    """Pair the before and after payloads field by field.

    An INSERT has no before and a DELETE has no after, so every field of those
    counts as changed — which is true: the whole row arrived, or the whole row
    left.
    """
    before = _readable(entry.data_before)
    after = _readable(entry.data_after)

    return [
        FieldChange(
            name=name,
            before=before.get(name),
            after=after.get(name),
            changed=before.get(name) != after.get(name),
        )
        for name in sorted(before.keys() | after.keys())
    ]


def _readable(payload: dict[str, Any] | None) -> dict[str, str]:
    """Turn one jsonb payload into strings a template can print."""
    if payload is None:
        return {}

    return {
        name: value if isinstance(value, str) else json.dumps(value, default=str)
        for name, value in payload.items()
        if name not in REDACTED_FIELDS
    }
