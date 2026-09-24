"""Segment migration between two runs (F7-04) — the classification itself.

ADR-0018 is explicit about the failure mode this exists to avoid: comparing
raw K-means cluster ids across two runs reports 100% migration on every run,
silently, because a cluster's arbitrary id has no meaning outside the fit
that produced it. The comparison here is on the stable label code only —
never on segment_id, and never on segmentation_run.method. Two runs from
different methods (RFM_RULES and KMEANS) compare cleanly because the label
vocabulary is the one contract both methods write to.

ADR-0003: this module is pure — no SQL, no Connection. It classifies over
plain dicts a caller (web/routes, or another service) builds from
web.db.segments.list_run_labels and get_run_at. That split is what lets the
tests here pass plain dicts instead of faking a cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MigrationCategory(Enum):
    """Every customer between two runs falls into exactly one of these."""

    UNCHANGED = "unchanged"
    MOVED = "moved"
    NEWLY_ASSIGNED = "newly_assigned"
    NEWLY_UNASSIGNED = "newly_unassigned"
    ABSENT_FROM_EARLIER = "absent_from_earlier"
    ABSENT_FROM_LATER = "absent_from_later"


class Direction(Enum):
    """Where a MOVED customer went, by segment_label.ordinal_position
    (ADR-0018's declared best-to-worst business order). Only meaningful for
    MOVED; every other category leaves this None."""

    IMPROVED = "improved"
    DECLINED = "declined"


@dataclass(frozen=True)
class CustomerMigration:
    """One customer's classification between an earlier and a later run.
    label_before/label_after are None exactly when the customer was
    unassigned in that run, or absent from it entirely — category
    disambiguates which. direction is set only when category is MOVED."""

    customer_id: Any
    label_before: str | None
    label_after: str | None
    category: MigrationCategory
    direction: Direction | None = None


class UnknownRun(ValueError):
    """One of the two run ids does not exist."""


def _classify(
    label_before: str | None,
    label_after: str | None,
    *,
    in_run_a: bool,
    in_run_b: bool,
    ordinals: dict[str, int],
) -> tuple[MigrationCategory, Direction | None]:
    """The classification rule, isolated as a pure function of two labels
    and two presence flags — no database, no run ids."""
    if not in_run_a:
        return MigrationCategory.ABSENT_FROM_EARLIER, None
    if not in_run_b:
        return MigrationCategory.ABSENT_FROM_LATER, None
    if label_before is None and label_after is None:
        return MigrationCategory.UNCHANGED, None
    if label_before is None:
        return MigrationCategory.NEWLY_ASSIGNED, None
    if label_after is None:
        return MigrationCategory.NEWLY_UNASSIGNED, None
    if label_before == label_after:
        return MigrationCategory.UNCHANGED, None

    # Lower ordinal_position is better (ADR-0018: declared best-to-worst).
    # A label missing from ordinals (should not happen with a consistent
    # vocabulary) leaves direction unset rather than guessing.
    before_rank = ordinals.get(label_before)
    after_rank = ordinals.get(label_after)
    direction = None
    if before_rank is not None and after_rank is not None:
        direction = (
            Direction.IMPROVED if after_rank < before_rank else Direction.DECLINED
        )
    return MigrationCategory.MOVED, direction


def classify_migration(
    labels_a: dict[Any, str | None],
    labels_b: dict[Any, str | None],
    ordinals: dict[str, int],
) -> list[CustomerMigration]:
    """Classify every customer present in either labels_a (earlier) or
    labels_b (later). Pure function — no database access, so it is the unit
    this module's tests exercise directly.

    A customer present in only one input is its own category rather than
    dropped, since a plain dict union already includes every key from both.
    """
    all_customers = set(labels_a) | set(labels_b)

    result = []
    for customer_id in sorted(all_customers, key=str):
        category, direction = _classify(
            labels_a.get(customer_id),
            labels_b.get(customer_id),
            in_run_a=customer_id in labels_a,
            in_run_b=customer_id in labels_b,
            ordinals=ordinals,
        )
        result.append(
            CustomerMigration(
                customer_id=customer_id,
                label_before=labels_a.get(customer_id),
                label_after=labels_b.get(customer_id),
                category=category,
                direction=direction,
            )
        )
    return result


def compute_migration(
    connection: Any, run_id_a: int, run_id_b: int
) -> list[CustomerMigration]:
    """Read both runs and classify every customer between them.

    Refuses an unknown run id or a reversed pair rather than silently
    returning a misleading result (ADR-0018's "plausible, no error" failure
    mode): both runs are fetched by id and ordered by their own run_at,
    never by the order the caller happened to pass them in, and never by
    method.
    """
    # Imported here, not at module load, to keep this module importable
    # without web.db in a pure-unit test context.
    from web.db.segments import get_label_ordinals, get_run_at, list_run_labels

    run_at_a = get_run_at(connection, run_id_a)
    run_at_b = get_run_at(connection, run_id_b)
    if run_at_a is None:
        raise UnknownRun(f"Run {run_id_a} does not exist.")
    if run_at_b is None:
        raise UnknownRun(f"Run {run_id_b} does not exist.")

    earlier_id, later_id = (
        (run_id_a, run_id_b) if run_at_a <= run_at_b else (run_id_b, run_id_a)
    )

    labels_earlier = list_run_labels(connection, earlier_id)
    labels_later = list_run_labels(connection, later_id)
    ordinals = get_label_ordinals(connection)

    return classify_migration(labels_earlier, labels_later, ordinals)
