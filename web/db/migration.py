"""Segment migration between two runs (F7-04) — read-only, web/db layer.

ADR-0018 is explicit about the failure mode this exists to avoid: comparing
raw K-means cluster ids across two runs reports 100% migration on every run,
silently, because a cluster's arbitrary id has no meaning outside the fit
that produced it. The comparison here is on the stable label code only —
never on segment_id, and never on segmentation_run.method. Two runs from
different methods (RFM_RULES and KMEANS) compare cleanly because the label
vocabulary is the one contract both methods write to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from psycopg import Connection


class MigrationCategory(Enum):
    """Every customer between two runs falls into exactly one of these."""

    UNCHANGED = "unchanged"
    MOVED = "moved"
    NEWLY_ASSIGNED = "newly_assigned"
    NEWLY_UNASSIGNED = "newly_unassigned"
    ABSENT_FROM_EARLIER = "absent_from_earlier"
    ABSENT_FROM_LATER = "absent_from_later"


@dataclass(frozen=True)
class CustomerMigration:
    """One customer's classification between run_a (earlier) and run_b
    (later). label_before/label_after are None exactly when the customer was
    unassigned in that run, or absent from it entirely — category
    disambiguates which."""

    customer_id: str
    label_before: str | None
    label_after: str | None
    category: MigrationCategory


def _classify(
    label_before: str | None,
    label_after: str | None,
    *,
    in_run_a: bool,
    in_run_b: bool,
) -> MigrationCategory:
    """The classification rule, isolated so cluster_id_permutation-style
    tests can drive it directly without a database."""
    if not in_run_a:
        return MigrationCategory.ABSENT_FROM_EARLIER
    if not in_run_b:
        return MigrationCategory.ABSENT_FROM_LATER
    if label_before is None and label_after is None:
        return MigrationCategory.UNCHANGED
    if label_before is None:
        return MigrationCategory.NEWLY_ASSIGNED
    if label_after is None:
        return MigrationCategory.NEWLY_UNASSIGNED
    if label_before == label_after:
        return MigrationCategory.UNCHANGED
    return MigrationCategory.MOVED


_ASSIGNMENTS_FOR_RUN = """
    SELECT customer_id, label_code
    FROM customer_segment_history
    WHERE run_id = %s
"""


def compute_migration(
    connection: Connection[Any], run_id_a: int, run_id_b: int
) -> list[CustomerMigration]:
    """Classify every customer that appears in either run_id_a (earlier) or
    run_id_b (later), comparing label_code only.

    Never reads segmentation_run.method and never reads segment_id — ADR-0018
    requires the comparison to be method-agnostic and cluster-id-blind, so
    those two columns are simply not part of the query this function issues.
    A customer present in only one run is its own category rather than
    dropped by a join that would otherwise silently exclude them.
    """
    with connection.cursor() as cursor:
        cursor.execute(_ASSIGNMENTS_FOR_RUN, (run_id_a,))
        labels_a = dict(cursor.fetchall())
        cursor.execute(_ASSIGNMENTS_FOR_RUN, (run_id_b,))
        labels_b = dict(cursor.fetchall())

    all_customers = set(labels_a) | set(labels_b)

    return [
        CustomerMigration(
            customer_id=customer_id,
            label_before=labels_a.get(customer_id),
            label_after=labels_b.get(customer_id),
            category=_classify(
                labels_a.get(customer_id),
                labels_b.get(customer_id),
                in_run_a=customer_id in labels_a,
                in_run_b=customer_id in labels_b,
            ),
        )
        for customer_id in sorted(all_customers)
    ]
